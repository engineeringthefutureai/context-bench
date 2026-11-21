"""String and amount helpers for the partner integrations."""
import re


def slugify(title: str) -> str:
    """URL-safe identifier for a title: lowercased words joined by underscores."""
    return "_".join(re.findall(r"\w+", title.lower()))


def sku_code(raw: str) -> str:
    """Product code as partners expect it: uppercase, spaces removed."""
    return raw.replace(" ", "").upper()


def amount_str(cents: int) -> str:
    """Amount as a plain decimal string, e.g. 1999 -> "19.99"."""
    return f"{cents / 100:.2f}"
