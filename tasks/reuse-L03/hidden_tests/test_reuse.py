from reuse_spy import spy

from shopkit.support.linking import order_refs


def test_uses_existing_helper(monkeypatch):
    calls = spy(monkeypatch, "shopkit.core.order_engine.parseOrderRef")
    order_refs("see SO-2019-000123")
    assert calls, "references not parsed with the shop's existing order reference parser"
