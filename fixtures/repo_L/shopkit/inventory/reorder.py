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
