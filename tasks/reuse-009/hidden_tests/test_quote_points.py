from datetime import date

from shopkit.catalog.models import Product
from shopkit.catalog.pricing import PriceBook
from shopkit.checkout import quote
from shopkit.customers.models import Address, Customer

SHIP_TO = Address("Ann", "1 Main St", "Austin", "78701", "US", region="TX")
PRODUCTS = {"MUG": Product("MUG", "Mug", 1299)}


def _quote(customer=None):
    return quote({"MUG": 2}, PRODUCTS, SHIP_TO, 800, PriceBook(), date(2026, 11, 20), customer=customer)


def test_no_customer_no_points():
    assert _quote().points == 0


def test_points_follow_the_customer_tier():
    assert _quote(Customer("c1", "a@shop.example", "Ann")).points == 25
    assert _quote(Customer("c2", "b@shop.example", "Bob", tier="gold")).points == 75


def test_totals_unchanged():
    assert _quote(Customer("c1", "a@shop.example", "Ann")).total_cents == _quote().total_cents
