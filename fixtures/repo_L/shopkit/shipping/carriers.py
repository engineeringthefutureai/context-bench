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
