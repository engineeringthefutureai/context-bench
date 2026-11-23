from datetime import datetime, timedelta

from shopkit.api.webhook_delivery import (DEAD, DELIVERED, HELD, MAX_ATTEMPTS, RETRYING, EndpointState, QueuedDelivery,
                                          deliver_pending, endpoint_health, shape_payload, signature_header)
from shopkit.events import EventBus

NOW = datetime(2026, 3, 10, 12, 0)


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, headers))
        r = self.responses.pop(0) if self.responses else (200, "ok")
        if isinstance(r, Exception):
            raise r
        return r


def _ep(**kw):
    return {"E1": EndpointState("E1", "https://partner.example/hook", "s3cret", {"order.*", "refund.settled"}, **kw)}


def _d(i, event="order.paid", order="O1", seq=0, **kw):
    return QueuedDelivery(i, "E1", event, {"order_id": order, "total_cents": 1200}, NOW - timedelta(minutes=5),
                          order_id=order, sequence=seq, **kw)


def _run(ds, eps, transport, **kw):
    return deliver_pending(ds, eps, transport, now=NOW, bus=EventBus(), clock=lambda: 1_770_000_000, **kw)


def test_delivers_with_signature_and_versioned_payload():
    t = Transport()
    d = _d(1)
    rep = _run([d], _ep(), t)
    assert rep.delivered == [1] and d.status == DELIVERED
    url, body, headers = t.calls[0]
    assert headers["Shopkit-Signature"] == signature_header(body, "s3cret", 1_770_000_000)
    assert '"type":"order.paid"' in body
    assert shape_payload(d, "2019-01") == {"order_id": "O1", "total_cents": 1200, "event": "order.paid"}


def test_orders_in_review_are_held_except_cancellations():
    t = Transport()
    paid, cancelled = _d(1, "order.paid", "O1", 0), _d(2, "order.cancelled", "O2", 0)
    rep = _run([paid, cancelled], _ep(), t, under_review=lambda oid: True)
    assert rep.held == [1] and paid.status == HELD and paid.attempts == 0 and rep.delivered == [2]
    rep2 = deliver_pending([paid], _ep(), t, now=NOW + timedelta(minutes=2), bus=EventBus(), under_review=lambda oid: False)
    assert rep2.delivered == [1]
    failing = _d(3, "order.paid", "O3")
    assert _run([failing], _ep(), t, under_review=lambda oid: 1 / 0).held == [3]     # lookup error: held


def test_retry_schedule_dead_letter_and_4xx():
    t = Transport((503, "busy"))
    d = _d(1)
    rep = _run([d], _ep(), t)
    assert rep.retrying == [1] and d.next_attempt_at == NOW + timedelta(minutes=1)
    d2 = _d(2, attempts=MAX_ATTEMPTS - 1)
    assert _run([d2], _ep(), Transport(TimeoutError("slow"))).dead == [2]
    d3 = _d(3)
    assert _run([d3], _ep(), Transport((400, "bad"))).dead == [3]
    d4 = _d(4)
    _run([d4], _ep(), Transport((429, '{"retry_after": 900}')))
    assert d4.next_attempt_at == NOW + timedelta(seconds=900)


def test_410_deactivates_endpoint_and_breaker_opens():
    eps = _ep()
    rep = _run([_d(1)], eps, Transport((410, "gone")))
    assert rep.deactivated == ["E1"] and not eps["E1"].active
    eps = _ep()
    ds = [_d(i, order=f"O{i}") for i in range(1, 8)]
    rep = _run(ds, eps, Transport(*[(500, "x")] * 10))
    assert eps["E1"].open_until == NOW + timedelta(minutes=10) and rep.skipped.get(6) == "circuit open"


def test_ordering_per_order_and_duplicates():
    t = Transport()
    first, second = _d(1, "order.paid", "O1", 0, next_attempt_at=NOW + timedelta(hours=1)), _d(2, "order.shipped", "O1", 1)
    rep = _run([first, second], _ep(), t)
    assert rep.skipped == {2: "waiting for sequence 0"} and t.calls == []
    done = _d(10, status=DELIVERED)
    dup = _d(11)
    rep = _run([done, dup], _ep(), Transport())
    assert dup.status == DELIVERED and "duplicate of 10" in dup.last_error and rep.stats["duplicates_suppressed"] == 1


def test_rate_limit_and_health():
    eps = _ep()
    eps["E1"].sent_times.extend([NOW - timedelta(seconds=10)] * 60)
    rep = _run([_d(1)], eps, Transport())
    assert rep.skipped == {1: "rate limited"}
    rows = endpoint_health(eps, [_d(1), _d(2, status=DELIVERED)], NOW)
    assert rows[0]["delivered"] == 1 and rows[0]["success_rate"] == 0.5
