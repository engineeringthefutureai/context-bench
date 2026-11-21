"""Automatic promotions (no code needed): buy-X-get-Y and category-wide sales."""
from dataclasses import dataclass

from shopkit.orders.models import Order
from shopkit.utils.numbers import apply_rate


@dataclass(frozen=True)
class BuyXGetY:
    sku: str
    buy: int
    free: int

    def discount(self, order: Order) -> int:
        """For every buy+free units of the SKU, `free` of them cost nothing."""
        for line in order.lines:
            if line.sku == self.sku:
                groups = line.qty // (self.buy + self.free)
                return groups * self.free * line.unit_cents
        return 0


@dataclass(frozen=True)
class SkuPercentOff:
    skus: frozenset[str]
    percent: int

    def discount(self, order: Order) -> int:
        return sum(apply_rate(l.total_cents, self.percent) for l in order.lines if l.sku in self.skus)


def best_discount(order: Order, rules: list) -> tuple[object | None, int]:
    """Promotions don't stack: the single rule giving the largest discount applies."""
    best, amount = None, 0
    for rule in rules:
        d = rule.discount(order)
        if d > amount:
            best, amount = rule, d
    return best, amount
