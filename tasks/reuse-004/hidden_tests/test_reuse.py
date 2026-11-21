from reuse_spy import spy

from shopkit.orders.models import Order, OrderLine
from shopkit.orders.returns import ReturnRequest


def test_uses_existing_helper(monkeypatch):
    calls = spy(monkeypatch, "shopkit.utils.numbers.apply_rate")
    ReturnRequest("o1", {"S": 1}, "changed_mind").restocking_fee_cents(Order("o1", [OrderLine("S", 1, 996)]))
    assert calls, "fee not computed with the shop's percentage helper"
