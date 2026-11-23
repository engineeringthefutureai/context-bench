"""Text helpers for product codes and slugs."""
import re


def normalize_code(raw: str) -> str:
    """Product code: upper case, everything that isn't a letter or digit removed. 'mug-1' -> 'MUG1'."""
    return re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()


def title_slug(title: str, max_len: int = 80) -> str:
    """Slug: lower case, words joined by '-', non-ASCII letters dropped."""
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return "-".join(words)[:max_len].rstrip("-")
