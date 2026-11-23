from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from shopkit.errors import Conflict, DuplicateOrder, OrderNotFound, ShopError
from shopkit.events import BUS, EventBus
from shopkit.inventory.stock import Stock
from shopkit.orders.models import Order, OrderLine
from shopkit.orders.notes import NoteBook
from shopkit.orders.status import CANCELLED, PAID, PENDING, is_open
from shopkit.orders.tracking import StatusBoard
from shopkit.utils.dates import utc_now
from shopkit.utils.ids import new_id

log = logging.getLogger(__name__)

MAX_LINES_PER_ORDER = 100
MAX_QTY_PER_LINE = 999


class OrderBook:
    """In-memory order store used by the API."""

    def __init__(self):
        self._orders: dict[str, Order] = {}

    def add(self, order: Order) -> None:
        if order.id in self._orders:
            raise DuplicateOrder(f"order {order.id} already exists", order_id=order.id)
        self._orders[order.id] = order

    def get(self, order_id: str) -> Order:
        try:
            return self._orders[order_id]
        except KeyError:
            raise OrderNotFound(f"no order {order_id}", order_id=order_id) from None

    # added with the order admin (2025): listing and lookups the admin pages need
    def __len__(self) -> int:
        return len(self._orders)

    def __contains__(self, order_id: str) -> bool:
        return order_id in self._orders

    def all(self) -> list[Order]:
        return list(self._orders.values())

    def remove(self, order_id: str) -> Order:
        """Only for orders that never got past pending (abandoned checkouts); see OrderService.purge_abandoned."""
        try:
            return self._orders.pop(order_id)
        except KeyError:
            raise OrderNotFound(f"no order {order_id}", order_id=order_id) from None


class OrderRejected(ShopError):
    http_status = 422


class OrderLocked(Conflict):
    """The order is past the point where its lines can change (paid or later)."""


@dataclass
class PlacedOrder:
    order: Order
    customer_id: str | None
    placed_at: datetime
    channel: str = "web"
    meta: dict = field(default_factory=dict)


@dataclass
class OrderService:
    """Order lifecycle around the plain Order value: placement, line edits before payment,
    payment, cancellation, notes. Status lives on the StatusBoard, events go to the bus."""
    book: OrderBook = field(default_factory=OrderBook)
    board: StatusBoard | None = None
    notes: NoteBook = field(default_factory=NoteBook)
    stock: Stock | None = None
    bus: EventBus = BUS
    placed: dict[str, PlacedOrder] = field(default_factory=dict)

    def __post_init__(self):
        if self.board is None:
            self.board = StatusBoard(self.bus)

    # ---- placement ----------------------------------------------------------------------------
    def validate_lines(self, lines: Iterable[OrderLine]) -> list[str]:
        """Problems with the lines, as messages for the API error body. Empty list = fine."""
        problems = []
        lines = list(lines)
        if not lines:
            problems.append("an order needs at least one line")
        if len(lines) > MAX_LINES_PER_ORDER:
            problems.append(f"at most {MAX_LINES_PER_ORDER} lines per order")
        seen = set()
        for line in lines:
            if line.qty < 1 or line.qty > MAX_QTY_PER_LINE:
                problems.append(f"{line.sku}: quantity must be 1-{MAX_QTY_PER_LINE}")
            if line.unit_cents < 0:
                problems.append(f"{line.sku}: negative price")
            if line.sku in seen:
                problems.append(f"{line.sku}: appears on more than one line")
            seen.add(line.sku)
            if self.stock is not None and self.stock.available(line.sku) < line.qty:
                problems.append(f"{line.sku}: only {self.stock.available(line.sku)} in stock")
        return problems

    def place(self, lines: list[OrderLine], *, customer_id: str | None = None, currency: str = "USD",
              channel: str = "web", order_id: str | None = None, at: datetime | None = None) -> Order:
        problems = self.validate_lines(lines)
        if problems:
            raise OrderRejected("order rejected: " + "; ".join(problems), problems=problems)
        order = Order(id=order_id or new_id("ord"), lines=list(lines), currency=currency)
        self.book.add(order)
        self.board.open(order.id)
        self.placed[order.id] = PlacedOrder(order, customer_id, at or utc_now(), channel)
        self.bus.publish("order.placed", order_id=order.id, customer_id=customer_id,
                         total_cents=order.total_cents, currency=currency, channel=channel)
        log.info("order %s placed (%d lines, %d cents, %s)", order.id, len(lines), order.total_cents, channel)
        return order

    # ---- changes before payment -----------------------------------------------------------------
    def _editable(self, order_id: str) -> Order:
        order = self.book.get(order_id)
        if self.board.status(order_id) != PENDING:
            raise OrderLocked(f"order {order_id} can no longer be changed", order_id=order_id,
                              status=self.board.status(order_id))
        return order

    def change_qty(self, order_id: str, sku: str, qty: int) -> Order:
        """Set a line's quantity (0 removes the line). Only before payment."""
        order = self._editable(order_id)
        for i, line in enumerate(order.lines):
            if line.sku == sku:
                if qty == 0:
                    del order.lines[i]
                else:
                    problems = self.validate_lines([OrderLine(sku, qty, line.unit_cents)])
                    if problems:
                        raise OrderRejected("; ".join(problems), problems=problems)
                    order.lines[i] = OrderLine(sku, qty, line.unit_cents)
                self.bus.publish("order.changed", order_id=order_id, sku=sku, qty=qty)
                return order
        raise OrderRejected(f"{sku} is not on order {order_id}", order_id=order_id, sku=sku)

    # ---- payment and cancellation ---------------------------------------------------------------
    def mark_paid(self, order_id: str, payment_id: str) -> None:
        self.board.move(order_id, PAID)
        self.placed[order_id].meta["payment_id"] = payment_id

    def cancel(self, order_id: str, *, actor: str, reason: str) -> None:
        """Cancel an open order. A paid order is refunded by the payments subscriber listening for
        order.cancelled (payments.refunds), not here."""
        status = self.board.status(order_id)
        if not is_open(status):
            raise OrderLocked(f"order {order_id} is {status}", order_id=order_id, status=status)
        self.board.move(order_id, CANCELLED)
        self.notes.add(order_id, actor, f"Cancelled: {reason}")
        self.bus.publish("order.cancel_requested", order_id=order_id, actor=actor, reason=reason,
                         was_paid=status != PENDING)

    def purge_abandoned(self, older_than: datetime) -> list[str]:
        """Remove pending orders placed before `older_than` (abandoned checkouts). Returns their ids."""
        gone = [oid for oid, p in self.placed.items()
                if p.placed_at < older_than and self.board.status(oid) == PENDING]
        for oid in gone:
            self.book.remove(oid)
            del self.placed[oid]
        if gone:
            log.info("purged %d abandoned orders", len(gone))
        return gone

    # ---- queries ------------------------------------------------------------------------------------
    def for_customer(self, customer_id: str) -> list[Order]:
        return [p.order for p in sorted(self.placed.values(), key=lambda p: p.placed_at, reverse=True)
                if p.customer_id == customer_id]

    def search(self, *, status: str | None = None, channel: str | None = None, sku: str | None = None,
               min_total_cents: int | None = None) -> list[Order]:
        out = []
        for oid, p in self.placed.items():
            if status and self.board.status(oid) != status:
                continue
            if channel and p.channel != channel:
                continue
            if sku and not any(l.sku == sku for l in p.order.lines):
                continue
            if min_total_cents is not None and p.order.total_cents < min_total_cents:
                continue
            out.append(p.order)
        return out
