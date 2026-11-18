from shopkit.orders.export import export_orders_csv
from shopkit.orders.models import Order, OrderLine


def _rows():
    orders = [
        Order(id="o1", lines=[OrderLine(sku="ab 12c", qty=3, unit_cents=123456)]),
        Order(id="o2", lines=[OrderLine(sku="x-9", qty=1, unit_cents=5), OrderLine(sku="Kz/77", qty=2, unit_cents=1000)]),
    ]
    return export_orders_csv(orders).strip().splitlines()


def test_header():
    assert _rows()[0] == "order_id,product_code,quantity,line_total"


def test_values_match_invoice_formatting():
    rows = _rows()
    assert rows[1] == 'o1,AB-12-C,3,"$3,703.68"'
    assert rows[2] == "o2,X-9,1,$0.05"
    assert rows[3] == "o2,KZ-77,2,$20.00"
