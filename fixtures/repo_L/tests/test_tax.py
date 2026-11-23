from shopkit.customers.models import Address
from shopkit.orders.models import Order, OrderLine
from shopkit.tax.calculator import gross_total, order_tax, tax_for
from shopkit.tax.rates import REDUCED, rate_for


def test_rates():
    assert rate_for("DE") == 19.0
    assert rate_for("DE", tax_class=REDUCED) == 7.0
    assert rate_for("US", "CA") == 7.25
    assert rate_for("ZZ") == 0.0


def test_tax_rounds_per_line():
    order = Order("o1", [OrderLine("A", 1, 333), OrderLine("B", 1, 333)])
    ship_to = Address("x", "y", "Berlin", "10115", "DE")
    assert tax_for(333, "DE") == 63
    assert order_tax(order, ship_to) == 126
    assert gross_total(order, ship_to) == 792
