from datetime import date, datetime

from shopkit.catalog.models import Product
from shopkit.core.pricing import makeCouponCode
from shopkit.customers.models import Address
from shopkit.pricing.cart import CartItem, price_cart
from shopkit.pricing.engine import PricingEngine
from shopkit.pricing.price_lists import PriceList, PriceListSet
from shopkit.pricing.schedules import ScheduledPrice
from shopkit.promotions.coupons import FIXED, PERCENT, Coupon
from shopkit.promotions.rules import BuyXGetY

AT = datetime(2026, 3, 10, 12)
NY = Address("Ann", "1 Main", "New York", "10001", "US", region="NY")
OR = Address("Bo", "2 Elm", "Portland", "97201", "US", region="OR")
PRODUCTS = {"MUG": Product("MUG", "Mug", 2000), "TEA": Product("TEA", "Tea", 1000), "GC50": Product("GC50", "Gift card", 5000)}


def _engine():
    e = PricingEngine()
    e.schedule.add(ScheduledPrice("TEA", "USD", 800, datetime(2026, 3, 1), datetime(2026, 3, 31), "Spring"))
    return e


def _price(items, **kw):
    kw.setdefault("engine", _engine())
    kw.setdefault("ship_to", OR)
    return price_cart(items, PRODUCTS, weight_g=400, at=AT, **kw)


def test_percent_coupon_skips_sale_items_but_they_count_for_the_minimum():
    coupon = Coupon("TEN", PERCENT, 10, date(2026, 1, 1), date(2026, 12, 31), min_order_cents=3_000)
    cart = _price([CartItem("MUG", 1), CartItem("TEA", 2)], coupon_code="ten", coupons={"TEN": coupon})
    assert cart.coupon_discount_cents == 200                       # 10% of the mug only
    assert "already on sale" in " ".join(cart.messages)
    small = _price([CartItem("MUG", 1)], coupon_code="TEN", coupons={"TEN": coupon})
    assert small.coupon_discount_cents == 0 and "larger order" in " ".join(small.messages)


def test_fixed_coupon_and_gift_cards():
    coupon = Coupon("FIVE", FIXED, 500, date(2026, 1, 1), date(2026, 12, 31))
    cart = _price([CartItem("TEA", 1), CartItem("GC50", 1, gift_card=True)], coupon_code="FIVE", coupons={"FIVE": coupon})
    assert cart.coupon_discount_cents == 500 and cart.lines[1].discount_cents == 0 and cart.lines[1].tax_cents == 0
    only_gc = _price([CartItem("GC50", 1, gift_card=True)])
    assert only_gc.shipping_cents == 0 and only_gc.total_cents == 5000


def test_printed_coupon_member_price_and_tax():
    e = _engine()
    member = PriceList("member-usd", "USD", customer_tier="member", priority=5)
    member.add("MUG", 1700)
    e.price_lists = PriceListSet([member])
    code = makeCouponCode(20, 2026)
    cart = _price([CartItem("MUG", 1)], engine=e, member=True, coupon_code=code, ship_to=NY)
    assert cart.lines[0].unit_cents == 1700 and cart.coupon_discount_cents == 340
    assert cart.tax_cents == 54                                     # 4% of 13.60


def test_promotions_merging_and_messages():
    cart = _price([CartItem("MUG", 1, added_unit_cents=1500), CartItem("MUG", 2), CartItem("NOPE", 1)],
                  promotions=[BuyXGetY("MUG", 2, 1)], metrics=(m := {}))
    assert cart.lines[0].qty == 3 and cart.promo_discount_cents == 2000
    assert any("changed" in x for x in cart.messages) and any("NOPE" in x for x in cart.messages)
    assert m["cart.merged_lines"] == 1
    assert "isn't valid" in " ".join(_price([CartItem("MUG", 1)], coupon_code="BOGUS").messages)
