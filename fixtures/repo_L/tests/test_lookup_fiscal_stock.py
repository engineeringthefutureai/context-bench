from datetime import date, datetime

import pytest

from shopkit.errors import OrderNotFound
from shopkit.events import EventBus
from shopkit.inventory.erp_import import apply_stock_file
from shopkit.orders.lookup import OrderLookup
from shopkit.orders.models import Order
from shopkit.orders.service import OrderBook
from shopkit.reports.fiscal import period_dates, period_of, revenue_by_fiscal_week
from shopkit.reports.sales import Sale


def test_lookup_by_storefront_sales_order_and_invoice_refs():
    book = OrderBook()
    book.add(Order("ord_0123456789ab"))
    look = OrderLookup(book, so_refs={"SO-2019-000123": "ord_0123456789ab"}, invoice_orders={"INV-2026-0001": "ord_0123456789ab"})
    assert look.find(" ORD_0123456789AB ").kind == "storefront"
    so = look.find("so 2019 123")
    assert so.normalized == "SO-2019-000123" and so.order is not None
    assert look.find("inv-2026-0001").kind == "invoice"
    assert look.find("SO-2020-000001").order is None
    with pytest.raises(OrderNotFound):
        look.find("hello")


def test_fiscal_reporting_uses_finance_calendar():
    sales = [Sale(Order("a"), datetime(2026, 1, 31, 12)), Sale(Order("b"), datetime(2026, 2, 1, 9))]
    assert list(revenue_by_fiscal_week(sales)) == ["FY2025-W53", "FY2026-W01"]
    assert period_of(date(2026, 2, 1)) == (2026, 1) and period_dates(2026, 1) == (date(2026, 2, 1), date(2026, 2, 28))


def test_stock_file_import_sets_levels():
    from shopkit.inventory.stock import Stock
    stock = {"MAIN": Stock()}
    stock["MAIN"].receive("MUG-1", 2)
    text = "MUG000000001" + "01" + "       10" + "        3" + "\nEOF 1\n"
    res = apply_stock_file(text, stock, ["MUG-1"], bus=EventBus())
    assert stock["MAIN"].available("MUG-1") == 7 and res.changed == {("MAIN", "MUG-1"): (2, 7)}
