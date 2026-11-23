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


# ---- surcharges (carrier contract 2025) -----------------------------------------------------------
REMOTE_POSTCODE_PREFIXES = {"US": ("995", "996", "997", "998", "999", "967", "968"), "GB": ("HS", "ZE", "KW15", "KW17")}
REMOTE_SURCHARGE_CENTS = 1500
FUEL_SURCHARGE_BP = 850                     # 8.5% on the band price, updated monthly by ops


def is_remote(country: str, postcode: str) -> bool:
    pc = postcode.replace(" ", "").upper()
    return any(pc.startswith(p) for p in REMOTE_POSTCODE_PREFIXES.get(country, ()))


def shipping_quote(country: str, postcode: str, weight_g: int, order_total_cents: int = 0,
                   fuel_bp: int = FUEL_SURCHARGE_BP) -> dict[str, int]:
    """Itemised shipping charge: band price, fuel surcharge on it, remote-area surcharge. Free
    shipping (domestic, over the threshold) waives the band and fuel parts but not the remote part."""
    band = shipping_cents(country, weight_g, order_total_cents)
    fuel = band * fuel_bp // 10_000
    remote = REMOTE_SURCHARGE_CENTS if is_remote(country, postcode) else 0
    return {"band": band, "fuel": fuel, "remote": remote, "total": band + fuel + remote}
