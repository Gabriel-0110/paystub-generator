"""
Reusable payroll profile storage.

Persists company, employee, tax-default, and deduction-default profiles as
JSON files, then composes them back into the existing EmployeePayConfig model.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from models.pay_period import PayFrequency
from models.payroll_calculator import (
    BenefitLine,
    DeductionLine,
    EarningLine,
    EmployeePayConfig,
    FilingStatus,
)


PROFILE_ROOT = Path("profiles")
PROFILE_DIRS = {
    "company": "companies",
    "employee": "employees",
    "tax": "tax_defaults",
    "deduction": "deduction_defaults",
    "assignment": "assignments",
}


@dataclass
class CompanyProfile:
    profile_id: str
    company_name: str
    company_address: str
    default_payroll_check_number: str = "000000001"


@dataclass
class EmployeeProfile:
    profile_id: str
    employee_id: str
    employee_name: str
    employee_address: str = ""
    bank_name: str = ""
    deposit_account_type: str = ""
    routing_number: str = ""
    account_number: str = ""
    direct_deposit_amount: float = 0.0
    social_security_number: str = ""
    earnings: list[EarningLine] = field(default_factory=list)
    other_benefits: list[BenefitLine] = field(default_factory=list)
    important_notes: list[str] = field(default_factory=list)


@dataclass
class TaxDefaultsProfile:
    profile_id: str
    filing_status: FilingStatus = FilingStatus.SINGLE
    frequency: PayFrequency = PayFrequency.BIWEEKLY
    allowances: int = 0
    additional_federal_wh: float = 0.0
    state: str = "NY"
    state_tax_rate_override: float | None = None
    local_tax_rate: float = 0.0
    local_tax_label: str = ""


@dataclass
class DeductionDefaultsProfile:
    profile_id: str
    pre_tax_deductions: list[DeductionLine] = field(default_factory=list)
    post_tax_deductions: list[DeductionLine] = field(default_factory=list)


@dataclass
class PayrollAssignmentProfile:
    profile_id: str
    company_profile_id: str
    employee_profile_id: str
    tax_profile_id: str
    deduction_profile_id: str
    payroll_check_number_start: int = 1


def _profile_path(profile_type: str, profile_id: str, root: str | Path = PROFILE_ROOT) -> Path:
    if profile_type not in PROFILE_DIRS:
        raise ValueError(f"Unknown profile type: {profile_type}")
    return Path(root) / PROFILE_DIRS[profile_type] / f"{profile_id}.json"


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    return value


def profile_to_dict(profile: Any) -> dict[str, Any]:
    return _to_jsonable(profile)


def _write_profile(profile_type: str, profile: Any, root: str | Path = PROFILE_ROOT) -> Path:
    path = _profile_path(profile_type, profile.profile_id, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_to_jsonable(profile), indent=2), encoding="utf-8")
    return path


def _read_profile(profile_type: str, profile_id: str, root: str | Path = PROFILE_ROOT) -> dict[str, Any]:
    path = _profile_path(profile_type, profile_id, root=root)
    return json.loads(path.read_text(encoding="utf-8"))


def save_company_profile(profile: CompanyProfile, root: str | Path = PROFILE_ROOT) -> Path:
    return _write_profile("company", profile, root=root)


def save_employee_profile(profile: EmployeeProfile, root: str | Path = PROFILE_ROOT) -> Path:
    return _write_profile("employee", profile, root=root)


def save_tax_defaults_profile(profile: TaxDefaultsProfile, root: str | Path = PROFILE_ROOT) -> Path:
    return _write_profile("tax", profile, root=root)


def save_deduction_defaults_profile(
    profile: DeductionDefaultsProfile,
    root: str | Path = PROFILE_ROOT,
) -> Path:
    return _write_profile("deduction", profile, root=root)


def save_payroll_assignment_profile(
    profile: PayrollAssignmentProfile,
    root: str | Path = PROFILE_ROOT,
) -> Path:
    return _write_profile("assignment", profile, root=root)


def load_company_profile(profile_id: str, root: str | Path = PROFILE_ROOT) -> CompanyProfile:
    return CompanyProfile(**_read_profile("company", profile_id, root=root))


def load_employee_profile(profile_id: str, root: str | Path = PROFILE_ROOT) -> EmployeeProfile:
    data = _read_profile("employee", profile_id, root=root)
    data["earnings"] = [EarningLine(**item) for item in data.get("earnings", [])]
    data["other_benefits"] = [BenefitLine(**item) for item in data.get("other_benefits", [])]
    return EmployeeProfile(**data)


def load_tax_defaults_profile(profile_id: str, root: str | Path = PROFILE_ROOT) -> TaxDefaultsProfile:
    data = _read_profile("tax", profile_id, root=root)
    data["filing_status"] = FilingStatus(data["filing_status"])
    data["frequency"] = PayFrequency(data["frequency"])
    return TaxDefaultsProfile(**data)


def load_deduction_defaults_profile(
    profile_id: str,
    root: str | Path = PROFILE_ROOT,
) -> DeductionDefaultsProfile:
    data = _read_profile("deduction", profile_id, root=root)
    data["pre_tax_deductions"] = [
        DeductionLine(**item) for item in data.get("pre_tax_deductions", [])
    ]
    data["post_tax_deductions"] = [
        DeductionLine(**item) for item in data.get("post_tax_deductions", [])
    ]
    return DeductionDefaultsProfile(**data)


def load_payroll_assignment_profile(
    profile_id: str,
    root: str | Path = PROFILE_ROOT,
) -> PayrollAssignmentProfile:
    return PayrollAssignmentProfile(**_read_profile("assignment", profile_id, root=root))


def list_profiles(profile_type: str, root: str | Path = PROFILE_ROOT) -> list[str]:
    profile_dir = Path(root) / PROFILE_DIRS[profile_type]
    if not profile_dir.exists():
        return []
    return sorted(path.stem for path in profile_dir.glob("*.json"))


def load_profiles_by_type(profile_type: str, root: str | Path = PROFILE_ROOT) -> list[Any]:
    loaders = {
        "company": load_company_profile,
        "employee": load_employee_profile,
        "tax": load_tax_defaults_profile,
        "deduction": load_deduction_defaults_profile,
        "assignment": load_payroll_assignment_profile,
    }
    if profile_type not in loaders:
        raise ValueError(f"Unknown profile type: {profile_type}")
    return [loaders[profile_type](profile_id, root=root) for profile_id in list_profiles(profile_type, root=root)]


def build_employee_pay_config(
    company: CompanyProfile,
    employee: EmployeeProfile,
    tax_defaults: TaxDefaultsProfile,
    deduction_defaults: DeductionDefaultsProfile,
    payroll_check_number: str | None = None,
) -> EmployeePayConfig:
    return EmployeePayConfig(
        employee_id=employee.employee_id,
        employee_name=employee.employee_name,
        employee_address=employee.employee_address,
        bank_name=employee.bank_name,
        deposit_account_type=employee.deposit_account_type,
        routing_number=employee.routing_number,
        account_number=employee.account_number,
        direct_deposit_amount=employee.direct_deposit_amount,
        social_security_number=employee.social_security_number,
        company_name=company.company_name,
        company_address=company.company_address,
        filing_status=tax_defaults.filing_status,
        frequency=tax_defaults.frequency,
        allowances=tax_defaults.allowances,
        additional_federal_wh=tax_defaults.additional_federal_wh,
        state=tax_defaults.state,
        state_tax_rate_override=tax_defaults.state_tax_rate_override,
        local_tax_rate=tax_defaults.local_tax_rate,
        local_tax_label=tax_defaults.local_tax_label,
        earnings=[EarningLine(**asdict(item)) for item in employee.earnings],
        pre_tax_deductions=[
            DeductionLine(**asdict(item)) for item in deduction_defaults.pre_tax_deductions
        ],
        post_tax_deductions=[
            DeductionLine(**asdict(item)) for item in deduction_defaults.post_tax_deductions
        ],
        other_benefits=[BenefitLine(**asdict(item)) for item in employee.other_benefits],
        important_notes=list(employee.important_notes),
        payroll_check_number=payroll_check_number or company.default_payroll_check_number,
    )


def split_employee_pay_config(
    config: EmployeePayConfig,
    company_profile_id: str,
    employee_profile_id: str,
    tax_profile_id: str,
    deduction_profile_id: str,
) -> tuple[CompanyProfile, EmployeeProfile, TaxDefaultsProfile, DeductionDefaultsProfile]:
    company = CompanyProfile(
        profile_id=company_profile_id,
        company_name=config.company_name,
        company_address=config.company_address,
        default_payroll_check_number=config.payroll_check_number,
    )
    employee = EmployeeProfile(
        profile_id=employee_profile_id,
        employee_id=config.employee_id,
        employee_name=config.employee_name,
        employee_address=config.employee_address,
        bank_name=config.bank_name,
        deposit_account_type=config.deposit_account_type,
        routing_number=config.routing_number,
        account_number=config.account_number,
        direct_deposit_amount=config.direct_deposit_amount,
        social_security_number=config.social_security_number,
        earnings=[EarningLine(**asdict(item)) for item in config.earnings],
        other_benefits=[BenefitLine(**asdict(item)) for item in config.other_benefits],
        important_notes=list(config.important_notes),
    )
    tax_defaults = TaxDefaultsProfile(
        profile_id=tax_profile_id,
        filing_status=config.filing_status,
        frequency=config.frequency,
        allowances=config.allowances,
        additional_federal_wh=config.additional_federal_wh,
        state=config.state,
        state_tax_rate_override=config.state_tax_rate_override,
        local_tax_rate=config.local_tax_rate,
        local_tax_label=config.local_tax_label,
    )
    deduction_defaults = DeductionDefaultsProfile(
        profile_id=deduction_profile_id,
        pre_tax_deductions=[
            DeductionLine(**asdict(item)) for item in config.pre_tax_deductions
        ],
        post_tax_deductions=[
            DeductionLine(**asdict(item)) for item in config.post_tax_deductions
        ],
    )
    return company, employee, tax_defaults, deduction_defaults


def load_employee_pay_config(
    company_profile_id: str,
    employee_profile_id: str,
    tax_profile_id: str,
    deduction_profile_id: str,
    root: str | Path = PROFILE_ROOT,
    payroll_check_number: str | None = None,
) -> EmployeePayConfig:
    company = load_company_profile(company_profile_id, root=root)
    employee = load_employee_profile(employee_profile_id, root=root)
    tax_defaults = load_tax_defaults_profile(tax_profile_id, root=root)
    deduction_defaults = load_deduction_defaults_profile(deduction_profile_id, root=root)
    return build_employee_pay_config(
        company=company,
        employee=employee,
        tax_defaults=tax_defaults,
        deduction_defaults=deduction_defaults,
        payroll_check_number=payroll_check_number,
    )


def load_assignment_employee_pay_config(
    assignment_profile_id: str,
    root: str | Path = PROFILE_ROOT,
    payroll_check_number: str | None = None,
) -> EmployeePayConfig:
    assignment = load_payroll_assignment_profile(assignment_profile_id, root=root)
    return load_employee_pay_config(
        company_profile_id=assignment.company_profile_id,
        employee_profile_id=assignment.employee_profile_id,
        tax_profile_id=assignment.tax_profile_id,
        deduction_profile_id=assignment.deduction_profile_id,
        root=root,
        payroll_check_number=(
            payroll_check_number
            or str(assignment.payroll_check_number_start).zfill(9)
        ),
    )
