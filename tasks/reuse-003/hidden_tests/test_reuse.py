from reuse_spy import spy

from datetime import datetime, timezone

from shopkit.payments.refunds import Refund, expected_arrival


def test_uses_existing_helper(monkeypatch):
    calls = spy(monkeypatch, "shopkit.utils.dates.add_business_days")
    expected_arrival(Refund("p", 1, "x", at=datetime(2026, 11, 20, tzinfo=timezone.utc)))
    assert calls, "arrival date not computed with the shop's business-day helper"
