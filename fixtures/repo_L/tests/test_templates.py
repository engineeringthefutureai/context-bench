import pytest

from shopkit.notifications.email import Outbox
from shopkit.notifications.templates import MissingValue, render_template


def test_render_template():
    assert render_template("Hi {{ name }}!", {"name": "Ann"}) == "Hi Ann!"
    assert render_template("{{customer.name}}", {"customer": {"name": "Bo"}}) == "Bo"
    with pytest.raises(MissingValue):
        render_template("{{ missing }}", {})


def test_outbox_send():
    outbox = Outbox()
    email = outbox.send("ann@example.com", "low_stock", sku="MUG-1", available=2)
    assert email.subject == "Low stock: MUG-1"
    assert outbox.drain() == [email] and outbox.queued == []
