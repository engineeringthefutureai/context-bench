from reuse_spy import spy

from datetime import date

from shopkit.catalog.models import Product
from shopkit.catalog.pricing import PriceBook
from shopkit.checkout import quote
from shopkit.customers.models import Address, Customer


def test_uses_existing_helper(monkeypatch):
    calls = spy(monkeypatch, "shopkit.customers.loyalty.points_for")
    quote({"M": 1}, {"M": Product("M", "Mug", 1299)}, Address("A", "1", "X", "1", "US"), 100, PriceBook(),
          date(2026, 11, 20), customer=Customer("c", "a@b.example", "A"))
    assert calls, "points not computed with the shop's loyalty helper"
