"""Sales reporting for the weekly business review."""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from shopkit.orders.models import Order
from shopkit.reports.formatting import fmt_amount, fmt_count, pad_table
from shopkit.utils.collections import group_by
from shopkit.utils.dates import iso_week_label


@dataclass(frozen=True)
class Sale:
    order: Order
    paid_at: datetime


def bucket_by_day(sales: Iterable[Sale]) -> dict[date, int]:
    """Revenue in cents per calendar day of payment (paid_at's own date, no timezone conversion),
    sorted by day. Days without sales are absent, not zero."""
    totals: dict[date, int] = {}
    for sale in sales:
        day = sale.paid_at.date()
        totals[day] = totals.get(day, 0) + sale.order.total_cents
    return dict(sorted(totals.items()))


def best_day(sales: list[Sale]) -> tuple[date, int] | None:
    """The day with the highest revenue (earliest wins a tie); None without sales."""
    by_day = bucket_by_day(sales)
    return max(by_day.items(), key=lambda kv: (kv[1], -kv[0].toordinal())) if by_day else None


def weekly_revenue(sales: list[Sale]) -> dict[str, int]:
    weeks = group_by(sales, key=lambda s: iso_week_label(s.paid_at.date()))
    return {w: sum(s.order.total_cents for s in ss) for w, ss in sorted(weeks.items())}


def top_skus(sales: list[Sale], n: int = 10) -> list[tuple[str, int]]:
    units: dict[str, int] = {}
    for sale in sales:
        for line in sale.order.lines:
            units[line.sku] = units.get(line.sku, 0) + line.qty
    return sorted(units.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def average_order_cents(sales: list[Sale]) -> int:
    return sum(s.order.total_cents for s in sales) // len(sales) if sales else 0


def weekly_report(sales: list[Sale]) -> str:
    rows = [["week", "orders", "revenue"]]
    by_week = group_by(sales, key=lambda s: iso_week_label(s.paid_at.date()))
    for week, ss in sorted(by_week.items()):
        rows.append([week, fmt_count(len(ss)), fmt_amount(sum(s.order.total_cents for s in ss))])
    return pad_table(rows)
