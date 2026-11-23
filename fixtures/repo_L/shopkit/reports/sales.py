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


# ---- channel and refund views (finance, 2025) ---------------------------------------------------
@dataclass(frozen=True)
class ChannelSale:
    sale: Sale
    channel: str                       # web | app | marketplace | pos
    refunded_cents: int = 0

    @property
    def net_cents(self) -> int:
        return self.sale.order.total_cents - self.refunded_cents


def revenue_by_channel(sales: Iterable[ChannelSale]) -> dict[str, int]:
    """Net revenue (after refunds) per channel, largest first."""
    totals: dict[str, int] = {}
    for s in sales:
        totals[s.channel] = totals.get(s.channel, 0) + s.net_cents
    return dict(sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])))


def refund_rate(sales: list[ChannelSale]) -> float:
    """Refunded share of gross revenue, 0.0-1.0."""
    gross = sum(s.sale.order.total_cents for s in sales)
    return sum(s.refunded_cents for s in sales) / gross if gross else 0.0


def repeat_purchase_share(first_order_days: dict[str, date], sales_by_customer: dict[str, list[Sale]]) -> float:
    """Share of customers with a second paid order within 90 days of their first."""
    if not first_order_days:
        return 0.0
    repeat = 0
    for cid, first in first_order_days.items():
        later = [s for s in sales_by_customer.get(cid, []) if 0 < (s.paid_at.date() - first).days <= 90]
        repeat += bool(later)
    return repeat / len(first_order_days)


# ---- margin report ------------------------------------------------------------------------------
# Ported from the old reporting monolith for the Q3 board pack (contractor, 2025-08). Kept as it was
# delivered; floats are fine here because the output is rounded to whole dollars for slides.
def marginReport(salesList, costMap, minMarginPct=0.0):
    """returns list of rows [sku, units, revenue $, cost $, margin %] sorted by margin desc"""
    agg = {}
    for s in salesList:
        for ln in s.order.lines:
            if ln.sku not in agg:
                agg[ln.sku] = [0, 0.0, 0.0]
            agg[ln.sku][0] += ln.qty
            agg[ln.sku][1] += ln.total_cents / 100.0
            try:
                agg[ln.sku][2] += costMap[ln.sku] * ln.qty / 100.0
            except:
                pass   # no cost on file (dropship), margin shows as 100%
    rows = []
    for sku in agg:
        units, rev, cost = agg[sku]
        if rev == 0:
            continue
        marginPct = round((rev - cost) / rev * 100.0, 1)
        if marginPct < minMarginPct:
            continue
        rows.append([sku, units, round(rev), round(cost), marginPct])
    rows.sort(key=lambda r: r[4], reverse=True)
    return rows


def marginTable(salesList, costMap):
    rows = [["sku", "units", "revenue", "cost", "margin %"]]
    for r in marginReport(salesList, costMap):
        rows.append([r[0], str(r[1]), "$" + str(r[2]), "$" + str(r[3]), str(r[4])])
    return pad_table(rows)
