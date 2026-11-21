from shopkit.api import serializers
from shopkit.invoices.render import render_invoice_text
from shopkit.orders.fulfillment import packing_slip, split_into_parcels
from shopkit.orders.models import Order, OrderLine

MESSAGE = "Happy birthday, Sam!"


def _order(qty=3, message=MESSAGE):
    return Order("o1", [OrderLine("MUG", qty, 1299)], gift_message=message)


def test_message_on_first_parcel_only():
    first, second = split_into_parcels(_order(qty=12))
    assert MESSAGE in packing_slip(first, 900)
    assert MESSAGE not in packing_slip(second, 300)


def test_no_message_no_extra_line():
    slip = packing_slip(split_into_parcels(_order(message=""))[0], 900)
    assert slip.count("\n") == 2                      # header, weight, one item line


def test_api_includes_message():
    assert serializers.order(_order())["gift_message"] == MESSAGE


def test_invoice_does_not_show_it():
    assert MESSAGE not in render_invoice_text(_order())
