"""Price rules: volume tiers for B2B customers and scheduled sale prices."""
from dataclasses import dataclass
from datetime import date

from shopkit.catalog.models import Product


@dataclass(frozen=True)
class Tier:
    min_qty: int
    unit_cents: int


@dataclass(frozen=True)
class SalePrice:
    sku: str
    unit_cents: int
    starts: date
    ends: date              # inclusive

    def active_on(self, day: date) -> bool:
        return self.starts <= day <= self.ends


def tier_price(tiers: list[Tier], qty: int, list_cents: int) -> int:
    """Unit price for `qty` under volume tiers: the tier with the largest min_qty <= qty wins.
    Below every tier (or with no tiers) the list price applies. Tiers may be given in any order."""
    if qty < 1:
        raise ValueError("qty must be >= 1")
    eligible = [t for t in tiers if t.min_qty <= qty]
    return max(eligible, key=lambda t: t.min_qty).unit_cents if eligible else list_cents


class PriceBook:
    def __init__(self, tiers: dict[str, list[Tier]] | None = None, sales: list[SalePrice] | None = None):
        self._tiers = tiers or {}
        self._sales = sales or []

    def unit_price(self, product: Product, qty: int, day: date) -> int:
        """The lowest of: list price under volume tiers, and any sale price active on `day`."""
        price = tier_price(self._tiers.get(product.sku, []), qty, product.price_cents)
        for sale in self._sales:
            if sale.sku == product.sku and sale.active_on(day):
                price = min(price, sale.unit_cents)
        return price

    def line_total(self, product: Product, qty: int, day: date) -> int:
        return self.unit_price(product, qty, day) * qty
