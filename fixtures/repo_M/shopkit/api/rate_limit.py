"""Token-bucket rate limiting per API key."""
import time
from dataclasses import dataclass

from shopkit.errors import ShopError


class TooManyRequests(ShopError):
    http_status = 429


@dataclass
class Bucket:
    capacity: int
    refill_per_s: float
    tokens: float
    updated: float


class RateLimiter:
    def __init__(self, capacity: int = 60, refill_per_s: float = 1.0, clock=time.monotonic):
        self.capacity = capacity
        self.refill_per_s = refill_per_s
        self._clock = clock
        self._buckets: dict[str, Bucket] = {}

    def check(self, key: str, cost: int = 1) -> None:
        """Consume `cost` tokens for `key` or raise TooManyRequests with a retry hint."""
        now = self._clock()
        b = self._buckets.get(key) or Bucket(self.capacity, self.refill_per_s, self.capacity, now)
        b.tokens = min(b.capacity, b.tokens + (now - b.updated) * b.refill_per_s)
        b.updated = now
        self._buckets[key] = b
        if b.tokens < cost:
            retry = (cost - b.tokens) / b.refill_per_s
            raise TooManyRequests("rate limit exceeded", retry_after_s=round(retry, 1))
        b.tokens -= cost
