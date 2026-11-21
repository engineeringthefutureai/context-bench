from shopkit.customers.models import Address
from shopkit.orders.fulfillment import Parcel
from shopkit.shipping.labels import label_code, label_text


def test_label_code_is_scanner_friendly():
    assert label_code("ab-12 x") == "AB12X"


def test_label_text():
    to = Address("Ann Lee", "1 Main St", "Austin", "78701", "US", region="TX")
    text = label_text(Parcel("o1", 1, [("ab-1", 2)]), to, parcels_total=2)
    assert text.startswith("Ann Lee\n1 Main St\nAustin, TX 78701")
    assert "parcel 1/2" in text and text.endswith("AB1 x2")
