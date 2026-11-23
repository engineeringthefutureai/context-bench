"""Coupon codes: percentage or fixed-amount discounts with validity windows and usage limits."""
from dataclasses import dataclass
from datetime import date

from shopkit.errors import ShopError
from shopkit.utils.numbers import apply_rate

PERCENT, FIXED = "percent", "fixed"


class CouponRejected(ShopError):
    http_status = 422


@dataclass
class Coupon:
    code: str
    kind: str                         # PERCENT or FIXED
    value: int                        # percent (0-100) or cents
    starts: date
    ends: date                        # inclusive
    min_order_cents: int = 0
    max_uses: int | None = None
    uses: int = 0


def coupon_is_active(coupon: Coupon, today: date) -> bool:
    """Within its date window (inclusive) and not used up."""
    within = coupon.starts <= today <= coupon.ends
    return within and (coupon.max_uses is None or coupon.uses < coupon.max_uses)


def discount_cents(coupon: Coupon, order_total_cents: int, today: date) -> int:
    """Discount the coupon gives on an order; never more than the order total.
    Raises CouponRejected with a reason the checkout page can show."""
    if not coupon_is_active(coupon, today):
        raise CouponRejected(f"coupon {coupon.code} is not active", code=coupon.code)
    if order_total_cents < coupon.min_order_cents:
        raise CouponRejected(f"coupon {coupon.code} needs a larger order", code=coupon.code,
                             min_order_cents=coupon.min_order_cents)
    amount = apply_rate(order_total_cents, coupon.value) if coupon.kind == PERCENT else coupon.value
    return min(amount, order_total_cents)


def normalize_code(code: str) -> str:
    return code.strip().upper().replace(" ", "")
