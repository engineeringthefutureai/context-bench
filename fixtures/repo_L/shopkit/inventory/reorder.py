"""Reorder suggestions for purchasing, based on recent sales velocity."""
from dataclasses import dataclass

from shopkit.config import SETTINGS
from shopkit.inventory.stock import Stock

LEAD_TIME_DAYS = 14
SAFETY_DAYS = 7


def days_of_cover(on_hand: int, units_sold: int, period_days: int) -> float:
    """How many days current stock lasts at the average daily sales rate of the period.
    No sales in the period means infinite cover (float('inf')); negative stock is 0 days."""
    if on_hand <= 0:
        return 0.0
    daily = units_sold / period_days if period_days else 0
    return float("inf") if daily == 0 else on_hand / daily


@dataclass(frozen=True)
class Suggestion:
    sku: str
    on_hand: int
    cover_days: float
    order_qty: int


def suggest(stock: Stock, sales_30d: dict[str, int]) -> list[Suggestion]:
    """SKUs whose cover won't last the supplier lead time plus safety stock, with a quantity that
    brings cover back to 60 days. Sorted by urgency (lowest cover first)."""
    out = []
    for sku, sold in sales_30d.items():
        on_hand = stock.available(sku)
        cover = days_of_cover(on_hand, sold, 30)
        if cover < LEAD_TIME_DAYS + SAFETY_DAYS or on_hand <= SETTINGS.low_stock_threshold:
            target = round(sold / 30 * 60)
            out.append(Suggestion(sku, on_hand, cover, max(target - on_hand, 1)))
    return sorted(out, key=lambda s: (s.cover_days, s.sku))


# ---- supplier terms (purchasing, 2025) -----------------------------------------------------------
@dataclass(frozen=True)
class SupplierTerms:
    supplier: str
    lead_time_days: int = LEAD_TIME_DAYS
    min_order_qty: int = 1
    pack_size: int = 1                       # orders are whole packs
    min_order_cents: int = 0                 # supplier-wide minimum per purchase order


def round_to_packs(qty: int, terms: SupplierTerms) -> int:
    """Quantity rounded up to the minimum order and to whole packs."""
    qty = max(qty, terms.min_order_qty)
    return -(-qty // terms.pack_size) * terms.pack_size


def suggest_with_terms(stock: Stock, sales_30d: dict[str, int], terms: dict[str, SupplierTerms],
                       unit_cost_cents: dict[str, int]) -> dict[str, list[Suggestion]]:
    """Suggestions grouped per supplier, quantities in whole packs, using each supplier's lead time.
    A supplier whose total is under its minimum order value gets its quantities scaled up
    proportionally (still whole packs) until the minimum is met."""
    by_supplier: dict[str, list[Suggestion]] = {}
    for sku, sold in sales_30d.items():
        t = terms.get(sku)
        if t is None:
            continue
        on_hand = stock.available(sku)
        cover = days_of_cover(on_hand, sold, 30)
        if cover >= t.lead_time_days + SAFETY_DAYS and on_hand > SETTINGS.low_stock_threshold:
            continue
        target = round(sold / 30 * (t.lead_time_days + 60))
        qty = round_to_packs(max(target - on_hand, 1), t)
        by_supplier.setdefault(t.supplier, []).append(Suggestion(sku, on_hand, cover, qty))
    for supplier, rows in by_supplier.items():
        t0 = terms[rows[0].sku]
        total = sum(unit_cost_cents.get(r.sku, 0) * r.order_qty for r in rows)
        if t0.min_order_cents and 0 < total < t0.min_order_cents:
            factor = t0.min_order_cents / total
            rows[:] = [Suggestion(r.sku, r.on_hand, r.cover_days,
                                  round_to_packs(int(r.order_qty * factor + 0.999), terms[r.sku])) for r in rows]
        rows.sort(key=lambda s: (s.cover_days, s.sku))
    return by_supplier
