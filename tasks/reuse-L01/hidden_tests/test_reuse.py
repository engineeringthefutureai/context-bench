from reuse_spy import spy

from shopkit.pos.till import amount_due


def test_uses_existing_helper(monkeypatch):
    calls = spy(monkeypatch, "shopkit.core.pricing.cashRoundCents")
    amount_due(1003, "CAD", "cash")
    assert calls, "cash amount not rounded with the shop's existing cash rounding"
