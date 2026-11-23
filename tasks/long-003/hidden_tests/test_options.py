from shopkit.shipping.options import express_available
from shopkit.warehouse.sites import Site

AIR_AND_GROUND = Site("MAIN", "Main")
AIR_ONLY = Site("EAST", "East", services=frozenset({"air"}))


def test_ordinary_carts_can_go_express():
    assert express_available(["MUG", "TEA"], [AIR_AND_GROUND, AIR_ONLY], frozenset({"BATT"})) is True


def test_dangerous_goods_never_go_express():
    assert express_available(["MUG", "BATT"], [AIR_AND_GROUND, AIR_ONLY], frozenset({"BATT"})) is False
    assert express_available(["BATT"], [AIR_ONLY], frozenset({"BATT"})) is False


def test_no_site_no_express():
    assert express_available(["MUG"], [], frozenset()) is False
