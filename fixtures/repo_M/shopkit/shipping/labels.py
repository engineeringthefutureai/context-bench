"""Shipping label content handed to the label printer service."""
from shopkit.customers.addresses import format_address
from shopkit.customers.models import Address
from shopkit.orders.fulfillment import Parcel


def label_code(sku: str) -> str:
    """Compact code printed under the barcode: uppercase, spaces and dashes removed (scanner-friendly)."""
    return sku.replace(" ", "").replace("-", "").upper()


def label_text(parcel: Parcel, to: Address, parcels_total: int) -> str:
    lines = [format_address(to), "", f"Order {parcel.order_id}  parcel {parcel.number}/{parcels_total}"]
    lines += [f"{label_code(sku)} x{qty}" for sku, qty in parcel.items]
    return "\n".join(lines)
