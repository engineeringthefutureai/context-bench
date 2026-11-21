"""Cursor-less offset pagination for list endpoints."""
from collections.abc import Sequence
from typing import TypeVar

from shopkit.config import SETTINGS

T = TypeVar("T")


def paginate(items: Sequence[T], page: int = 1, per_page: int | None = None) -> dict:
    """One page of `items` plus metadata for the API envelope:
    {"items": [...], "page": 2, "per_page": 50, "total": 120, "pages": 3}.
    per_page defaults to SETTINGS.page_size and is capped at SETTINGS.max_page_size; page < 1 is
    treated as 1; a page past the end returns an empty list (not an error)."""
    per_page = min(per_page or SETTINGS.page_size, SETTINGS.max_page_size)
    page = max(page, 1)
    total = len(items)
    start = (page - 1) * per_page
    return {"items": list(items[start:start + per_page]), "page": page, "per_page": per_page,
            "total": total, "pages": max(1, -(-total // per_page))}
