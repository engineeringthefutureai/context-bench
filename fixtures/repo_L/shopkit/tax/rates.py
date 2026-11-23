"""Sales tax / VAT rates by destination. Rates are percentages."""

STANDARD_RATES = {
    "GB": 20.0, "DE": 19.0, "FR": 20.0, "NL": 21.0, "IE": 23.0, "ES": 21.0, "IT": 22.0,
}
REDUCED_RATES = {"GB": 0.0, "DE": 7.0, "FR": 5.5, "NL": 9.0, "IE": 13.5, "ES": 10.0, "IT": 10.0}
US_STATE_RATES = {"CA": 7.25, "NY": 4.0, "TX": 6.25, "WA": 6.5, "OR": 0.0, "MT": 0.0}

# product tax classes
STANDARD, REDUCED, EXEMPT = "standard", "reduced", "exempt"


def rate_for(country: str, region: str = "", tax_class: str = STANDARD) -> float:
    """Percentage rate for a destination and product tax class. Unknown destinations are 0."""
    if tax_class == EXEMPT:
        return 0.0
    if country == "US":
        return US_STATE_RATES.get(region, 0.0)
    table = REDUCED_RATES if tax_class == REDUCED else STANDARD_RATES
    return table.get(country, 0.0)
