import re
import unicodedata


def normalize_sku(raw: str) -> str:
    """Canonical product code: uppercase, separators removed, dash between letter and digit groups."""
    s = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    return "-".join(re.findall(r"[A-Z]+|\d+", s))


def slug_for_title(title: str) -> str:
    """URL slug for a product or campaign title."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_title.lower())
    return "-".join(words)
