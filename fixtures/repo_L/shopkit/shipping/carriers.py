"""Carrier selection and delivery estimates."""
from dataclasses import dataclass
from datetime import date

from shopkit.shipping.zones import zone_for_country
from shopkit.utils.dates import add_business_days


@dataclass(frozen=True)
class Carrier:
    code: str
    name: str
    transit_days: dict[str, int]             # zone -> business days
    tracking_url: str                        # with {number} placeholder


CARRIERS = [
    Carrier("ups", "UPS", {"domestic": 2, "north_america": 4, "eu": 5, "europe_other": 5,
                           "rest_of_world": 8}, "https://track.example/ups/{number}"),
    Carrier("post", "National Post", {"domestic": 4, "north_america": 8, "eu": 10, "europe_other": 10,
                                      "rest_of_world": 15}, "https://track.example/post/{number}"),
]


def carrier(code: str) -> Carrier:
    return next(c for c in CARRIERS if c.code == code)


def estimated_delivery(carrier_code: str, country: str, shipped_on: date, home: str = "US") -> date:
    zone = zone_for_country(country, home)
    return add_business_days(shipped_on, carrier(carrier_code).transit_days[zone])


def tracking_link(carrier_code: str, number: str) -> str:
    return carrier(carrier_code).tracking_url.format(number=number.strip().upper())


# ---- cut-off times and carrier choice (2025) -------------------------------------------------------
# Parcels handed over after the carrier's daily cut-off leave the next business day. Checkout shows
# the earliest delivery date; the warehouse picks the cheapest carrier that still meets it.
from datetime import datetime, time

from shopkit.utils.dates import is_business_day

CUTOFFS = {"ups": time(16, 0), "post": time(12, 30)}


def ship_date(carrier_code: str, ready_at: datetime) -> date:
    """The day a parcel ready at `ready_at` (warehouse local time) actually leaves."""
    day = ready_at.date()
    if not is_business_day(day) or ready_at.time() > CUTOFFS.get(carrier_code, time(23, 59)):
        day = add_business_days(day, 1)
    return day


def delivery_options(country: str, ready_at: datetime, prices: dict[str, int],
                     home: str = "US") -> list[tuple[str, date, int]]:
    """(carrier, estimated delivery, price) for carriers we have a price for, fastest first, then cheapest."""
    out = []
    for c in CARRIERS:
        if c.code not in prices:
            continue
        leaves = ship_date(c.code, ready_at)
        out.append((c.code, estimated_delivery(c.code, country, leaves, home), prices[c.code]))
    return sorted(out, key=lambda o: (o[1], o[2]))


def cheapest_meeting(country: str, ready_at: datetime, prices: dict[str, int], promised: date,
                     home: str = "US") -> str | None:
    """Cheapest carrier whose estimate is on or before the promised date; None if none can make it."""
    ok = [o for o in delivery_options(country, ready_at, prices, home) if o[1] <= promised]
    return min(ok, key=lambda o: (o[2], o[1]))[0] if ok else None
