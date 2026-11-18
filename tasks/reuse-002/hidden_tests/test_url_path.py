from shopkit.catalog.models import Product


def test_simple_title():
    assert Product("p1", "Blue Mug", 1200).url_path == "/products/blue-mug"


def test_accents_and_punctuation():
    p = Product("p2", "Café Crème — Deluxe 2.0!", 1500)
    assert p.url_path == "/products/cafe-creme-deluxe-2-0"


def test_is_read_only_property():
    assert isinstance(Product.__dict__["url_path"], property)
