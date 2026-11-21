from datetime import date

import pytest

from shopkit.catalog.models import Product
from shopkit.catalog.pricing import PriceBook, SalePrice, Tier, tier_price

TIERS = [Tier(10, 900), Tier(50, 800)]


def test_tier_price():
    assert tier_price(TIERS, 1, 1000) == 1000
    assert tier_price(TIERS, 10, 1000) == 900
    assert tier_price(list(reversed(TIERS)), 75, 1000) == 800
    with pytest.raises(ValueError):
        tier_price(TIERS, 0, 1000)


def test_price_book_takes_lowest_of_tier_and_sale():
    p = Product("MUG-1", "Mug", 1000)
    book = PriceBook({"MUG-1": TIERS}, [SalePrice("MUG-1", 850, date(2026, 11, 1), date(2026, 11, 30))])
    assert book.unit_price(p, 1, date(2026, 11, 15)) == 850
    assert book.unit_price(p, 60, date(2026, 11, 15)) == 800
    assert book.unit_price(p, 1, date(2026, 12, 1)) == 1000
