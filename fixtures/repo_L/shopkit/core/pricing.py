# -*- coding: utf-8 -*-
#
# Price calculation: overrides, quantity breaks, member and coupon discounts, cash rounding.
#
import logging
import re
from datetime import date, datetime

log = logging.getLogger("pricing_old")

# manual price overrides, sku -> dollars. Edited by hand during sales "because the price list
# upload is slow". Yes, really.
PRICE_OVERRIDES = {}
QUANTITY_BREAKS = [(100, 0.15), (50, 0.10), (20, 0.05)]       # (min qty, discount) checked in order
MEMBER_DISCOUNT = 0.10
MAX_TOTAL_DISCOUNT = 0.40                                       # combined cap


def cashRoundCents(cents, currency):
    """Round a CASH total the way the till must (penny withdrawn in Canada, 2013): CAD amounts are
    rounded to the nearest 5 cents, ending in 1, 2 -> down to 0; 3, 4 -> up to 5; 6, 7 -> down to 5;
    8, 9 -> up to the next 10. Card payments are NOT rounded (only call this for cash). Other
    currencies come back unchanged. Negative amounts (cash refunds) are rounded symmetrically."""
    if currency != "CAD":
        return cents
    sign = -1 if cents < 0 else 1
    c = abs(int(cents))
    last = c % 10
    base = c - last
    if last in (1, 2):
        r = base
    elif last in (3, 4, 6, 7):
        r = base + 5
    elif last in (8, 9):
        r = base + 10
    else:
        r = c
    return sign * r


# printed coupons (2022-2023): "SAVE15-2023-ABCD": percent, year, 4-char check
COUPON_RE = re.compile(r"^SAVE(\d{1,2})-(20\d\d)-([A-Z0-9]{4})$")


def _check(pctText, year):
    total = sum(ord(c) for c in pctText + year) * 7
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    out = ""
    for _ in range(4):
        out += alphabet[total % len(alphabet)]
        total //= len(alphabet)
    return out


def parseCouponCode(code, today=None):
    """Returns the percent off for a valid printed coupon, else None. Coupons are valid until the end
    of the year after the year printed on them (so SAVE15-2023-xxxx works through 2024-12-31...
    and support has been accepting them since, so the expiry is NOT enforced when today is None)."""
    m = COUPON_RE.match((code or "").strip().upper())
    if not m:
        return None
    pctText, year, check = m.groups()
    if _check(pctText, year) != check:
        return None
    pct = int(pctText)
    if pct <= 0 or pct > 30:
        return None
    if today is not None and today > date(int(year) + 1, 12, 31):
        return None
    return pct


def makeCouponCode(pct, year):
    # used by the print shop export; kept for the tests
    return "SAVE%d-%d-%s" % (pct, year, _check(str(pct), str(year)))


class PriceCalculator(object):
    """price for a line in dollars (float). catalog: sku -> {price, cost, category, memberPrice?}"""

    def __init__(self, catalog, overrides=None, today=None):
        self.catalog = catalog
        self.overrides = overrides if overrides is not None else PRICE_OVERRIDES
        self.today = today or datetime.utcnow().date()
        self.log = []

    def basePrice(self, sku):
        if sku in self.overrides:
            self.log.append("%s: override %.2f" % (sku, self.overrides[sku]))
            return float(self.overrides[sku])
        item = self.catalog.get(sku)
        if item is None:
            raise KeyError(sku)
        return float(item["price"])

    def quantityDiscount(self, qty):
        for minQty, disc in QUANTITY_BREAKS:
            if qty >= minQty:
                return disc
        return 0.0

    def linePrice(self, sku, qty, member=False, couponPct=0, b2b=False):
        """unit price after discounts. Discounts add up (qty + member + coupon) but are capped at
        MAX_TOTAL_DISCOUNT, and never go below cost (if cost is known). b2b customers don't get the
        member discount (they have their own B2B rate in the order engine)."""
        base = self.basePrice(sku)
        item = self.catalog.get(sku, {})
        if member and not b2b and item.get("memberPrice"):
            base = min(base, float(item["memberPrice"]))
            member = False                       # member price replaces the member discount
        disc = self.quantityDiscount(qty)
        if member and not b2b:
            disc += MEMBER_DISCOUNT
        if couponPct:
            disc += couponPct / 100.0
        if disc > MAX_TOTAL_DISCOUNT:
            self.log.append("%s: discount %.2f capped" % (sku, disc))
            disc = MAX_TOTAL_DISCOUNT
        price = round(base * (1 - disc), 2)
        cost = item.get("cost")
        if cost is not None and price < float(cost):
            self.log.append("%s: price %.2f below cost %.2f" % (sku, price, cost))
            price = float(cost)
        return price

    def orderPrices(self, lines, member=False, coupon=None, b2b=False):
        pct = parseCouponCode(coupon, self.today) if coupon else 0
        if coupon and not pct:
            self.log.append("coupon %s rejected" % coupon)
            pct = 0
        out = []
        for ln in lines:
            try:
                p = self.linePrice(ln["sku"], int(ln["qty"]), member, pct or 0, b2b)
            except KeyError:
                log.warning("unknown sku %s in orderPrices" % ln["sku"])
                continue
            out.append({"sku": ln["sku"], "qty": int(ln["qty"]), "price": p})
        return out

    def categorySale(self, category, pct, lines):
        # "20% off kitchen" banners; applied after linePrice by the phone screen, no cap (!)
        for ln in lines:
            if self.catalog.get(ln["sku"], {}).get("category") == category:
                ln["price"] = round(ln["price"] * (1 - pct / 100.0), 2)
        return lines
