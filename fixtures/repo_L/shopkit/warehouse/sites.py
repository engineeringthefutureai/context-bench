from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Site:
    code: str
    name: str
    country: str = "US"
    services: frozenset[str] = frozenset({"ground", "air"})      # carrier services available at the dock
    safety_stock: int = 0                                         # units per SKU never allocated (store pickups)
    priority: int = 0                                             # lower ships first when several can


@dataclass
class Lot:
    sku: str
    site: str
    bin: str
    qty: int
    expires: date | None = None
    received: date | None = None


@dataclass
class SiteStock:
    """Lots per site. Allocation takes from lots (FEFO: first expiring first, then oldest received)."""
    lots: list[Lot] = field(default_factory=list)

    def available(self, site: str, sku: str, on: date | None = None) -> int:
        return sum(l.qty for l in self.lots if l.site == site and l.sku == sku and l.qty > 0
                   and (on is None or l.expires is None or l.expires >= on))

    def fefo(self, site: str, sku: str, on: date) -> list[Lot]:
        usable = [l for l in self.lots if l.site == site and l.sku == sku and l.qty > 0
                  and (l.expires is None or l.expires >= on)]
        return sorted(usable, key=lambda l: (l.expires or date.max, l.received or date.min, l.bin))

    def take(self, lot: Lot, qty: int) -> None:
        if qty > lot.qty:
            raise ValueError(f"lot {lot.bin}/{lot.sku} has {lot.qty}, can't take {qty}")
        lot.qty -= qty
