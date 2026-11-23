"""Holds: a seller's money for an order is held until the order's return window has passed, so a
return can still be refunded from it (migration 0023, INC-2291)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from shopkit.payouts.models import Earning

RETURN_WINDOW_DAYS = 30          # matches orders.returns.RETURN_WINDOW_DAYS


@dataclass
class Hold:
    seller_id: str
    order_id: str
    amount_cents: int
    release_after: date
    released: bool = False


@dataclass
class HoldBook:
    holds: dict[tuple[str, str], Hold] = field(default_factory=dict)

    def place(self, earning: Earning, net_cents: int) -> Hold:
        """Hold the net amount of an earning. Not delivered yet: held until delivery + window; the
        release date is set when delivery is known (update_delivery)."""
        key = (earning.seller_id, earning.order_id)
        release = (earning.delivered_on or date.max - timedelta(days=RETURN_WINDOW_DAYS)) + timedelta(days=RETURN_WINDOW_DAYS)
        existing = self.holds.get(key)
        if existing and not existing.released:
            existing.amount_cents += net_cents
            existing.release_after = max(existing.release_after, release) if earning.delivered_on else existing.release_after
            return existing
        hold = Hold(earning.seller_id, earning.order_id, net_cents, release)
        self.holds[key] = hold
        return hold

    def update_delivery(self, seller_id: str, order_id: str, delivered_on: date) -> None:
        hold = self.holds.get((seller_id, order_id))
        if hold and not hold.released:
            hold.release_after = delivered_on + timedelta(days=RETURN_WINDOW_DAYS)

    def releasable(self, seller_id: str, on: date) -> list[Hold]:
        return [h for h in self.holds.values() if h.seller_id == seller_id and not h.released and h.release_after <= on]

    def release(self, seller_id: str, on: date) -> int:
        """Release every hold of the seller due on `on`; returns the amount released."""
        total = 0
        for h in self.releasable(seller_id, on):
            h.released = True
            total += h.amount_cents
        return total

    def outstanding(self, seller_id: str) -> int:
        return sum(h.amount_cents for h in self.holds.values() if h.seller_id == seller_id and not h.released)
