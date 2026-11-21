from shopkit.orders.models import Order, OrderLine
from shopkit.orders.returns import ReturnRequest


def _fee(unit_cents, reason="changed_mind"):
    order = Order("o1", [OrderLine("SKU", 1, unit_cents)])
    return ReturnRequest("o1", {"SKU": 1}, reason).restocking_fee_cents(order)


def test_whole_cents():
    assert _fee(2000) == 250


def test_half_cents_round_like_the_rest_of_the_shop():
    assert _fee(996) == 125        # 124.5
    assert _fee(1012) == 127       # 126.5


def test_only_changed_mind_pays():
    assert _fee(996, "damaged") == 0
