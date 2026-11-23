"""Live tax rates from the tax provider, with a local cache and the static table as fallback.

The provider is authoritative for US destinations (it knows city and district rates); the static
table in tax.rates covers the rest and is used whenever the provider is down."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from shopkit.tax.rates import STANDARD, rate_for

log = logging.getLogger(__name__)

CACHE_TTL_S = 6 * 3600
FAILURE_BACKOFF_S = 120


@dataclass
class TaxProvider:
    transport: object                       # callable(country, region, postcode, tax_class) -> rate percent (float)
    clock: object = time.time
    cache: dict[tuple, tuple[float, float]] = field(default_factory=dict)      # key -> (rate, fetched_at)
    down_until: float = 0.0
    calls: int = 0
    fallbacks: int = 0

    def rate(self, country: str, region: str = "", postcode: str = "", tax_class: str = STANDARD) -> float:
        if country != "US":
            return rate_for(country, region, tax_class)
        key = (country, region, postcode[:5], tax_class)
        now = self.clock()
        hit = self.cache.get(key)
        if hit and now - hit[1] < CACHE_TTL_S:
            return hit[0]
        if now < self.down_until:
            self.fallbacks += 1
            return hit[0] if hit else rate_for(country, region, tax_class)
        try:
            self.calls += 1
            value = float(self.transport(country, region, postcode[:5], tax_class))
            if not 0 <= value < 30:
                raise ValueError(f"implausible rate {value}")
        except Exception as e:
            log.warning("tax provider failed (%s), using fallback for %s", e, key)
            self.down_until = now + FAILURE_BACKOFF_S
            self.fallbacks += 1
            return hit[0] if hit else rate_for(country, region, tax_class)
        self.cache[key] = (value, now)
        return value
