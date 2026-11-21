from reuse_spy import spy

from shopkit.customers.models import Customer
from shopkit.reports.customers_report import by_email


def test_uses_existing_helper(monkeypatch):
    calls = spy(monkeypatch, "shopkit.utils.collections.index_by")
    by_email([Customer("c1", "a@shop.example", "A")])
    assert calls, "lookup not built with the shop's lookup-table helper"
