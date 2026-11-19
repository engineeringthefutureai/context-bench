from reuse_spy import spy

from shopkit.catalog.models import Product


def test_uses_existing_helper(monkeypatch):
    calls = spy(monkeypatch, "shopkit.utils.text.slug_for_title")
    Product("p1", "Blue Mug", 1200).url_path
    assert calls, "slug not built with the shop's existing helper"
