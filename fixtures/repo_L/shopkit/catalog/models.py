from dataclasses import dataclass


@dataclass
class Product:
    sku: str
    title: str
    price_cents: int
    active: bool = True

    def is_purchasable(self) -> bool:
        return self.active and self.price_cents > 0
