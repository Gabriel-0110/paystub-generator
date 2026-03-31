from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class EarningItem(BaseModel):
    label: str
    rate: float = 0.0
    hours: float = 0.0
    current: float
    ytd: float


class DraftEarningItem(BaseModel):
    label: str
    rate: float = 0.0
    hours: float = 0.0
    amount: float = 0.0


class DeductionItem(BaseModel):
    label: str
    current: float
    ytd: float


class DraftDeductionItem(BaseModel):
    label: str
    amount: float = 0.0
    is_pretax: bool = False


class NoteItem(BaseModel):
    label: str
    current: float
    ytd: float


class Paystub(BaseModel):
    company_name: str
    company_address: str
    employee_name: str
    employee_address: str = ""
    employee_id: str
    pay_date: str
    pay_period_start: str
    pay_period_end: str

    bank_name: str = ""
    deposit_account_type: str = ""
    routing_number: str = ""
    account_number: str = ""
    direct_deposit_amount: float = 0.0

    social_security_number: str = ""
    company_logo: str = ""
    taxable_marital_status: str = ""
    exemptions_allowances: str = ""
    payroll_check_number: str = ""
    work_state: str = "NY"
    pay_frequency: str = "biweekly"
    allowances_count: int = 0
    additional_federal_withholding: float = 0.0
    compensation_type: str = "hourly"
    primary_earning_label: str = "Regular"
    annual_salary: float = 0.0
    salary_period_amount: float = 0.0
    weekly_hours: float = 40.0
    hourly_rate: float = 0.0
    regular_hours: float = 0.0
    draft_mode: bool = False
    auto_calculate_taxes: bool = True
    auto_add_state_deductions: bool = False

    source_earnings: List[DraftEarningItem] = Field(default_factory=list)
    source_deductions: List[DraftDeductionItem] = Field(default_factory=list)

    earnings: List[EarningItem] = Field(default_factory=list)
    taxes: List[DeductionItem] = Field(default_factory=list)
    deductions: List[DeductionItem] = Field(default_factory=list)
    adjustments: List[DeductionItem] = Field(default_factory=list)
    other_benefits: List[NoteItem] = Field(default_factory=list)
    important_notes: List[str] = Field(default_factory=list)
    footnotes: List[str] = Field(default_factory=list)

    # Totals are optional — if omitted they are computed from line items.
    gross_pay_current: Optional[float] = None
    gross_pay_ytd: Optional[float] = None
    total_taxes_current: Optional[float] = None
    total_taxes_ytd: Optional[float] = None
    total_deductions_current: Optional[float] = None
    total_deductions_ytd: Optional[float] = None
    net_pay_current: Optional[float] = None
    net_pay_ytd: Optional[float] = None

    manual_stub_amount: Optional[float] = None

    @model_validator(mode="after")
    def compute_totals(self) -> "Paystub":
        """Auto-compute any totals not explicitly provided, from line items."""
        if self.earnings:
            if self.gross_pay_current is None:
                self.gross_pay_current = round(
                    sum(e.current for e in self.earnings), 2
                )
            if self.gross_pay_ytd is None:
                self.gross_pay_ytd = round(
                    sum(e.ytd for e in self.earnings), 2
                )

        all_deductions = list(self.deductions) + list(self.adjustments)

        if self.taxes or all_deductions:
            if self.total_taxes_current is None:
                self.total_taxes_current = round(
                    sum(t.current for t in self.taxes), 2
                )
            if self.total_taxes_ytd is None:
                self.total_taxes_ytd = round(
                    sum(t.ytd for t in self.taxes), 2
                )
            if self.total_deductions_current is None:
                self.total_deductions_current = round(
                    sum(d.current for d in all_deductions), 2
                )
            if self.total_deductions_ytd is None:
                self.total_deductions_ytd = round(
                    sum(d.ytd for d in all_deductions), 2
                )

        if self.net_pay_current is None and self.gross_pay_current is not None:
            self.net_pay_current = round(
                self.gross_pay_current
                - (self.total_taxes_current or 0.0)
                - (self.total_deductions_current or 0.0),
                2,
            )
        if self.net_pay_ytd is None and self.gross_pay_ytd is not None:
            self.net_pay_ytd = round(
                self.gross_pay_ytd
                - (self.total_taxes_ytd or 0.0)
                - (self.total_deductions_ytd or 0.0),
                2,
            )
        return self
