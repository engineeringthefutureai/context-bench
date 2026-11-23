from datetime import date, timedelta

import pytest

from shopkit.subscriptions import dunning
from shopkit.subscriptions.billing import change_plan, mark_paid, proration, renewal_invoice
from shopkit.subscriptions.plans import (ACTIVE, BUSINESS, CANCELLED, PAST_DUE, PAUSED, PLUS_MONTHLY, PLUS_YEARLY,
                                         TRIALING, InvalidSubscriptionState, Plan, PlanSubscription, get_plan,
                                         PlanNotFound)
from shopkit.subscriptions.revenue import deferred_balance, recognised_through, schedule

D = date(2026, 3, 1)


def test_start_with_trial_then_renew():
    sub = PlanSubscription.start("S1", "C1", PLUS_MONTHLY, D)
    assert sub.status == TRIALING and sub.period_end == D + timedelta(days=14)
    assert renewal_invoice(sub, D + timedelta(days=13)) is None
    inv = renewal_invoice(sub, sub.period_end)
    assert inv.amount_cents == 999 and inv.period_start == D + timedelta(days=14)
    mark_paid(sub, inv)
    assert sub.status == ACTIVE and sub.period_start == D + timedelta(days=14)


def test_upgrade_charges_prorated_difference():
    sub = PlanSubscription.start("S1", "C1", PLUS_MONTHLY, D, trial=False)        # 30-day period
    today = D + timedelta(days=10)                                                # 20 days left
    credit, cost = proration(sub, BUSINESS, today)
    assert (credit, cost) == (666, 3267)
    inv = change_plan(sub, BUSINESS, today)
    assert inv.amount_cents == 3267 - 666 and sub.plan is BUSINESS


def test_downgrade_keeps_credit_for_next_renewal():
    sub = PlanSubscription.start("S1", "C1", BUSINESS, D, trial=False)
    assert change_plan(sub, PLUS_MONTHLY, D + timedelta(days=15)) is None
    assert sub.credit_cents == 2450 - 500
    inv = renewal_invoice(sub, sub.period_end)
    assert inv.amount_cents == 0 and inv.credit_applied_cents == 999 and sub.credit_cents == 1950 - 999


def test_cancel_at_period_end_and_pause_resume():
    sub = PlanSubscription.start("S1", "C1", PLUS_MONTHLY, D, trial=False)
    sub.pause(D + timedelta(days=5), D + timedelta(days=20))
    assert sub.status == PAUSED and not sub.has_perk("free_shipping", D + timedelta(days=6))
    sub.resume(D + timedelta(days=12))
    assert sub.period_end == D + timedelta(days=37)                # +7 paused days
    sub.cancel(D + timedelta(days=20))
    assert renewal_invoice(sub, sub.period_end) is None and sub.status == CANCELLED
    with pytest.raises(InvalidSubscriptionState):
        sub.cancel(D + timedelta(days=40))


def test_dunning_retries_then_cancels_and_hard_declines_stop_early():
    sub = PlanSubscription.start("S1", "C1", PLUS_MONTHLY, D, trial=False)
    state = dunning.start(sub, sub.period_end, "insufficient_funds")
    assert sub.status == PAST_DUE and dunning.in_grace(sub, sub.period_end + timedelta(days=6))
    for i in range(3):
        assert dunning.record_attempt(sub, state, D, success=False, decline_code="insufficient_funds") == "retrying"
    assert dunning.record_attempt(sub, state, D, success=False, decline_code="insufficient_funds") == "cancelled"
    sub2 = PlanSubscription.start("S2", "C1", PLUS_MONTHLY, D, trial=False)
    s2 = dunning.start(sub2, D, "stolen_card")
    assert s2.done and s2.next_attempt() is None


def test_revenue_recognition_sums_exactly():
    sub = PlanSubscription.start("S1", "C1", PLUS_YEARLY, D, trial=False)
    inv = renewal_invoice(sub, sub.period_end)
    rows = schedule(inv)
    assert len(rows) == 365 and sum(r.amount_cents for r in rows) == 9900
    assert recognised_through(inv, inv.period_start) == 27
    assert deferred_balance([inv], inv.period_start) == 9900 - 27


def test_plan_validation_and_lookup():
    with pytest.raises(ValueError):
        Plan("x", "X", 100, 12)
    with pytest.raises(PlanNotFound):
        get_plan("gold")
