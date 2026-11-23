import pytest

from shopkit.api import serializers
from shopkit.db import connect
from shopkit.db.repositories import OrderRepository
from shopkit.orders.models import Order, OrderLine
from shopkit.web import views


def test_column_exists_and_round_trips():
    db = connect()
    assert "delivery_note" in {r["name"] for r in db.query("PRAGMA table_info(orders)")}
    repo = OrderRepository(db)
    repo.add(Order("O1", [OrderLine("MUG", 1, 1200)], delivery_note="Leave at the door"))
    repo.add(Order("O2", [OrderLine("MUG", 1, 1200)]))
    assert repo.get("O1").delivery_note == "Leave at the door" and repo.get("O2").delivery_note == ""


def test_long_notes_are_rejected():
    repo = OrderRepository(connect())
    with pytest.raises(ValueError):
        repo.add(Order("O1", [OrderLine("MUG", 1, 1200)], delivery_note="x" * 201))
    repo.add(Order("O2", [OrderLine("MUG", 1, 1200)], delivery_note="x" * 200))


def test_page_and_api_show_the_note():
    order = Order("O1", [OrderLine("MUG", 1, 1200)], delivery_note="Ring <twice> & wait")
    html = views.order_detail(order, status="paid")
    assert "Delivery note" in html and "Ring &lt;twice&gt; &amp; wait" in html
    assert "Delivery note" not in views.order_detail(Order("O2", [OrderLine("MUG", 1, 1200)]), status="paid")
    assert serializers.order(order)["delivery_note"] == "Ring <twice> & wait"
