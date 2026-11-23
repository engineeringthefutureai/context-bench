from datetime import datetime, timedelta

import pytest

from shopkit.errors import Conflict
from shopkit.events import EventBus
from shopkit.fraud.rules import OrderFacts, evaluate
from shopkit.fraud.scoring import ACCEPT, REJECT, REVIEW, ReviewQueue, assess, rule_stats

AT = datetime(2026, 3, 10, 12)


def _facts(**kw):
    base = dict(order_id="O1", customer_id="C1", email="ann@example.com", total_cents=5_000, currency="USD",
                billing_country="US", shipping_country="US")
    base.update(kw)
    return OrderFacts(**base)


def test_clean_order_is_accepted_and_trusted_customers_get_negative_points():
    assert assess(_facts(), AT).decision == ACCEPT
    trusted = assess(_facts(previous_orders=9, account_age_days=400, shipping_country="CA"), AT)
    assert trusted.score == 0 and trusted.decision == ACCEPT


def test_risky_orders_review_or_reject():
    a = assess(_facts(email="x@mailinator.com", shipping_country="CA", card_country="GB"), AT)
    assert {s.code for s in a.signals} == {"disposable_email", "country_mismatch", "card_country"} and a.decision == REVIEW
    r = assess(_facts(recent_orders_same_card=6, previous_chargebacks=1, skus=["GC50", "GC100"]), AT)
    assert r.decision == REJECT


def test_chargeback_history_always_reviewed():
    a = assess(_facts(previous_chargebacks=1, previous_orders=10, account_age_days=500), AT, review_at=60)
    assert a.score == 40 and a.decision == REVIEW          # under review_at, but a chargeback forces review


def test_private_ip_ignored_bad_ip_flagged():
    assert evaluate(_facts(ip="10.0.0.5", ip_country="DE")) == []
    assert [s.code for s in evaluate(_facts(ip="999.1.1.1"))] == ["bad_ip"]


def test_review_queue_flow_and_sla():
    q = ReviewQueue(bus=EventBus())
    q.add(assess(_facts(order_id="O1", email="x@yopmail.com", shipping_country="CA"), AT))
    q.add(assess(_facts(order_id="O2", email="y@yopmail.com", shipping_country="CA"), AT - timedelta(hours=5)))
    assert q.under_review("O1") and not q.under_review("O3")
    q.claim("O1", "ann")
    with pytest.raises(Conflict):
        q.claim("O1", "bo")
    q.decide("O1", "ann", REJECT, AT)
    assert not q.under_review("O1") and q.expire(AT) == ["O2"]
    assert rule_stats(list(q.items.values()))["disposable_email"] == {"seen": 2, "rejected": 1}
