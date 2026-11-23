"""Find an order from whatever reference a customer or support agent has: a storefront id
(ord_1a2b3c4d5e6f), a sales order reference (SO-2019-000123, as printed on invoices and emails),
or an invoice number."""
from __future__ import annotations

from dataclasses import dataclass

from shopkit.errors import OrderNotFound
from shopkit.core.order_engine import formatOrderRef, parseOrderRef
from shopkit.orders.models import Order
from shopkit.orders.service import OrderBook


@dataclass
class LookupResult:
    order: Order | None
    kind: str                   # "storefront" | "sales_order" | "invoice"
    normalized: str


class OrderLookup:
    def __init__(self, book: OrderBook, so_refs: dict[str, str] | None = None,
                 invoice_orders: dict[str, str] | None = None):
        self.book = book
        self.so_refs = so_refs or {}                # "SO-2019-000123" -> storefront order id
        self.invoice_orders = invoice_orders or {}  # invoice number -> order id

    def find(self, text: str) -> LookupResult:
        raw = (text or "").strip()
        if raw.upper() in self.invoice_orders:
            oid = self.invoice_orders[raw.upper()]
            return LookupResult(self._get(oid), "invoice", raw.upper())
        parsed = parseOrderRef(raw)
        if parsed is None:
            raise OrderNotFound(f"not an order reference: {raw!r}", ref=raw)
        kind, year, seq = parsed
        ref = formatOrderRef(kind, year, seq)
        if kind == "ord":
            return LookupResult(self._get(ref), "storefront", ref)
        oid = self.so_refs.get(ref)
        return LookupResult(self._get(oid) if oid else None, "sales_order", ref)

    def _get(self, order_id: str) -> Order | None:
        try:
            return self.book.get(order_id)
        except OrderNotFound:
            return None
