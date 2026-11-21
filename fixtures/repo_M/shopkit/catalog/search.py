"""Naive storefront search over titles and SKUs. Good enough for a few thousand products."""
import re

from shopkit.catalog.models import Product
from shopkit.utils.text import normalize_sku


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def search(products: list[Product], query: str, limit: int = 20) -> list[Product]:
    """Exact SKU match first, then products whose title contains every query word, ranked by how
    early the first word appears. Inactive products are never returned."""
    query = query.strip()
    if not query:
        return []
    active = [p for p in products if p.active]
    sku = normalize_sku(query)
    exact = [p for p in active if normalize_sku(p.sku) == sku]
    words = _tokens(query)
    matches = [p for p in active if p not in exact and words <= _tokens(p.title)]
    first_word = sorted(words)[0] if words else ""
    matches.sort(key=lambda p: (p.title.lower().find(first_word), p.title))
    return (exact + matches)[:limit]


def suggest(products: list[Product], prefix: str, limit: int = 5) -> list[str]:
    prefix = prefix.lower().strip()
    titles = sorted({p.title for p in products if p.active and p.title.lower().startswith(prefix)})
    return titles[:limit]
