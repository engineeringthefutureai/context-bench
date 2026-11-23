"""Server-rendered pages: each view builds a context and renders a template from `templates/`.

Views take plain domain objects (no request object), so the HTTP layer (`shopkit.api.routes`) and
the tests call them the same way. Money is formatted in templates with the `money` filter.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shopkit.catalog.models import Product
from shopkit.config import SETTINGS, Settings
from shopkit.customers.addresses import format_address
from shopkit.customers.models import Customer
from shopkit.orders.models import Order
from shopkit.orders.status import TERMINAL
from shopkit.web.templating import Environment, default_env

STATUS_LABELS = {
    "pending": "Awaiting payment", "paid": "Paid", "packed": "Being packed", "shipped": "On its way",
    "delivered": "Delivered", "cancelled": "Cancelled", "refunded": "Refunded", "new": "Received",
}


@dataclass
class Breadcrumb:
    label: str
    href: str = ""


@dataclass
class PageMeta:
    title: str
    breadcrumbs: list[Breadcrumb] = field(default_factory=list)
    admin: bool = False


def _env(env: Environment | None) -> Environment:
    return env or default_env()


def _base(meta: PageMeta, settings: Settings) -> dict:
    return {"meta": meta, "settings": settings, "support_email": settings.support_email}


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status.replace("_", " ").capitalize())


def order_lines_context(order: Order, products: dict[str, Product] | None = None) -> list[dict]:
    products = products or {}
    rows = []
    for line in order.lines:
        product = products.get(line.sku)
        rows.append({"sku": line.sku, "title": product.title if product else line.sku, "qty": line.qty,
                     "unit_cents": line.unit_cents, "total_cents": line.total_cents})
    return rows


def order_detail(order: Order, *, status: str, history: list[tuple[str, str, str]] | None = None,
                 products: dict[str, Product] | None = None, customer: Customer | None = None,
                 tracking: list[dict] | None = None, admin: bool = False,
                 settings: Settings = SETTINGS, env: Environment | None = None) -> str:
    """The order page: lines, totals, status timeline and tracking. Admins also see the audit timeline."""
    meta = PageMeta(f"Order {order.id}", [Breadcrumb("Orders", "/orders"), Breadcrumb(order.id)], admin)
    ctx = {
        **_base(meta, settings),
        "order": order,
        "lines": order_lines_context(order, products),
        "status": status,
        "status_label": status_label(status),
        "is_final": status in TERMINAL,
        "history": [{"status": s, "label": status_label(s), "at": at, "actor": actor}
                    for s, at, actor in (history or [])],
        "customer": customer,
        "ship_to": format_address(customer.default_address) if customer and customer.default_address else "",
        "tracking": tracking or [],
        "free_shipping": order.total_cents >= settings.free_shipping_over_cents,
    }
    return _env(env).render("orders/detail.html", ctx)


def order_list(rows: list, *, total: int, offset: int = 0, limit: int = 50, status: str | None = None,
               settings: Settings = SETTINGS, env: Environment | None = None) -> str:
    meta = PageMeta("Orders", [Breadcrumb("Orders")])
    ctx = {**_base(meta, settings),
           "rows": [{"id": r.id, "status": status_label(r.status), "total_cents": r.total_cents,
                     "currency": r.currency, "created_at": r.created_at, "channel": r.channel} for r in rows],
           "total": total, "offset": offset, "limit": limit, "status": status,
           "prev_offset": max(offset - limit, 0) if offset else None,
           "next_offset": offset + limit if offset + len(rows) < total else None}
    return _env(env).render("orders/list.html", ctx)


def product_page(product: Product, *, unit_cents: int | None = None, in_stock: int = 0,
                 reviews: list[dict] | None = None, settings: Settings = SETTINGS,
                 env: Environment | None = None) -> str:
    """Product page. `unit_cents` is the customer's price (price lists), else the list price."""
    meta = PageMeta(product.title, [Breadcrumb("Shop", "/products"), Breadcrumb(product.title)])
    price = product.price_cents if unit_cents is None else unit_cents
    ctx = {**_base(meta, settings), "product": product, "price_cents": price,
           "was_cents": product.price_cents if price < product.price_cents else None,
           "in_stock": in_stock, "low_stock": 0 < in_stock <= settings.low_stock_threshold,
           "purchasable": product.is_purchasable() and in_stock > 0, "reviews": reviews or [],
           "currency": settings.default_currency}
    return _env(env).render("products/detail.html", ctx)


def account_page(customer: Customer, *, recent_orders: list | None = None, points: int = 0,
                 settings: Settings = SETTINGS, env: Environment | None = None) -> str:
    meta = PageMeta("Your account", [Breadcrumb("Account")])
    ctx = {**_base(meta, settings), "customer": customer, "points": points,
           "addresses": [format_address(a) for a in customer.addresses],
           "orders": [{"id": r.id, "status": status_label(r.status), "total_cents": r.total_cents,
                       "currency": r.currency, "created_at": r.created_at} for r in (recent_orders or [])]}
    return _env(env).render("customers/account.html", ctx)


def invoice_page(number: str, order: Order, *, bill_to: str, issued: str,
                 products: dict[str, Product] | None = None, settings: Settings = SETTINGS,
                 env: Environment | None = None) -> str:
    meta = PageMeta(f"Invoice {number}", [Breadcrumb("Invoices", "/invoices"), Breadcrumb(number)])
    ctx = {**_base(meta, settings), "number": number, "order": order, "bill_to": bill_to, "issued": issued,
           "lines": order_lines_context(order, products)}
    return _env(env).render("invoices/invoice.html", ctx)


def error_page(status: int, message: str, *, settings: Settings = SETTINGS, env: Environment | None = None) -> str:
    meta = PageMeta({404: "Not found", 409: "Conflict"}.get(status, "Something went wrong"))
    return _env(env).render("errors/error.html", {**_base(meta, settings), "status": status, "message": message})
