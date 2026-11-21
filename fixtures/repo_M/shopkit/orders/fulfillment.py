"""Picking and packing. Builds pick lists for the warehouse and splits orders into parcels."""
from dataclasses import dataclass, field

from shopkit.orders.models import Order
from shopkit.shipping.packaging import format_weight
from shopkit.utils.collections import chunked, group_by

MAX_ITEMS_PER_PARCEL = 10


@dataclass
class Parcel:
    order_id: str
    number: int
    items: list[tuple[str, int]] = field(default_factory=list)    # (sku, qty)

    @property
    def item_count(self) -> int:
        return sum(q for _, q in self.items)


def pick_list(orders: list[Order]) -> list[tuple[str, int]]:
    """Total quantity per SKU across orders, sorted by SKU (the order pickers walk the aisles)."""
    by_sku = group_by((line for o in orders for line in o.lines), key=lambda l: l.sku)
    return sorted((sku, sum(l.qty for l in lines)) for sku, lines in by_sku.items())


def split_into_parcels(order: Order, max_items: int = MAX_ITEMS_PER_PARCEL) -> list[Parcel]:
    """One unit per slot, at most `max_items` units per parcel, lines kept together where possible."""
    units = [line.sku for line in order.lines for _ in range(line.qty)]
    parcels = []
    for n, batch in enumerate(chunked(units, max_items), start=1):
        counts = group_by(batch, key=lambda s: s)
        parcels.append(Parcel(order.id, n, [(sku, len(v)) for sku, v in counts.items()]))
    return parcels


def packing_slip(parcel: Parcel, weight_g: int) -> str:
    """Slip packed inside each parcel: order, parcel number, contents and weight."""
    lines = [f"Order {parcel.order_id} - parcel {parcel.number}", f"Weight: {format_weight(weight_g)}"]
    lines += [f"  {qty} x {sku}" for sku, qty in parcel.items]
    return "\n".join(lines)
