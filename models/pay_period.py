"""
Pay-period date engine.

Supports weekly, biweekly, semi-monthly, and monthly frequencies.
Automatically adjusts pay dates that fall on weekends or US federal holidays.
"""
import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from functools import lru_cache
from typing import Iterable, List


class PayFrequency(str, Enum):
    WEEKLY      = "weekly"       # 52 periods/year
    BIWEEKLY    = "biweekly"     # 26 periods/year
    SEMIMONTHLY = "semimonthly"  # 24 periods/year
    MONTHLY     = "monthly"      # 12 periods/year


class BusinessDayAdjustment(str, Enum):
    FOLLOWING = "following"
    PRECEDING = "preceding"


PERIODS_PER_YEAR: dict[PayFrequency, int] = {
    PayFrequency.WEEKLY:      52,
    PayFrequency.BIWEEKLY:    26,
    PayFrequency.SEMIMONTHLY: 24,
    PayFrequency.MONTHLY:     12,
}


def _observed_holiday(actual: date) -> date:
    if actual.weekday() == 5:
        return actual - timedelta(days=1)
    if actual.weekday() == 6:
        return actual + timedelta(days=1)
    return actual


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first_day = date(year, month, 1)
    offset = (weekday - first_day.weekday()) % 7
    return first_day + timedelta(days=offset + (occurrence - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


@lru_cache(maxsize=None)
def us_federal_holidays(year: int) -> frozenset[date]:
    """Observed US federal holidays for *year*."""
    holidays = {
        _observed_holiday(date(year, 1, 1)),          # New Year's Day
        _nth_weekday(year, 1, calendar.MONDAY, 3),    # Martin Luther King Jr. Day
        _nth_weekday(year, 2, calendar.MONDAY, 3),    # Presidents Day
        _last_weekday(year, 5, calendar.MONDAY),      # Memorial Day
        _observed_holiday(date(year, 6, 19)),         # Juneteenth
        _observed_holiday(date(year, 7, 4)),          # Independence Day
        _nth_weekday(year, 9, calendar.MONDAY, 1),    # Labor Day
        _nth_weekday(year, 10, calendar.MONDAY, 2),   # Columbus Day
        _observed_holiday(date(year, 11, 11)),        # Veterans Day
        _nth_weekday(year, 11, calendar.THURSDAY, 4), # Thanksgiving
        _observed_holiday(date(year, 12, 25)),        # Christmas Day
    }
    return frozenset(holidays)


def holiday_calendar(years: Iterable[int]) -> set[date]:
    holidays: set[date] = set()
    for year in years:
        holidays.update(us_federal_holidays(year))
    return holidays


def adjust_business_day(
    d: date,
    holidays: set[date] | None = None,
    adjustment: BusinessDayAdjustment = BusinessDayAdjustment.FOLLOWING,
) -> date:
    """Move *d* to a business day using the requested adjustment rule."""
    if holidays is None:
        holidays = holiday_calendar({d.year - 1, d.year, d.year + 1})

    step = timedelta(days=1 if adjustment == BusinessDayAdjustment.FOLLOWING else -1)
    while d.weekday() >= 5 or d in holidays:
        d += step
    return d


def next_business_day(d: date, holidays: set[date] | None = None) -> date:
    """Backward-compatible wrapper for following business-day adjustment."""
    return adjust_business_day(d, holidays, BusinessDayAdjustment.FOLLOWING)


@dataclass(frozen=True)
class PayPeriod:
    start:         date
    end:           date
    pay_date:      date
    period_number: int   # 1-based index within the calendar year


def get_pay_periods(
    year:         int,
    frequency:    PayFrequency,
    anchor_start: date | None = None,
    pay_lag_days: int | None = None,
    adjustment: BusinessDayAdjustment = BusinessDayAdjustment.FOLLOWING,
    holidays: set[date] | None = None,
) -> List[PayPeriod]:
    """
    Return the standard set of payroll periods for ``year``.

    anchor_start: the first day of the first pay period of the year.
                  Defaults to January 1 of that year.
    """
    if anchor_start is None:
        anchor_start = date(year, 1, 1)
    if holidays is None:
        holidays = holiday_calendar({year - 1, year, year + 1})

    if frequency == PayFrequency.SEMIMONTHLY:
        return _semimonthly(year, holidays, adjustment)
    if frequency == PayFrequency.MONTHLY:
        return _monthly(year, holidays)

    # Weekly / Biweekly
    period_days = 7 if frequency == PayFrequency.WEEKLY else 14
    pay_lag = timedelta(days=5 if pay_lag_days is None else pay_lag_days)

    periods: List[PayPeriod] = []
    start = anchor_start
    num = 1
    target_count = PERIODS_PER_YEAR[frequency]
    while len(periods) < target_count:
        end      = start + timedelta(days=period_days - 1)
        pay_date = adjust_business_day(end + pay_lag, holidays, adjustment)
        periods.append(PayPeriod(start, end, pay_date, num))
        num += 1
        start += timedelta(days=period_days)

    return periods


def generate_pay_schedule(
    year: int,
    frequency: PayFrequency,
    anchor_start: date | None = None,
    pay_lag_days: int | None = None,
    adjustment: BusinessDayAdjustment = BusinessDayAdjustment.FOLLOWING,
    holidays: set[date] | None = None,
) -> List[PayPeriod]:
    """Explicit alias for callers that want a schedule-focused API."""
    return get_pay_periods(
        year=year,
        frequency=frequency,
        anchor_start=anchor_start,
        pay_lag_days=pay_lag_days,
        adjustment=adjustment,
        holidays=holidays,
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _semimonthly(
    year: int,
    holidays: set[date],
    adjustment: BusinessDayAdjustment,
) -> List[PayPeriod]:
    """Two periods per month: 1st–15th (pay 20th) and 16th–EOM (pay 5th next month)."""
    periods: List[PayPeriod] = []
    num = 1
    for month in range(1, 13):
        # First half
        s1 = date(year, month, 1)
        e1 = date(year, month, 15)
        p1 = adjust_business_day(date(year, month, 20), holidays, adjustment)
        periods.append(PayPeriod(s1, e1, p1, num)); num += 1

        # Second half
        last_day = calendar.monthrange(year, month)[1]
        s2 = date(year, month, 16)
        e2 = date(year, month, last_day)
        next_mo, next_yr = (month + 1, year) if month < 12 else (1, year + 1)
        p2 = adjust_business_day(date(next_yr, next_mo, 5), holidays, adjustment)
        periods.append(PayPeriod(s2, e2, p2, num)); num += 1

    return periods


def _monthly(year: int, holidays: set[date]) -> List[PayPeriod]:
    """One period per month; pay on last business day of month."""
    periods: List[PayPeriod] = []
    for month in range(1, 13):
        s    = date(year, month, 1)
        last = calendar.monthrange(year, month)[1]
        e    = date(year, month, last)
        p    = adjust_business_day(e, holidays, BusinessDayAdjustment.PRECEDING)
        periods.append(PayPeriod(s, e, p, month))
    return periods
