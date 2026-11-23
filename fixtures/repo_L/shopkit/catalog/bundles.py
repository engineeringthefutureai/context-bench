"""Product bundles: a bundle SKU sold as one item but picked as its components (gift sets, starter
kits). The bundle has its own price; revenue, tax and stock are allocated to the components."""
from __future__ import annotations

from dataclasses import dataclass, field

from shopkit.catalog.models import Product
from shopkit.errors import ShopError
from shopkit.inventory.stock import Stock


class BundleError(ShopError):
    http_status = 422


@dataclass(frozen=True)
class Component:
    sku: str
    qty: int


@dataclass
class Bundle:
    sku: str
    title: str
    price_cents: int
    components: list[Component] = field(default_factory=list)

    def validate(self, products: dict[str, Product]) -> None:
        if not self.components:
            raise BundleError(f"bundle {self.sku} has no components", sku=self.sku)
        seen = set()
        for c in self.components:
            if c.sku == self.sku:
                raise BundleError("a bundle can't contain itself", sku=self.sku)
            if c.sku in seen:
                raise BundleError(f"{c.sku} listed twice in {self.sku}", sku=self.sku)
            if c.qty < 1:
                raise BundleError(f"bad quantity for {c.sku}", sku=self.sku)
            if c.sku not in products:
                raise BundleError(f"unknown component {c.sku}", sku=self.sku, component=c.sku)
            seen.add(c.sku)

    def list_value(self, products: dict[str, Product]) -> int:
        return sum(products[c.sku].price_cents * c.qty for c in self.components)

    def savings_cents(self, products: dict[str, Product]) -> int:
        return max(self.list_value(products) - self.price_cents, 0)

    def available(self, stock: Stock) -> int:
        """How many bundles can be assembled from stock."""
        return min((stock.available(c.sku) // c.qty for c in self.components), default=0)

    def allocate_revenue(self, products: dict[str, Product], qty: int = 1) -> dict[str, int]:
        """Split the bundle price over the components in proportion to their list value (largest
        remainder method, so the parts add up exactly). Used for per-component tax and reports."""
        total_price = self.price_cents * qty
        values = {c.sku: products[c.sku].price_cents * c.qty * qty for c in self.components}
        base = sum(values.values())
        if base == 0:
            even = total_price // len(values)
            out = {sku: even for sku in values}
            out[next(iter(values))] += total_price - even * len(values)
            return out
        raw = {sku: total_price * v / base for sku, v in values.items()}
        out = {sku: int(r) for sku, r in raw.items()}
        rest = total_price - sum(out.values())
        for sku in sorted(raw, key=lambda s: (raw[s] - out[s], s), reverse=True)[:rest]:
            out[sku] += 1
        return out

    def explode(self, qty: int) -> list[tuple[str, int]]:
        """Pick lines for `qty` bundles."""
        return [(c.sku, c.qty * qty) for c in self.components]
