"""Subscribe-and-save: recurring orders generated on a schedule with a fixed discount."""
from dataclasses import dataclass, field
from datetime import date, timedelta

from shopkit.orders.models import Order, OrderLine
from shopkit.utils.ids import new_id
from shopkit.utils.numbers import apply_rate

SUBSCRIPTION_DISCOUNT_PCT = 10
INTERVALS = {"weekly": 7, "fortnightly": 14, "monthly": 30}


@dataclass
class Subscription:
    id: str
    customer_id: str
    items: dict[str, int]                         # sku -> qty
    interval: str                                 # key of INTERVALS
    next_run: date
    paused: bool = False
    skipped: set[date] = field(default_factory=set)

    def due(self, today: date) -> bool:
        return not self.paused and self.next_run <= today

    def advance(self) -> None:
        self.next_run += timedelta(days=INTERVALS[self.interval])

    def skip_next(self) -> date:
        """Skip the upcoming delivery; returns the date that was skipped."""
        skipped = self.next_run
        self.skipped.add(skipped)
        self.advance()
        return skipped


def generate_order(sub: Subscription, prices: dict[str, int], currency: str = "USD") -> Order:
    """Order for one delivery: list prices minus the subscription discount, per unit."""
    lines = []
    for sku, qty in sorted(sub.items.items()):
        unit = prices[sku] - apply_rate(prices[sku], SUBSCRIPTION_DISCOUNT_PCT)
        lines.append(OrderLine(sku, qty, unit))
    return Order(id=new_id("ord"), lines=lines, currency=currency)


def run_due(subs: list[Subscription], prices: dict[str, int], today: date) -> list[Order]:
    orders = []
    for sub in subs:
        if sub.due(today):
            orders.append(generate_order(sub, prices))
            sub.advance()
    return orders
