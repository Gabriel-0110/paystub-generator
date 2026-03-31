"""
Paystub validation layer.

Catches common payroll data errors before a PDF is generated or a record
is saved.  All public functions return a (possibly empty) list of human-
readable error strings so callers decide how to handle problems.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.paystub import Paystub

_TOLERANCE = 0.02   # cents rounding tolerance


def validate_paystub(paystub: "Paystub") -> list[str]:
    """
    Run all validation checks on a Paystub instance.

    Returns a list of error strings.  An empty list means the paystub is valid.
    """
    errors: list[str] = []
    errors += _check_gross_pay(paystub)
    errors += _check_net_pay(paystub)
    errors += _check_ytd_consistency(paystub)
    errors += _check_hours_and_rate(paystub)
    errors += _check_required_tax_lines(paystub)
    errors += _check_fica_rates(paystub)
    return errors


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_gross_pay(p: "Paystub") -> list[str]:
    errors: list[str] = []
    if p.earnings and p.gross_pay_current is not None:
        expected = round(sum(e.current for e in p.earnings), 2)
        if abs(p.gross_pay_current - expected) > _TOLERANCE:
            errors.append(
                f"Gross pay mismatch: stated ${p.gross_pay_current:,.2f} "
                f"but earnings sum to ${expected:,.2f}."
            )
    return errors


def _check_net_pay(p: "Paystub") -> list[str]:
    errors: list[str] = []
    if p.gross_pay_current is None or p.net_pay_current is None:
        return errors
    tax_total = round(sum(t.current for t in p.taxes), 2)
    ded_total = round(
        sum(d.current for d in p.deductions)
        + sum(a.current for a in p.adjustments), 2
    )
    expected_net = round(p.gross_pay_current - tax_total - ded_total, 2)
    if abs(p.net_pay_current - expected_net) > _TOLERANCE:
        errors.append(
            f"Net pay mismatch: stated ${p.net_pay_current:,.2f} "
            f"but gross − taxes − deductions = ${expected_net:,.2f}."
        )
    if p.net_pay_current < 0:
        errors.append(
            f"Net pay is negative (${p.net_pay_current:,.2f}). "
            "Deductions exceed gross pay."
        )
    return errors


def _check_ytd_consistency(p: "Paystub") -> list[str]:
    errors: list[str] = []
    # Each YTD value should be >= current-period value
    for e in p.earnings:
        if e.ytd < e.current - _TOLERANCE:
            errors.append(
                f"Earnings YTD < current for '{e.label}': "
                f"YTD ${e.ytd:,.2f} < current ${e.current:,.2f}."
            )
    for t in p.taxes:
        if t.ytd < t.current - _TOLERANCE:
            errors.append(
                f"Tax YTD < current for '{t.label}': "
                f"YTD ${t.ytd:,.2f} < current ${t.current:,.2f}."
            )
    for d in p.deductions:
        if d.ytd < d.current - _TOLERANCE:
            errors.append(
                f"Deduction YTD < current for '{d.label}': "
                f"YTD ${d.ytd:,.2f} < current ${d.current:,.2f}."
            )
    if p.gross_pay_ytd is not None and p.gross_pay_current is not None and p.gross_pay_ytd < p.gross_pay_current - _TOLERANCE:
        errors.append(
            f"Gross pay YTD (${p.gross_pay_ytd:,.2f}) is less than "
            f"current gross (${p.gross_pay_current:,.2f})."
        )
    return errors


def _check_hours_and_rate(p: "Paystub") -> list[str]:
    errors: list[str] = []
    for e in p.earnings:
        # Flag suspiciously high hours (>168h = more than 7×24h in a week)
        if e.hours > 168:
            errors.append(
                f"Earnings '{e.label}': hours ({e.hours}) exceed 168 "
                "(maximum possible in a 7-day period)."
            )
        # Verify rate × hours = current when both are provided
        if e.rate > 0 and e.hours > 0:
            expected = round(e.rate * e.hours, 2)
            if abs(e.current - expected) > _TOLERANCE:
                errors.append(
                    f"Earnings '{e.label}': rate ({e.rate}) × hours ({e.hours}) "
                    f"= ${expected:,.2f} but current is ${e.current:,.2f}."
                )
    return errors


def _check_required_tax_lines(p: "Paystub") -> list[str]:
    """Warn if standard FICA lines are missing on a non-exempt paystub."""
    errors: list[str] = []
    if not p.taxes:
        return errors   # no taxes at all is allowed (e.g. tax-exempt)
    tax_labels = {t.label for t in p.taxes}
    for required in ("Federal Income Tax", "Social Security Tax", "Medicare Tax"):
        if required not in tax_labels:
            errors.append(f"Missing expected tax line: '{required}'.")
    return errors


def _check_fica_rates(p: "Paystub") -> list[str]:
    """Cross-check SS and Medicare amounts against the standard rates."""
    from models.payroll_calculator import SS_RATE, MEDICARE_RATE, SS_WAGE_BASE
    errors: list[str] = []
    if p.gross_pay_current is None:
        return errors
    for t in p.taxes:
        if t.label == "Social Security Tax":
            # Expected SS can be zero if wage base exceeded; only flag if obviously wrong
            max_ss = round(p.gross_pay_current * SS_RATE, 2)
            if t.current > max_ss + _TOLERANCE:
                errors.append(
                    f"Social Security Tax ${t.current:,.2f} exceeds maximum "
                    f"possible (${max_ss:,.2f} at {SS_RATE*100:.1f}% of gross)."
                )
        elif t.label == "Medicare Tax":
            expected = round(p.gross_pay_current * MEDICARE_RATE, 2)
            # Allow slightly more due to additional 0.9%
            if t.current < expected - _TOLERANCE:
                errors.append(
                    f"Medicare Tax ${t.current:,.2f} appears too low "
                    f"(expected at least ${expected:,.2f})."
                )
    return errors


def assert_valid(paystub: "Paystub", raise_on_error: bool = True) -> list[str]:
    """
    Run validation and optionally raise ValueError listing all errors.

    If raise_on_error is False, returns the error list instead.
    """
    errors = validate_paystub(paystub)
    if errors and raise_on_error:
        bullet_list = "\n  • ".join(errors)
        raise ValueError(f"Paystub validation failed:\n  • {bullet_list}")
    return errors
