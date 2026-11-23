"""Scheduled price changes: sales and permanent price changes that take effect at a set time.

Times are UTC. A sale has an end; a permanent change doesn't. Overlapping sales on one SKU: the
lowest active price wins, which is what customers would pick anyway, and merchandising asked for it
after the "two sales, higher price shown" complaints (SUP-412).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from shopkit.errors import Conflict


class ScheduleConflict(Conflict):
    pass


@dataclass(frozen=True)
class ScheduledPrice:
    sku: str
    currency: str
    unit_cents: int
    starts: datetime
    ends: datetime | None = None          # None: permanent from `starts`
    label: str = ""                        # shown on the product page, e.g. "Spring sale"

    def active_at(self, at: datetime) -> bool:
        return self.starts <= at and (self.ends is None or at < self.ends)

    @property
    def is_sale(self) -> bool:
        return self.ends is not None


@dataclass
class Schedule:
    entries: list[ScheduledPrice] = field(default_factory=list)

    def add(self, entry: ScheduledPrice) -> None:
        if entry.ends is not None and entry.ends <= entry.starts:
            raise ValueError("a sale must end after it starts")
        if not entry.is_sale:
            clash = [e for e in self.entries
                     if not e.is_sale and e.sku == entry.sku and e.currency == entry.currency
                     and e.starts == entry.starts]
            if clash:
                raise ScheduleConflict(f"{entry.sku} already has a price change at {entry.starts}",
                                       sku=entry.sku)
        self.entries.append(entry)

    def base_price(self, sku: str, currency: str, at: datetime) -> ScheduledPrice | None:
        """The latest permanent change in effect at `at`."""
        changes = [e for e in self.entries if not e.is_sale and e.sku == sku and e.currency == currency
                   and e.active_at(at)]
        return max(changes, key=lambda e: e.starts) if changes else None

    def best_sale(self, sku: str, currency: str, at: datetime) -> ScheduledPrice | None:
        sales = [e for e in self.entries if e.is_sale and e.sku == sku and e.currency == currency
                 and e.active_at(at)]
        return min(sales, key=lambda e: e.unit_cents) if sales else None

    def upcoming(self, after: datetime, limit: int = 20) -> list[ScheduledPrice]:
        future = sorted((e for e in self.entries if e.starts > after), key=lambda e: e.starts)
        return future[:limit]

    def expire(self, before: datetime) -> int:
        """Drop sales that ended before `before`; returns how many were removed."""
        keep = [e for e in self.entries if not (e.is_sale and e.ends is not None and e.ends <= before)]
        removed = len(self.entries) - len(keep)
        self.entries = keep
        return removed
