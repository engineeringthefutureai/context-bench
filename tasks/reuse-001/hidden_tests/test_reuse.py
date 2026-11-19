from reuse_spy import spy

from shopkit.orders import export
from shopkit.orders.models import Order, OrderLine


def test_uses_existing_helpers(monkeypatch):
    sku_calls = spy(monkeypatch, "shopkit.utils.text.normalize_sku")
    money_calls = spy(monkeypatch, "shopkit.money.format_money")
    export.export_orders_csv([Order(id="o1", lines=[OrderLine(sku="ab1", qty=1, unit_cents=100)])])
    assert sku_calls, "product codes not cleaned with the shop's existing helper"
    assert money_calls, "line totals not formatted with the shop's existing helper"
