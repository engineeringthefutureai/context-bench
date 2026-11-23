"""Price lists: per currency and (optionally) customer tier, with quantity breaks.

A price list has a priority; when several lists apply to a customer (e.g. "EUR-retail" and
"EUR-gold"), the applicable list with the highest priority that has an entry for the SKU wins. Within
a list, the entry with the largest min_qty <= qty wins (quantity breaks).
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from shopkit.errors import NotFound, ShopError
from shopkit.pricing.endings import has_ending


class NoPrice(NotFound):
    pass


class PriceListImportError(ShopError):
    http_status = 422


@dataclass(frozen=True)
class Entry:
    sku: str
    min_qty: int
    unit_cents: int


@dataclass
class PriceList:
    code: str
    currency: str
    customer_tier: str | None = None        # None: everyone
    priority: int = 0
    active: bool = True
    entries: dict[str, list[Entry]] = field(default_factory=dict)

    def add(self, sku: str, unit_cents: int, min_qty: int = 1) -> None:
        if unit_cents < 0:
            raise ValueError("price must be >= 0")
        if min_qty < 1:
            raise ValueError("min_qty must be >= 1")
        rows = [e for e in self.entries.get(sku, []) if e.min_qty != min_qty]
        rows.append(Entry(sku, min_qty, unit_cents))
        self.entries[sku] = sorted(rows, key=lambda e: e.min_qty)

    def applies_to(self, currency: str, tier: str) -> bool:
        return self.active and self.currency == currency and self.customer_tier in (None, tier)

    def unit_price(self, sku: str, qty: int) -> int | None:
        best = None
        for e in self.entries.get(sku, []):
            if e.min_qty <= qty:
                best = e
        return best.unit_cents if best else None

    def breaks(self, sku: str) -> list[Entry]:
        return list(self.entries.get(sku, []))


@dataclass
class PriceListSet:
    lists: list[PriceList] = field(default_factory=list)

    def add(self, price_list: PriceList) -> None:
        if any(pl.code == price_list.code for pl in self.lists):
            raise ValueError(f"duplicate price list {price_list.code}")
        self.lists.append(price_list)

    def get(self, code: str) -> PriceList:
        for pl in self.lists:
            if pl.code == code:
                return pl
        raise NotFound(f"no price list {code}", code=code)

    def applicable(self, currency: str, tier: str) -> list[PriceList]:
        """Lists for this currency and tier, highest priority first; tier-specific lists win ties."""
        found = [pl for pl in self.lists if pl.applies_to(currency, tier)]
        return sorted(found, key=lambda pl: (pl.priority, pl.customer_tier is not None), reverse=True)

    def resolve(self, sku: str, qty: int, currency: str, tier: str = "standard") -> tuple[int, str]:
        """(unit price, code of the list it came from). Raises NoPrice when no list has the SKU."""
        for pl in self.applicable(currency, tier):
            price = pl.unit_price(sku, qty)
            if price is not None:
                return price, pl.code
        raise NoPrice(f"no {currency} price for {sku}", sku=sku, currency=currency, tier=tier)


def import_csv(text: str, price_list: PriceList, *, require_endings: bool = False) -> int:
    """Load rows `sku,min_qty,price` (price as 12.99) into a list; returns the number of rows.

    The whole file is validated first: a bad row aborts the import and nothing is changed."""
    reader = csv.DictReader(io.StringIO(text))
    missing = {"sku", "min_qty", "price"} - set(reader.fieldnames or [])
    if missing:
        raise PriceListImportError(f"missing columns: {', '.join(sorted(missing))}")
    parsed, errors = [], []
    for n, row in enumerate(reader, start=2):
        try:
            sku = row["sku"].strip()
            if not sku:
                raise ValueError("empty sku")
            min_qty = int(row["min_qty"])
            whole, _, frac = row["price"].strip().partition(".")
            cents = int(whole) * 100 + int((frac + "00")[:2])
            if require_endings and not has_ending(cents, price_list.currency):
                raise ValueError(f"{row['price']} doesn't follow the {price_list.currency} price ending")
            parsed.append((sku, cents, min_qty))
        except (ValueError, KeyError) as e:
            errors.append(f"line {n}: {e}")
    if errors:
        raise PriceListImportError(f"{len(errors)} bad rows", errors=errors[:20])
    for sku, cents, min_qty in parsed:
        price_list.add(sku, cents, min_qty)
    return len(parsed)


def export_csv(price_list: PriceList) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["sku", "min_qty", "price"])
    for sku in sorted(price_list.entries):
        for e in price_list.entries[sku]:
            writer.writerow([sku, e.min_qty, f"{e.unit_cents // 100}.{e.unit_cents % 100:02d}"])
    return out.getvalue()
