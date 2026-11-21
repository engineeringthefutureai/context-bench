from reuse_spy import spy

from shopkit.customers.models import Address
from shopkit.orders.fulfillment import Parcel
from shopkit.shipping.labels import label_text


def test_uses_existing_helper(monkeypatch):
    calls = spy(monkeypatch, "shopkit.shipping.packaging.format_weight")
    us = Address("Ann Lee", "1 Main St", "Austin", "78701", "US", region="TX")
    label_text(Parcel("o1", 1, [("ab-1", 2)]), us, 1, weight_g=1500)
    assert calls, "weight not formatted with the shop's existing helper"
