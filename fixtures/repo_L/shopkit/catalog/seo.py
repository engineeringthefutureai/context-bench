"""Meta tags for product pages."""
from shopkit.catalog.models import Product
from shopkit.money import format_money

MAX_DESCRIPTION = 155


def meta_title(product: Product, store_name: str = "Shopkit") -> str:
    return f"{product.title} | {store_name}"


def meta_description(product: Product, blurb: str = "") -> str:
    """Search-snippet text, cut at a word boundary to MAX_DESCRIPTION characters."""
    text = f"{product.title} for {format_money(product.price_cents)}. {blurb}".strip()
    if len(text) <= MAX_DESCRIPTION:
        return text
    cut = text[:MAX_DESCRIPTION - 1].rsplit(" ", 1)[0]
    return cut + "…"


def keywords(product: Product) -> list[str]:
    words = [w.lower() for w in product.title.split() if len(w) > 2]
    return list(dict.fromkeys(words))
