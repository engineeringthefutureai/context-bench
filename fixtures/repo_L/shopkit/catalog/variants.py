"""Product variants (size/colour). A variant's SKU is the parent SKU plus option codes."""
from dataclasses import dataclass, field

from shopkit.catalog.models import Product
from shopkit.utils.text import normalize_sku


@dataclass
class Variant:
    parent_sku: str
    options: dict[str, str] = field(default_factory=dict)     # e.g. {"size": "M", "color": "red"}
    price_delta_cents: int = 0

    @property
    def sku(self) -> str:
        codes = [v[:3] for _, v in sorted(self.options.items())]
        return normalize_sku(" ".join([self.parent_sku, *codes]))

    def label(self) -> str:
        return ", ".join(f"{k}: {v}" for k, v in sorted(self.options.items()))


def expand(product: Product, axes: dict[str, list[str]]) -> list[Variant]:
    """Every combination of option values, e.g. sizes x colours."""
    combos: list[dict[str, str]] = [{}]
    for axis, values in sorted(axes.items()):
        combos = [{**c, axis: v} for c in combos for v in values]
    return [Variant(product.sku, c) for c in combos]


def price_of(product: Product, variant: Variant) -> int:
    return product.price_cents + variant.price_delta_cents
