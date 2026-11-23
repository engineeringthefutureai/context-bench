"""Persistence for the main aggregates. Services take a repository, so tests can pass the in-memory
`OrderBook` (orders.service) or these SQLite-backed ones interchangeably.

Money is integer cents throughout; timestamps are ISO strings (UTC).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from shopkit.catalog.models import Product
from shopkit.customers.models import Customer
from shopkit.db.connection import Database
from shopkit.db.query import Page, Select, insert, update
from shopkit.errors import DuplicateOrder, NotFound, OrderNotFound
from shopkit.orders.models import Order, OrderLine
from shopkit.utils.dates import utc_now


class ProductNotFound(NotFound):
    pass


class CustomerNotFound(NotFound):
    pass


class ProductRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, product: Product) -> None:
        self.db.execute(*insert("products", {"sku": product.sku, "title": product.title,
                                             "price_cents": product.price_cents,
                                             "active": int(product.active)}))

    def get(self, sku: str) -> Product:
        row = self.db.query_one("SELECT sku, title, price_cents, active FROM products WHERE sku = ?", (sku,))
        if row is None:
            raise ProductNotFound(f"no product {sku}", sku=sku)
        return Product(sku=row["sku"], title=row["title"], price_cents=row["price_cents"],
                       active=bool(row["active"]))

    def set_price(self, sku: str, price_cents: int) -> None:
        if price_cents < 0:
            raise ValueError("price must be >= 0")
        cur = self.db.execute(*update("products", {"price_cents": price_cents}, "sku = ?", sku))
        if cur.rowcount == 0:
            raise ProductNotFound(f"no product {sku}", sku=sku)

    def active(self, category: str | None = None, limit: int = 200) -> list[Product]:
        q = Select("products").select("sku", "title", "price_cents", "active").where("active = ?", 1)
        if category:
            q = q.where("category = ?", category)
        rows = self.db.query(*q.order_by("sku").limit(limit).build())
        return [Product(r["sku"], r["title"], r["price_cents"], bool(r["active"])) for r in rows]

    def by_skus(self, skus: list[str]) -> dict[str, Product]:
        rows = self.db.query(*Select("products").where_in("sku", skus).build())
        return {r["sku"]: Product(r["sku"], r["title"], r["price_cents"], bool(r["active"])) for r in rows}


class CustomerRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, customer: Customer, created_at: datetime | None = None) -> None:
        self.db.execute(*insert("customers", {
            "id": customer.id, "email": customer.email.strip().lower(), "name": customer.name,
            "created_at": (created_at or utc_now()).isoformat()}))

    def get(self, customer_id: str) -> Customer:
        row = self.db.query_one("SELECT * FROM customers WHERE id = ?", (customer_id,))
        if row is None:
            raise CustomerNotFound(f"no customer {customer_id}", customer_id=customer_id)
        return _customer(row)

    def find_by_email(self, email: str) -> Customer | None:
        row = self.db.query_one("SELECT * FROM customers WHERE email = ?", (email.strip().lower(),))
        return _customer(row) if row else None

    def set_tier(self, customer_id: str, tier: str) -> None:
        self.db.execute(*update("customers", {"tier": tier}, "id = ?", customer_id))


def _customer(row) -> Customer:
    keys = row.keys()
    return Customer(id=row["id"], email=row["email"], name=row["name"],
                    tier=row["tier"] if "tier" in keys else "standard")


@dataclass
class OrderRow:
    """What the order list pages and reports read: the stored header without lines."""
    id: str
    customer_id: str | None
    currency: str
    status: str
    total_cents: int
    created_at: str
    channel: str = "web"


class OrderRepository:
    """Orders with their lines. `add` stores the totals computed at checkout (migration 0011)."""

    def __init__(self, db: Database):
        self.db = db

    def add(self, order: Order, customer_id: str | None = None, status: str = "new",
            created_at: datetime | None = None, channel: str = "web") -> None:
        if self.db.scalar("SELECT 1 FROM orders WHERE id = ?", (order.id,)):
            raise DuplicateOrder(f"order {order.id} already exists", order_id=order.id)
        with self.db.transaction():
            self.db.execute(*insert("orders", {
                "id": order.id, "customer_id": customer_id, "currency": order.currency, "status": status,
                "created_at": (created_at or utc_now()).isoformat(), "subtotal_cents": order.total_cents,
                "total_cents": order.total_cents, "channel": channel}))
            for no, line in enumerate(order.lines, 1):
                self.db.execute(*insert("order_lines", {"order_id": order.id, "line_no": no, "sku": line.sku,
                                                        "qty": line.qty, "unit_cents": line.unit_cents}))
            self._history(order.id, status)

    def get(self, order_id: str) -> Order:
        head = self.db.query_one("SELECT id, currency FROM orders WHERE id = ?", (order_id,))
        if head is None:
            raise OrderNotFound(f"no order {order_id}", order_id=order_id)
        lines = self.db.query("SELECT sku, qty, unit_cents FROM order_lines WHERE order_id = ? ORDER BY line_no",
                              (order_id,))
        return Order(id=head["id"], currency=head["currency"],
                     lines=[OrderLine(r["sku"], r["qty"], r["unit_cents"]) for r in lines])

    def status(self, order_id: str) -> str:
        status = self.db.scalar("SELECT status FROM orders WHERE id = ?", (order_id,))
        if status is None:
            raise OrderNotFound(f"no order {order_id}", order_id=order_id)
        return status

    def set_status(self, order_id: str, status: str, actor: str = "system") -> None:
        with self.db.transaction():
            cur = self.db.execute(*update("orders", {"status": status}, "id = ?", order_id))
            if cur.rowcount == 0:
                raise OrderNotFound(f"no order {order_id}", order_id=order_id)
            self._history(order_id, status, actor)

    def history(self, order_id: str) -> list[tuple[str, str, str]]:
        rows = self.db.query("SELECT status, at, actor FROM order_status_history WHERE order_id = ? ORDER BY rowid",
                             (order_id,))
        return [(r["status"], r["at"], r["actor"]) for r in rows]

    def _history(self, order_id: str, status: str, actor: str = "system") -> None:
        self.db.execute(*insert("order_status_history", {"order_id": order_id, "status": status,
                                                         "at": utc_now().isoformat(), "actor": actor}))

    def page(self, *, status: str | None = None, customer_id: str | None = None,
             offset: int = 0, limit: int = 50) -> Page:
        q = Select("orders").select("id", "customer_id", "currency", "status", "total_cents", "created_at", "channel")
        if status:
            q = q.where("status = ?", status)
        if customer_id:
            q = q.where("customer_id = ?", customer_id)
        total = self.db.scalar(*q.count())
        rows = self.db.query(*q.order_by("created_at", desc=True).order_by("id").limit(limit, offset).build())
        return Page(rows=[OrderRow(**dict(r)) for r in rows], total=total, offset=offset, limit=limit)


class AuditRepository:
    """Durable copy of the audit log (shopkit.audit_log keeps the in-process list)."""

    def __init__(self, db: Database):
        self.db = db

    def record(self, actor: str, action: str, subject: str, **details) -> None:
        self.db.execute(*insert("audit_entries", {"actor": actor, "action": action, "subject": subject,
                                                  "details": json.dumps(details, sort_keys=True, default=str),
                                                  "at": utc_now().isoformat()}))

    def for_subject(self, subject: str) -> list[dict]:
        rows = self.db.query("SELECT actor, action, details, at FROM audit_entries WHERE subject = ? ORDER BY id",
                             (subject,))
        return [{"actor": r["actor"], "action": r["action"], "details": json.loads(r["details"]), "at": r["at"]}
                for r in rows]
