from datetime import date, datetime

from shopkit.catalog.models import Product
from shopkit.customers.models import Address
from shopkit.pricing.cart import CartItem
from shopkit.pricing.coupon_preview import coupon_savings
from shopkit.pricing.engine import PricingEngine
from shopkit.pricing.schedules import ScheduledPrice
from shopkit.promotions.coupons import FIXED, PERCENT, Coupon

AT = datetime(2026, 3, 10, 12)
TO = Address("Bo", "2 Elm", "Portland", "97201", "US", region="OR")
PRODUCTS = {"MUG": Product("MUG", "Mug", 2000), "TEA": Product("TEA", "Tea", 1000)}
TEN = Coupon("TEN", PERCENT, 10, date(2026, 1, 1), date(2026, 12, 31), min_order_cents=3_000)
FIVE = Coupon("FIVE", FIXED, 500, date(2026, 1, 1), date(2026, 12, 31))


def _engine():
    e = PricingEngine()
    e.schedule.add(ScheduledPrice("TEA", "USD", 800, datetime(2026, 3, 1), datetime(2026, 3, 31), "Spring"))
    return e


def _save(items, code):
    return coupon_savings(items, PRODUCTS, code, {"TEN": TEN, "FIVE": FIVE}, _engine(), TO, 400, AT)


def test_percentage_codes_skip_sale_items_but_sale_items_count_for_the_minimum():
    assert _save([CartItem("MUG", 1), CartItem("TEA", 2)], "ten") == 200
    assert _save([CartItem("MUG", 1)], "TEN") == 0                      # 20.00 < 30.00 minimum


def test_fixed_and_unknown_codes():
    assert _save([CartItem("TEA", 1)], "FIVE") == 500
    assert _save([CartItem("MUG", 1)], "NOPE") == 0
