import pytest

from shopkit.domain.ids import CustomerId, OrderId
from shopkit.domain.money import CurrencyMismatch, Money, total
from shopkit.domain.orders import Order
from shopkit.domain.text import normalize_code, title_slug
from shopkit.orders.models import Order as V1Order, OrderLine
from shopkit.utils.helpers import business_days_after, clean_sku, fmt_price, price_from_text


def test_money_arithmetic_and_format():
    a = Money.of("12.345")
    assert a == Money(1234) and (a + Money(66)).format() == "13.00 USD" and Money(500, "JPY").format() == "500 JPY"
    assert [m.minor for m in Money(100).allocate([1, 1, 1])] == [34, 33, 33]
    assert Money(1000).percent(15) == Money(150) and total([Money(1), Money(2)]) == Money(3)
    with pytest.raises(CurrencyMismatch):
        Money(1) + Money(1, "EUR")
    with pytest.raises(TypeError):
        Money(1) * 1.5


def test_typed_ids():
    assert str(OrderId("ord_0123456789ab")) == "ord_0123456789ab" and str(OrderId.new()).startswith("ord_")
    with pytest.raises(ValueError):
        CustomerId("ord_0123456789ab")


def test_order_from_plain_order():
    o = Order.from_v1(V1Order("ord_0123456789ab", [OrderLine("A", 2, 1999)]))
    assert o.subtotal == Money(3998) and o.grand_total.format() == "39.98 USD"


def test_text_and_helpers():
    assert normalize_code("mug-1") == "MUG1" and title_slug("Blue Mug, Large!") == "blue-mug-large"
    assert fmt_price(123456) == "$1,234.56" and price_from_text("$12.50") == 1250 and clean_sku("mug1") == "MUG-1"
    from datetime import date
    assert business_days_after(date(2026, 3, 6), 1) == date(2026, 3, 9)
