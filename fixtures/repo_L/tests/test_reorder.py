import math

from shopkit.inventory.reorder import days_of_cover, suggest
from shopkit.inventory.stock import Stock


def test_days_of_cover():
    assert days_of_cover(30, 60, 30) == 15.0
    assert math.isinf(days_of_cover(10, 0, 30))
    assert days_of_cover(0, 10, 30) == 0.0


def test_suggest_orders_low_cover_first():
    stock = Stock()
    stock.receive("A", 10)
    stock.receive("B", 100)
    stock.receive("C", 2)
    result = suggest(stock, {"A": 30, "B": 30, "C": 0})
    assert [s.sku for s in result] == ["A", "C"]
    assert result[0].order_qty == 50
