"""Quarterly tax report: tax collected per country (and US state) for the accountants."""
from dataclasses import dataclass

from shopkit.customers.models import Address
from shopkit.orders.models import Order
from shopkit.reports.formatting import fmt_amount, pad_table
from shopkit.tax.calculator import order_tax


@dataclass(frozen=True)
class TaxedSale:
    order: Order
    ship_to: Address


def tax_by_jurisdiction(sales: list[TaxedSale]) -> dict[str, tuple[int, int]]:
    """{"DE": (net_cents, tax_cents), "US-CA": (...)}, sorted by jurisdiction."""
    out: dict[str, tuple[int, int]] = {}
    for sale in sales:
        a = sale.ship_to
        key = f"US-{a.region}" if a.country == "US" else a.country
        net, tax = out.get(key, (0, 0))
        out[key] = (net + sale.order.total_cents, tax + order_tax(sale.order, a))
    return dict(sorted(out.items()))


def tax_report(sales: list[TaxedSale]) -> str:
    rows = [["jurisdiction", "net", "tax"]]
    for key, (net, tax) in tax_by_jurisdiction(sales).items():
        rows.append([key, fmt_amount(net), fmt_amount(tax)])
    return pad_table(rows)
