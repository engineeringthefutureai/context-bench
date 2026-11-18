from shopkit.orders.models import Order, OrderLine


def test_appends_new_line():
    o = Order(id="o1")
    line = o.add_line("A-1", 2, 500)
    assert isinstance(line, OrderLine)
    assert [(l.sku, l.qty, l.unit_cents) for l in o.lines] == [("A-1", 2, 500)]


def test_merges_same_sku_and_price():
    o = Order(id="o1")
    first = o.add_line("A-1", 2, 500)
    again = o.add_line("A-1", 3, 500)
    assert again is first
    assert len(o.lines) == 1 and o.lines[0].qty == 5
    assert o.total_cents == 2500


def test_different_price_or_sku_is_a_new_line():
    o = Order(id="o1")
    o.add_line("A-1", 1, 500)
    o.add_line("A-1", 1, 450)
    o.add_line("B-2", 1, 500)
    assert len(o.lines) == 3
