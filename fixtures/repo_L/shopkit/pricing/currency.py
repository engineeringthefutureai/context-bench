"""Currency conversion with pinned daily rates.

Rates are quoted as "units of `quote` per 1 unit of `base`" and stored as integers in millionths
(rate_micros) so conversion stays exact: 1 USD = 0.921500 EUR is stored as 921_500. A rate is
pinned per day: everything priced on one day uses the same rate, so a cart doesn't change price
between page views, and refunds can be converted back with the rate of the original order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from shopkit.errors import ShopError

MICROS = 1_000_000
MAX_RATE_AGE_DAYS = 4          # weekends + a bank holiday: older rates are refused


class RateUnavailable(ShopError):
    http_status = 503


@dataclass(frozen=True)
class Rate:
    base: str
    quote: str
    day: date
    rate_micros: int

    def __post_init__(self):
        if self.rate_micros <= 0:
            raise ValueError("rate must be positive")
        if len(self.base) != 3 or len(self.quote) != 3:
            raise ValueError("currencies are ISO 4217 codes")

    @property
    def inverse_micros(self) -> int:
        return int((Decimal(MICROS) * MICROS / self.rate_micros).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def convert_cents(cents: int, rate_micros: int) -> int:
    """Convert an amount with a rate in millionths, rounding half to even (bankers' rounding, as the
    payment provider settles). convert_cents(1000, 921_500) -> 922. Minor units are assumed to be
    cents on both sides (true for every currency we sell in)."""
    value = Decimal(cents) * Decimal(rate_micros) / Decimal(MICROS)
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


@dataclass
class RateTable:
    """Daily rates, filled by the ERP feed (integrations.erp_client) every morning."""
    rates: dict[tuple[str, str, date], Rate] = field(default_factory=dict)

    def add(self, rate: Rate) -> None:
        self.rates[(rate.base, rate.quote, rate.day)] = rate

    def add_many(self, base: str, day: date, quotes: dict[str, int]) -> None:
        for quote, micros in quotes.items():
            self.add(Rate(base, quote, day, micros))

    def rate(self, base: str, quote: str, day: date) -> Rate:
        """The rate for `day`, or the latest earlier one within MAX_RATE_AGE_DAYS. Falls back to the
        inverse of the opposite pair. Same currency: 1.0."""
        if base == quote:
            return Rate(base, quote, day, MICROS)
        for back in range(MAX_RATE_AGE_DAYS + 1):
            d = day - timedelta(days=back)
            direct = self.rates.get((base, quote, d))
            if direct:
                return direct
            opposite = self.rates.get((quote, base, d))
            if opposite:
                return Rate(base, quote, d, opposite.inverse_micros)
        raise RateUnavailable(f"no {base}/{quote} rate for {day} or the {MAX_RATE_AGE_DAYS} days before",
                              base=base, quote=quote, day=day.isoformat())

    def convert(self, cents: int, base: str, quote: str, day: date) -> int:
        return convert_cents(cents, self.rate(base, quote, day).rate_micros)

    def cross(self, base: str, quote: str, via: str, day: date) -> Rate:
        """A rate through a third currency (e.g. GBP->EUR via USD) when there is no direct quote."""
        a = self.rate(base, via, day).rate_micros
        b = self.rate(via, quote, day).rate_micros
        micros = int((Decimal(a) * b / MICROS).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
        return Rate(base, quote, day, micros)

    def latest_day(self) -> date | None:
        return max((k[2] for k in self.rates), default=None)

    def prune(self, keep_days: int, today: date) -> int:
        """Drop rates older than keep_days; returns how many were removed. Orders store the rate
        they used, so old rates are not needed for refunds."""
        cutoff = today - timedelta(days=keep_days)
        old = [k for k in self.rates if k[2] < cutoff]
        for k in old:
            del self.rates[k]
        return len(old)


def parse_rate(text: str) -> int:
    """'0.9215' -> 921500 rate_micros. At most six decimals; anything else is a feed error."""
    text = text.strip()
    whole, _, frac = text.partition(".")
    if not whole.isdigit() or (frac and not frac.isdigit()) or len(frac) > 6:
        raise ValueError(f"bad rate {text!r}")
    return int(whole) * MICROS + int((frac + "000000")[:6])
