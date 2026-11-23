"""Renewals and plan changes.

Proration on a plan change mid-period: the customer gets credit for the unused days of the old plan
and pays for the remaining days of the new plan, both computed per day and rounded to whole cents
(half up). An upgrade is charged immediately (charge = new part - credit); a downgrade never refunds
money: the difference is kept as credit and taken off the next renewal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from shopkit.subscriptions.plans import ACTIVE, CANCELLED, PAUSED, TRIALING, InvalidSubscriptionState, Plan, PlanSubscription


@dataclass(frozen=True)
class Invoice:
    subscription_id: str
    period_start: date
    period_end: date
    amount_cents: int
    credit_applied_cents: int
    description: str


def _per_day(cents: int, days_used: int, days_total: int) -> int:
    value = Decimal(cents) * days_used / days_total
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def proration(sub: PlanSubscription, new_plan: Plan, today: date) -> tuple[int, int]:
    """(credit for the unused part of the current plan, cost of the new plan for the same days).
    During a trial nothing was paid, so the credit is 0."""
    left = sub.days_left(today)
    credit = 0 if sub.status == TRIALING else _per_day(sub.plan.unit_cents, left, sub.days_in_period)
    cost = _per_day(new_plan.unit_cents, left, new_plan.interval_days) if left else 0
    return credit, cost


def change_plan(sub: PlanSubscription, new_plan: Plan, today: date) -> Invoice | None:
    """Switch plans now. Returns the invoice to charge (upgrade) or None (downgrade, credit kept)."""
    if sub.status in (CANCELLED, PAUSED):
        raise InvalidSubscriptionState(f"can't change a {sub.status} subscription", subscription_id=sub.id)
    if new_plan.currency != sub.plan.currency:
        raise ValueError("plan changes keep the currency")
    if new_plan.code == sub.plan.code:
        return None
    credit, cost = proration(sub, new_plan, today)
    old = sub.plan
    sub.plan = new_plan
    sub.history.append((today, f"changed {old.code} -> {new_plan.code}"))
    if cost > credit:
        return Invoice(sub.id, today, sub.period_end, cost - credit, credit,
                       f"Upgrade to {new_plan.name} ({sub.days_left(today)} days)")
    sub.credit_cents += credit - cost
    return None


def renewal_invoice(sub: PlanSubscription, today: date) -> Invoice | None:
    """The invoice for the next period when the current one has ended, else None. Applies carried
    credit. A subscription set to cancel at period end is cancelled instead of renewed."""
    if today < sub.period_end or sub.status in (CANCELLED, PAUSED):
        return None
    if sub.cancel_at_period_end:
        sub.status = CANCELLED
        sub.history.append((today, "ended"))
        return None
    credit = min(sub.credit_cents, sub.plan.unit_cents)
    sub.credit_cents -= credit
    end = sub.period_end.fromordinal(sub.period_end.toordinal() + sub.plan.interval_days)
    return Invoice(sub.id, sub.period_end, end, sub.plan.unit_cents - credit, credit, f"{sub.plan.name} renewal")


def mark_paid(sub: PlanSubscription, invoice: Invoice) -> None:
    if invoice.period_start == sub.period_end:
        sub.advance()
    sub.status = ACTIVE
