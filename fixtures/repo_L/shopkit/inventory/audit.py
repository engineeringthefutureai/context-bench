"""Cycle counts: compare physical counts with the system and report discrepancies."""
from dataclasses import dataclass

from shopkit.inventory.stock import Stock


@dataclass(frozen=True)
class Discrepancy:
    sku: str
    system: int
    counted: int

    @property
    def delta(self) -> int:
        return self.counted - self.system


def compare(stock: Stock, counts: dict[str, int]) -> list[Discrepancy]:
    return sorted((Discrepancy(sku, stock.available(sku), n) for sku, n in counts.items()
                   if stock.available(sku) != n), key=lambda d: d.sku)


def shrinkage_rate(discrepancies: list[Discrepancy], counted_units: int) -> float:
    """Units lost (negative deltas only) as a share of all counted units."""
    lost = -sum(d.delta for d in discrepancies if d.delta < 0)
    return lost / counted_units if counted_units else 0.0
