"""The storefront price for a SKU, quantity, currency and customer.

Order of precedence:
1. a scheduled sale active now (lowest wins), unless the customer's price list is lower
2. the customer's price list (tier lists first, then by priority), with quantity breaks
3. a permanent scheduled price change, else the product's list price (in the shop currency),
   converted with the day's pinned rate and rounded to the currency's price ending

The result never goes below the floor price (cost + minimum margin) when a cost is known; the floor
exists because of the "-90% typo" incident (a price list row entered as 1.99 instead of 19.99).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from shopkit.catalog.models import Product
from shopkit.config import SETTINGS, Settings
from shopkit.pricing.currency import RateTable
from shopkit.pricing.endings import apply_ending
from shopkit.pricing.price_lists import NoPrice, PriceListSet
from shopkit.pricing.schedules import Schedule

log = logging.getLogger(__name__)

MIN_MARGIN_BP = 500           # 5% over cost


@dataclass(frozen=True)
class PricePoint:
    unit_cents: int
    currency: str
    source: str                      # "sale:<label>", "list:<code>", "schedule", "converted", "list_price"
    compare_at_cents: int | None = None   # the struck-through "was" price on the product page
    floored: bool = False


@dataclass
class PricingEngine:
    price_lists: PriceListSet = field(default_factory=PriceListSet)
    schedule: Schedule = field(default_factory=Schedule)
    rates: RateTable = field(default_factory=RateTable)
    costs: dict[str, int] = field(default_factory=dict)       # sku -> unit cost in the shop currency
    settings: Settings = SETTINGS

    def floor_cents(self, sku: str, currency: str, at: datetime) -> int | None:
        cost = self.costs.get(sku)
        if cost is None:
            return None
        floor = cost + cost * MIN_MARGIN_BP // 10_000
        if currency != self.settings.default_currency:
            floor = self.rates.convert(floor, self.settings.default_currency, currency, at.date())
        return floor

    def regular_price(self, product: Product, qty: int, currency: str, tier: str,
                      at: datetime) -> PricePoint:
        """Price without sales: price list, else scheduled base price, else converted list price."""
        try:
            cents, code = self.price_lists.resolve(product.sku, qty, currency, tier)
            return PricePoint(cents, currency, f"list:{code}")
        except NoPrice:
            pass
        base = self.schedule.base_price(product.sku, currency, at)
        if base is not None:
            return PricePoint(base.unit_cents, currency, "schedule")
        shop = self.settings.default_currency
        if currency == shop:
            return PricePoint(product.price_cents, currency, "list_price")
        converted = self.rates.convert(product.price_cents, shop, currency, at.date())
        return PricePoint(apply_ending(converted, currency), currency, "converted")

    def price(self, product: Product, qty: int = 1, *, currency: str | None = None, tier: str = "standard",
              at: datetime) -> PricePoint:
        currency = currency or self.settings.default_currency
        if qty < 1:
            raise ValueError("qty must be >= 1")
        regular = self.regular_price(product, qty, currency, tier, at)
        point = regular
        sale = self.schedule.best_sale(product.sku, currency, at)
        if sale is not None and sale.unit_cents < regular.unit_cents:
            point = PricePoint(sale.unit_cents, currency, f"sale:{sale.label or 'sale'}",
                               compare_at_cents=regular.unit_cents)
        floor = self.floor_cents(product.sku, currency, at)
        if floor is not None and point.unit_cents < floor:
            log.warning("price for %s %s below floor (%d < %d), using floor", product.sku, currency,
                        point.unit_cents, floor)
            point = PricePoint(floor, currency, point.source, point.compare_at_cents, floored=True)
        return point

    def line_total(self, product: Product, qty: int, *, currency: str | None = None, tier: str = "standard",
                   at: datetime) -> int:
        return self.price(product, qty, currency=currency, tier=tier, at=at).unit_cents * qty

    def cart_prices(self, cart: dict[str, int], products: dict[str, Product], *, currency: str | None = None,
                    tier: str = "standard", at: datetime) -> dict[str, PricePoint]:
        return {sku: self.price(products[sku], qty, currency=currency, tier=tier, at=at)
                for sku, qty in cart.items()}
