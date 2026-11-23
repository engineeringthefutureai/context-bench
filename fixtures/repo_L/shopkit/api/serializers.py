"""Domain objects -> JSON-ready dicts for API responses. Amounts are sent as integer cents plus a
display string."""
from shopkit.catalog.models import Product
from shopkit.customers.models import Customer
from shopkit.money import format_money
from shopkit.orders.models import Order


def money(cents: int, currency: str = "USD") -> dict:
    return {"cents": cents, "display": format_money(cents, currency), "currency": currency}


def product(p: Product) -> dict:
    return {"sku": p.sku, "title": p.title, "price": money(p.price_cents), "active": p.active,
            "purchasable": p.is_purchasable()}


def order(o: Order, status: str | None = None) -> dict:
    return {
        "id": o.id,
        "status": status,
        "lines": [{"sku": l.sku, "qty": l.qty, "unit": money(l.unit_cents, o.currency),
                   "total": money(l.total_cents, o.currency)} for l in o.lines],
        "total": money(o.total_cents, o.currency),
    }


def customer(c: Customer) -> dict:
    return {"id": c.id, "email": c.email, "name": c.name, "tier": c.tier,
            "marketing_opt_in": c.marketing_opt_in}
