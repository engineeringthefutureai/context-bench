from reuse_spy import spy

from datetime import datetime, timezone

from shopkit.inventory.transfers import Transfer, transfers_by_week


def test_uses_existing_helper(monkeypatch):
    calls = spy(monkeypatch, "shopkit.utils.dates.iso_week_label")
    transfers_by_week([Transfer("S", 1, "a", "b", at=datetime(2026, 11, 18, tzinfo=timezone.utc))])
    assert calls, "weeks not labelled with the dashboards' week helper"
