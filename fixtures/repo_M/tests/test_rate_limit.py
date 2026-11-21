import pytest

from shopkit.api.rate_limit import RateLimiter, TooManyRequests


def test_bucket_refills_over_time():
    now = [0.0]
    limiter = RateLimiter(capacity=2, refill_per_s=1.0, clock=lambda: now[0])
    limiter.check("k")
    limiter.check("k")
    with pytest.raises(TooManyRequests) as e:
        limiter.check("k")
    assert e.value.http_status == 429 and e.value.details["retry_after_s"] == 1.0
    now[0] = 1.0
    limiter.check("k")
    limiter.check("other")            # buckets are per key
