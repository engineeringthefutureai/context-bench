from shopkit.catalog.models import Product
from shopkit.customers.wishlist import Wishlist


def test_wishlist_price_drops_and_dedupe():
    mug = Product("MUG-1", "Mug", 1200)
    w = Wishlist("c1")
    w.add(mug)
    w.add(mug)
    assert len(w.items) == 1
    assert w.price_drops({"MUG-1": Product("MUG-1", "Mug", 900)}) == [("MUG-1", 1200, 900)]
    assert w.back_in_stock({"MUG-1": 3}, {"MUG-1"}) == ["MUG-1"]
    w.remove("MUG-1")
    assert w.items == []
