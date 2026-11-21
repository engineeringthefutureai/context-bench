"""Shipping zones. Rates and delivery estimates are defined per zone, not per country."""

DOMESTIC, EU, EUROPE_OTHER, NORTH_AMERICA, REST_OF_WORLD = (
    "domestic", "eu", "europe_other", "north_america", "rest_of_world")

_EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV",
       "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}
_EUROPE_OTHER = {"GB", "NO", "CH", "IS"}
_NORTH_AMERICA = {"US", "CA", "MX"}


def zone_for_country(country: str, home: str = "US") -> str:
    """Rate zone for a destination ISO country code, seen from the store's home country.
    The home country is always DOMESTIC; lowercase codes are accepted."""
    country = country.strip().upper()
    if country == home:
        return DOMESTIC
    if country in _EU:
        return EU
    if country in _EUROPE_OTHER:
        return EUROPE_OTHER
    if country in _NORTH_AMERICA:
        return NORTH_AMERICA
    return REST_OF_WORLD
