"""Cart pricing for the storefront: the numbers on the cart page and the checkout summary.

checkout.quote() prices the older B2B checkout path with catalog.pricing.PriceBook; the storefront
cart uses this module (price lists, schedules, member prices, coupons and promotions together)."""
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from shopkit.catalog.models import Product
from shopkit.config import SETTINGS, Settings
from shopkit.core.pricing import parseCouponCode
from shopkit.customers.models import Address
from shopkit.errors import ShopError
from shopkit.pricing.engine import PricingEngine
from shopkit.promotions.coupons import FIXED, PERCENT, Coupon, CouponRejected, coupon_is_active, normalize_code
from shopkit.shipping.rates import TooHeavy, shipping_quote
from shopkit.tax.rates import EXEMPT, STANDARD, rate_for
from shopkit.utils.numbers import apply_rate

log = logging.getLogger(__name__)

GIFT_CARD_TAX_CLASS = EXEMPT
MAX_CART_LINES = 100
MEMBER_PRICE_LIST_TIER = "member"
PRICE_CHANGE_NOTICE_BP = 200          # tell the shopper when a price moved more than 2% since it was added


class CartError(ShopError):
    http_status = 422


@dataclass
class CartItem:
    sku: str
    qty: int
    added_unit_cents: int | None = None     # price when it was put in the cart
    gift_card: bool = False


@dataclass
class PricedLine:
    sku: str
    qty: int
    unit_cents: int
    list_unit_cents: int
    source: str
    on_sale: bool
    discount_cents: int = 0
    tax_cents: int = 0
    tax_class: str = STANDARD
    notes: list[str] = field(default_factory=list)

    @property
    def subtotal_cents(self) -> int:
        return self.unit_cents * self.qty


@dataclass
class PricedCart:
    currency: str
    lines: list[PricedLine] = field(default_factory=list)
    coupon_code: str | None = None
    coupon_discount_cents: int = 0
    promo_discount_cents: int = 0
    shipping_cents: int = 0
    shipping_breakdown: dict = field(default_factory=dict)
    tax_cents: int = 0
    messages: list[str] = field(default_factory=list)

    @property
    def subtotal_cents(self) -> int:
        return sum(l.subtotal_cents for l in self.lines)

    @property
    def total_cents(self) -> int:
        return self.subtotal_cents - self.coupon_discount_cents - self.promo_discount_cents + self.shipping_cents + self.tax_cents


def price_cart(items: list[CartItem], products: dict[str, Product], *, engine: PricingEngine, ship_to: Address,
               weight_g: int, at: datetime, tier: str = "standard", member: bool = False,
               coupon_code: str | None = None, coupons: dict[str, Coupon] | None = None, promotions=(),
               tax_classes: dict[str, str] | None = None, currency: str | None = None,
               settings: Settings = SETTINGS, metrics: dict | None = None) -> PricedCart:
    """Price a cart. Order of operations: unit prices (engine: lists, schedules, floor), member
    prices, automatic promotions, coupon, tax per line on what's left after discounts, shipping.
    Messages are shown to the shopper (price changes, rejected coupons, unavailable items)."""
    t0 = time.perf_counter()
    trace = settings.flag("cart_trace")
    currency = currency or settings.default_currency
    coupons = coupons or {}
    tax_classes = tax_classes or {}
    stats = Counter()
    cart = PricedCart(currency)
    if trace:
        log.info("INC-2390 TRACE cart start items=%s tier=%s member=%s coupon=%s currency=%s ship_to=%s/%s",
                 [(i.sku, i.qty) for i in items], tier, member, coupon_code, currency, ship_to.country, ship_to.postcode)

    # --- lines -----------------------------------------------------------------------------------------
    if len(items) > MAX_CART_LINES:
        raise CartError(f"a cart can hold at most {MAX_CART_LINES} different items", lines=len(items))
    merged: dict[str, CartItem] = {}
    for it in items:
        # the app sent the same SKU twice when the add button was tapped fast (INC-2390): merge them
        if it.sku in merged:
            merged[it.sku].qty += it.qty
            stats["merged_lines"] += 1
        else:
            merged[it.sku] = CartItem(it.sku, it.qty, it.added_unit_cents, it.gift_card)
    for it in merged.values():
        if it.qty < 1:
            stats["zero_qty"] += 1
            continue
        product = products.get(it.sku)
        if product is None or not product.is_purchasable():
            cart.messages.append(f"{it.sku} is no longer available and was removed.")
            stats["unavailable"] += 1
            continue
        t_line = time.perf_counter()
        if it.gift_card:
            # gift cards are sold at face value: no price list, no sale, no member price, no tax
            line = PricedLine(it.sku, it.qty, product.price_cents, product.price_cents, "gift_card", False,
                              tax_class=GIFT_CARD_TAX_CLASS)
        else:
            point = engine.price(product, it.qty, currency=currency, tier=tier, at=at)
            regular = engine.regular_price(product, it.qty, currency, tier, at)
            unit, source = point.unit_cents, point.source
            on_sale = point.compare_at_cents is not None
            if member:
                try:
                    member_point = engine.price(product, it.qty, currency=currency, tier=MEMBER_PRICE_LIST_TIER, at=at)
                    if member_point.source.startswith("list:") and member_point.unit_cents < unit:
                        unit, source = member_point.unit_cents, member_point.source
                        on_sale = False         # a member price is not a sale (coupons still apply to it)
                        stats["member_priced"] += 1
                except Exception as e:          # the member list may not exist in every currency
                    log.info("INC-2390 member price lookup failed for %s: %s", it.sku, e)
            line = PricedLine(it.sku, it.qty, unit, regular.unit_cents, source, on_sale,
                              tax_class=tax_classes.get(it.sku, STANDARD))
            if point.floored:
                line.notes.append("price floor applied")
                stats["floored"] += 1
        if it.added_unit_cents is not None and it.added_unit_cents != line.unit_cents:
            moved = abs(line.unit_cents - it.added_unit_cents) * 10_000 // max(it.added_unit_cents, 1)
            if moved > PRICE_CHANGE_NOTICE_BP:
                cart.messages.append(f"The price of {product.title} changed since you added it.")
                stats["price_changed"] += 1
        cart.lines.append(line)
        if trace:
            log.info("INC-2390 TRACE line sku=%s qty=%d unit=%d list=%d source=%s sale=%s took=%.1fms", line.sku,
                     line.qty, line.unit_cents, line.list_unit_cents, line.source, line.on_sale,
                     (time.perf_counter() - t_line) * 1000)
    t_lines = time.perf_counter()
    if not cart.lines:
        cart.messages.append("Your cart is empty.")
        return cart

    # --- automatic promotions (best single rule, see promotions.rules) -------------------------------------
    if promotions:
        from shopkit.orders.models import Order, OrderLine
        as_order = Order("cart", [OrderLine(l.sku, l.qty, l.unit_cents) for l in cart.lines if l.tax_class != GIFT_CARD_TAX_CLASS],
                         currency=currency)
        from shopkit.promotions.rules import best_discount
        rule, amount = best_discount(as_order, list(promotions))
        if amount:
            cart.promo_discount_cents = amount
            cart.messages.append(f"Promotion applied: {type(rule).__name__}.")
            # spread the promotion over the lines it touched, for per-line tax
            touched = [l for l in cart.lines if l.tax_class != GIFT_CARD_TAX_CLASS]
            base = sum(l.subtotal_cents for l in touched)
            left = amount
            for i, l in enumerate(touched):
                share = amount * l.subtotal_cents // base if i < len(touched) - 1 else left
                l.discount_cents += share
                left -= share
            stats["promo_applied"] += 1
    t_promos = time.perf_counter()

    # --- coupon ---------------------------------------------------------------------------------------------
    # Percentage coupons don't discount items that are already on sale, or gift cards. On-sale items
    # still count towards the coupon's minimum order value (merchandising rule, 2025-04: "the minimum
    # is about basket size, the exclusion is about margin"). Fixed-amount coupons apply to the whole
    # eligible subtotal (gift cards excluded), never more than it. Printed coupon codes (SAVEnn-YYYY-XXXX)
    # are honoured as percentage coupons with the same exclusions and no minimum.
    if coupon_code:
        code = normalize_code(coupon_code)
        coupon = coupons.get(code)
        printed_pct = None
        if coupon is None:
            printed_pct = parseCouponCode(code)
            if printed_pct:
                coupon = Coupon(code, PERCENT, printed_pct, at.date(), at.date())
                stats["printed_coupon"] += 1
        if coupon is None:
            cart.messages.append(f"The code {code} isn't valid.")
            stats["coupon_invalid"] += 1
        else:
            not_gift = [l for l in cart.lines if l.tax_class != GIFT_CARD_TAX_CLASS]
            basket = sum(l.subtotal_cents - l.discount_cents for l in not_gift)          # counts sale items
            if coupon.kind == PERCENT:
                eligible = [l for l in not_gift if not l.on_sale]
            else:
                eligible = not_gift
            eligible_base = sum(l.subtotal_cents - l.discount_cents for l in eligible)
            try:
                if not coupon_is_active(coupon, at.date()):
                    raise CouponRejected(f"coupon {coupon.code} is not active", code=coupon.code)
                if printed_pct is None and basket < coupon.min_order_cents:
                    raise CouponRejected(f"coupon {coupon.code} needs a larger order", code=coupon.code,
                                         min_order_cents=coupon.min_order_cents)
                if coupon.kind == PERCENT:
                    total_off = apply_rate(eligible_base, coupon.value)
                else:
                    total_off = min(coupon.value, eligible_base)
                # old behaviour (before INC-2390), percentage of the whole basket including sale items:
                # total_off = apply_rate(basket, coupon.value) if coupon.kind == PERCENT else min(coupon.value, basket)
                left = total_off
                for i, l in enumerate(eligible):
                    base_l = l.subtotal_cents - l.discount_cents
                    share = total_off * base_l // eligible_base if (eligible_base and i < len(eligible) - 1) else left
                    l.discount_cents += share
                    left -= share
                cart.coupon_code = coupon.code
                cart.coupon_discount_cents = total_off
                if coupon.kind == PERCENT and len(eligible) < len(not_gift):
                    cart.messages.append("Items already on sale aren't discounted by this code.")
                stats["coupon_applied"] += 1
                if trace:
                    log.info("INC-2390 TRACE coupon %s kind=%s value=%d basket=%d eligible=%d off=%d", coupon.code,
                             coupon.kind, coupon.value, basket, eligible_base, total_off)
            except CouponRejected as e:
                cart.messages.append(str(e).replace("coupon ", "The code ", 1) + ".")
                stats["coupon_rejected"] += 1
    t_coupon = time.perf_counter()

    # --- tax per line, on the discounted amount ----------------------------------------------------------------
    for l in cart.lines:
        taxable = l.subtotal_cents - l.discount_cents
        rate = rate_for(ship_to.country, ship_to.region, l.tax_class)
        l.tax_cents = apply_rate(taxable, rate) if taxable > 0 else 0
    cart.tax_cents = sum(l.tax_cents for l in cart.lines)
    # INC-2390 shadow: tax on the order total (how checkout.quote does it) for comparison
    shadow = 0
    for l in cart.lines:
        shadow += apply_rate(l.subtotal_cents, rate_for(ship_to.country, ship_to.region, l.tax_class))
    if cart.coupon_discount_cents or cart.promo_discount_cents:
        pre = cart.subtotal_cents
        shadow = shadow * (pre - cart.coupon_discount_cents - cart.promo_discount_cents) // pre if pre else 0
    if abs(shadow - cart.tax_cents) > len(cart.lines):
        log.info("INC-2390 tax differs from quote-style tax by %d cents", cart.tax_cents - shadow)
        stats["tax_shadow_diff"] += 1

    # --- shipping ------------------------------------------------------------------------------------------------
    goods_only = all(l.tax_class == GIFT_CARD_TAX_CLASS for l in cart.lines)
    if goods_only:
        cart.shipping_cents = 0                 # gift cards are emailed
    else:
        after_discounts = sum(l.subtotal_cents - l.discount_cents for l in cart.lines if l.tax_class != GIFT_CARD_TAX_CLASS)
        try:
            quote = shipping_quote(ship_to.country, ship_to.postcode, weight_g, after_discounts)
            cart.shipping_cents = quote["total"]
            cart.shipping_breakdown = quote
        except TooHeavy as e:
            cart.messages.append("This order is too heavy to ship as one parcel; we'll contact you.")
            cart.shipping_cents = 0
            log.warning("INC-2390 too heavy cart %d g: %s", weight_g, e)
            stats["too_heavy"] += 1
    t_end = time.perf_counter()

    # --- metrics ------------------------------------------------------------------------------------------------
    if metrics is not None:
        metrics["cart.priced"] = metrics.get("cart.priced", 0) + 1
        for k, v in stats.items():
            metrics["cart." + k] = metrics.get("cart." + k, 0) + v
        metrics["cart.last_ms"] = round((t_end - t0) * 1000, 2)
    if (t_end - t0) > 0.25:
        log.warning("INC-2390 slow cart pricing %.0f ms (lines %.0f, promos %.0f, coupon %.0f)", (t_end - t0) * 1000,
                    (t_lines - t0) * 1000, (t_promos - t_lines) * 1000, (t_coupon - t_promos) * 1000)
    if trace:
        log.info("INC-2390 TRACE cart done %s", json.dumps({"subtotal": cart.subtotal_cents, "coupon": cart.coupon_discount_cents,
                                                         "promo": cart.promo_discount_cents, "tax": cart.tax_cents,
                                                         "shipping": cart.shipping_cents, "total": cart.total_cents}))
    return cart
