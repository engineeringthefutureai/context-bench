from shopkit.customers.models import Address
from shopkit.orders.fulfillment import Parcel
from shopkit.shipping.labels import label_text

US = Address("Ann Lee", "1 Main St", "Austin", "78701", "US", region="TX")
GB = Address("Bo Chen", "10 Downing St", "London", "SW1A 2AA", "GB")
PARCEL = Parcel("o1", 1, [("ab-1", 2)])


def _after_order_line(text: str) -> str:
    lines = text.splitlines()
    return lines[lines.index("Order o1  parcel 1/1") + 1]


def test_us_label_shows_pounds():
    assert _after_order_line(label_text(PARCEL, US, 1, weight_g=1500)) == "Weight: 3.31 lb"


def test_pounds_use_the_exact_conversion():
    # 10 kg = 22.05 lb; the old 2.2 factor would print 22.00
    assert _after_order_line(label_text(PARCEL, US, 1, weight_g=10000)) == "Weight: 22.05 lb"


def test_other_destinations_show_kilograms():
    assert _after_order_line(label_text(PARCEL, GB, 1, weight_g=1500)) == "Weight: 1.5 kg"
    assert _after_order_line(label_text(PARCEL, GB, 1, weight_g=1000)) == "Weight: 1 kg"


def test_weight_is_optional():
    text = label_text(PARCEL, US, 1)
    assert "Weight" not in text and text.endswith("AB1 x2")
