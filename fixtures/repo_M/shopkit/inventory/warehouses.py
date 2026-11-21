"""Multi-warehouse stock. Each warehouse holds its own Stock; allocation prefers the closest one."""
from dataclasses import dataclass, field

from shopkit.inventory.stock import Stock


@dataclass
class Warehouse:
    code: str
    country: str
    priority: int = 100                  # lower ships first when several can fulfil
    stock: Stock = field(default_factory=Stock)


class Network:
    def __init__(self, warehouses: list[Warehouse]):
        self._by_code = {w.code: w for w in warehouses}

    def get(self, code: str) -> Warehouse:
        return self._by_code[code]

    def total_available(self, sku: str) -> int:
        return sum(w.stock.available(sku) for w in self._by_code.values())

    def choose(self, sku: str, qty: int, country: str) -> Warehouse | None:
        """A single warehouse that can ship all of `qty`: same-country first, then by priority."""
        candidates = [w for w in self._by_code.values() if w.stock.available(sku) >= qty]
        candidates.sort(key=lambda w: (w.country != country, w.priority, w.code))
        return candidates[0] if candidates else None
