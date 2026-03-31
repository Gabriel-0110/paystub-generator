"""
Batch paystub generation.

Supports three generation modes:
  1. Single paystub for a specific pay period.
  2. All paystubs for one employee across a full calendar year.
  3. A range of consecutive pay periods for one employee.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from models.pay_period import PayPeriod, get_pay_periods
from models.profile_store import (
    PROFILE_ROOT,
    list_profiles,
    load_assignment_employee_pay_config,
    load_payroll_assignment_profile,
)
from models.payroll_calculator import EmployeePayConfig, YTDState, compute_paystub_data
from models.validator import assert_valid
from models.paystub import Paystub
from generators.pdf_generator import PaystubTemplate, generate_paystub_pdf


def build_ytd_state(
    config: EmployeePayConfig,
    year: int,
    through_period_number: int,
    validate: bool = False,
) -> YTDState:
    """
    Replay payroll periods through *through_period_number* and return ending YTD.

    The returned state reflects totals after the requested period has completed.
    """
    if through_period_number <= 0:
        return YTDState()

    periods = get_pay_periods(year, config.frequency)
    ytd = YTDState()

    for period in periods:
        if period.period_number > through_period_number:
            break
        data = compute_paystub_data(config, period.start, period.end, period.pay_date, ytd)
        paystub = Paystub(**data)
        if validate:
            assert_valid(paystub)
        ytd.advance(paystub)

    return ytd


def generate_single(
    config:    EmployeePayConfig,
    period:    PayPeriod,
    ytd:       YTDState | None = None,
    auto_ytd:  bool = True,
    validate:  bool = True,
    template:  PaystubTemplate | str = PaystubTemplate.DETACHED_CHECK,
    output_dir: str | Path = "output",
) -> str:
    """
    Generate one paystub PDF for a given pay period.

    Returns the path to the generated PDF.
    ytd: YTD state *before* this period (None = first period of year).
    """
    if ytd is not None:
        seed_ytd = ytd.copy()
    elif auto_ytd and period.period_number > 1:
        seed_ytd = build_ytd_state(
            config,
            period.start.year,
            through_period_number=period.period_number - 1,
            validate=validate,
        )
    else:
        seed_ytd = YTDState()

    data     = compute_paystub_data(config, period.start, period.end, period.pay_date, seed_ytd)
    paystub  = Paystub(**data)
    if validate:
        assert_valid(paystub)
    return generate_paystub_pdf(data, output_dir=str(output_dir), template=template)


def generate_year(
    config:     EmployeePayConfig,
    year:       int,
    validate:   bool = True,
    template:   PaystubTemplate | str = PaystubTemplate.DETACHED_CHECK,
    output_dir: str | Path = "output",
    check_number_start: int | None = None,
) -> List[str]:
    """
    Generate paystub PDFs for every pay period in *year*.

    YTD totals are accumulated automatically across periods.
    check_number_start: if provided, auto-increments the payroll check number
                        starting from this value.

    Returns a list of generated PDF paths in chronological order.
    """
    periods  = get_pay_periods(year, config.frequency)
    ytd      = YTDState()
    paths: List[str] = []

    for i, period in enumerate(periods):
        # Auto-increment check number if requested
        if check_number_start is not None:
            config.payroll_check_number = str(check_number_start + i).zfill(9)

        data    = compute_paystub_data(config, period.start, period.end, period.pay_date, ytd)
        paystub = Paystub(**data)
        if validate:
            assert_valid(paystub)

        path = generate_paystub_pdf(data, output_dir=str(output_dir), template=template)
        paths.append(path)

        # Advance YTD for the next period
        ytd.advance(paystub)

    return paths


def generate_range(
    config:     EmployeePayConfig,
    year:       int,
    first_period: int = 1,
    last_period:  int | None = None,
    ytd_before:  YTDState | None = None,
    auto_ytd:    bool = True,
    validate:    bool = True,
    template:    PaystubTemplate | str = PaystubTemplate.DETACHED_CHECK,
    output_dir:  str | Path = "output",
    check_number_start: int | None = None,
) -> List[str]:
    """
    Generate paystub PDFs for a contiguous range of pay periods within *year*.

    first_period / last_period: 1-based period numbers (inclusive).
    ytd_before: pre-existing YTD state before *first_period* starts.

    Returns a list of generated PDF paths.
    """
    all_periods = get_pay_periods(year, config.frequency)

    if last_period is None:
        last_period = len(all_periods)

    selected = [p for p in all_periods
                if first_period <= p.period_number <= last_period]

    if ytd_before is not None:
        ytd = ytd_before.copy()
    elif auto_ytd and first_period > 1:
        ytd = build_ytd_state(
            config,
            year,
            through_period_number=first_period - 1,
            validate=validate,
        )
    else:
        ytd = YTDState()
    paths: List[str] = []

    for i, period in enumerate(selected):
        if check_number_start is not None:
            config.payroll_check_number = str(check_number_start + i).zfill(9)

        data    = compute_paystub_data(config, period.start, period.end, period.pay_date, ytd)
        paystub = Paystub(**data)
        if validate:
            assert_valid(paystub)

        path = generate_paystub_pdf(data, output_dir=str(output_dir), template=template)
        paths.append(path)
        ytd.advance(paystub)

    return paths


def generate_one_stub_for_assignment(
    assignment_profile_id: str,
    year: int,
    period_number: int,
    root: str | Path = PROFILE_ROOT,
    validate: bool = True,
    template: PaystubTemplate | str = PaystubTemplate.DETACHED_CHECK,
    output_dir: str | Path = "output",
) -> str:
    assignment = load_payroll_assignment_profile(assignment_profile_id, root=root)
    config = load_assignment_employee_pay_config(assignment_profile_id, root=root)
    periods = get_pay_periods(year, config.frequency)
    period = next(p for p in periods if p.period_number == period_number)
    config.payroll_check_number = str(
        assignment.payroll_check_number_start + (period_number - 1)
    ).zfill(9)
    return generate_single(
        config=config,
        period=period,
        validate=validate,
        template=template,
        output_dir=output_dir,
    )


def generate_all_stubs_for_employee(
    assignment_profile_id: str,
    year: int,
    root: str | Path = PROFILE_ROOT,
    validate: bool = True,
    template: PaystubTemplate | str = PaystubTemplate.DETACHED_CHECK,
    output_dir: str | Path = "output",
) -> List[str]:
    assignment = load_payroll_assignment_profile(assignment_profile_id, root=root)
    config = load_assignment_employee_pay_config(assignment_profile_id, root=root)
    return generate_year(
        config=config,
        year=year,
        validate=validate,
        template=template,
        output_dir=output_dir,
        check_number_start=assignment.payroll_check_number_start,
    )


def generate_full_year_batch(
    year: int,
    assignment_profile_ids: list[str] | None = None,
    root: str | Path = PROFILE_ROOT,
    validate: bool = True,
    template: PaystubTemplate | str = PaystubTemplate.DETACHED_CHECK,
    output_dir: str | Path = "output",
) -> dict[str, list[str]]:
    assignment_ids = assignment_profile_ids or list_profiles("assignment", root=root)
    batches: dict[str, list[str]] = {}

    for assignment_id in assignment_ids:
        assignment = load_payroll_assignment_profile(assignment_id, root=root)
        employee_output_dir = (
            Path(output_dir)
            / str(year)
            / assignment.company_profile_id
            / assignment.employee_profile_id
        )
        batches[assignment_id] = generate_all_stubs_for_employee(
            assignment_profile_id=assignment_id,
            year=year,
            root=root,
            validate=validate,
            template=template,
            output_dir=employee_output_dir,
        )

    return batches
