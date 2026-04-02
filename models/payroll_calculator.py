"""
Payroll calculation engine.

Implements automatic withholding and deduction calculations for the supported
paystub workflows, including FICA, federal/state income tax withholding, and
state-specific payroll deductions used by the web app.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, List

from models.pay_period import PayFrequency, PERIODS_PER_YEAR


# ── Enumerations ──────────────────────────────────────────────────────────────

class FilingStatus(str, Enum):
    SINGLE             = "Single"
    MARRIED            = "Married"
    HEAD_OF_HOUSEHOLD  = "Head of Household"


# ── Tax year support ───────────────────────────────────────────────────────────

SUPPORTED_TAX_YEARS: tuple[int, ...] = (2025, 2026)
_DEFAULT_TAX_YEAR: int = 2026


def _resolve_tax_year(year: int | None) -> int:
    """Return *year* if it has a tax table, otherwise fall back to the default."""
    if year in SUPPORTED_TAX_YEARS:
        return year
    return _DEFAULT_TAX_YEAR


# ── Federal standard deductions ───────────────────────────────────────────────

_STD_DEDUCTION: dict[int, dict[FilingStatus, float]] = {
    2025: {
        FilingStatus.SINGLE:            15_750,
        FilingStatus.MARRIED:           31_500,
        FilingStatus.HEAD_OF_HOUSEHOLD: 23_625,
    },
    2026: {
        FilingStatus.SINGLE:            16_100,
        FilingStatus.MARRIED:           32_200,
        FilingStatus.HEAD_OF_HOUSEHOLD: 24_150,
    },
}

# ── Federal income tax brackets ───────────────────────────────────────────────
# (lower, upper, marginal_rate) — income above lower up to upper is taxed at rate

_FEDERAL_BRACKETS: dict[int, dict[FilingStatus, list[tuple[float, float, float]]]] = {
    2025: {
        FilingStatus.SINGLE: [
            (0,        11_925,  0.10),
            (11_925,   48_475,  0.12),
            (48_475,  103_350,  0.22),
            (103_350, 197_300,  0.24),
            (197_300, 250_525,  0.32),
            (250_525, 626_350,  0.35),
            (626_350, float("inf"), 0.37),
        ],
        FilingStatus.MARRIED: [
            (0,        23_850,  0.10),
            (23_850,   96_950,  0.12),
            (96_950,  206_700,  0.22),
            (206_700, 394_600,  0.24),
            (394_600, 501_050,  0.32),
            (501_050, 751_600,  0.35),
            (751_600, float("inf"), 0.37),
        ],
        FilingStatus.HEAD_OF_HOUSEHOLD: [
            (0,        17_000,  0.10),
            (17_000,   64_850,  0.12),
            (64_850,  103_350,  0.22),
            (103_350, 197_300,  0.24),
            (197_300, 250_500,  0.32),
            (250_500, 626_350,  0.35),
            (626_350, float("inf"), 0.37),
        ],
    },
    2026: {
        FilingStatus.SINGLE: [
            (0,        12_400,  0.10),
            (12_400,   50_400,  0.12),
            (50_400,  105_700,  0.22),
            (105_700, 201_775,  0.24),
            (201_775, 256_225,  0.32),
            (256_225, 640_600,  0.35),
            (640_600, float("inf"), 0.37),
        ],
        FilingStatus.MARRIED: [
            (0,        24_800,  0.10),
            (24_800,  100_800,  0.12),
            (100_800, 211_400,  0.22),
            (211_400, 403_550,  0.24),
            (403_550, 512_450,  0.32),
            (512_450, 768_700,  0.35),
            (768_700, float("inf"), 0.37),
        ],
        FilingStatus.HEAD_OF_HOUSEHOLD: [
            (0,        17_700,  0.10),
            (17_700,   67_450,  0.12),
            (67_450,  105_700,  0.22),
            (105_700, 201_750,  0.24),
            (201_750, 256_200,  0.32),
            (256_200, 640_600,  0.35),
            (640_600, float("inf"), 0.37),
        ],
    },
}

# ── FICA constants ────────────────────────────────────────────────────────────

_SS_RATE:        dict[int, float] = {2025: 0.0620, 2026: 0.0620}
_SS_WAGE_BASE:   dict[int, float] = {2025: 176_100.0, 2026: 184_500.0}
_MEDICARE_RATE:  dict[int, float] = {2025: 0.0145, 2026: 0.0145}
_MEDICARE_ADDL_RATE: dict[int, float] = {2025: 0.0090, 2026: 0.0090}
_MEDICARE_ADDL_THRESHOLD: dict[int, dict[FilingStatus, float]] = {
    2025: {
        FilingStatus.SINGLE:            200_000,
        FilingStatus.MARRIED:           250_000,
        FilingStatus.HEAD_OF_HOUSEHOLD: 200_000,
    },
    2026: {
        FilingStatus.SINGLE:            200_000,
        FilingStatus.MARRIED:           250_000,
        FilingStatus.HEAD_OF_HOUSEHOLD: 200_000,
    },
}

# Backward-compatible aliases (default to current tax year)
SS_RATE:             float = _SS_RATE[_DEFAULT_TAX_YEAR]
SS_WAGE_BASE:        float = _SS_WAGE_BASE[_DEFAULT_TAX_YEAR]
MEDICARE_RATE:       float = _MEDICARE_RATE[_DEFAULT_TAX_YEAR]
MEDICARE_ADDL_RATE:  float = _MEDICARE_ADDL_RATE[_DEFAULT_TAX_YEAR]

# ── New York State brackets ───────────────────────────────────────────────────

_NY_BRACKETS_SINGLE: dict[int, list[tuple[float, float, float]]] = {
    2025: [
        (0,           8_500, 0.0400),
        (8_500,      11_700, 0.0450),
        (11_700,     13_900, 0.0525),
        (13_900,     80_650, 0.0550),
        (80_650,    215_400, 0.0600),
        (215_400, 1_077_550, 0.0685),
        (1_077_550, 5_000_000, 0.0965),
        (5_000_000, 25_000_000, 0.1030),
        (25_000_000, float("inf"), 0.1090),
    ],
    2026: [
        (0,          17_650, 0.0400),
        (17_650,     24_300, 0.0450),
        (24_300,     28_700, 0.0525),
        (28_700,    166_050, 0.0585),
        (166_050,   332_175, 0.0625),
        (332_175, 2_218_185, 0.0685),
        (2_218_185, float("inf"), 0.1090),
    ],
}

_NY_BRACKETS_MARRIED: dict[int, list[tuple[float, float, float]]] = {
    2025: [
        (0,          17_150, 0.0400),
        (17_150,     23_600, 0.0450),
        (23_600,     27_900, 0.0525),
        (27_900,    161_550, 0.0550),
        (161_550,   323_200, 0.0600),
        (323_200, 2_155_350, 0.0685),
        (2_155_350, 5_000_000, 0.0965),
        (5_000_000, 25_000_000, 0.1030),
        (25_000_000, float("inf"), 0.1090),
    ],
    2026: [
        (0,          26_600, 0.0400),
        (26_600,     36_500, 0.0450),
        (36_500,     43_000, 0.0525),
        (43_000,    323_200, 0.0585),
        (323_200, 2_155_350, 0.0685),
        (2_155_350, float("inf"), 0.1090),
    ],
}

_NY_STD_DEDUCTION: dict[int, dict[FilingStatus, float]] = {
    2025: {
        FilingStatus.SINGLE:            8_000,
        FilingStatus.MARRIED:           16_050,
        FilingStatus.HEAD_OF_HOUSEHOLD: 11_200,
    },
    2026: {
        FilingStatus.SINGLE:            8_000,
        FilingStatus.MARRIED:           16_050,
        FilingStatus.HEAD_OF_HOUSEHOLD: 11_200,
    },
}

_NY_PFL_RATE:       dict[int, float] = {2025: 0.00388, 2026: 0.00432}
_NY_PFL_ANNUAL_CAP: dict[int, float] = {2025: 354.53,  2026: 411.91}

# Backward-compatible aliases
NY_PFL_RATE:      float = _NY_PFL_RATE[_DEFAULT_TAX_YEAR]
NY_PFL_ANNUAL_CAP: float = _NY_PFL_ANNUAL_CAP[_DEFAULT_TAX_YEAR]

# Approximate effective state tax rates for states without bracket tables here.
# Use these as fallback when state_tax_rate is not explicitly provided.
STATE_DEFAULT_RATES: dict[str, float | None] = {
    "AL": 0.050, "AK": 0.000, "AZ": 0.025, "AR": 0.047, "CA": 0.093,
    "CO": 0.044, "CT": 0.050, "DE": 0.066, "FL": 0.000, "GA": 0.055,
    "HI": 0.079, "ID": 0.058, "IL": 0.0495,"IN": 0.030, "IA": 0.060,
    "KS": 0.057, "KY": 0.045, "LA": 0.042, "ME": 0.075, "MD": 0.048,
    "MA": 0.050, "MI": 0.043, "MN": 0.068, "MS": 0.050, "MO": 0.053,
    "MT": 0.069, "NE": 0.068, "NV": 0.000, "NH": 0.000, "NJ": 0.065,
    "NM": 0.059, "NY": None,  "NC": 0.045, "ND": 0.025, "OH": 0.040,
    "OK": 0.047, "OR": 0.096, "PA": 0.031, "RI": 0.060, "SC": 0.070,
    "SD": 0.000, "TN": 0.000, "TX": 0.000, "UT": 0.046, "VT": 0.066,
    "VA": 0.058, "WA": 0.000, "WV": 0.065, "WI": 0.065, "WY": 0.000,
}


# ── Low-level tax math ────────────────────────────────────────────────────────

def _bracketed_tax(
    annual_income: float,
    brackets: list[tuple[float, float, float]],
) -> float:
    """Apply a progressive bracket table to *annual_income*. Returns annual tax."""
    tax = 0.0
    for low, high, rate in brackets:
        if annual_income <= low:
            break
        taxable = min(annual_income, high) - low
        tax += taxable * rate
    return tax


def compute_federal_withholding(
    gross_period:    float,
    filing_status:   FilingStatus,
    frequency:       PayFrequency,
    allowances:      int   = 0,
    additional_wh:   float = 0.0,
    year:            int   = _DEFAULT_TAX_YEAR,
) -> float:
    """
    IRS Percentage Method withholding for one pay period.

    allowances: legacy W-4 allowances (each reduces annualized wage by $4,400).
    additional_wh: flat additional amount added to each period (new W-4 line 4c).
    """
    yr           = _resolve_tax_year(year)
    periods      = PERIODS_PER_YEAR[frequency]
    annual_wage  = gross_period * periods
    allowance_val = 4_400 * allowances
    taxable      = max(0.0, annual_wage - _STD_DEDUCTION[yr][filing_status] - allowance_val)
    annual_tax   = _bracketed_tax(taxable, _FEDERAL_BRACKETS[yr][filing_status])
    per_period   = round(annual_tax / periods, 2)
    return round(per_period + additional_wh, 2)


def compute_social_security(
    gross_period: float,
    ytd_gross_before: float,
    year: int = _DEFAULT_TAX_YEAR,
) -> float:
    """SS withholding, capped at the annual wage base."""
    yr        = _resolve_tax_year(year)
    remaining = max(0.0, _SS_WAGE_BASE[yr] - ytd_gross_before)
    taxable   = min(gross_period, remaining)
    return round(taxable * _SS_RATE[yr], 2)


def compute_medicare(
    gross_period:     float,
    ytd_gross_before: float,
    filing_status:    FilingStatus = FilingStatus.SINGLE,
    year:             int = _DEFAULT_TAX_YEAR,
) -> float:
    """Medicare withholding including 0.9% Additional Medicare Tax above threshold."""
    yr         = _resolve_tax_year(year)
    base       = gross_period * _MEDICARE_RATE[yr]
    threshold  = _MEDICARE_ADDL_THRESHOLD[yr][filing_status]
    prev_above = max(0.0, ytd_gross_before - threshold)
    cur_above  = max(0.0, ytd_gross_before + gross_period - threshold) - prev_above
    return round(base + cur_above * _MEDICARE_ADDL_RATE[yr], 2)


def compute_ny_state_tax(
    gross_period:  float,
    filing_status: FilingStatus,
    frequency:     PayFrequency,
    year:          int = _DEFAULT_TAX_YEAR,
) -> float:
    """New York State income tax withholding (annualized percentage method)."""
    yr           = _resolve_tax_year(year)
    periods      = PERIODS_PER_YEAR[frequency]
    annual_wage  = gross_period * periods
    std_ded      = _NY_STD_DEDUCTION[yr].get(filing_status, 8_000)
    taxable      = max(0.0, annual_wage - std_ded)
    brackets     = (_NY_BRACKETS_SINGLE[yr] if filing_status == FilingStatus.SINGLE
                    else _NY_BRACKETS_MARRIED[yr])
    annual_tax   = _bracketed_tax(taxable, brackets)
    return round(annual_tax / periods, 2)


def compute_state_tax(
    gross_period:   float,
    state:          str,
    filing_status:  FilingStatus,
    frequency:      PayFrequency,
    override_rate:  float | None = None,
    year:           int = _DEFAULT_TAX_YEAR,
) -> float:
    """
    State income tax withholding.

    For NY, uses progressive bracket tables.
    For all other states, applies a flat effective rate (from STATE_DEFAULT_RATES
    or override_rate).
    """
    if override_rate is not None:
        return round(gross_period * override_rate, 2)
    if state.upper() == "NY":
        return compute_ny_state_tax(gross_period, filing_status, frequency, year=year)
    rate = STATE_DEFAULT_RATES.get(state.upper(), 0.0)
    if rate is None:   # NY placeholder resolved above; None means "use brackets"
        rate = 0.0
    return round(gross_period * rate, 2)


def compute_ny_paid_family_leave(
    gross_period: float,
    ytd_before: float,
    year: int = _DEFAULT_TAX_YEAR,
) -> float:
    """New York Paid Family Leave employee contribution."""
    yr = _resolve_tax_year(year)
    cap = _NY_PFL_ANNUAL_CAP[yr]
    if ytd_before >= cap:
        return 0.0
    contribution = round(gross_period * _NY_PFL_RATE[yr], 2)
    remaining_cap = max(0.0, round(cap - ytd_before, 2))
    return round(min(contribution, remaining_cap), 2)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class EarningLine:
    """One line of earnings on a paystub."""
    label:       str
    rate:        float = 0.0    # hourly / salary rate
    hours:       float = 0.0    # hours worked this period
    flat_amount: float = 0.0    # override rate×hours (e.g. bonus, reimbursement)

    @property
    def current(self) -> float:
        if self.flat_amount:
            return round(self.flat_amount, 2)
        return round(self.rate * self.hours, 2)


@dataclass
class BenefitLine:
    """Informational benefit / accrual shown in the right panel."""
    label:   str
    current: float = 0.0
    ytd:     float = 0.0


@dataclass
class DeductionLine:
    """Pre-tax or post-tax deduction per period."""
    label:    str
    amount:   float
    is_pretax: bool = True   # pre-tax deductions reduce federal taxable wages


@dataclass
class EmployeePayConfig:
    """
    Complete employee + payroll configuration.

    Pass this to ``compute_paystub_data()`` to produce a fully-calculated
    paystub dict that can be fed directly to ``Paystub(**data)``.
    """
    # Identity
    employee_id:            str
    employee_name:          str
    employee_address:       str

    # Employer
    company_name:    str
    company_address: str

    employee_title:         str = ""
    social_security_number: str = ""
    bank_name:              str = ""
    deposit_account_type:   str = ""
    routing_number:         str = ""
    account_number:         str = ""
    direct_deposit_amount:  float = 0.0

    # Pay setup
    filing_status:  FilingStatus  = FilingStatus.SINGLE
    frequency:      PayFrequency  = PayFrequency.BIWEEKLY
    allowances:     int           = 0
    additional_federal_wh: float  = 0.0

    # State / local
    state:            str   = "NY"
    state_tax_rate_override: float | None = None   # None = use built-in tables
    local_tax_rate:   float = 0.0                  # e.g. 0.03876 for NYC
    local_tax_label:  str   = ""
    apply_ny_paid_family_leave: bool = True

    # Earnings and deductions
    earnings:            List[EarningLine]  = field(default_factory=list)
    pre_tax_deductions:  List[DeductionLine] = field(default_factory=list)
    post_tax_deductions: List[DeductionLine] = field(default_factory=list)
    other_benefits:      List[BenefitLine]   = field(default_factory=list)
    important_notes:     List[str]            = field(default_factory=list)

    payroll_check_number: str = "000000001"


# ── YTD tracking ──────────────────────────────────────────────────────────────

@dataclass
class YTDState:
    """Accumulated year-to-date totals before the current pay period."""
    gross:       float                = 0.0
    earnings:    dict[str, float]     = field(default_factory=dict)
    taxes:       dict[str, float]     = field(default_factory=dict)
    deductions:  dict[str, float]     = field(default_factory=dict)
    adjustments: dict[str, float]     = field(default_factory=dict)
    other_benefits: dict[str, float]  = field(default_factory=dict)

    def copy(self) -> "YTDState":
        return YTDState(
            gross=self.gross,
            earnings=dict(self.earnings),
            taxes=dict(self.taxes),
            deductions=dict(self.deductions),
            adjustments=dict(self.adjustments),
            other_benefits=dict(self.other_benefits),
        )

    @classmethod
    def from_paystubs(cls, paystubs: List[dict | Any]) -> "YTDState":
        state = cls()
        for paystub in paystubs:
            state.advance(paystub)
        return state

    def advance(self, paystub_data: dict | Any) -> None:
        """Update from a just-computed paystub dict or Paystub model."""
        data = paystub_data.model_dump() if hasattr(paystub_data, "model_dump") else paystub_data

        self.gross = round(self.gross + data["gross_pay_current"], 2)
        for item in data.get("earnings", []):
            self.earnings[item["label"]] = (
                self.earnings.get(item["label"], 0.0) + item["current"]
            )
        for item in data.get("taxes", []):
            self.taxes[item["label"]] = (
                self.taxes.get(item["label"], 0.0) + item["current"]
            )
        for item in data.get("deductions", []):
            self.deductions[item["label"]] = (
                self.deductions.get(item["label"], 0.0) + item["current"]
            )
        for item in data.get("adjustments", []):
            self.adjustments[item["label"]] = (
                self.adjustments.get(item["label"], 0.0) + item["current"]
            )
        for item in data.get("other_benefits", []):
            self.other_benefits[item["label"]] = (
                self.other_benefits.get(item["label"], 0.0) + item["current"]
            )


# ── Main calculation function ─────────────────────────────────────────────────

def compute_paystub_data(
    config:           EmployeePayConfig,
    pay_period_start: date,
    pay_period_end:   date,
    pay_date:         date,
    ytd:              YTDState | None = None,
    tax_year:         int | None = None,
) -> dict:
    """
    Compute a complete paystub data dict from employee config and dates.

    ytd: accumulated YTD state *before* this period. Pass None for the first
         period of the year (everything starts at zero).
    tax_year: override the tax year used for bracket/rate lookups.
              Defaults to ``pay_date.year``.

    Returns a dict suitable for ``Paystub(**data)`` and ``generate_paystub_pdf(data)``.
    """
    if ytd is None:
        ytd = YTDState()

    year = _resolve_tax_year(tax_year if tax_year is not None else pay_date.year)

    # ── Earnings ──────────────────────────────────────────────────────────────
    earnings_items = []
    gross_current  = 0.0
    for e in config.earnings:
        cur = e.current
        gross_current += cur
        ytd_for_line = ytd.earnings.get(e.label, 0.0) + cur
        earnings_items.append({
            "label":   e.label,
            "rate":    e.rate,
            "hours":   e.hours,
            "current": cur,
            "ytd":     ytd_for_line,
        })

    # ── Pre-tax deductions reduce federal/state taxable wages ─────────────────
    pretax_total     = sum(d.amount for d in config.pre_tax_deductions if d.is_pretax)
    federal_taxable  = max(0.0, gross_current - pretax_total)

    # ── Tax calculations ──────────────────────────────────────────────────────
    fed_wh   = compute_federal_withholding(
        federal_taxable, config.filing_status, config.frequency,
        config.allowances, config.additional_federal_wh, year=year,
    )
    ss       = compute_social_security(gross_current, ytd.gross, year=year)
    medicare = compute_medicare(gross_current, ytd.gross, config.filing_status, year=year)
    state_tx = compute_state_tax(
        federal_taxable, config.state,
        config.filing_status, config.frequency,
        config.state_tax_rate_override, year=year,
    )

    taxes: list[dict] = [
        {"label": "Federal Income Tax",
         "current": fed_wh,
         "ytd": ytd.taxes.get("Federal Income Tax", 0.0) + fed_wh},
        {"label": "Social Security Tax",
         "current": ss,
         "ytd": ytd.taxes.get("Social Security Tax", 0.0) + ss},
        {"label": "Medicare Tax",
         "current": medicare,
         "ytd": ytd.taxes.get("Medicare Tax", 0.0) + medicare},
        {"label": f"{config.state} State Income Tax",
         "current": state_tx,
         "ytd": ytd.taxes.get(f"{config.state} State Income Tax", 0.0) + state_tx},
    ]

    if config.local_tax_rate:
        local_label = config.local_tax_label or f"{config.state} Local Tax"
        local_tx    = round(gross_current * config.local_tax_rate, 2)
        taxes.append({
            "label":   local_label,
            "current": local_tx,
            "ytd":     ytd.taxes.get(local_label, 0.0) + local_tx,
        })

    # ── Deductions ────────────────────────────────────────────────────────────
    deductions: list[dict] = []
    for d in config.pre_tax_deductions:
        deductions.append({
            "label":   d.label,
            "current": d.amount,
            "ytd":     ytd.deductions.get(d.label, 0.0) + d.amount,
        })
    for d in config.post_tax_deductions:
        deductions.append({
            "label":   d.label,
            "current": d.amount,
            "ytd":     ytd.deductions.get(d.label, 0.0) + d.amount,
        })

    if config.state.upper() == "NY" and config.apply_ny_paid_family_leave:
        prior_pfl = ytd.deductions.get("NY Paid Family Leave", 0.0)
        pfl_current = compute_ny_paid_family_leave(gross_current, prior_pfl, year=year)
        if pfl_current:
            deductions.append({
                "label": "NY Paid Family Leave",
                "current": pfl_current,
                "ytd": round(prior_pfl + pfl_current, 2),
            })

    # ── Totals ────────────────────────────────────────────────────────────────
    total_taxes_current      = round(sum(t["current"] for t in taxes), 2)
    total_deductions_current = round(sum(d["current"] for d in deductions), 2)
    net_pay_current          = round(
        gross_current - total_taxes_current - total_deductions_current, 2
    )

    ytd_gross            = ytd.gross + gross_current
    total_taxes_ytd      = round(sum(t["ytd"] for t in taxes), 2)
    total_deductions_ytd = round(sum(d["ytd"] for d in deductions), 2)
    net_pay_ytd          = round(ytd_gross - total_taxes_ytd - total_deductions_ytd, 2)

    # ── Other benefits (informational) ────────────────────────────────────────
    other_benefits = []
    for b in config.other_benefits:
        opening_ytd = max(0.0, b.ytd - b.current)
        prior_ytd = ytd.other_benefits.get(b.label, opening_ytd)
        other_benefits.append({
            "label": b.label,
            "current": b.current,
            "ytd": round(prior_ytd + b.current, 2),
        })

    # ── Footnotes ─────────────────────────────────────────────────────────────
    footnotes = [
        "* Excluded from federal taxable wages",
        f"Your federal wages this period are ${federal_taxable:,.2f}",
    ]
    if config.state.upper() == "NY" and config.apply_ny_paid_family_leave:
        footnotes.append("NY Paid Family Leave is withheld automatically based on current New York employee rates.")

    return {
        # Employer / employee identity
        "company_name":            config.company_name,
        "company_address":         config.company_address,
        "employee_name":           config.employee_name,
        "employee_title":          config.employee_title,
        "employee_address":        config.employee_address,
        "employee_id":             config.employee_id,
        "bank_name":               config.bank_name,
        "deposit_account_type":    config.deposit_account_type,
        "routing_number":          config.routing_number,
        "account_number":          config.account_number,
        "direct_deposit_amount":   round(config.direct_deposit_amount, 2),
        # Dates
        "pay_date":                pay_date.strftime("%Y-%m-%d"),
        "pay_period_start":        pay_period_start.strftime("%Y-%m-%d"),
        "pay_period_end":          pay_period_end.strftime("%Y-%m-%d"),
        # Employee meta
        "social_security_number":  config.social_security_number,
        "taxable_marital_status":  config.filing_status.value,
        "exemptions_allowances":   (
            f"Federal: {config.allowances}, "
            f"${config.additional_federal_wh:,.2f} Additional Tax"
        ),
        "payroll_check_number":    config.payroll_check_number,
        # Line items
        "earnings":                earnings_items,
        "taxes":                   taxes,
        "deductions":              deductions,
        "adjustments":             [],
        "other_benefits":          other_benefits,
        "important_notes":         config.important_notes,
        "footnotes":               footnotes,
        # Computed totals
        "gross_pay_current":       gross_current,
        "gross_pay_ytd":           ytd_gross,
        "total_taxes_current":     total_taxes_current,
        "total_taxes_ytd":         total_taxes_ytd,
        "total_deductions_current": total_deductions_current,
        "total_deductions_ytd":    total_deductions_ytd,
        "net_pay_current":         net_pay_current,
        "net_pay_ytd":             net_pay_ytd,
    }
