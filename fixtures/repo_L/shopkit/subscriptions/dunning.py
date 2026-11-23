"""Failed renewal payments: retry on a schedule, keep perks during a grace period, then cancel.

Retry schedule (days after the first failure): 1, 3, 5, 7. The subscription is `past_due` while
retrying and keeps its perks until GRACE_DAYS after the period ended; after the last failed retry
it is cancelled and the customer is emailed (notifications.email, template "plan_cancelled").
Cards that fail with a hard decline (stolen, closed account) skip the remaining retries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from shopkit.subscriptions.plans import CANCELLED, PAST_DUE, PlanSubscription

RETRY_DAYS = (1, 3, 5, 7)
GRACE_DAYS = 7
HARD_DECLINES = {"stolen_card", "lost_card", "account_closed", "do_not_honor_permanent"}


@dataclass
class DunningState:
    subscription_id: str
    first_failure: date
    attempts: int = 0
    last_code: str = ""
    done: bool = False
    log: list[tuple[date, str]] = field(default_factory=list)

    def next_attempt(self) -> date | None:
        if self.done or self.attempts >= len(RETRY_DAYS):
            return None
        return self.first_failure + timedelta(days=RETRY_DAYS[self.attempts])


def start(sub: PlanSubscription, today: date, decline_code: str) -> DunningState:
    sub.status = PAST_DUE
    state = DunningState(sub.id, today, last_code=decline_code)
    state.log.append((today, f"renewal failed: {decline_code}"))
    if decline_code in HARD_DECLINES:
        state.done = True
    return state


def record_attempt(sub: PlanSubscription, state: DunningState, today: date, *, success: bool,
                   decline_code: str = "") -> str:
    """Record a retry; returns "recovered", "retrying" or "cancelled"."""
    state.attempts += 1
    if success:
        state.done = True
        state.log.append((today, "recovered"))
        return "recovered"
    state.last_code = decline_code
    state.log.append((today, f"retry {state.attempts} failed: {decline_code}"))
    if decline_code in HARD_DECLINES or state.attempts >= len(RETRY_DAYS):
        state.done = True
        sub.status = CANCELLED
        sub.history.append((today, "cancelled after failed payments"))
        return "cancelled"
    return "retrying"


def in_grace(sub: PlanSubscription, today: date) -> bool:
    """Perks continue while past due, for GRACE_DAYS after the period ended."""
    return sub.status == PAST_DUE and today < sub.period_end + timedelta(days=GRACE_DAYS)


def due_today(states: list[DunningState], today: date) -> list[DunningState]:
    return [s for s in states if (d := s.next_attempt()) is not None and d <= today]
