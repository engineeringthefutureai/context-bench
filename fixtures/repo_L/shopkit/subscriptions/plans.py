"""Plans and the subscription record. A subscription bills in advance for one period at a time;
a period is [start, end) in whole days."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from shopkit.errors import Conflict, NotFound

ACTIVE, PAST_DUE, CANCELLED, PAUSED, TRIALING = "active", "past_due", "cancelled", "paused", "trialing"


class PlanNotFound(NotFound):
    pass


class InvalidSubscriptionState(Conflict):
    pass


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    unit_cents: int
    interval_days: int
    currency: str = "USD"
    trial_days: int = 0
    perks: frozenset[str] = frozenset()       # "free_shipping", "member_prices", "priority_support"

    def __post_init__(self):
        if self.interval_days not in (7, 30, 90, 365):
            raise ValueError("plans bill weekly, monthly (30 days), quarterly (90) or yearly (365)")
        if self.unit_cents < 0:
            raise ValueError("price must be >= 0")

    @property
    def daily_cents(self) -> float:
        return self.unit_cents / self.interval_days


PLUS_MONTHLY = Plan("plus-monthly", "Shopkit Plus (monthly)", 999, 30, trial_days=14,
                    perks=frozenset({"free_shipping", "member_prices"}))
PLUS_YEARLY = Plan("plus-yearly", "Shopkit Plus (yearly)", 9900, 365, trial_days=14,
                   perks=frozenset({"free_shipping", "member_prices"}))
BUSINESS = Plan("business", "Business", 4900, 30,
                perks=frozenset({"free_shipping", "member_prices", "priority_support"}))
CATALOG = {p.code: p for p in (PLUS_MONTHLY, PLUS_YEARLY, BUSINESS)}


def get_plan(code: str) -> Plan:
    try:
        return CATALOG[code]
    except KeyError:
        raise PlanNotFound(f"no plan {code}", plan=code) from None


@dataclass
class PlanSubscription:
    id: str
    customer_id: str
    plan: Plan
    period_start: date
    period_end: date
    status: str = ACTIVE
    cancel_at_period_end: bool = False
    paused_until: date | None = None
    credit_cents: int = 0                       # carried to the next invoice (downgrades)
    history: list[tuple[date, str]] = field(default_factory=list)

    @classmethod
    def start(cls, sub_id: str, customer_id: str, plan: Plan, today: date, *, trial: bool = True) -> "PlanSubscription":
        days = plan.trial_days if trial and plan.trial_days else plan.interval_days
        sub = cls(sub_id, customer_id, plan, today, today + timedelta(days=days),
                  status=TRIALING if trial and plan.trial_days else ACTIVE)
        sub.history.append((today, f"started {plan.code}"))
        return sub

    @property
    def days_in_period(self) -> int:
        return (self.period_end - self.period_start).days

    def days_left(self, today: date) -> int:
        return max((self.period_end - today).days, 0)

    def has_perk(self, perk: str, today: date) -> bool:
        return self.status in (ACTIVE, TRIALING, PAST_DUE) and today < self.period_end and perk in self.plan.perks

    def advance(self) -> None:
        """Move to the next period (called after the renewal is paid)."""
        self.period_start = self.period_end
        self.period_end = self.period_start + timedelta(days=self.plan.interval_days)
        if self.status in (TRIALING, PAST_DUE):
            self.status = ACTIVE

    def cancel(self, today: date, *, immediately: bool = False) -> None:
        if self.status == CANCELLED:
            raise InvalidSubscriptionState("already cancelled", subscription_id=self.id)
        if immediately:
            self.status = CANCELLED
            self.period_end = today
        else:
            self.cancel_at_period_end = True
        self.history.append((today, "cancelled" + (" now" if immediately else " at period end")))

    def pause(self, today: date, until: date) -> None:
        if self.status not in (ACTIVE,):
            raise InvalidSubscriptionState(f"can't pause a {self.status} subscription", subscription_id=self.id)
        if (until - today).days > 90:
            raise ValueError("pauses are limited to 90 days")
        self.status = PAUSED
        self.paused_until = until
        self.history.append((today, f"paused until {until}"))

    def resume(self, today: date) -> None:
        if self.status != PAUSED:
            raise InvalidSubscriptionState("not paused", subscription_id=self.id)
        # the unused part of the period is kept: the period is extended by the time spent paused
        paused_since = next(d for d, what in reversed(self.history) if what.startswith("paused"))
        self.period_end += timedelta(days=(today - paused_since).days)
        self.status, self.paused_until = ACTIVE, None
        self.history.append((today, "resumed"))
