import json

from shopkit.orders.models import Order


def export_orders_json(orders: list[Order]) -> str:
    """Raw JSON export for the warehouse integration."""
    payload = [
        {
            "id": o.id,
            "currency": o.currency,
            "lines": [{"sku": l.sku, "qty": l.qty, "unit_cents": l.unit_cents} for l in o.lines],
        }
        for o in orders
    ]
    return json.dumps(payload, indent=2)
