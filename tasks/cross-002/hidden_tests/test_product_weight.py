from shopkit.api import serializers
from shopkit.catalog.models import Product
from shopkit.orders.fulfillment import Parcel, parcel_weight_g

MUG = Product("MUG", "Mug", 1299, weight_g=350)
TEE = Product("TEE", "T-shirt", 1999, weight_g=200)


def test_weight_defaults_to_zero():
    assert Product("X", "Thing", 100).weight_g == 0


def test_parcel_weight():
    parcel = Parcel("o1", 1, [("MUG", 2), ("TEE", 1)])
    assert parcel_weight_g(parcel, {"MUG": MUG, "TEE": TEE}) == 900


def test_api_includes_weight():
    body = serializers.product(MUG)
    assert body["weight_g"] == 350 and body["sku"] == "MUG" and body["price"]["cents"] == 1299
