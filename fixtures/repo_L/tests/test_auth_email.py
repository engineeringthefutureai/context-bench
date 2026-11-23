from datetime import datetime, timedelta

import pytest

from shopkit.api.auth import (ApiKey, Forbidden, ROTATION_GRACE_S, ScopedKey, ScopedKeyRing, SessionStore,
                              Unauthorized, hash_secret, require_scope)
from shopkit.notifications.email import HOURLY_LIMIT_PER_RECIPIENT, Outbox, Suppressed, SuppressionList, _html_to_text


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def test_scopes_limit_a_key():
    scoped = ScopedKey(ApiKey("p1", hash_secret("s"), "admin"), frozenset({"orders:read"}))
    require_scope(scoped, "orders:read")
    with pytest.raises(Forbidden):
        require_scope(scoped, "refunds:write")
    require_scope(ScopedKey(ApiKey("p2", hash_secret("s"), "admin")), "refunds:write")


def test_rotation_keeps_old_secret_for_the_grace_period():
    clock = Clock()
    ring = ScopedKeyRing([ScopedKey(ApiKey("p1", hash_secret("old"), "staff"))], clock=clock)
    ring.rotate("p1", "new")
    assert ring.authenticate("Key p1:new") and ring.authenticate("Key p1:old")
    clock.t += ROTATION_GRACE_S + 1
    with pytest.raises(Unauthorized):
        ring.authenticate("Key p1:old")


def test_sessions_expire():
    clock = Clock()
    store = SessionStore(clock=clock)
    s = store.create("ann", "staff")
    assert store.get(s.token).user == "ann"
    clock.t += 9 * 3600
    with pytest.raises(Unauthorized):
        store.get(s.token)


def test_suppression_and_hourly_limit():
    sup = SuppressionList()
    sup.add("bounce@example.com", "bounce")
    sup.add("unsub@example.com", "unsubscribe")
    box = Outbox(sup)
    with pytest.raises(Suppressed):
        box.send_html("bounce@example.com", "Hi", "refund", {"amount_cents": 100, "currency": "USD",
                                                             "order_id": "O1", "reason": "damaged", "customer": None})
    email = box.send_html("unsub@example.com", "Refund", "refund",
                          {"amount_cents": 1250, "currency": "USD", "order_id": "O1", "reason": "damaged", "customer": None})
    assert "$12.50" in email.html and "$12.50" in email.body and "<p>" not in email.body
    with pytest.raises(Suppressed):
        box.send_html("unsub@example.com", "Sale", "refund", {}, category="marketing")
    now = datetime(2026, 3, 1, 12)
    for i in range(HOURLY_LIMIT_PER_RECIPIENT - 1):
        box.send_html("ok@example.com", "x", "refund", {}, now=now)
    box.send_html("ok@example.com", "x", "refund", {}, now=now)
    with pytest.raises(Suppressed):
        box.send_html("ok@example.com", "x", "refund", {}, now=now + timedelta(minutes=5))
    box.send_html("ok@example.com", "x", "refund", {}, now=now + timedelta(hours=2))


def test_html_to_text():
    assert _html_to_text("<p>Hi <b>Ann</b></p>\n\n\n<p>Bye</p>") == "Hi Ann\n\nBye"
