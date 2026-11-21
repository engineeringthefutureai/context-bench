from datetime import date

import pytest

from shopkit.promotions.coupons import FIXED, PERCENT, Coupon, CouponRejected, coupon_is_active, discount_cents

NOV = (date(2026, 11, 1), date(2026, 11, 30))


def test_active_window_is_inclusive_and_respects_max_uses():
    c = Coupon("SAVE10", PERCENT, 10, *NOV, max_uses=1)
    assert coupon_is_active(c, date(2026, 11, 30))
    assert not coupon_is_active(c, date(2026, 12, 1))
    c.uses = 1
    assert not coupon_is_active(c, date(2026, 11, 15))


def test_discount():
    assert discount_cents(Coupon("P", PERCENT, 10, *NOV), 1995, date(2026, 11, 2)) == 200
    assert discount_cents(Coupon("F", FIXED, 5000, *NOV), 1200, date(2026, 11, 2)) == 1200


def test_min_order_rejected():
    with pytest.raises(CouponRejected):
        discount_cents(Coupon("M", FIXED, 500, *NOV, min_order_cents=3000), 2000, date(2026, 11, 2))
