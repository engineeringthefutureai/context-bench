"""Integer-cent arithmetic. Money is always int cents; never round through float formatting."""
from decimal import ROUND_HALF_UP, Decimal


def apply_rate(cents: int, rate_pct: float | Decimal | str) -> int:
    """`rate_pct` percent of `cents`, rounded half-up to a whole cent (half away from zero).
    apply_rate(1999, 15) -> 300; apply_rate(-1999, 15) -> -300. Used for tax, commission, discounts."""
    value = Decimal(cents) * Decimal(str(rate_pct)) / Decimal(100)
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def split_evenly(cents: int, parts: int) -> list[int]:
    """Split an amount into `parts` integer shares that sum exactly to `cents`; earlier shares get the
    leftover cents. split_evenly(1000, 3) -> [334, 333, 333]."""
    if parts < 1:
        raise ValueError("parts must be >= 1")
    base, extra = divmod(cents, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0
