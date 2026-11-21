from datetime import date

import pytest

from shopkit.shipping.carriers import estimated_delivery, tracking_link
from shopkit.shipping.packaging import BOXES, chargeable_weight_g, dims_weight_g, pick_box
from shopkit.shipping.rates import TooHeavy, shipping_cents
from shopkit.shipping.zones import DOMESTIC, EU, REST_OF_WORLD, zone_for_country


def test_zones():
    assert zone_for_country("us") == DOMESTIC
    assert zone_for_country("DE") == EU
    assert zone_for_country("JP") == REST_OF_WORLD
    assert zone_for_country("US", home="GB") == "north_america"


def test_dims_weight_rounds_up():
    assert dims_weight_g(20, 15, 10) == 600
    assert dims_weight_g(10, 10, 10.01) == 201      # 200.2 g rounds up


def test_chargeable_weight_and_box():
    small = BOXES[0]
    assert chargeable_weight_g(small, 100) == 600
    assert chargeable_weight_g(small, 900) == 1020
    assert pick_box(2000).name == "S"
    with pytest.raises(ValueError):
        pick_box(10**6)


def test_rates():
    assert shipping_cents("US", 400) == 495
    assert shipping_cents("US", 400, order_total_cents=20_000) == 0
    assert shipping_cents("FR", 1500) == 2495
    with pytest.raises(TooHeavy):
        shipping_cents("FR", 20_000)


def test_carriers():
    assert estimated_delivery("ups", "US", date(2026, 11, 20)) == date(2026, 11, 24)
    assert tracking_link("post", " ab12 ") == "https://track.example/post/AB12"
