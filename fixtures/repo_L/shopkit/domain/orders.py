"""Order with Money-typed lines and totals."""
from __future__ import annotations

from dataclasses import dataclass, field

from shopkit.domain.ids import CustomerId, OrderId
from shopkit.domain.money import Money, total, zero


@dataclass(frozen=True)
class Line:
    sku: str
    qty: int
    unit: Money

    def __post_init__(self):
        if self.qty < 1:
            raise ValueError("qty must be >= 1")

    @property
    def amount(self) -> Money:
        return self.unit * self.qty


@dataclass
class Order:
    id: OrderId
    customer: CustomerId | None = None
    currency: str = "USD"
    lines: list[Line] = field(default_factory=list)
    discount: Money | None = None

    def add(self, sku: str, qty: int, unit: Money) -> Line:
        if unit.currency != self.currency:
            raise ValueError("line currency differs from the order's")
        line = Line(sku, qty, unit)
        self.lines.append(line)
        return line

    @property
    def subtotal(self) -> Money:
        return total([l.amount for l in self.lines], self.currency)

    @property
    def grand_total(self) -> Money:
        t = self.subtotal - (self.discount or zero(self.currency))
        return t if t.minor > 0 else zero(self.currency)

    @classmethod
    def from_v1(cls, order) -> "Order":
        """Build from an orders.models.Order."""
        o = cls(OrderId(order.id) if order.id.startswith("ord_") else OrderId.new(), currency=order.currency)
        for l in order.lines:
            o.add(l.sku, l.qty, Money(l.unit_cents, order.currency))
        return o
