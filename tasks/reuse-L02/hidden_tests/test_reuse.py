from reuse_spy import spy

from shopkit.warehouse.labels import pallet_label


def test_uses_existing_helper(monkeypatch):
    calls = spy(monkeypatch, "shopkit.core.erp.toErpSku")
    pallet_label("P-1", [("MUG-1", 1)])
    assert calls, "item numbers not produced with the shop's existing finance item mapping"
