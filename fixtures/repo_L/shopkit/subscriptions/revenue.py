"""Daily revenue recognition for prepaid periods: an invoice paid for [start, end) is recognised
evenly per day, with the rounding remainder on the last day so the total matches exactly."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from shopkit.subscriptions.billing import Invoice


@dataclass(frozen=True)
class Recognition:
    ref: str
    day: date
    amount_cents: int


def schedule(invoice: Invoice) -> list[Recognition]:
    days = (invoice.period_end - invoice.period_start).days
    if days <= 0 or invoice.amount_cents == 0:
        return []
    per_day, remainder = divmod(invoice.amount_cents, days)
    out = []
    for i in range(days):
        amount = per_day + (remainder if i == days - 1 else 0)
        out.append(Recognition(f"{invoice.subscription_id}:{invoice.period_start}:{i}",
                               invoice.period_start + timedelta(days=i), amount))
    return out


def recognised_through(invoice: Invoice, day: date) -> int:
    """Revenue recognised from an invoice up to and including `day`."""
    return sum(r.amount_cents for r in schedule(invoice) if r.day <= day)


def deferred_balance(invoices: list[Invoice], day: date) -> int:
    """Paid but not yet recognised at the end of `day` (the deferred revenue liability)."""
    return sum(i.amount_cents - recognised_through(i, day) for i in invoices)
