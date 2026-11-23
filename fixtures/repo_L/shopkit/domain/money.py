"""Money as a value type. Amount in minor units plus currency; arithmetic refuses to mix currencies."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

MINOR_UNITS = {"USD": 2, "EUR": 2, "GBP": 2, "CAD": 2, "JPY": 0, "CHF": 2}


class CurrencyMismatch(ValueError):
    pass


@dataclass(frozen=True, order=True)
class Money:
    minor: int
    currency: str = "USD"

    def __post_init__(self):
        if self.currency not in MINOR_UNITS:
            raise ValueError(f"unsupported currency {self.currency}")

    @classmethod
    def of(cls, amount: str | int | Decimal, currency: str = "USD") -> "Money":
        """Money.of("12.34", "USD") -> Money(1234, "USD"); rounds half to even."""
        exp = MINOR_UNITS.get(currency, 2)
        value = (Decimal(str(amount)) * (10 ** exp)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
        return cls(int(value), currency)

    def _same(self, other: "Money") -> None:
        if not isinstance(other, Money) or other.currency != self.currency:
            raise CurrencyMismatch(f"{self.currency} vs {getattr(other, 'currency', other)}")

    def __add__(self, other: "Money") -> "Money":
        self._same(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same(other)
        return Money(self.minor - other.minor, self.currency)

    def __mul__(self, factor: int) -> "Money":
        if not isinstance(factor, int):
            raise TypeError("multiply Money by an int; use allocate() or percent() for fractions")
        return Money(self.minor * factor, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.minor, self.currency)

    def percent(self, pct: int | str | Decimal) -> "Money":
        value = (Decimal(self.minor) * Decimal(str(pct)) / 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
        return Money(int(value), self.currency)

    def allocate(self, ratios: list[int]) -> list["Money"]:
        total = sum(ratios)
        shares = [self.minor * r // total for r in ratios]
        for i in range(self.minor - sum(shares)):
            shares[i % len(shares)] += 1
        return [Money(s, self.currency) for s in shares]

    def format(self) -> str:
        """'1234.56 USD', '-5.00 EUR', '500 JPY'. No grouping separators."""
        exp = MINOR_UNITS[self.currency]
        sign = "-" if self.minor < 0 else ""
        m = abs(self.minor)
        if exp == 0:
            return f"{sign}{m} {self.currency}"
        return f"{sign}{m // 10 ** exp}.{m % 10 ** exp:0{exp}d} {self.currency}"

    def is_zero(self) -> bool:
        return self.minor == 0


def zero(currency: str = "USD") -> Money:
    return Money(0, currency)


def total(amounts: list[Money], currency: str = "USD") -> Money:
    out = zero(currency)
    for a in amounts:
        out = out + a
    return out
