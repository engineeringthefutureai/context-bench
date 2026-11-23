from shopkit.money import format_money
from shopkit.orders.models import Order
from shopkit.utils.text import normalize_sku


def render_invoice_text(order: Order) -> str:
    """Plain-text invoice sent to customers by email."""
    rows = [f"Invoice {order.id}"]
    for line in order.lines:
        rows.append(f"{normalize_sku(line.sku):<16}{line.qty:>4} x {format_money(line.unit_cents, order.currency):>12}"
                    f"  = {format_money(line.total_cents, order.currency):>12}")
    rows.append(f"{'TOTAL':<36}{format_money(order.total_cents, order.currency):>12}")
    return "\n".join(rows)
