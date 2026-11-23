from datetime import datetime, timedelta

import pytest

from shopkit.api.auth import ApiKey, hash_secret
from shopkit.api.routes import AdminApp
from shopkit.catalog.models import Product
from shopkit.customers.service import CustomerDirectory
from shopkit.events import EventBus
from shopkit.inventory.stock import Stock
from shopkit.orders.models import OrderLine
from shopkit.orders.service import OrderBook, OrderLocked, OrderRejected, OrderService


def _service(**kw):
    return OrderService(bus=EventBus(), **kw)


def test_place_validates_lines_and_publishes():
    stock = Stock()
    stock.receive("MUG", 5)
    svc = _service(stock=stock)
    order = svc.place([OrderLine("MUG", 2, 1200)], customer_id="C1", order_id="O1")
    assert svc.board.status("O1") == "pending" and svc.bus.history[-1].name == "order.placed"
    with pytest.raises(OrderRejected) as e:
        svc.place([OrderLine("MUG", 9, 1200), OrderLine("MUG", 1, 1200)])
    assert any("only 5 in stock" in p for p in e.value.details["problems"])
    assert any("more than one line" in p for p in e.value.details["problems"])
    assert stock.available("MUG") == 5                     # placing doesn't touch stock


def test_lines_change_only_before_payment():
    svc = _service()
    svc.place([OrderLine("MUG", 2, 1200), OrderLine("TEA", 1, 500)], order_id="O1")
    svc.change_qty("O1", "MUG", 3)
    svc.change_qty("O1", "TEA", 0)
    assert [(l.sku, l.qty) for l in svc.book.get("O1").lines] == [("MUG", 3)]
    svc.mark_paid("O1", "pay_1")
    with pytest.raises(OrderLocked):
        svc.change_qty("O1", "MUG", 1)


def test_cancel_and_purge_abandoned():
    svc = _service()
    old = datetime(2026, 3, 1)
    svc.place([OrderLine("MUG", 1, 100)], order_id="O1", at=old)
    svc.place([OrderLine("MUG", 1, 100)], order_id="O2", at=old)
    svc.mark_paid("O2", "pay")
    assert svc.purge_abandoned(old + timedelta(days=1)) == ["O1"] and "O1" not in svc.book
    svc.cancel("O2", actor="ann", reason="customer asked")
    assert svc.board.status("O2") == "cancelled" and svc.bus.history[-1].payload["was_paid"]
    with pytest.raises(OrderLocked):
        svc.cancel("O2", actor="ann", reason="again")


def test_customer_merge_erase_and_tiers():
    d = CustomerDirectory(bus=EventBus())
    a = d.register("ann@example.com", "Ann", marketing_opt_in=True)
    b = d.register("ann.lee@example.com", "Ann Lee", marketing_opt_in=False)
    b.tags.add("vip")
    kept = d.merge(a.id, b.id)
    assert d.get(b.id) is kept and "vip" in kept.tags and not kept.marketing_opt_in
    assert d.recalculate_tier(a.id, 60_000) == "silver" and d.recalculate_tier(a.id, 0) == "silver"
    assert d.review_tiers({}) == {a.id: "standard"}
    assert '"email": "ann@example.com"' in d.export(a.id)
    erased = d.erase(a.id, datetime(2026, 3, 1).date())
    assert erased.email.endswith("@erased.invalid") and erased.addresses == []


def test_admin_cancel_requires_staff_and_reason():
    book = OrderBook()
    svc = _service(book=book)
    svc.place([OrderLine("MUG", 1, 1200)], order_id="O1")
    app = AdminApp(book, CustomerDirectory(), [Product("MUG", "Mug", 1200)], service=svc)
    viewer = ApiKey("v1", hash_secret("x"), "viewer")
    staff = ApiKey("s1", hash_secret("y"), "staff")
    assert app.cancel_order(viewer, "O1", {"reason": "x"})[0] == 403
    assert app.cancel_order(staff, "O1", {})[0] == 400
    status, body = app.cancel_order(staff, "O1", {"reason": "duplicate"})
    assert status == 200 and body["result"]["status"] == "cancelled"
    code, html = app.order_page(viewer, "O1")
    assert code == 200 and "Order O1" in html and "(closed)" in html
    assert app.product_page("NOPE")[0] == 404
