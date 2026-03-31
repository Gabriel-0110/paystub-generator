import os
import json
import shutil
import subprocess
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
import pypdfium2 as pdfium

from generators.batch_generator import (
    build_ytd_state,
    generate_all_stubs_for_employee,
    generate_full_year_batch,
    generate_one_stub_for_assignment,
)
from generators.pdf_generator import PaystubTemplate, amount_to_words, generate_paystub_pdf
from models.pay_period import (
    BusinessDayAdjustment,
    PayFrequency,
    adjust_business_day,
    get_pay_periods,
    us_federal_holidays,
)
from models.profile_store import (
    CompanyProfile,
    DeductionDefaultsProfile,
    EmployeeProfile,
    PayrollAssignmentProfile,
    TaxDefaultsProfile,
    list_profiles,
    load_company_profile,
    load_deduction_defaults_profile,
    load_employee_pay_config,
    load_employee_profile,
    load_payroll_assignment_profile,
    load_tax_defaults_profile,
    save_company_profile,
    save_deduction_defaults_profile,
    save_employee_profile,
    save_payroll_assignment_profile,
    save_tax_defaults_profile,
    split_employee_pay_config,
)
from models.profile_io import (
    export_profiles_csv,
    export_profiles_excel,
    export_profiles_json,
    import_profiles_csv,
    import_profiles_excel,
    import_profiles_json,
)
from models.payroll_calculator import compute_paystub_data
from models.payroll_calculator import BenefitLine, DeductionLine, EarningLine, FilingStatus
from sample_data import sample_employee
from tests.pdf_snapshot_utils import (
    DEFAULT_FAILURE_PREVIEW_DIR,
    SNAPSHOT_FIXTURE,
    build_long_content_paystub_data,
    build_sample_paystub_data,
    load_snapshot_fixture,
    render_template_snapshots,
    save_pdf_preview_pngs,
)
from tests.web_snapshot_utils import (
    DEFAULT_FAILURE_PREVIEW_DIR as WEB_FAILURE_PREVIEW_DIR,
    capture_webapp_snapshots,
    load_snapshot_fixture as load_web_snapshot_fixture,
)
from webapp import service as web_service
from webapp.app import app


class PayPeriodTests(unittest.TestCase):
    def test_us_holidays_are_generated_for_any_year(self) -> None:
        holidays_2027 = us_federal_holidays(2027)
        self.assertIn(date(2027, 7, 5), holidays_2027)
        self.assertIn(date(2027, 11, 25), holidays_2027)

    def test_business_day_adjustment_supports_following_and_preceding(self) -> None:
        holiday = date(2027, 7, 5)
        self.assertEqual(
            adjust_business_day(holiday, adjustment=BusinessDayAdjustment.FOLLOWING),
            date(2027, 7, 6),
        )
        self.assertEqual(
            adjust_business_day(holiday, adjustment=BusinessDayAdjustment.PRECEDING),
            date(2027, 7, 2),
        )

    def test_biweekly_schedule_adjusts_holiday_pay_date(self) -> None:
        periods = get_pay_periods(2026, PayFrequency.BIWEEKLY)
        self.assertEqual(periods[0].start, date(2026, 1, 1))
        self.assertEqual(periods[0].end, date(2026, 1, 14))
        self.assertEqual(periods[0].pay_date, date(2026, 1, 20))


class YTDEngineTests(unittest.TestCase):
    def test_build_ytd_state_rolls_forward_prior_periods(self) -> None:
        periods = get_pay_periods(2026, sample_employee.frequency)

        first_period = periods[0]
        first_data = compute_paystub_data(
            sample_employee,
            first_period.start,
            first_period.end,
            first_period.pay_date,
        )

        seeded_ytd = build_ytd_state(sample_employee, 2026, through_period_number=1)

        second_period = periods[1]
        second_data = compute_paystub_data(
            sample_employee,
            second_period.start,
            second_period.end,
            second_period.pay_date,
            ytd=seeded_ytd,
        )

        self.assertEqual(second_data["gross_pay_ytd"], round(first_data["gross_pay_current"] * 2, 2))
        self.assertEqual(second_data["net_pay_ytd"], round(first_data["net_pay_current"] * 2, 2))

        tax_map = {item["label"]: item["ytd"] for item in second_data["taxes"]}
        deduction_map = {item["label"]: item["ytd"] for item in second_data["deductions"]}
        benefit_map = {item["label"]: item["ytd"] for item in second_data["other_benefits"]}

        self.assertEqual(
            tax_map["Federal Income Tax"],
            round(first_data["taxes"][0]["current"] * 2, 2),
        )
        self.assertEqual(deduction_map["401(k)"], 125.0)
        self.assertEqual(benefit_map["Group Term Life"], 1.02)
        self.assertEqual(benefit_map["Accrual Hrs"], 80.0)

    def test_generation_plan_rolls_ytd_forward_for_multiple_stubs(self) -> None:
        paystub = build_sample_paystub_data()
        plan = web_service.generation_plan_payload(
            paystub,
            {
                "mode": "multiple",
                "sequence_type": "pay_frequency",
                "pay_frequency": "biweekly",
                "stub_count": 3,
            },
        )

        self.assertEqual(plan["stub_count"], 3)
        self.assertEqual(plan["entries"][0]["pay_date"], paystub["pay_date"])
        self.assertGreater(plan["entries"][1]["gross_pay_ytd"], plan["entries"][0]["gross_pay_ytd"])
        self.assertGreater(plan["entries"][2]["net_pay_ytd"], plan["entries"][1]["net_pay_ytd"])

    def test_generation_schedule_falls_back_to_day_based_roll_forward_when_dates_do_not_match_frequency(self) -> None:
        paystub = build_sample_paystub_data()
        paystub["pay_period_start"] = "2026-01-03"
        paystub["pay_period_end"] = "2026-01-16"
        paystub["pay_date"] = "2026-01-23"

        schedule = web_service.build_generation_schedule(
            paystub,
            {
                "mode": "multiple",
                "sequence_type": "pay_frequency",
                "pay_frequency": "biweekly",
                "stub_count": 3,
            },
        )

        self.assertEqual(schedule["periods"][0]["pay_period_start"], "2026-01-03")
        self.assertEqual(schedule["periods"][1]["pay_period_start"], "2026-01-17")
        self.assertEqual(schedule["periods"][2]["pay_date"], "2026-02-20")

    def test_weekly_generation_uses_seven_day_spacing(self) -> None:
        paystub = build_sample_paystub_data()
        paystub["pay_period_start"] = "2026-01-03"
        paystub["pay_period_end"] = "2026-01-09"
        paystub["pay_date"] = "2026-01-10"

        schedule = web_service.build_generation_schedule(
            paystub,
            {
                "mode": "multiple",
                "sequence_type": "weekly",
                "pay_frequency": "weekly",
                "stub_count": 3,
            },
        )

        self.assertEqual(schedule["periods"][0]["pay_period_start"], "2026-01-03")
        self.assertEqual(schedule["periods"][1]["pay_period_start"], "2026-01-10")
        self.assertEqual(schedule["periods"][2]["pay_period_start"], "2026-01-17")
        self.assertEqual(schedule["periods"][1]["pay_date"], "2026-01-17")

    def test_generation_plan_rejects_invalid_multiple_stub_counts(self) -> None:
        paystub = build_sample_paystub_data()

        with self.assertRaisesRegex(Exception, "between 1 and 26"):
            web_service.build_generation_schedule(
                paystub,
                {
                    "mode": "multiple",
                    "sequence_type": "pay_frequency",
                    "pay_frequency": "biweekly",
                    "stub_count": 27,
                },
            )

    def test_generation_schedule_supports_latest_anchor(self) -> None:
        paystub = build_sample_paystub_data()
        schedule = web_service.build_generation_schedule(
            paystub,
            {
                "mode": "multiple",
                "sequence_type": "pay_frequency",
                "pay_frequency": "biweekly",
                "stub_count": 3,
                "anchor": "latest",
            },
        )

        self.assertEqual(schedule["periods"][-1]["pay_date"], paystub["pay_date"])
        self.assertLess(schedule["periods"][0]["pay_date"], schedule["periods"][-1]["pay_date"])
        self.assertEqual(schedule["summary"]["anchor"], "latest")

    def test_guided_draft_preview_calculates_taxes_without_extra_deductions(self) -> None:
        draft = web_service.empty_paystub_payload()
        draft.update(
            {
                "company_name": "Acme Payroll LLC",
                "company_address": "1 Main St\nAlbany, NY 12207",
                "employee_name": "Jamie Doe",
                "employee_id": "EMP-9001",
                "taxable_marital_status": "Single",
                "work_state": "NY",
                "pay_frequency": "biweekly",
                "pay_period_start": "2026-01-01",
                "pay_period_end": "2026-01-14",
                "pay_date": "2026-01-16",
                "payroll_check_number": "000000101",
                "compensation_type": "hourly",
                "hourly_rate": 25.0,
                "regular_hours": 80.0,
            }
        )

        preview = web_service.preview_payload(draft)

        self.assertEqual(preview["summary"]["gross_pay_current"], 2000.0)
        self.assertIn("Federal Income Tax", [item["label"] for item in preview["paystub"]["taxes"]])
        self.assertIn("NY State Income Tax", [item["label"] for item in preview["paystub"]["taxes"]])
        self.assertEqual(preview["paystub"]["deductions"], [])
        self.assertEqual(preview["paystub"]["other_benefits"], [])

    def test_guided_salary_draft_derives_hourly_rate_from_annual_salary_and_weekly_hours(self) -> None:
        draft = web_service.empty_paystub_payload()
        draft.update(
            {
                "company_name": "Acme Payroll LLC",
                "company_address": "1 Main St\nAlbany, NY 12207",
                "employee_name": "Jamie Doe",
                "employee_id": "EMP-9001",
                "taxable_marital_status": "Single",
                "work_state": "NY",
                "pay_frequency": "biweekly",
                "pay_period_start": "2026-01-01",
                "pay_period_end": "2026-01-14",
                "pay_date": "2026-01-16",
                "payroll_check_number": "000000101",
                "compensation_type": "salary",
                "annual_salary": 104000.0,
                "weekly_hours": 40.0,
            }
        )

        preview = web_service.preview_payload(draft)

        primary_line = preview["paystub"]["earnings"][0]
        self.assertEqual(preview["paystub"]["hourly_rate"], 50.0)
        self.assertEqual(preview["paystub"]["regular_hours"], 80.0)
        self.assertEqual(primary_line["rate"], 50.0)
        self.assertEqual(primary_line["hours"], 80.0)
        self.assertEqual(primary_line["current"], 4000.0)
        self.assertIn("Federal Income Tax", [item["label"] for item in preview["paystub"]["taxes"]])

    def test_guided_salary_draft_can_use_salary_period_amount(self) -> None:
        draft = web_service.empty_paystub_payload()
        draft.update(
            {
                "company_name": "Acme Payroll LLC",
                "company_address": "1 Main St\nAlbany, NY 12207",
                "employee_name": "Jamie Doe",
                "employee_id": "EMP-9001",
                "taxable_marital_status": "Single",
                "work_state": "NY",
                "pay_frequency": "weekly",
                "pay_period_start": "2026-01-01",
                "pay_period_end": "2026-01-07",
                "pay_date": "2026-01-09",
                "payroll_check_number": "000000101",
                "compensation_type": "salary",
                "salary_period_amount": 1250.0,
                "weekly_hours": 40.0,
            }
        )

        preview = web_service.preview_payload(draft)

        primary_line = preview["paystub"]["earnings"][0]
        self.assertEqual(preview["paystub"]["salary_period_amount"], 1250.0)
        self.assertEqual(preview["paystub"]["annual_salary"], 65000.0)
        self.assertEqual(preview["paystub"]["hourly_rate"], 31.25)
        self.assertEqual(preview["paystub"]["regular_hours"], 40.0)
        self.assertEqual(primary_line["current"], 1250.0)

    def test_guided_draft_generation_plan_rolls_ytd_forward_from_latest_anchor(self) -> None:
        draft = web_service.empty_paystub_payload()
        draft.update(
            {
                "company_name": "Acme Payroll LLC",
                "company_address": "1 Main St\nAlbany, NY 12207",
                "employee_name": "Jamie Doe",
                "employee_id": "EMP-9001",
                "taxable_marital_status": "Single",
                "work_state": "NY",
                "pay_frequency": "biweekly",
                "pay_period_start": "2026-03-05",
                "pay_period_end": "2026-03-18",
                "pay_date": "2026-03-20",
                "payroll_check_number": "000000205",
                "compensation_type": "hourly",
                "hourly_rate": 30.0,
                "regular_hours": 80.0,
            }
        )
        plan = web_service.generation_plan_payload(
            draft,
            {
                "mode": "multiple",
                "sequence_type": "pay_frequency",
                "pay_frequency": "biweekly",
                "stub_count": 3,
                "anchor": "latest",
            },
        )

        self.assertEqual(plan["entries"][-1]["pay_date"], "2026-03-20")
        self.assertGreater(plan["entries"][-1]["gross_pay_ytd"], plan["entries"][0]["gross_pay_ytd"])
        self.assertEqual(plan["entries"][-1]["gross_pay_ytd"], round(plan["entries"][-1]["gross_pay_current"] * 3, 2))

    def test_guided_draft_generation_plan_can_use_fixed_stub_amount(self) -> None:
        draft = web_service.empty_paystub_payload()
        draft.update(
            {
                "company_name": "Acme Payroll LLC",
                "company_address": "1 Main St\nAlbany, NY 12207",
                "employee_name": "Jamie Doe",
                "employee_id": "EMP-9001",
                "taxable_marital_status": "Single",
                "work_state": "NY",
                "pay_frequency": "weekly",
                "pay_period_start": "2026-01-01",
                "pay_period_end": "2026-01-07",
                "pay_date": "2026-01-09",
                "compensation_type": "hourly",
                "hourly_rate": 25.0,
                "regular_hours": 40.0,
            }
        )

        plan = web_service.generation_plan_payload(
            draft,
            {
                "mode": "multiple",
                "sequence_type": "weekly",
                "pay_frequency": "weekly",
                "stub_count": 3,
                "amount_mode": "fixed",
                "fixed_amount": 1250.0,
            },
        )

        self.assertEqual([entry["gross_pay_current"] for entry in plan["entries"]], [1250.0, 1250.0, 1250.0])

    def test_guided_draft_generation_plan_can_use_manual_stub_amounts(self) -> None:
        draft = web_service.empty_paystub_payload()
        draft.update(
            {
                "company_name": "Acme Payroll LLC",
                "company_address": "1 Main St\nAlbany, NY 12207",
                "employee_name": "Jamie Doe",
                "employee_id": "EMP-9001",
                "taxable_marital_status": "Single",
                "work_state": "NY",
                "pay_frequency": "weekly",
                "pay_period_start": "2026-01-01",
                "pay_period_end": "2026-01-07",
                "pay_date": "2026-01-09",
                "compensation_type": "hourly",
                "hourly_rate": 25.0,
                "regular_hours": 40.0,
            }
        )

        plan = web_service.generation_plan_payload(
            draft,
            {
                "mode": "multiple",
                "sequence_type": "weekly",
                "pay_frequency": "weekly",
                "stub_count": 3,
                "amount_mode": "manual",
                "manual_amounts": [1250.0, 1325.0, 1190.0],
            },
        )

        self.assertEqual([entry["gross_pay_current"] for entry in plan["entries"]], [1250.0, 1325.0, 1190.0])


class TemplateRendererTests(unittest.TestCase):
    maxDiff = None

    def _render_templates(self, temp_dir: str, data: dict, case_name: str) -> dict[str, str]:
        hashes, pdf_paths = render_template_snapshots(temp_dir, data, case_name)
        for pdf_path in pdf_paths.values():
            self.assertTrue(pdf_path.exists())
            self.assertGreater(pdf_path.stat().st_size, 0)
        return hashes

    def _assert_snapshot_case(self, case_name: str, data: dict) -> None:
        expected = load_snapshot_fixture()
        with TemporaryDirectory() as temp_dir:
            actual, pdf_paths = render_template_snapshots(temp_dir, data, case_name)

            expected_case = {key: expected[key] for key in actual}
            if actual != expected_case:
                preview_message = ""
                if os.environ.get("PAYSTUB_SAVE_SNAPSHOT_PREVIEWS"):
                    preview_root = Path(
                        os.environ.get("PAYSTUB_SNAPSHOT_PREVIEW_DIR", str(DEFAULT_FAILURE_PREVIEW_DIR))
                    )
                    case_preview_dir = preview_root / case_name
                    for snapshot_key, pdf_path in pdf_paths.items():
                        save_pdf_preview_pngs(
                            pdf_path,
                            case_preview_dir,
                            snapshot_key.split(":", 1)[1],
                        )
                    preview_message = f" Preview PNGs written to {case_preview_dir}."

                self.fail(
                    f"Snapshot mismatch for '{case_name}'. "
                    f"Update {SNAPSHOT_FIXTURE} if the change is intentional.{preview_message}"
                )

    def test_amount_to_words_includes_only_and_rounds_cleanly(self) -> None:
        self.assertEqual(amount_to_words(1793.09), "ONE THOUSAND SEVEN HUNDRED NINETY-THREE AND 09/100 DOLLARS ONLY")
        self.assertEqual(amount_to_words(10.999), "ELEVEN AND 00/100 DOLLARS ONLY")

    def test_all_templates_render_distinct_pdfs(self) -> None:
        data = build_sample_paystub_data()

        with TemporaryDirectory() as temp_dir:
            paths = []
            for template in (
                PaystubTemplate.ADP,
                PaystubTemplate.SIMPLE,
                PaystubTemplate.DETACHED_CHECK,
            ):
                path = Path(generate_paystub_pdf(data, output_dir=temp_dir, template=template))
                self.assertTrue(path.exists())
                self.assertGreater(path.stat().st_size, 0)
                paths.append(path.name)

        self.assertEqual(len(paths), 3)
        self.assertEqual(len(set(paths)), 3)

    def test_visual_snapshots_match_baseline(self) -> None:
        self._assert_snapshot_case("sample", build_sample_paystub_data())

    def test_long_content_visual_snapshots_match_baseline(self) -> None:
        self._assert_snapshot_case("long_content", build_long_content_paystub_data())

    def test_detached_check_pdf_uses_live_values_without_sample_placeholders(self) -> None:
        data = build_sample_paystub_data()
        data.update(
            {
                "pay_period_end": "2026-02-28",
                "pay_date": "2026-03-05",
                "payroll_check_number": "000004219",
                "allowances_count": 0,
                "additional_federal_withholding": 0.0,
                "work_state": "NY",
                "social_security_number": "109-96-8419",
                "employee_address": "4834 64TH STREET, FL 2\nWOODSIDE, NY 11377",
                "other_benefits": [],
                "important_notes": [],
                "footnotes": [],
                "bank_name": "JP Morgan Chase Bank, N.A.",
                "deposit_account_type": "Checking",
                "account_number": "XXXX6986",
                "routing_number": "021000021",
            }
        )

        with TemporaryDirectory() as temp_dir:
            path = Path(generate_paystub_pdf(data, output_dir=temp_dir, template=PaystubTemplate.DETACHED_CHECK))
            pdf = pdfium.PdfDocument(str(path))
            page = pdf[0]
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            textpage.close()
            page.close()
            pdf.close()

        self.assertIn("Period ending: 2026-02-28", text)
        self.assertIn("Pay date: 2026-03-05", text)
        self.assertIn("4834 64TH STREET, FL 2", text)
        self.assertIn("WOODSIDE, NY 11377", text)
        self.assertIn("Payroll check number: 000004219", text)
        self.assertIn("Taxable Marital Status: Single", text)
        self.assertIn("State: NY", text)
        self.assertNotIn("Federal: Federal:", text)
        self.assertNotIn("Additional Tax: 0.00", text)
        self.assertIn("Social Security No. xxx-xx-8419", text)
        self.assertNotIn("Social Security No. 109-96-8419", text)
        self.assertNotIn("SAMPLE", text)
        self.assertNotIn("VOID", text)
        self.assertNotIn("NON-NEGOTIABLE", text)
        self.assertNotIn("BANK NAME", text)
        self.assertNotIn("CITY, STATE ZIP", text)
        self.assertIn("OTHER BENEFITS AND INFORMATION", text)
        self.assertIn("IMPORTANT NOTES", text)
        self.assertNotIn("No employer-paid items", text)


class ProfileStoreTests(unittest.TestCase):
    def test_profile_round_trip_and_config_build(self) -> None:
        company = CompanyProfile(
            profile_id="acme",
            company_name="Acme Payroll LLC",
            company_address="500 MARKET ST\nMETROPOLIS, USA 10001",
            default_payroll_check_number="000000321",
        )
        employee = EmployeeProfile(
            profile_id="jane_doe",
            employee_id="EMP-2001",
            employee_name="Jane Doe",
            employee_address="100 OAK AVE\nMETROPOLIS, USA 10002",
            social_security_number="111-22-3333",
            earnings=[EarningLine(label="Regular", rate=40.0, hours=80.0)],
            other_benefits=[BenefitLine(label="PTO Hrs", current=8.0, ytd=8.0)],
            important_notes=["REMOTE EMPLOYEE"],
        )
        tax_defaults = TaxDefaultsProfile(
            profile_id="ca_biweekly",
            filing_status=FilingStatus.SINGLE,
            frequency=PayFrequency.BIWEEKLY,
            state="CA",
            local_tax_rate=0.01,
            local_tax_label="Local Payroll Tax",
        )
        deductions = DeductionDefaultsProfile(
            profile_id="std_dedns",
            pre_tax_deductions=[DeductionLine(label="401(k)", amount=100.0, is_pretax=True)],
            post_tax_deductions=[DeductionLine(label="Union", amount=25.0, is_pretax=False)],
        )

        with TemporaryDirectory() as temp_dir:
            save_company_profile(company, root=temp_dir)
            save_employee_profile(employee, root=temp_dir)
            save_tax_defaults_profile(tax_defaults, root=temp_dir)
            save_deduction_defaults_profile(deductions, root=temp_dir)

            self.assertEqual(list_profiles("company", root=temp_dir), ["acme"])
            self.assertEqual(load_company_profile("acme", root=temp_dir).company_name, company.company_name)
            self.assertEqual(load_employee_profile("jane_doe", root=temp_dir).employee_id, employee.employee_id)
            self.assertEqual(load_tax_defaults_profile("ca_biweekly", root=temp_dir).state, "CA")
            self.assertEqual(
                load_deduction_defaults_profile("std_dedns", root=temp_dir).post_tax_deductions[0].label,
                "Union",
            )

            config = load_employee_pay_config(
                company_profile_id="acme",
                employee_profile_id="jane_doe",
                tax_profile_id="ca_biweekly",
                deduction_profile_id="std_dedns",
                root=temp_dir,
            )

        self.assertEqual(config.company_name, "Acme Payroll LLC")
        self.assertEqual(config.employee_name, "Jane Doe")
        self.assertEqual(config.frequency, PayFrequency.BIWEEKLY)
        self.assertEqual(config.local_tax_label, "Local Payroll Tax")
        self.assertEqual(config.pre_tax_deductions[0].label, "401(k)")
        self.assertEqual(config.post_tax_deductions[0].label, "Union")
        self.assertEqual(config.payroll_check_number, "000000321")

    def test_sample_profiles_load_into_existing_config_model(self) -> None:
        self.assertEqual(sample_employee.company_name, "Northwind Ops LLC")
        self.assertEqual(sample_employee.employee_id, "EMP-2001")
        self.assertEqual(sample_employee.frequency, PayFrequency.BIWEEKLY)
        self.assertEqual(sample_employee.pre_tax_deductions[0].label, "401(k)")

    def test_existing_config_can_be_split_into_profiles(self) -> None:
        company, employee, tax_defaults, deductions = split_employee_pay_config(
            sample_employee,
            company_profile_id="company_copy",
            employee_profile_id="employee_copy",
            tax_profile_id="tax_copy",
            deduction_profile_id="deduction_copy",
        )

        self.assertEqual(company.company_name, sample_employee.company_name)
        self.assertEqual(employee.employee_name, sample_employee.employee_name)
        self.assertEqual(tax_defaults.state, sample_employee.state)
        self.assertEqual(deductions.pre_tax_deductions[0].label, "401(k)")


class ProfileIOTests(unittest.TestCase):
    def _seed_profiles(self, root: str) -> None:
        save_company_profile(
            CompanyProfile(
                profile_id="acme",
                company_name="Acme Payroll LLC",
                company_address="500 MARKET ST\nMETROPOLIS, USA 10001",
                default_payroll_check_number="000000321",
            ),
            root=root,
        )
        save_employee_profile(
            EmployeeProfile(
                profile_id="jane_doe",
                employee_id="EMP-2001",
                employee_name="Jane Doe",
                employee_address="100 OAK AVE\nMETROPOLIS, USA 10002",
                bank_name="Metro Credit Union",
                deposit_account_type="checking",
                routing_number="021000021",
                account_number="9876543210",
                direct_deposit_amount=1200.0,
                social_security_number="111-22-3333",
                earnings=[EarningLine(label="Regular", rate=40.0, hours=80.0)],
                other_benefits=[BenefitLine(label="PTO Hrs", current=8.0, ytd=8.0)],
                important_notes=["REMOTE EMPLOYEE"],
            ),
            root=root,
        )
        save_tax_defaults_profile(
            TaxDefaultsProfile(
                profile_id="ca_biweekly",
                filing_status=FilingStatus.SINGLE,
                frequency=PayFrequency.BIWEEKLY,
                allowances=1,
                additional_federal_wh=15.0,
                state="CA",
                local_tax_rate=0.01,
                local_tax_label="Local Payroll Tax",
            ),
            root=root,
        )
        save_deduction_defaults_profile(
            DeductionDefaultsProfile(
                profile_id="std_dedns",
                pre_tax_deductions=[DeductionLine(label="401(k)", amount=100.0, is_pretax=True)],
                post_tax_deductions=[DeductionLine(label="Union", amount=25.0, is_pretax=False)],
            ),
            root=root,
        )
        save_payroll_assignment_profile(
            PayrollAssignmentProfile(
                profile_id="jane_payroll",
                company_profile_id="acme",
                employee_profile_id="jane_doe",
                tax_profile_id="ca_biweekly",
                deduction_profile_id="std_dedns",
                payroll_check_number_start=321,
            ),
            root=root,
        )

    def _assert_imported_profiles(self, root: str) -> None:
        self.assertEqual(list_profiles("company", root=root), ["acme"])
        self.assertEqual(load_company_profile("acme", root=root).company_name, "Acme Payroll LLC")
        self.assertEqual(load_employee_profile("jane_doe", root=root).employee_id, "EMP-2001")
        self.assertEqual(load_tax_defaults_profile("ca_biweekly", root=root).allowances, 1)
        self.assertEqual(
            load_deduction_defaults_profile("std_dedns", root=root).post_tax_deductions[0].label,
            "Union",
        )
        self.assertEqual(
            load_payroll_assignment_profile("jane_payroll", root=root).payroll_check_number_start,
            321,
        )

    def test_json_export_import_round_trip(self) -> None:
        with TemporaryDirectory() as source_root, TemporaryDirectory() as target_root, TemporaryDirectory() as temp_dir:
            self._seed_profiles(source_root)
            export_path = export_profiles_json(Path(temp_dir) / "profiles.json", root=source_root)
            import_profiles_json(export_path, root=target_root)
            self._assert_imported_profiles(target_root)

    def test_csv_export_import_round_trip(self) -> None:
        with TemporaryDirectory() as source_root, TemporaryDirectory() as target_root, TemporaryDirectory() as temp_dir:
            self._seed_profiles(source_root)
            export_dir = Path(temp_dir) / "csv_bundle"
            export_profiles_csv(export_dir, root=source_root)
            import_profiles_csv(export_dir, root=target_root)
            self._assert_imported_profiles(target_root)

    def test_excel_export_import_round_trip(self) -> None:
        with TemporaryDirectory() as source_root, TemporaryDirectory() as target_root, TemporaryDirectory() as temp_dir:
            self._seed_profiles(source_root)
            export_path = export_profiles_excel(Path(temp_dir) / "profiles.xlsx", root=source_root)
            import_profiles_excel(export_path, root=target_root)
            self._assert_imported_profiles(target_root)


class AssignmentBatchTests(unittest.TestCase):
    def test_generate_one_stub_for_assignment(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(
                generate_one_stub_for_assignment(
                    assignment_profile_id="sample_payroll",
                    year=2026,
                    period_number=1,
                    output_dir=temp_dir,
                    template=PaystubTemplate.SIMPLE,
                )
            )
            self.assertTrue(path.exists())
            self.assertIn("simple", path.name)

    def test_generate_all_stubs_for_one_employee(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = generate_all_stubs_for_employee(
                assignment_profile_id="sample_payroll",
                year=2026,
                output_dir=temp_dir,
                template=PaystubTemplate.ADP,
            )
            self.assertEqual(len(paths), 26)
            self.assertTrue(all(Path(path).exists() for path in paths))

    def test_generate_full_year_batch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            batches = generate_full_year_batch(
                year=2026,
                assignment_profile_ids=["sample_payroll"],
                output_dir=temp_dir,
                template=PaystubTemplate.DETACHED_CHECK,
            )
            self.assertEqual(list(batches.keys()), ["sample_payroll"])
            self.assertEqual(len(batches["sample_payroll"]), 26)
            self.assertTrue(all(Path(path).exists() for path in batches["sample_payroll"]))


class WebAppTests(unittest.TestCase):
    def _seed_profiles(self, root: str) -> None:
        save_company_profile(
            CompanyProfile(
                profile_id="acme",
                company_name="Acme Payroll LLC",
                company_address="500 MARKET ST\nMETROPOLIS, USA 10001",
                default_payroll_check_number="000000321",
            ),
            root=root,
        )
        save_employee_profile(
            EmployeeProfile(
                profile_id="jane_doe",
                employee_id="EMP-2001",
                employee_name="Jane Doe",
                employee_address="100 OAK AVE\nMETROPOLIS, USA 10002",
                bank_name="Metro Credit Union",
                deposit_account_type="checking",
                routing_number="021000021",
                account_number="9876543210",
                direct_deposit_amount=1200.0,
                social_security_number="111-22-3333",
                earnings=[EarningLine(label="Regular", rate=40.0, hours=80.0)],
                other_benefits=[BenefitLine(label="PTO Hrs", current=8.0, ytd=8.0)],
                important_notes=["REMOTE EMPLOYEE"],
            ),
            root=root,
        )
        save_tax_defaults_profile(
            TaxDefaultsProfile(
                profile_id="ca_biweekly",
                filing_status=FilingStatus.SINGLE,
                frequency=PayFrequency.BIWEEKLY,
                allowances=1,
                additional_federal_wh=15.0,
                state="CA",
                local_tax_rate=0.01,
                local_tax_label="Local Payroll Tax",
            ),
            root=root,
        )
        save_deduction_defaults_profile(
            DeductionDefaultsProfile(
                profile_id="std_dedns",
                pre_tax_deductions=[DeductionLine(label="401(k)", amount=100.0, is_pretax=True)],
                post_tax_deductions=[DeductionLine(label="Union", amount=25.0, is_pretax=False)],
            ),
            root=root,
        )
        save_payroll_assignment_profile(
            PayrollAssignmentProfile(
                profile_id="jane_payroll",
                company_profile_id="acme",
                employee_profile_id="jane_doe",
                tax_profile_id="ca_biweekly",
                deduction_profile_id="std_dedns",
                payroll_check_number_start=321,
            ),
            root=root,
        )

    def test_index_and_bootstrap_routes_load(self) -> None:
        client = TestClient(app)

        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Paystub Studio", page.text)

        bootstrap = client.get("/api/bootstrap")
        self.assertEqual(bootstrap.status_code, 200)
        payload = bootstrap.json()
        self.assertIn("sample_paystub", payload)
        self.assertEqual(payload["default_template"], PaystubTemplate.DETACHED_CHECK.value)
        self.assertIn("assignment_options", payload)
        self.assertIn("profile_summary", payload)

    def test_preview_and_generate_routes_reuse_existing_backend(self) -> None:
        client = TestClient(app)
        paystub = build_sample_paystub_data()

        preview = client.post("/api/preview", json={"paystub": paystub})
        self.assertEqual(preview.status_code, 200)
        preview_payload = preview.json()
        self.assertGreater(preview_payload["summary"]["net_pay_current"], 0)

        with TemporaryDirectory() as temp_dir:
            with patch.object(web_service, "WEB_OUTPUT_DIR", Path(temp_dir)):
                generate = client.post(
                    "/api/generate",
                    json={"template": PaystubTemplate.SIMPLE.value, "paystub": paystub},
                )
                self.assertEqual(generate.status_code, 200)
                generate_payload = generate.json()
                file_path = Path(generate_payload["path"])
                self.assertTrue(file_path.exists())
                self.assertEqual(generate_payload["download_url"], f"/api/downloads/{file_path.name}")

                download = client.get(generate_payload["download_url"])
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download.headers["content-type"], "application/pdf")

    def test_preview_and_generate_routes_support_multiple_stub_batches(self) -> None:
        client = TestClient(app)
        paystub = build_sample_paystub_data()
        generation_plan = {
            "mode": "multiple",
            "sequence_type": "pay_frequency",
            "pay_frequency": "biweekly",
            "stub_count": 3,
        }

        preview = client.post("/api/preview", json={"paystub": paystub, "generation_plan": generation_plan})
        self.assertEqual(preview.status_code, 200)
        preview_payload = preview.json()
        self.assertEqual(preview_payload["generation_plan"]["stub_count"], 3)
        self.assertEqual(len(preview_payload["generation_plan"]["entries"]), 3)

        with TemporaryDirectory() as temp_dir:
            with patch.object(web_service, "WEB_OUTPUT_DIR", Path(temp_dir)):
                generate = client.post(
                    "/api/generate",
                    json={"template": PaystubTemplate.SIMPLE.value, "paystub": paystub, "generation_plan": generation_plan},
                )
                self.assertEqual(generate.status_code, 200)
                generate_payload = generate.json()
                self.assertEqual(generate_payload["mode"], "multiple")
                self.assertEqual(generate_payload["document_count"], 3)
                self.assertTrue(Path(generate_payload["path"]).exists())
                self.assertEqual(generate_payload["download_url"], f"/api/downloads/{Path(generate_payload['path']).name}")
                self.assertEqual(len(generate_payload["documents"]), 3)

                download = client.get(generate_payload["download_url"])
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download.headers["content-type"], "application/zip")

    def test_assignment_routes_load_saved_profiles_into_preview(self) -> None:
        with TemporaryDirectory() as temp_dir:
            self._seed_profiles(temp_dir)
            with patch.object(web_service, "PROFILES_ROOT", Path(temp_dir)):
                client = TestClient(app)

                periods = client.get("/api/assignments/jane_payroll/periods", params={"year": 2026})
                self.assertEqual(periods.status_code, 200)
                period_payload = periods.json()
                self.assertEqual(period_payload["frequency"], PayFrequency.BIWEEKLY.value)
                self.assertEqual(period_payload["periods"][0]["check_number"], "000000321")

                load = client.post(
                    "/api/profiles/load-assignment",
                    json={"assignment_id": "jane_payroll", "year": 2026, "period_number": 1},
                )
                self.assertEqual(load.status_code, 200)
                load_payload = load.json()
                self.assertEqual(load_payload["paystub"]["employee_name"], "Jane Doe")
                self.assertEqual(load_payload["paystub"]["payroll_check_number"], "000000321")
                self.assertEqual(load_payload["paystub"]["bank_name"], "Metro Credit Union")
                self.assertEqual(load_payload["paystub"]["deposit_account_type"], "checking")
                self.assertGreater(load_payload["preview"]["summary"]["net_pay_current"], 0)

    def test_profile_export_and_import_routes_round_trip_json(self) -> None:
        with TemporaryDirectory() as source_root, TemporaryDirectory() as export_dir, TemporaryDirectory() as target_root:
            self._seed_profiles(source_root)
            client = TestClient(app)

            with patch.object(web_service, "PROFILES_ROOT", Path(source_root)):
                with patch.object(web_service, "PROFILE_EXPORT_DIR", Path(export_dir)):
                    export_response = client.post("/api/profiles/export", json={"file_format": "json"})
                    self.assertEqual(export_response.status_code, 200)
                    export_payload = export_response.json()
                    download = client.get(export_payload["download_url"])
                    self.assertEqual(download.status_code, 200)

            with patch.object(web_service, "PROFILES_ROOT", Path(target_root)):
                import_response = client.post(
                    "/api/profiles/import",
                    data={"file_format": "json"},
                    files={"upload": ("profiles.json", download.content, "application/json")},
                )
                self.assertEqual(import_response.status_code, 200)
                import_payload = import_response.json()
                self.assertEqual(import_payload["summary"]["assignments"], 1)
                self.assertEqual(import_payload["assignment_options"][0]["value"], "jane_payroll")

    def test_profile_import_route_can_infer_csv_bundle_format_from_filename(self) -> None:
        with TemporaryDirectory() as source_root, TemporaryDirectory() as export_dir, TemporaryDirectory() as target_root:
            self._seed_profiles(source_root)
            client = TestClient(app)

            with patch.object(web_service, "PROFILES_ROOT", Path(source_root)):
                with patch.object(web_service, "PROFILE_EXPORT_DIR", Path(export_dir)):
                    export_response = client.post("/api/profiles/export", json={"file_format": "csv"})
                    self.assertEqual(export_response.status_code, 200)
                    export_payload = export_response.json()
                    download = client.get(export_payload["download_url"])
                    self.assertEqual(download.status_code, 200)

            with patch.object(web_service, "PROFILES_ROOT", Path(target_root)):
                import_response = client.post(
                    "/api/profiles/import",
                    files={"upload": ("profiles.zip", download.content, "application/zip")},
                )
                self.assertEqual(import_response.status_code, 200)
                import_payload = import_response.json()
                self.assertEqual(import_payload["summary"]["assignments"], 1)
                self.assertEqual(import_payload["assignment_options"][0]["value"], "jane_payroll")

    def test_profile_editor_routes_can_load_templates_and_save_records(self) -> None:
        with TemporaryDirectory() as temp_dir:
            self._seed_profiles(temp_dir)
            client = TestClient(app)

            with patch.object(web_service, "PROFILES_ROOT", Path(temp_dir)):
                catalog = client.get("/api/profiles/catalog")
                self.assertEqual(catalog.status_code, 200)
                catalog_payload = catalog.json()
                self.assertIn("acme", catalog_payload["profile_catalog"]["company"])

                new_tax = client.get("/api/profiles/tax/_new")
                self.assertEqual(new_tax.status_code, 200)
                self.assertEqual(new_tax.json()["record"]["frequency"], PayFrequency.BIWEEKLY.value)

                loaded = client.get("/api/profiles/company/acme")
                self.assertEqual(loaded.status_code, 200)
                self.assertEqual(loaded.json()["record"]["company_name"], "Acme Payroll LLC")

                save = client.post(
                    "/api/profiles/company",
                    json={
                        "record": {
                            "profile_id": "bravo",
                            "company_name": "Bravo Payroll Group",
                            "company_address": "10 UNION SQ\nNEW YORK, NY 10003",
                            "default_payroll_check_number": "000000777",
                        }
                    },
                )
                self.assertEqual(save.status_code, 200)
                save_payload = save.json()
                self.assertIn("bravo", save_payload["profile_catalog"]["company"])
                self.assertEqual(load_company_profile("bravo", root=temp_dir).company_name, "Bravo Payroll Group")


class FakeHttpxResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.content = b"" if payload is None else self.text.encode("utf-8")

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> object:
        return self._payload


class SupabaseWebAppTests(unittest.TestCase):
    def _seed_store(self) -> dict[tuple[str, str], dict]:
        return {
            ("company", "acme"): {
                "profile_id": "acme",
                "company_name": "Acme Payroll LLC",
                "company_address": "500 MARKET ST\nMETROPOLIS, USA 10001",
                "default_payroll_check_number": "000000321",
            },
            ("employee", "jane_doe"): {
                "profile_id": "jane_doe",
                "employee_id": "EMP-2001",
                "employee_name": "Jane Doe",
                "employee_address": "100 OAK AVE\nMETROPOLIS, USA 10002",
                "social_security_number": "111-22-3333",
                "earnings": [{"label": "Regular", "rate": 40.0, "hours": 80.0}],
                "other_benefits": [{"label": "PTO Hrs", "current": 8.0, "ytd": 8.0}],
                "important_notes": ["REMOTE EMPLOYEE"],
            },
            ("tax", "ca_biweekly"): {
                "profile_id": "ca_biweekly",
                "filing_status": "Single",
                "frequency": "biweekly",
                "allowances": 1,
                "additional_federal_wh": 15.0,
                "state": "CA",
                "state_tax_rate_override": None,
                "local_tax_rate": 0.01,
                "local_tax_label": "Local Payroll Tax",
            },
            ("deduction", "std_dedns"): {
                "profile_id": "std_dedns",
                "pre_tax_deductions": [{"label": "401(k)", "amount": 100.0, "is_pretax": True}],
                "post_tax_deductions": [{"label": "Union", "amount": 25.0, "is_pretax": False}],
            },
            ("assignment", "jane_payroll"): {
                "profile_id": "jane_payroll",
                "company_profile_id": "acme",
                "employee_profile_id": "jane_doe",
                "tax_profile_id": "ca_biweekly",
                "deduction_profile_id": "std_dedns",
                "payroll_check_number_start": 321,
            },
        }

    def _patch_supabase(self, store: dict[tuple[str, str], dict]):
        def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
            params = params or {}
            if method == "GET":
                rows = []
                for profile_type, profile_id in sorted(store):
                    if params.get("profile_type") and params["profile_type"] != f"eq.{profile_type}":
                        continue
                    if params.get("profile_id") and params["profile_id"] != f"eq.{profile_id}":
                        continue
                    rows.append(
                        {
                            "profile_type": profile_type,
                            "profile_id": profile_id,
                            "payload": store[(profile_type, profile_id)],
                        }
                    )
                return FakeHttpxResponse(rows)

            if method == "POST":
                records = json if isinstance(json, list) else [json]
                rows = []
                for row in records:
                    key = (row["profile_type"], row["profile_id"])
                    store[key] = row["payload"]
                    rows.append(
                        {
                            "profile_type": row["profile_type"],
                            "profile_id": row["profile_id"],
                            "payload": store[key],
                        }
                    )
                return FakeHttpxResponse(rows)

            return FakeHttpxResponse({"message": f"Unsupported method {method}"}, status_code=405)

        return patch.object(web_service.httpx, "request", side_effect=fake_request)

    def test_bootstrap_and_assignment_routes_work_with_supabase_storage(self) -> None:
        store = self._seed_store()
        client = TestClient(app)

        with patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "http://supabase.test",
                "SUPABASE_PUBLISHABLE_KEY": "test-key",
            },
            clear=False,
        ):
            with self._patch_supabase(store):
                bootstrap = client.get("/api/bootstrap")
                self.assertEqual(bootstrap.status_code, 200)
                payload = bootstrap.json()
                self.assertEqual(payload["storage_mode"], "supabase")
                self.assertEqual(payload["profile_summary"]["assignments"], 1)
                self.assertEqual(payload["assignment_options"][0]["value"], "jane_payroll")

                catalog = client.get("/api/profiles/catalog")
                self.assertEqual(catalog.status_code, 200)
                self.assertIn("acme", catalog.json()["profile_catalog"]["company"])

                periods = client.get("/api/assignments/jane_payroll/periods", params={"year": 2026})
                self.assertEqual(periods.status_code, 200)
                self.assertEqual(periods.json()["periods"][0]["check_number"], "000000321")

                load = client.post(
                    "/api/profiles/load-assignment",
                    json={"assignment_id": "jane_payroll", "year": 2026, "period_number": 1},
                )
                self.assertEqual(load.status_code, 200)
                self.assertEqual(load.json()["paystub"]["employee_name"], "Jane Doe")

    def test_profile_save_and_load_routes_round_trip_through_supabase(self) -> None:
        store = self._seed_store()
        client = TestClient(app)

        with patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "http://supabase.test",
                "SUPABASE_PUBLISHABLE_KEY": "test-key",
            },
            clear=False,
        ):
            with self._patch_supabase(store):
                save = client.post(
                    "/api/profiles/company",
                    json={
                        "record": {
                            "profile_id": "bravo",
                            "company_name": "Bravo Payroll Group",
                            "company_address": "10 UNION SQ\nNEW YORK, NY 10003",
                            "default_payroll_check_number": "000000777",
                        }
                    },
                )
                self.assertEqual(save.status_code, 200)
                self.assertEqual(store[("company", "bravo")]["company_name"], "Bravo Payroll Group")

                loaded = client.get("/api/profiles/company/bravo")
                self.assertEqual(loaded.status_code, 200)
                self.assertEqual(loaded.json()["record"]["default_payroll_check_number"], "000000777")

    def test_profile_export_and_import_round_trip_with_supabase_storage(self) -> None:
        store = self._seed_store()
        client = TestClient(app)

        with TemporaryDirectory() as export_dir:
            with patch.dict(
                os.environ,
                {
                    "SUPABASE_URL": "http://supabase.test",
                    "SUPABASE_PUBLISHABLE_KEY": "test-key",
                },
                clear=False,
            ):
                with self._patch_supabase(store):
                    with patch.object(web_service, "PROFILE_EXPORT_DIR", Path(export_dir)):
                        export_response = client.post("/api/profiles/export", json={"file_format": "json"})
                        self.assertEqual(export_response.status_code, 200)
                        export_payload = export_response.json()
                        download = client.get(export_payload["download_url"])
                        self.assertEqual(download.status_code, 200)

                        store.clear()

                        import_response = client.post(
                            "/api/profiles/import",
                            data={"file_format": "json"},
                            files={"upload": ("profiles.json", download.content, "application/json")},
                        )
                        self.assertEqual(import_response.status_code, 200)
                        self.assertEqual(import_response.json()["summary"]["assignments"], 1)
                        self.assertIn(("assignment", "jane_payroll"), store)


class WebVisualSnapshotTests(unittest.TestCase):
    def test_web_visual_snapshots_match_baseline(self) -> None:
        try:
            from playwright.sync_api import Error as PlaywrightError  # noqa: F401
        except Exception as exc:  # pragma: no cover - dependency presence
            self.skipTest(f"Playwright unavailable: {exc}")

        host = "127.0.0.1"
        port = "8124"
        base_url = f"http://{host}:{port}"
        server = subprocess.Popen(
            [
                os.sys.executable,
                "-m",
                "uvicorn",
                "webapp.app:app",
                "--host",
                host,
                "--port",
                port,
            ],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            from tests.web_snapshot_utils import wait_for_url

            wait_for_url(base_url)
            with TemporaryDirectory() as temp_dir:
                actual = capture_webapp_snapshots(base_url, temp_dir)
                expected = load_web_snapshot_fixture()
                expected_case = {key: expected[key] for key in actual}
                if actual != expected_case:
                    case_preview_dir = WEB_FAILURE_PREVIEW_DIR
                    if case_preview_dir.exists():
                        shutil.rmtree(case_preview_dir)
                    shutil.copytree(temp_dir, case_preview_dir)
                    self.fail(
                        "Web visual snapshot mismatch. "
                        f"Update tests/fixtures/web_visual_snapshots.json if the change is intentional. "
                        f"Actual previews written to {case_preview_dir}."
                    )
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    unittest.main()
