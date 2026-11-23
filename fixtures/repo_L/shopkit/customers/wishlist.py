"""Wishlists: saved products, with back-in-stock and price-drop alerts."""
from dataclasses import dataclass, field
from datetime import datetime

from shopkit.catalog.models import Product
from shopkit.utils.dates import utc_now

MAX_ITEMS = 100


@dataclass
class WishlistItem:
    sku: str
    price_when_added: int
    added: datetime = field(default_factory=utc_now)


@dataclass
class Wishlist:
    customer_id: str
    items: list[WishlistItem] = field(default_factory=list)

    def add(self, product: Product) -> None:
        if any(i.sku == product.sku for i in self.items):
            return
        if len(self.items) >= MAX_ITEMS:
            raise ValueError(f"wishlist is limited to {MAX_ITEMS} items")
        self.items.append(WishlistItem(product.sku, product.price_cents))

    def remove(self, sku: str) -> None:
        self.items = [i for i in self.items if i.sku != sku]

    def price_drops(self, catalog: dict[str, Product]) -> list[tuple[str, int, int]]:
        """(sku, old, new) for saved products that got cheaper since they were added."""
        out = []
        for item in self.items:
            product = catalog.get(item.sku)
            if product and product.price_cents < item.price_when_added:
                out.append((item.sku, item.price_when_added, product.price_cents))
        return out

    def back_in_stock(self, available: dict[str, int], previously_out: set[str]) -> list[str]:
        return [i.sku for i in self.items if i.sku in previously_out and available.get(i.sku, 0) > 0]
