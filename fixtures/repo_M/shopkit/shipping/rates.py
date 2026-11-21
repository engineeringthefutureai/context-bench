"""Shipping prices: per zone, by chargeable weight band. Free domestic shipping over a threshold."""
from shopkit.config import SETTINGS
from shopkit.shipping.zones import DOMESTIC, zone_for_country

# (max grams, price cents) per zone; the first band whose max >= weight applies
BANDS = {
    "domestic": [(500, 495), (2000, 795), (10000, 1495)],
    "north_america": [(500, 1295), (2000, 2195), (10000, 4495)],
    "eu": [(500, 1495), (2000, 2495), (10000, 5495)],
    "europe_other": [(500, 1695), (2000, 2795), (10000, 5995)],
    "rest_of_world": [(500, 1995), (2000, 3495), (10000, 7995)],
}


class TooHeavy(ValueError):
    pass


def shipping_cents(country: str, weight_g: int, order_total_cents: int = 0) -> int:
    zone = zone_for_country(country, SETTINGS.store_country)
    if zone == DOMESTIC and order_total_cents >= SETTINGS.free_shipping_over_cents:
        return 0
    for max_g, price in BANDS[zone]:
        if weight_g <= max_g:
            return price
    raise TooHeavy(f"{weight_g} g exceeds the {BANDS[zone][-1][0]} g limit for {zone}")
