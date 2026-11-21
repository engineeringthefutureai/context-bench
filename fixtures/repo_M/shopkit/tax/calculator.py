"""Tax on orders. Prices are tax-exclusive; tax is computed per line and summed."""
from shopkit.customers.models import Address
from shopkit.orders.models import Order, OrderLine
from shopkit.tax.rates import STANDARD, rate_for
from shopkit.utils.numbers import apply_rate


def tax_for(amount_cents: int, country: str, region: str = "", tax_class: str = STANDARD) -> int:
    """Tax on a tax-exclusive amount for a destination, rounded half-up to the cent."""
    return apply_rate(amount_cents, rate_for(country, region, tax_class))


def line_tax(line: OrderLine, address: Address, tax_classes: dict[str, str] | None = None) -> int:
    tax_class = (tax_classes or {}).get(line.sku, STANDARD)
    return tax_for(line.total_cents, address.country, address.region, tax_class)


def order_tax(order: Order, address: Address, tax_classes: dict[str, str] | None = None) -> int:
    """Sum of per-line tax (rounding per line, as the invoice shows it)."""
    return sum(line_tax(line, address, tax_classes) for line in order.lines)


def gross_total(order: Order, address: Address, tax_classes: dict[str, str] | None = None) -> int:
    return order.total_cents + order_tax(order, address, tax_classes)
