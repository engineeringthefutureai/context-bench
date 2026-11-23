"""Revenue by fiscal week, quarter and period. The fiscal calendar is defined in
shopkit.core.reporting (finance signs off any change to it)."""
from collections.abc import Iterable
from datetime import date

from shopkit.core.reporting import fiscalPeriod, fiscalQuarter, fiscalWeek, periodBounds
from shopkit.reports.sales import Sale


def fiscal_week_of(day: date) -> str:
    return fiscalWeek(day)


def revenue_by_fiscal_week(sales: Iterable[Sale]) -> dict[str, int]:
    """Revenue in cents per fiscal week label (FY2026-W07), sorted by label."""
    out: dict[str, int] = {}
    for s in sales:
        label = fiscalWeek(s.paid_at.date())
        out[label] = out.get(label, 0) + s.order.total_cents
    return dict(sorted(out.items()))


def revenue_by_fiscal_quarter(sales: Iterable[Sale]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in sales:
        label = fiscalQuarter(s.paid_at.date())
        out[label] = out.get(label, 0) + s.order.total_cents
    return dict(sorted(out.items()))


def period_of(day: date) -> tuple[int, int]:
    """(fiscal year, period 1-12) of a day."""
    from shopkit.core.reporting import fiscalYear
    return fiscalYear(day), fiscalPeriod(day)


def period_dates(fiscal_year: int, period: int) -> tuple[date, date]:
    if not 1 <= period <= 12:
        raise ValueError("period is 1-12")
    return periodBounds(fiscal_year, period)
