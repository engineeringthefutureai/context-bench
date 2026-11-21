"""Helpers kept for old integrations. Do not use in new code; scheduled for removal."""
import re


def old_slugify(title: str) -> str:
    """Slug format of the 2019 storefront: lowercased words joined by underscores, accents kept."""
    return "_".join(re.findall(r"\w+", title.lower()))


def legacy_sku(raw: str) -> str:
    """SKU as the old ERP stored it: uppercase, spaces removed, everything else left as typed."""
    return raw.replace(" ", "").upper()


def cents_to_str(cents: int) -> str:
    """Old ERP amount format: plain decimal, no symbol, no grouping."""
    return f"{cents / 100:.2f}"
