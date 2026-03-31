from __future__ import annotations

import os
import shutil
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException, UploadFile
import httpx

from generators.pdf_generator import PaystubTemplate, generate_paystub_pdf
from models.pay_period import get_pay_periods
from models.payroll_calculator import (
    BenefitLine,
    DeductionLine,
    EarningLine,
    EmployeePayConfig,
    FilingStatus,
    YTDState,
    compute_paystub_data,
)
from models.paystub import Paystub
from models.profile_io import (
    export_profiles_csv,
    export_profiles_excel,
    export_profiles_json,
    import_profiles_csv,
    import_profiles_excel,
    import_profiles_json,
)
from models.profile_store import (
    CompanyProfile,
    DeductionDefaultsProfile,
    EmployeeProfile,
    PROFILE_ROOT,
    PayrollAssignmentProfile,
    TaxDefaultsProfile,
    build_employee_pay_config,
    list_profiles,
    load_assignment_employee_pay_config,
    load_company_profile,
    load_deduction_defaults_profile,
    load_employee_profile,
    load_payroll_assignment_profile,
    load_tax_defaults_profile,
    profile_to_dict,
    save_company_profile,
    save_deduction_defaults_profile,
    save_employee_profile,
    save_payroll_assignment_profile,
    save_tax_defaults_profile,
)
from models.pay_period import PayFrequency
from sample_data import sample_employee


PROFILES_ROOT = Path(PROFILE_ROOT)
WEB_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output" / "web"
PROFILE_EXPORT_DIR = WEB_OUTPUT_DIR / "profile_exports"

PROFILE_EXPORT_FORMATS = ("json", "excel", "csv")
PROFILE_EXPORT_MEDIA_TYPES = {
    "json": "application/json",
    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "application/zip",
}
PROFILE_IMPORT_SUFFIXES = {
    ".json": "json",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".zip": "csv",
}
PROFILE_TYPES = ("company", "employee", "tax", "deduction", "assignment")
SUPABASE_PROFILE_TABLE = "paystub_profile_records"
GENERATION_MODES = {"single", "multiple"}
GENERATION_SEQUENCE_TYPES = {"pay_frequency", "weekly"}
GENERATION_ANCHORS = {"initial", "latest"}
GENERATION_AMOUNT_MODES = {"auto", "fixed", "manual"}
MAX_BATCH_STUBS = 26


def supabase_enabled(root: Path | None = None) -> bool:
    if root is not None:
        return False
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_PUBLISHABLE_KEY"))


def _supabase_headers(*, prefer: str | None = None) -> dict[str, str]:
    api_key = os.environ["SUPABASE_PUBLISHABLE_KEY"]
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _supabase_rest_url(table: str) -> str:
    return f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/{table}"


def _supabase_request(
    method: str,
    table: str,
    *,
    params: dict[str, str] | None = None,
    json_body: list[dict] | dict | None = None,
    prefer: str | None = None,
) -> object:
    response = httpx.request(
        method,
        _supabase_rest_url(table),
        headers=_supabase_headers(prefer=prefer),
        params=params,
        json=json_body,
        timeout=20.0,
    )
    if response.is_success:
        if not response.content:
            return None
        return response.json()
    raise HTTPException(
        status_code=502,
        detail=f"Supabase request failed with status {response.status_code}: {response.text}",
    )


def _supabase_fetch_records(
    *,
    profile_type: str | None = None,
    profile_id: str | None = None,
) -> list[dict]:
    params = {
        "select": "profile_type,profile_id,payload",
        "order": "profile_type.asc,profile_id.asc",
    }
    if profile_type:
        params["profile_type"] = f"eq.{profile_type}"
    if profile_id:
        params["profile_id"] = f"eq.{profile_id}"
    records = _supabase_request("GET", SUPABASE_PROFILE_TABLE, params=params)
    return list(records or [])


def _supabase_load_profile_record(profile_type: str, profile_id: str) -> dict:
    matches = _supabase_fetch_records(profile_type=profile_type, profile_id=profile_id)
    if not matches:
        raise HTTPException(status_code=404, detail="Profile not found.")
    payload = dict(matches[0].get("payload") or {})
    payload.setdefault("profile_id", matches[0]["profile_id"])
    return payload


def _supabase_upsert_profile_record(profile_type: str, record: dict) -> dict:
    profile_id = str(record.get("profile_id", "")).strip()
    result = _supabase_request(
        "POST",
        SUPABASE_PROFILE_TABLE,
        params={
            "on_conflict": "profile_type,profile_id",
        },
        json_body=[
            {
                "profile_type": profile_type,
                "profile_id": profile_id,
                "payload": record,
            }
        ],
        prefer="resolution=merge-duplicates,return=representation",
    )
    rows = list(result or [])
    if not rows:
        raise HTTPException(status_code=502, detail="Supabase save returned no profile data.")
    payload = dict(rows[0].get("payload") or {})
    payload.setdefault("profile_id", rows[0]["profile_id"])
    return payload


def _supabase_profile_catalog() -> dict[str, list[str]]:
    catalog: dict[str, list[str]] = {profile_type: [] for profile_type in PROFILE_TYPES}
    for row in _supabase_fetch_records():
        row_type = row.get("profile_type")
        row_id = row.get("profile_id")
        if row_type in catalog and row_id:
            catalog[row_type].append(str(row_id))
    for profile_type in catalog:
        catalog[profile_type] = sorted(catalog[profile_type])
    return catalog


def _supabase_profile_summary() -> dict[str, int]:
    catalog = _supabase_profile_catalog()
    return {
        "companies": len(catalog["company"]),
        "employees": len(catalog["employee"]),
        "tax_defaults": len(catalog["tax"]),
        "deduction_defaults": len(catalog["deduction"]),
        "assignments": len(catalog["assignment"]),
    }


def _write_profile_record_to_root(profile_type: str, record: dict, root: Path) -> None:
    profile = _build_profile_instance(profile_type, record)
    if profile_type == "company":
        save_company_profile(profile, root=root)
    elif profile_type == "employee":
        save_employee_profile(profile, root=root)
    elif profile_type == "tax":
        save_tax_defaults_profile(profile, root=root)
    elif profile_type == "deduction":
        save_deduction_defaults_profile(profile, root=root)
    elif profile_type == "assignment":
        save_payroll_assignment_profile(profile, root=root)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported profile type: {profile_type}")


def _export_supabase_profiles_to_root(root: Path) -> None:
    for row in _supabase_fetch_records():
        profile_type = str(row["profile_type"])
        payload = dict(row.get("payload") or {})
        payload.setdefault("profile_id", row["profile_id"])
        _write_profile_record_to_root(profile_type, payload, root)


def _sync_root_profiles_to_supabase(root: Path) -> None:
    for profile_type in PROFILE_TYPES:
        for profile_id in list_profiles(profile_type, root=root):
            record = load_profile_record(profile_type, profile_id, root=root)
            _supabase_upsert_profile_record(profile_type, record)


def _supabase_profile_instances(profile_type: str) -> dict[str, object]:
    instances: dict[str, object] = {}
    for row in _supabase_fetch_records(profile_type=profile_type):
        payload = dict(row.get("payload") or {})
        payload.setdefault("profile_id", row["profile_id"])
        instances[str(row["profile_id"])] = _build_profile_instance(profile_type, payload)
    return instances


def _load_assignment_employee_pay_config_supabase(assignment_id: str):
    assignment = _build_profile_instance(
        "assignment",
        _supabase_load_profile_record("assignment", assignment_id),
    )
    company = _build_profile_instance(
        "company",
        _supabase_load_profile_record("company", assignment.company_profile_id),
    )
    employee = _build_profile_instance(
        "employee",
        _supabase_load_profile_record("employee", assignment.employee_profile_id),
    )
    tax_defaults = _build_profile_instance(
        "tax",
        _supabase_load_profile_record("tax", assignment.tax_profile_id),
    )
    deductions = _build_profile_instance(
        "deduction",
        _supabase_load_profile_record("deduction", assignment.deduction_profile_id),
    )
    config = build_employee_pay_config(
        company=company,
        employee=employee,
        tax_defaults=tax_defaults,
        deduction_defaults=deductions,
        payroll_check_number=str(assignment.payroll_check_number_start).zfill(9),
    )
    return assignment, config


def empty_paystub_payload() -> dict:
    return {
        "draft_mode": True,
        "company_name": "",
        "company_address": "",
        "employee_name": "",
        "employee_address": "",
        "employee_id": "",
        "bank_name": "",
        "deposit_account_type": "",
        "routing_number": "",
        "account_number": "",
        "direct_deposit_amount": 0.0,
        "pay_date": "",
        "pay_period_start": "",
        "pay_period_end": "",
        "social_security_number": "",
        "taxable_marital_status": FilingStatus.SINGLE.value,
        "exemptions_allowances": "",
        "payroll_check_number": "",
        "work_state": "NY",
        "pay_frequency": PayFrequency.BIWEEKLY.value,
        "allowances_count": 0,
        "additional_federal_withholding": 0.0,
        "compensation_type": "hourly",
        "primary_earning_label": "Regular",
        "salary_period_amount": 0.0,
        "annual_salary": 0.0,
        "weekly_hours": 40.0,
        "hourly_rate": 0.0,
        "regular_hours": 80.0,
        "auto_calculate_taxes": True,
        "auto_add_state_deductions": False,
        "source_earnings": [],
        "source_deductions": [],
        "earnings": [],
        "taxes": [],
        "deductions": [],
        "adjustments": [],
        "other_benefits": [],
        "important_notes": [],
        "footnotes": [],
        "manual_stub_amount": None,
    }


def sample_paystub_payload() -> dict:
    periods = get_pay_periods(2026, sample_employee.frequency)
    first_period = periods[0]
    data = compute_paystub_data(
        sample_employee,
        first_period.start,
        first_period.end,
        first_period.pay_date,
    )
    return Paystub(**data).model_dump(mode="json")


def normalize_paystub_payload(payload: dict | Paystub) -> dict:
    paystub = payload if isinstance(payload, Paystub) else Paystub(**payload)
    if _paystub_uses_automatic_builder(paystub) and not (paystub.earnings or paystub.taxes or paystub.deductions):
        return _compute_automatic_paystub(paystub)
    return paystub.model_dump(mode="json")


def preview_payload(payload: dict | Paystub) -> dict:
    paystub = Paystub(**payload) if isinstance(payload, dict) else payload
    normalized = normalize_paystub_payload(paystub)
    return {
        "paystub": normalized,
        "summary": {
            "gross_pay_current": normalized["gross_pay_current"],
            "total_taxes_current": normalized["total_taxes_current"],
            "total_deductions_current": normalized["total_deductions_current"],
            "net_pay_current": normalized["net_pay_current"],
            "gross_pay_ytd": normalized["gross_pay_ytd"],
            "net_pay_ytd": normalized["net_pay_ytd"],
        },
    }


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def _format_iso_date(value: date) -> str:
    return value.isoformat()


def _round_money(value: float) -> float:
    return round(float(value or 0.0), 2)


def _coerce_frequency(value: str | PayFrequency | None) -> PayFrequency:
    if isinstance(value, PayFrequency):
        return value
    normalized = str(value or PayFrequency.BIWEEKLY.value).strip().lower()
    try:
        return PayFrequency(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unsupported pay frequency: {value}") from exc


def _coerce_filing_status(value: str | FilingStatus | None) -> FilingStatus:
    if isinstance(value, FilingStatus):
        return value
    normalized = str(value or FilingStatus.SINGLE.value).strip().lower()
    for status in FilingStatus:
        if normalized == status.value.lower():
            return status
    raise HTTPException(status_code=400, detail=f"Unsupported filing status: {value}")


def _paystub_uses_automatic_builder(paystub: Paystub) -> bool:
    return bool(paystub.draft_mode)


def _build_automatic_employee_config(paystub: Paystub):
    frequency = _coerce_frequency(paystub.pay_frequency)
    filing_status = _coerce_filing_status(paystub.taxable_marital_status)

    earnings: list[EarningLine] = []
    primary_label = str(paystub.primary_earning_label or "Regular").strip() or "Regular"
    manual_stub_amount = max(0.0, float(paystub.manual_stub_amount or 0.0))
    resolved_weekly_hours = max(0.0, float(paystub.weekly_hours or 0.0))
    resolved_hourly_rate = max(0.0, float(paystub.hourly_rate or 0.0))
    resolved_regular_hours = max(0.0, float(paystub.regular_hours or 0.0))
    salary_period_amount = 0.0
    annual_salary = 0.0
    if manual_stub_amount:
        earnings.append(EarningLine(label=primary_label, flat_amount=manual_stub_amount))
        resolved_hourly_rate = 0.0
        resolved_regular_hours = 0.0
    elif str(paystub.compensation_type).lower() == "salary":
        periods = get_pay_periods(_parse_iso_date(paystub.pay_date).year, frequency)
        salary_period_amount = max(0.0, float(paystub.salary_period_amount or 0.0))
        annual_salary = max(0.0, float(paystub.annual_salary or 0.0))
        if salary_period_amount > 0:
            annual_salary = round(salary_period_amount * max(1, len(periods)), 2)
        elif annual_salary > 0:
            salary_period_amount = round(annual_salary / max(1, len(periods)), 2)
        if annual_salary and salary_period_amount:
            if resolved_weekly_hours > 0:
                resolved_hourly_rate = round(annual_salary / (resolved_weekly_hours * 52), 2)
                resolved_regular_hours = round((resolved_weekly_hours * 52) / max(1, len(periods)), 2)
            else:
                resolved_hourly_rate = 0.0
                resolved_regular_hours = 0.0
            earnings.append(
                EarningLine(
                    label=primary_label,
                    rate=resolved_hourly_rate,
                    hours=resolved_regular_hours,
                    flat_amount=salary_period_amount,
                )
            )
    else:
        if resolved_hourly_rate or resolved_regular_hours:
            earnings.append(EarningLine(label=primary_label, rate=resolved_hourly_rate, hours=resolved_regular_hours))

    for item in paystub.source_earnings:
        if not str(item.label).strip():
            continue
        amount = round(float(item.amount or 0.0), 2)
        if amount:
            earnings.append(EarningLine(label=item.label, flat_amount=amount))
            continue
        earnings.append(
            EarningLine(
                label=item.label,
                rate=max(0.0, float(item.rate or 0.0)),
                hours=max(0.0, float(item.hours or 0.0)),
            )
        )

    pre_tax_deductions: list[DeductionLine] = []
    post_tax_deductions: list[DeductionLine] = []
    for item in paystub.source_deductions:
        if not str(item.label).strip() or float(item.amount or 0.0) <= 0:
            continue
        deduction = DeductionLine(
            label=item.label,
            amount=round(float(item.amount or 0.0), 2),
            is_pretax=bool(item.is_pretax),
        )
        if deduction.is_pretax:
            pre_tax_deductions.append(deduction)
        else:
            post_tax_deductions.append(deduction)

    config = EmployeePayConfig(
        employee_id=paystub.employee_id,
        employee_name=paystub.employee_name,
        employee_address=paystub.employee_address,
        bank_name=paystub.bank_name,
        deposit_account_type=paystub.deposit_account_type,
        routing_number=paystub.routing_number,
        account_number=paystub.account_number,
        direct_deposit_amount=max(0.0, float(paystub.direct_deposit_amount or 0.0)),
        social_security_number=paystub.social_security_number,
        company_name=paystub.company_name,
        company_address=paystub.company_address,
        filing_status=filing_status,
        frequency=frequency,
        allowances=max(0, int(paystub.allowances_count or 0)),
        additional_federal_wh=max(0.0, float(paystub.additional_federal_withholding or 0.0)),
        state=str(paystub.work_state or "NY").upper(),
        earnings=earnings,
        pre_tax_deductions=pre_tax_deductions,
        post_tax_deductions=post_tax_deductions,
        other_benefits=[BenefitLine(label=item.label, current=item.current, ytd=item.ytd) for item in paystub.other_benefits],
        important_notes=list(paystub.important_notes),
        payroll_check_number=paystub.payroll_check_number,
        apply_ny_paid_family_leave=bool(paystub.auto_add_state_deductions),
    )
    return config, {
        "salary_period_amount": round(salary_period_amount if str(paystub.compensation_type).lower() == "salary" else 0.0, 2),
        "annual_salary": round(annual_salary if str(paystub.compensation_type).lower() == "salary" else 0.0, 2),
        "weekly_hours": round(resolved_weekly_hours, 2),
        "hourly_rate": round(resolved_hourly_rate, 2),
        "regular_hours": round(resolved_regular_hours, 2),
    }


def _compute_automatic_paystub(paystub: Paystub, *, ytd_state=None, period: dict | None = None) -> dict:
    config, resolved_compensation = _build_automatic_employee_config(paystub)
    if period:
        config.payroll_check_number = period["payroll_check_number"]
        pay_period_start = _parse_iso_date(period["pay_period_start"])
        pay_period_end = _parse_iso_date(period["pay_period_end"])
        pay_date = _parse_iso_date(period["pay_date"])
    else:
        pay_period_start = _parse_iso_date(paystub.pay_period_start)
        pay_period_end = _parse_iso_date(paystub.pay_period_end)
        pay_date = _parse_iso_date(paystub.pay_date)
    computed = compute_paystub_data(
        config,
        pay_period_start,
        pay_period_end,
        pay_date,
        ytd=ytd_state,
    )
    adjustments = []
    for item in paystub.adjustments:
        prior_adjustment_ytd = 0.0 if ytd_state is None else ytd_state.adjustments.get(item.label, 0.0)
        adjustments.append(
            {
                "label": item.label,
                "current": item.current,
                "ytd": round(prior_adjustment_ytd + item.current, 2),
            }
        )
    computed.update(
        {
            "draft_mode": paystub.draft_mode,
            "bank_name": paystub.bank_name,
            "deposit_account_type": paystub.deposit_account_type,
            "routing_number": paystub.routing_number,
            "account_number": paystub.account_number,
            "direct_deposit_amount": round(float(paystub.direct_deposit_amount or 0.0), 2),
            "work_state": str(paystub.work_state or "NY").upper(),
            "pay_frequency": config.frequency.value,
            "allowances_count": max(0, int(paystub.allowances_count or 0)),
            "additional_federal_withholding": max(0.0, float(paystub.additional_federal_withholding or 0.0)),
            "compensation_type": paystub.compensation_type,
            "primary_earning_label": paystub.primary_earning_label,
            "salary_period_amount": resolved_compensation["salary_period_amount"],
            "annual_salary": resolved_compensation["annual_salary"],
            "weekly_hours": resolved_compensation["weekly_hours"],
            "hourly_rate": resolved_compensation["hourly_rate"],
            "regular_hours": resolved_compensation["regular_hours"],
            "auto_calculate_taxes": paystub.auto_calculate_taxes,
            "auto_add_state_deductions": paystub.auto_add_state_deductions,
            "source_earnings": [item.model_dump(mode="json") for item in paystub.source_earnings],
            "source_deductions": [item.model_dump(mode="json") for item in paystub.source_deductions],
            "adjustments": adjustments,
            "important_notes": paystub.important_notes,
            "footnotes": computed.get("footnotes", []) + list(paystub.footnotes),
            "manual_stub_amount": round(float(paystub.manual_stub_amount or 0.0), 2) or None,
        }
    )
    return Paystub(**computed).model_dump(mode="json")


def normalize_generation_plan(plan: dict | None) -> dict:
    raw = dict(plan or {})
    mode = str(raw.get("mode", "single") or "single").strip().lower()
    if mode not in GENERATION_MODES:
        raise HTTPException(status_code=400, detail=f"Unsupported generation mode: {mode}")

    sequence_type = str(raw.get("sequence_type", "pay_frequency") or "pay_frequency").strip().lower()
    if sequence_type not in GENERATION_SEQUENCE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported sequence type: {sequence_type}")

    try:
        stub_count = int(raw.get("stub_count", 1) or 1)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Stub count must be a whole number.") from exc

    if mode == "single":
        stub_count = 1
    if stub_count < 1 or stub_count > MAX_BATCH_STUBS:
        raise HTTPException(status_code=400, detail=f"Stub count must be between 1 and {MAX_BATCH_STUBS}.")

    anchor = str(raw.get("anchor", "initial") or "initial").strip().lower()
    if anchor not in GENERATION_ANCHORS:
        raise HTTPException(status_code=400, detail=f"Unsupported schedule anchor: {anchor}")

    amount_mode = str(raw.get("amount_mode", "auto") or "auto").strip().lower()
    if amount_mode not in GENERATION_AMOUNT_MODES:
        raise HTTPException(status_code=400, detail=f"Unsupported amount mode: {amount_mode}")

    fixed_amount = float(raw.get("fixed_amount", 0.0) or 0.0)
    manual_amounts = [float(value or 0.0) for value in (raw.get("manual_amounts") or [])]
    if amount_mode == "fixed" and fixed_amount <= 0:
        raise HTTPException(status_code=400, detail="Enter a fixed gross amount for generated stubs.")
    if amount_mode == "manual":
        if len(manual_amounts) != stub_count:
            raise HTTPException(status_code=400, detail="Enter one manual amount for each generated stub.")
        if any(value <= 0 for value in manual_amounts):
            raise HTTPException(status_code=400, detail="Manual stub amounts must all be greater than zero.")

    return {
        "mode": mode,
        "sequence_type": sequence_type,
        "stub_count": stub_count,
        "pay_frequency": _coerce_frequency(raw.get("pay_frequency")).value,
        "anchor": anchor,
        "amount_mode": amount_mode,
        "fixed_amount": round(fixed_amount, 2),
        "manual_amounts": [round(value, 2) for value in manual_amounts],
    }


def _increment_check_number(value: str, offset: int) -> str:
    raw = str(value or "").strip()
    if not raw:
        return raw
    if raw.isdigit():
        return str(int(raw) + offset).zfill(len(raw))
    return raw if offset == 0 else f"{raw}{offset:+d}"


def _sequence_periods_by_days(paystub: Paystub, count: int, step_days: int, anchor: str) -> list[dict]:
    start = _parse_iso_date(paystub.pay_period_start)
    end = _parse_iso_date(paystub.pay_period_end)
    pay_date = _parse_iso_date(paystub.pay_date)
    duration_days = (end - start).days

    periods: list[dict] = []
    starting_offset = -(count - 1) if anchor == "latest" else 0
    for index in range(count):
        offset = starting_offset + index
        shifted_start = start + timedelta(days=step_days * offset)
        shifted_end = shifted_start + timedelta(days=duration_days)
        shifted_pay_date = pay_date + timedelta(days=step_days * offset)
        periods.append(
            {
                "sequence_number": index + 1,
                "pay_period_start": _format_iso_date(shifted_start),
                "pay_period_end": _format_iso_date(shifted_end),
                "pay_date": _format_iso_date(shifted_pay_date),
                "payroll_check_number": _increment_check_number(paystub.payroll_check_number, offset),
            }
        )
    return periods


def _sequence_periods_by_frequency(
    paystub: Paystub,
    count: int,
    frequency: PayFrequency,
    anchor: str,
) -> list[dict] | None:
    base_start = _parse_iso_date(paystub.pay_period_start)
    base_end = _parse_iso_date(paystub.pay_period_end)
    base_pay_date = _parse_iso_date(paystub.pay_date)

    candidates = []
    for year in range(base_pay_date.year - 1, base_pay_date.year + 2):
        candidates.extend(get_pay_periods(year, frequency))
    candidates.sort(key=lambda period: (period.pay_date, period.start, period.end))

    matched_index: int | None = None
    for index, period in enumerate(candidates):
        if period.start == base_start and period.end == base_end and period.pay_date == base_pay_date:
            matched_index = index
            break
    if matched_index is None:
        for index, period in enumerate(candidates):
            if period.pay_date == base_pay_date:
                matched_index = index
                break
    if matched_index is None:
        for index, period in enumerate(candidates):
            if period.start == base_start and period.end == base_end:
                matched_index = index
                break
    if matched_index is None:
        return None

    if anchor == "latest":
        start_index = matched_index - count + 1
        end_index = matched_index + 1
    else:
        start_index = matched_index
        end_index = matched_index + count
    if start_index < 0 or end_index > len(candidates):
        return None

    periods: list[dict] = []
    for index, period in enumerate(candidates[start_index:end_index]):
        offset = (start_index + index) - matched_index
        periods.append(
            {
                "sequence_number": index + 1,
                "pay_period_start": _format_iso_date(period.start),
                "pay_period_end": _format_iso_date(period.end),
                "pay_date": _format_iso_date(period.pay_date),
                "payroll_check_number": _increment_check_number(paystub.payroll_check_number, offset),
            }
        )
    return periods


def build_generation_schedule(payload: dict | Paystub, plan: dict | None = None) -> dict:
    paystub = Paystub(**payload) if isinstance(payload, dict) else payload
    normalized_plan = normalize_generation_plan(plan)
    frequency = _coerce_frequency(normalized_plan["pay_frequency"])
    anchor = normalized_plan["anchor"]

    if normalized_plan["mode"] == "single":
        periods = [
            {
                "sequence_number": 1,
                "pay_period_start": paystub.pay_period_start,
                "pay_period_end": paystub.pay_period_end,
                "pay_date": paystub.pay_date,
                "payroll_check_number": paystub.payroll_check_number,
            }
        ]
    elif normalized_plan["sequence_type"] == "weekly":
        periods = _sequence_periods_by_days(paystub, normalized_plan["stub_count"], 7, anchor)
    else:
        periods = _sequence_periods_by_frequency(paystub, normalized_plan["stub_count"], frequency, anchor)
        if periods is None:
            fallback_days = {
                PayFrequency.WEEKLY: 7,
                PayFrequency.BIWEEKLY: 14,
                PayFrequency.SEMIMONTHLY: 15,
                PayFrequency.MONTHLY: 30,
            }[frequency]
            periods = _sequence_periods_by_days(paystub, normalized_plan["stub_count"], fallback_days, anchor)

    summary = {
        "mode": normalized_plan["mode"],
        "sequence_type": normalized_plan["sequence_type"],
        "pay_frequency": frequency.value,
        "anchor": anchor,
        "stub_count": len(periods),
        "first_pay_date": periods[0]["pay_date"],
        "last_pay_date": periods[-1]["pay_date"],
    }
    return {
        **normalized_plan,
        "periods": periods,
        "summary": summary,
    }


def _roll_section_ytd(lines: list[dict], offset: int) -> list[dict]:
    rolled: list[dict] = []
    for item in lines:
        row = dict(item)
        base_ytd = _round_money(row.get("ytd", 0.0))
        current = _round_money(row.get("current", 0.0))
        row["ytd"] = _round_money(base_ytd + (current * offset))
        rolled.append(row)
    return rolled


def _roll_stub_forward(paystub: Paystub, period: dict, offset: int) -> dict:
    payload = paystub.model_dump(mode="json")
    payload["pay_period_start"] = period["pay_period_start"]
    payload["pay_period_end"] = period["pay_period_end"]
    payload["pay_date"] = period["pay_date"]
    payload["payroll_check_number"] = period["payroll_check_number"]
    for section in ("earnings", "taxes", "deductions", "adjustments", "other_benefits"):
        payload[section] = _roll_section_ytd(payload.get(section, []), offset)

    payload["gross_pay_ytd"] = _round_money(payload.get("gross_pay_ytd", 0.0) + (payload.get("gross_pay_current", 0.0) * offset))
    payload["total_taxes_ytd"] = _round_money(payload.get("total_taxes_ytd", 0.0) + (payload.get("total_taxes_current", 0.0) * offset))
    payload["total_deductions_ytd"] = _round_money(
        payload.get("total_deductions_ytd", 0.0) + (payload.get("total_deductions_current", 0.0) * offset)
    )
    payload["net_pay_ytd"] = _round_money(payload.get("net_pay_ytd", 0.0) + (payload.get("net_pay_current", 0.0) * offset))
    return Paystub(**payload).model_dump(mode="json")


def _stub_amount_override(schedule: dict, index: int) -> float | None:
    mode = schedule.get("amount_mode", "auto")
    if mode == "fixed":
        return float(schedule.get("fixed_amount", 0.0) or 0.0)
    if mode == "manual":
        amounts = schedule.get("manual_amounts") or []
        if index < len(amounts):
            return float(amounts[index] or 0.0)
    return None


def _paystub_with_manual_amount(paystub: Paystub, amount: float | None) -> Paystub:
    if amount is None or amount <= 0:
        return paystub
    clone = paystub.model_copy(deep=True)
    clone.manual_stub_amount = round(amount, 2)
    clone.compensation_type = "manual"
    clone.annual_salary = 0.0
    clone.hourly_rate = 0.0
    clone.regular_hours = 0.0
    clone.source_earnings = []
    return clone


def build_generation_sequence(payload: dict | Paystub, plan: dict | None = None) -> dict:
    paystub = Paystub(**payload) if isinstance(payload, dict) else payload
    schedule = build_generation_schedule(paystub, plan)
    if _paystub_uses_automatic_builder(paystub):
        paystubs: list[dict] = []
        ytd_state = YTDState()
        for index, period in enumerate(schedule["periods"]):
            override_amount = _stub_amount_override(schedule, index)
            computed = _compute_automatic_paystub(
                _paystub_with_manual_amount(paystub, override_amount),
                ytd_state=ytd_state,
                period=period,
            )
            paystubs.append(computed)
            ytd_state.advance(computed)
        return {
            "schedule": schedule,
            "paystubs": paystubs,
        }

    paystubs = [
        _roll_stub_forward(paystub, period=period, offset=index)
        for index, period in enumerate(schedule["periods"])
    ]
    return {
        "schedule": schedule,
        "paystubs": paystubs,
    }


def generation_plan_payload(payload: dict | Paystub, plan: dict | None = None) -> dict:
    sequence = build_generation_sequence(payload, plan)
    entries = []
    for paystub_data, period in zip(sequence["paystubs"], sequence["schedule"]["periods"], strict=False):
        preview = preview_payload(paystub_data)
        entries.append(
            {
                "sequence_number": period["sequence_number"],
                "pay_period_start": period["pay_period_start"],
                "pay_period_end": period["pay_period_end"],
                "pay_date": period["pay_date"],
                "payroll_check_number": period["payroll_check_number"],
                "gross_pay_current": preview["summary"]["gross_pay_current"],
                "net_pay_current": preview["summary"]["net_pay_current"],
                "gross_pay_ytd": preview["summary"]["gross_pay_ytd"],
                "net_pay_ytd": preview["summary"]["net_pay_ytd"],
            }
        )
    return {
        **sequence["schedule"],
        "entries": entries,
    }


def generate_pdf_document(
    payload: dict | Paystub,
    template: PaystubTemplate | str = PaystubTemplate.DETACHED_CHECK,
    output_dir: Path | None = None,
) -> dict:
    normalized = normalize_paystub_payload(payload)
    paystub = Paystub(**normalized)
    target_dir = output_dir or WEB_OUTPUT_DIR
    pdf_path = Path(
        generate_paystub_pdf(
            paystub.model_dump(mode="json"),
            output_dir=str(target_dir),
            template=template,
        )
    )
    return {
        "filename": pdf_path.name,
        "path": str(pdf_path),
        "template": PaystubTemplate(template).value if not isinstance(template, PaystubTemplate) else template.value,
        "preview": preview_payload(paystub),
    }


def generate_pdf_batch(
    payload: dict | Paystub,
    *,
    plan: dict | None = None,
    template: PaystubTemplate | str = PaystubTemplate.DETACHED_CHECK,
    output_dir: Path | None = None,
) -> dict:
    sequence = build_generation_sequence(payload, plan)
    target_dir = output_dir or WEB_OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    template_value = PaystubTemplate(template).value if not isinstance(template, PaystubTemplate) else template.value

    documents: list[dict] = []
    for paystub_data in sequence["paystubs"]:
        document = generate_pdf_document(paystub_data, template=template, output_dir=target_dir)
        documents.append(
            {
                "filename": document["filename"],
                "path": document["path"],
                "template": document["template"],
                "download_url": f"/api/downloads/{document['filename']}",
                "preview": document["preview"],
            }
        )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_filename = f"paystubs_{sequence['paystubs'][0]['employee_id']}_{stamp}_{template_value}.zip".replace(":", "-")
    zip_path = target_dir / zip_filename
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for document in documents:
            archive.write(document["path"], Path(document["path"]).name)

    return {
        "mode": "multiple",
        "filename": zip_path.name,
        "path": str(zip_path),
        "template": template_value,
        "document_count": len(documents),
        "documents": documents,
        "generation_plan": generation_plan_payload(payload, plan),
        "preview": documents[0]["preview"] if documents else preview_payload(payload),
    }


def profile_summary(root: Path | None = None) -> dict[str, int]:
    if supabase_enabled(root):
        return _supabase_profile_summary()
    target_root = Path(root or PROFILES_ROOT)
    return {
        "companies": len(list_profiles("company", root=target_root)),
        "employees": len(list_profiles("employee", root=target_root)),
        "tax_defaults": len(list_profiles("tax", root=target_root)),
        "deduction_defaults": len(list_profiles("deduction", root=target_root)),
        "assignments": len(list_profiles("assignment", root=target_root)),
    }


def profile_catalog(root: Path | None = None) -> dict[str, list[str]]:
    if supabase_enabled(root):
        return _supabase_profile_catalog()
    target_root = Path(root or PROFILES_ROOT)
    return {
        profile_type: list_profiles(profile_type, root=target_root)
        for profile_type in PROFILE_TYPES
    }


def empty_profile_record(profile_type: str) -> dict:
    if profile_type == "company":
        return profile_to_dict(
            CompanyProfile(
                profile_id="",
                company_name="",
                company_address="",
            )
        )
    if profile_type == "employee":
        return profile_to_dict(
            EmployeeProfile(
                profile_id="",
                employee_id="",
                employee_name="",
            )
        )
    if profile_type == "tax":
        return profile_to_dict(
            TaxDefaultsProfile(
                profile_id="",
                filing_status=FilingStatus.SINGLE,
                frequency=PayFrequency.BIWEEKLY,
            )
        )
    if profile_type == "deduction":
        return profile_to_dict(DeductionDefaultsProfile(profile_id=""))
    if profile_type == "assignment":
        return profile_to_dict(
            PayrollAssignmentProfile(
                profile_id="",
                company_profile_id="",
                employee_profile_id="",
                tax_profile_id="",
                deduction_profile_id="",
            )
        )
    raise HTTPException(status_code=400, detail=f"Unsupported profile type: {profile_type}")


def load_profile_record(profile_type: str, profile_id: str, root: Path | None = None) -> dict:
    if supabase_enabled(root):
        return _supabase_load_profile_record(profile_type, profile_id)
    target_root = Path(root or PROFILES_ROOT)
    try:
        if profile_type == "company":
            profile = load_company_profile(profile_id, root=target_root)
        elif profile_type == "employee":
            profile = load_employee_profile(profile_id, root=target_root)
        elif profile_type == "tax":
            profile = load_tax_defaults_profile(profile_id, root=target_root)
        elif profile_type == "deduction":
            profile = load_deduction_defaults_profile(profile_id, root=target_root)
        elif profile_type == "assignment":
            profile = load_payroll_assignment_profile(profile_id, root=target_root)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported profile type: {profile_type}")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Profile not found.") from exc
    return profile_to_dict(profile)


def _as_float(value: object, *, default: float = 0.0, nullable: bool = False) -> float | None:
    if value in ("", None):
        return None if nullable else default
    return float(value)


def _as_int(value: object, *, default: int = 0) -> int:
    if value in ("", None):
        return default
    return int(value)


def _build_profile_instance(profile_type: str, record: dict) -> object:
    if profile_type == "company":
        return CompanyProfile(
            profile_id=str(record.get("profile_id", "")).strip(),
            company_name=str(record.get("company_name", "")).strip(),
            company_address=str(record.get("company_address", "")).strip(),
            default_payroll_check_number=str(record.get("default_payroll_check_number", "000000001") or "000000001"),
        )
    if profile_type == "employee":
        return EmployeeProfile(
            profile_id=str(record.get("profile_id", "")).strip(),
            employee_id=str(record.get("employee_id", "")).strip(),
            employee_name=str(record.get("employee_name", "")).strip(),
            employee_address=str(record.get("employee_address", "") or ""),
            bank_name=str(record.get("bank_name", "") or ""),
            deposit_account_type=str(record.get("deposit_account_type", "") or ""),
            routing_number=str(record.get("routing_number", "") or ""),
            account_number=str(record.get("account_number", "") or ""),
            direct_deposit_amount=float(record.get("direct_deposit_amount", 0.0) or 0.0),
            social_security_number=str(record.get("social_security_number", "") or ""),
            earnings=[EarningLine(**item) for item in record.get("earnings", [])],
            other_benefits=[BenefitLine(**item) for item in record.get("other_benefits", [])],
            important_notes=[str(item) for item in record.get("important_notes", [])],
        )
    if profile_type == "tax":
        return TaxDefaultsProfile(
            profile_id=str(record.get("profile_id", "")).strip(),
            filing_status=FilingStatus(str(record.get("filing_status", FilingStatus.SINGLE.value))),
            frequency=PayFrequency(str(record.get("frequency", PayFrequency.BIWEEKLY.value))),
            allowances=_as_int(record.get("allowances"), default=0),
            additional_federal_wh=float(record.get("additional_federal_wh", 0.0) or 0.0),
            state=str(record.get("state", "NY") or "NY").upper(),
            state_tax_rate_override=_as_float(record.get("state_tax_rate_override"), nullable=True),
            local_tax_rate=float(record.get("local_tax_rate", 0.0) or 0.0),
            local_tax_label=str(record.get("local_tax_label", "") or ""),
        )
    if profile_type == "deduction":
        return DeductionDefaultsProfile(
            profile_id=str(record.get("profile_id", "")).strip(),
            pre_tax_deductions=[
                DeductionLine(
                    label=str(item.get("label", "")).strip(),
                    amount=float(item.get("amount", 0.0) or 0.0),
                    is_pretax=True,
                )
                for item in record.get("pre_tax_deductions", [])
            ],
            post_tax_deductions=[
                DeductionLine(
                    label=str(item.get("label", "")).strip(),
                    amount=float(item.get("amount", 0.0) or 0.0),
                    is_pretax=False,
                )
                for item in record.get("post_tax_deductions", [])
            ],
        )
    if profile_type == "assignment":
        return PayrollAssignmentProfile(
            profile_id=str(record.get("profile_id", "")).strip(),
            company_profile_id=str(record.get("company_profile_id", "")).strip(),
            employee_profile_id=str(record.get("employee_profile_id", "")).strip(),
            tax_profile_id=str(record.get("tax_profile_id", "")).strip(),
            deduction_profile_id=str(record.get("deduction_profile_id", "")).strip(),
            payroll_check_number_start=_as_int(record.get("payroll_check_number_start"), default=1),
        )
    raise HTTPException(status_code=400, detail=f"Unsupported profile type: {profile_type}")


def save_profile_record(profile_type: str, record: dict, root: Path | None = None) -> dict:
    profile = _build_profile_instance(profile_type, record)

    if not getattr(profile, "profile_id", "").strip():
        raise HTTPException(status_code=400, detail="Profile ID is required.")

    if supabase_enabled(root):
        saved_record = _supabase_upsert_profile_record(profile_type, profile_to_dict(profile))
        return {
            "record": saved_record,
            "profile_catalog": profile_catalog(),
            "profile_summary": profile_summary(),
            "assignment_options": list_assignment_options(),
        }

    target_root = Path(root or PROFILES_ROOT)
    if profile_type == "company":
        save_company_profile(profile, root=target_root)
    elif profile_type == "employee":
        save_employee_profile(profile, root=target_root)
    elif profile_type == "tax":
        save_tax_defaults_profile(profile, root=target_root)
    elif profile_type == "deduction":
        save_deduction_defaults_profile(profile, root=target_root)
    elif profile_type == "assignment":
        save_payroll_assignment_profile(profile, root=target_root)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported profile type: {profile_type}")

    return {
        "record": profile_to_dict(profile),
        "profile_catalog": profile_catalog(target_root),
        "profile_summary": profile_summary(target_root),
        "assignment_options": list_assignment_options(target_root),
    }


def list_assignment_options(root: Path | None = None) -> list[dict]:
    if supabase_enabled(root):
        companies = _supabase_profile_instances("company")
        employees = _supabase_profile_instances("employee")
        taxes = _supabase_profile_instances("tax")
        options: list[dict] = []
        for assignment_id, assignment in _supabase_profile_instances("assignment").items():
            company = companies.get(assignment.company_profile_id)
            employee = employees.get(assignment.employee_profile_id)
            tax_defaults = taxes.get(assignment.tax_profile_id)
            if not company or not employee or not tax_defaults:
                continue
            options.append(
                {
                    "value": assignment_id,
                    "label": f"{employee.employee_name} / {company.company_name}",
                    "employee_name": employee.employee_name,
                    "company_name": company.company_name,
                    "frequency": tax_defaults.frequency.value,
                    "check_number_start": assignment.payroll_check_number_start,
                }
            )
        return sorted(options, key=lambda item: item["value"])

    target_root = Path(root or PROFILES_ROOT)
    options: list[dict] = []
    for assignment_id in list_profiles("assignment", root=target_root):
        assignment = load_payroll_assignment_profile(assignment_id, root=target_root)
        company = load_company_profile(assignment.company_profile_id, root=target_root)
        employee = load_employee_profile(assignment.employee_profile_id, root=target_root)
        tax_defaults = load_tax_defaults_profile(assignment.tax_profile_id, root=target_root)
        options.append(
            {
                "value": assignment_id,
                "label": f"{employee.employee_name} / {company.company_name}",
                "employee_name": employee.employee_name,
                "company_name": company.company_name,
                "frequency": tax_defaults.frequency.value,
                "check_number_start": assignment.payroll_check_number_start,
            }
        )
    return options


def list_assignment_periods(assignment_id: str, year: int, root: Path | None = None) -> dict:
    if supabase_enabled(root):
        assignment, config = _load_assignment_employee_pay_config_supabase(assignment_id)
        periods = get_pay_periods(year, config.frequency)
        return {
            "assignment_id": assignment_id,
            "frequency": config.frequency.value,
            "periods": [
                {
                    "number": index + 1,
                    "start": str(period.start),
                    "end": str(period.end),
                    "pay_date": str(period.pay_date),
                    "check_number": str(assignment.payroll_check_number_start + index).zfill(9),
                }
                for index, period in enumerate(periods)
            ],
        }

    target_root = Path(root or PROFILES_ROOT)
    config = load_assignment_employee_pay_config(assignment_id, root=target_root)
    periods = get_pay_periods(year, config.frequency)
    assignment = load_payroll_assignment_profile(assignment_id, root=target_root)
    return {
        "assignment_id": assignment_id,
        "frequency": config.frequency.value,
        "periods": [
            {
                "number": index + 1,
                "start": str(period.start),
                "end": str(period.end),
                "pay_date": str(period.pay_date),
                "check_number": str(assignment.payroll_check_number_start + index).zfill(9),
            }
            for index, period in enumerate(periods)
        ],
    }


def load_assignment_paystub(assignment_id: str, year: int, period_number: int, root: Path | None = None) -> dict:
    if supabase_enabled(root):
        assignment, config = _load_assignment_employee_pay_config_supabase(assignment_id)
        periods = get_pay_periods(year, config.frequency)
        if period_number < 1 or period_number > len(periods):
            raise HTTPException(status_code=400, detail="Invalid period number for the selected year.")

        period = periods[period_number - 1]
        check_number = str(assignment.payroll_check_number_start + (period_number - 1)).zfill(9)
        config.payroll_check_number = check_number

        paystub = Paystub(
            **compute_paystub_data(
                config,
                period.start,
                period.end,
                period.pay_date,
            )
        )
        return {
            "assignment_id": assignment_id,
            "period": {
                "number": period_number,
                "start": str(period.start),
                "end": str(period.end),
                "pay_date": str(period.pay_date),
                "check_number": check_number,
            },
            "paystub": paystub.model_dump(mode="json"),
            "preview": preview_payload(paystub),
        }

    target_root = Path(root or PROFILES_ROOT)
    config = load_assignment_employee_pay_config(assignment_id, root=target_root)
    periods = get_pay_periods(year, config.frequency)
    if period_number < 1 or period_number > len(periods):
        raise HTTPException(status_code=400, detail="Invalid period number for the selected year.")

    assignment = load_payroll_assignment_profile(assignment_id, root=target_root)
    period = periods[period_number - 1]
    check_number = str(assignment.payroll_check_number_start + (period_number - 1)).zfill(9)
    config.payroll_check_number = check_number

    paystub = Paystub(
        **compute_paystub_data(
            config,
            period.start,
            period.end,
            period.pay_date,
        )
    )
    return {
        "assignment_id": assignment_id,
        "period": {
            "number": period_number,
            "start": str(period.start),
            "end": str(period.end),
            "pay_date": str(period.pay_date),
            "check_number": check_number,
        },
        "paystub": paystub.model_dump(mode="json"),
        "preview": preview_payload(paystub),
    }


def build_bootstrap_payload() -> dict:
    return {
        "app_name": "Paystub Studio",
        "storage_mode": "supabase" if supabase_enabled() else "filesystem",
        "default_template": PaystubTemplate.DETACHED_CHECK.value,
        "default_generation_plan": {
            "mode": "single",
            "sequence_type": "pay_frequency",
            "stub_count": 1,
            "pay_frequency": sample_employee.frequency.value,
            "anchor": "initial",
        },
        "templates": [
            {"value": template.value, "label": template.value.replace("_", " ").title()}
            for template in PaystubTemplate
        ],
        "empty_paystub": empty_paystub_payload(),
        "sample_paystub": sample_paystub_payload(),
        "assignment_options": list_assignment_options(),
        "profile_catalog": profile_catalog(),
        "profile_summary": profile_summary(),
        "profile_formats": {
            "export": list(PROFILE_EXPORT_FORMATS),
            "import": list(PROFILE_EXPORT_FORMATS),
        },
    }


def _ensure_export_dir() -> None:
    PROFILE_EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def resolve_profile_import_format(file_format: str | None, filename: str | None) -> str:
    requested = (file_format or "").strip().lower()
    inferred = PROFILE_IMPORT_SUFFIXES.get(Path(filename or "").suffix.lower(), "")

    if requested and requested not in PROFILE_EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"Unsupported import format: {file_format}")
    if requested and inferred and requested != inferred:
        raise HTTPException(
            status_code=400,
            detail=f"Selected import format '{requested}' does not match the uploaded file type.",
        )
    if requested:
        return requested
    if inferred:
        return inferred
    raise HTTPException(status_code=400, detail="Unable to infer the import format from the uploaded file.")


def _extract_csv_bundle(zip_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_root = target_dir.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = (target_dir / member.filename).resolve()
            if not str(member_path).startswith(str(target_root)):
                raise HTTPException(status_code=400, detail="Unsafe ZIP entry in uploaded CSV bundle.")
            if member.is_dir():
                member_path.mkdir(parents=True, exist_ok=True)
                continue
            member_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, member_path.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def export_profiles_bundle(file_format: str, root: Path | None = None) -> dict:
    fmt = file_format.strip().lower()
    if fmt not in PROFILE_EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"Unsupported export format: {file_format}")

    _ensure_export_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    if supabase_enabled(root):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir) / "profiles"
            _export_supabase_profiles_to_root(temp_root)
            if fmt == "json":
                path = export_profiles_json(PROFILE_EXPORT_DIR / f"profiles-{stamp}.json", root=temp_root)
            elif fmt == "excel":
                path = export_profiles_excel(PROFILE_EXPORT_DIR / f"profiles-{stamp}.xlsx", root=temp_root)
            else:
                export_dir = Path(temp_dir) / "csv_bundle"
                export_profiles_csv(export_dir, root=temp_root)
                path = PROFILE_EXPORT_DIR / f"profiles-{stamp}-csv.zip"
                with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for csv_path in sorted(export_dir.glob("*.csv")):
                        archive.write(csv_path, csv_path.name)
    else:
        target_root = Path(root or PROFILES_ROOT)
        if fmt == "json":
            path = export_profiles_json(PROFILE_EXPORT_DIR / f"profiles-{stamp}.json", root=target_root)
        elif fmt == "excel":
            path = export_profiles_excel(PROFILE_EXPORT_DIR / f"profiles-{stamp}.xlsx", root=target_root)
        else:
            with TemporaryDirectory() as temp_dir:
                export_dir = Path(temp_dir) / "csv_bundle"
                export_profiles_csv(export_dir, root=target_root)
                path = PROFILE_EXPORT_DIR / f"profiles-{stamp}-csv.zip"
                with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for csv_path in sorted(export_dir.glob("*.csv")):
                        archive.write(csv_path, csv_path.name)

    return {
        "format": fmt,
        "filename": path.name,
        "path": str(path),
        "media_type": PROFILE_EXPORT_MEDIA_TYPES[fmt],
        "summary": profile_summary(root),
    }


async def import_profiles_bundle(upload: UploadFile, file_format: str | None, root: Path | None = None) -> dict:
    fmt = resolve_profile_import_format(file_format, upload.filename)
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir) / (upload.filename or f"profiles.{fmt}")
        with temp_path.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)

        if supabase_enabled(root):
            temp_root = Path(temp_dir) / "profiles"
            if fmt == "json":
                import_profiles_json(temp_path, root=temp_root)
            elif fmt == "excel":
                import_profiles_excel(temp_path, root=temp_root)
            else:
                extracted_dir = Path(temp_dir) / "csv_bundle"
                _extract_csv_bundle(temp_path, extracted_dir)
                import_profiles_csv(extracted_dir, root=temp_root)
            _sync_root_profiles_to_supabase(temp_root)
        else:
            target_root = Path(root or PROFILES_ROOT)
            if fmt == "json":
                import_profiles_json(temp_path, root=target_root)
            elif fmt == "excel":
                import_profiles_excel(temp_path, root=target_root)
            else:
                extracted_dir = Path(temp_dir) / "csv_bundle"
                _extract_csv_bundle(temp_path, extracted_dir)
                import_profiles_csv(extracted_dir, root=target_root)

    return {
        "summary": profile_summary(root),
        "profile_catalog": profile_catalog(root),
        "assignment_options": list_assignment_options(root),
    }
