from shopkit.api.handlers import handle
from shopkit.inventory.stock import Stock


def _stock():
    s = Stock()
    s.receive("A-1", 2)
    return s


def test_reserve_lowers_available():
    s = _stock()
    status, _ = handle(s.reserve, "A-1", 2)
    assert status == 200
    assert s.available("A-1") == 0


def test_insufficient_stock_is_a_409_with_details():
    s = _stock()
    status, body = handle(s.reserve, "A-1", 5)
    assert status == 409
    assert body["requested"] == 5
    assert body["available"] == 2
    assert s.available("A-1") == 2
