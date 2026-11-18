import json

from shopkit.invoices.render import render_invoice_text
from shopkit.orders.export import export_orders_json
from shopkit.orders.models import Order, OrderLine


def _order(pct):
    return Order(id="o9", lines=[OrderLine(sku="ab12", qty=3, unit_cents=333)], discount_percent=pct)


def test_total_is_discounted_and_rounded_down():
    assert _order(10).total_cents == 900       # 999 - floor(99.9)
    assert _order(0).total_cents == 999
    assert _order(100).total_cents == 0


def test_invoice_shows_discount_line_before_total():
    lines = render_invoice_text(_order(10)).splitlines()
    assert lines[-1].startswith("TOTAL") and lines[-1].endswith("$9.00")
    assert "discount" in lines[-2].lower() and "0.99" in lines[-2]


def test_undiscounted_invoice_unchanged():
    assert render_invoice_text(_order(0)) == (
        "Invoice o9\n"
        "AB-12              3 x        $3.33  =        $9.99\n"
        "TOTAL                                      $9.99"
    )


def test_json_export_includes_discount():
    assert json.loads(export_orders_json([_order(10)]))[0]["discount_percent"] == 10
