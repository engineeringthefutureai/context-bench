"""Lot tracking for perishable and batch-controlled goods (first-expired, first-out)."""
from dataclasses import dataclass
from datetime import date

from shopkit.errors import Conflict


class NotEnoughStock(Conflict):
    pass


@dataclass
class Lot:
    sku: str
    lot_number: str
    qty: int
    expires: date | None = None


class LotLedger:
    def __init__(self):
        self._lots: list[Lot] = []

    def receive(self, lot: Lot) -> None:
        if lot.qty < 1:
            raise ValueError("qty must be >= 1")
        self._lots.append(lot)

    def on_hand(self, sku: str, today: date) -> int:
        return sum(l.qty for l in self._usable(sku, today))

    def _usable(self, sku: str, today: date) -> list[Lot]:
        lots = [l for l in self._lots if l.sku == sku and l.qty > 0
                and (l.expires is None or l.expires >= today)]
        return sorted(lots, key=lambda l: (l.expires is None, l.expires or date.max, l.lot_number))

    def pick(self, sku: str, qty: int, today: date) -> list[tuple[str, int]]:
        """Take `qty` units from the soonest-expiring usable lots; returns (lot_number, qty) picked.
        Expired lots are never picked. Raises NotEnoughStock without changing anything."""
        usable = self._usable(sku, today)
        if sum(l.qty for l in usable) < qty:
            raise NotEnoughStock(f"not enough {sku}", sku=sku, requested=qty,
                                 available=sum(l.qty for l in usable))
        picked = []
        for lot in usable:
            take = min(lot.qty, qty)
            lot.qty -= take
            qty -= take
            picked.append((lot.lot_number, take))
            if qty == 0:
                break
        return picked

    def expiring(self, today: date, within_days: int) -> list[Lot]:
        return [l for l in self._lots if l.qty and l.expires
                and 0 <= (l.expires - today).days <= within_days]
