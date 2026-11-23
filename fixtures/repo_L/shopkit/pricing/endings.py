"""Price endings: converted and tier prices are rounded to the endings merchandising uses per
currency, so a converted price reads 24.99 rather than 24.37.

Rules (agreed with merchandising, 2025-02):
- USD, CAD: end in .99; amounts under 1.00 are left exact.
- EUR: end in .95 under 100.00, whole euros from 100.00 up (e.g. 129.00).
- GBP: end in .99 under 50.00, .00 from 50.00 up.
- Anything else: unchanged.
Endings never raise a price by more than MAX_UPLIFT_BP of the exact price; if the next ending is
further away, the price is rounded *down* to the previous ending instead.
"""
from __future__ import annotations

MAX_UPLIFT_BP = 300           # 3%


def _next_ending(cents: int, ending: int, step: int = 100) -> int:
    """Smallest amount >= cents whose value modulo `step` equals `ending`."""
    base = cents - cents % step + ending
    return base if base >= cents else base + step


def _prev_ending(cents: int, ending: int, step: int = 100) -> int:
    base = cents - cents % step + ending
    return base if base <= cents else base - step


def _pick(cents: int, ending: int, step: int = 100) -> int:
    up = _next_ending(cents, ending, step)
    if (up - cents) * 10_000 <= cents * MAX_UPLIFT_BP:
        return up
    down = _prev_ending(cents, ending, step)
    return down if down > 0 else up


def apply_ending(cents: int, currency: str) -> int:
    """`cents` rounded to the currency's price ending (see the module docstring)."""
    if cents <= 0:
        return cents
    if currency in ("USD", "CAD"):
        return cents if cents < 100 else _pick(cents, 99)
    if currency == "EUR":
        if cents < 10_000:
            return _pick(cents, 95)
        return _pick(cents, 0)
    if currency == "GBP":
        return _pick(cents, 99) if cents < 5_000 else _pick(cents, 0)
    return cents


def has_ending(cents: int, currency: str) -> bool:
    """True when the amount already follows the currency's ending rule (used by the price list import)."""
    return apply_ending(cents, currency) == cents
