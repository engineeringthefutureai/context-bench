"""Address normalisation and formatting for labels, invoices and fraud checks."""
import re

from shopkit.customers.models import Address

_UK_POSTCODE = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?)(\d[A-Z]{2})$")


def normalize_postcode(postcode: str, country: str) -> str:
    """Canonical postcode for comparisons and labels.
    GB: uppercase with a single space before the inward code ("sw1a1aa" -> "SW1A 1AA").
    US: the 5-digit ZIP (ZIP+4 suffix dropped). Other countries: uppercase, whitespace collapsed.
    Raises ValueError for a GB/US postcode that doesn't match the national format."""
    compact = re.sub(r"\s+", "", postcode).upper()
    if country == "GB":
        m = _UK_POSTCODE.match(compact)
        if not m:
            raise ValueError(f"invalid UK postcode: {postcode!r}")
        return f"{m.group(1)} {m.group(2)}"
    if country == "US":
        m = re.match(r"^(\d{5})(-?\d{4})?$", compact)
        if not m:
            raise ValueError(f"invalid US ZIP: {postcode!r}")
        return m.group(1)
    return " ".join(postcode.upper().split())


def format_address(address: Address) -> str:
    """Multi-line postal format used on shipping labels and invoices. Empty parts are skipped;
    the postcode is normalised; US/CA put region and postcode on the city line."""
    postcode = normalize_postcode(address.postcode, address.country)
    if address.country in ("US", "CA"):
        city_line = f"{address.city}, {address.region} {postcode}".replace(" ,", ",").strip()
        lines = [address.name, address.line1, address.line2, city_line, address.country]
    else:
        lines = [address.name, address.line1, address.line2, address.city, address.region, postcode,
                 address.country]
    return "\n".join(part for part in lines if part)


def same_address(a: Address, b: Address) -> bool:
    """Loose equality used to detect repeat shipping destinations."""
    def key(x: Address) -> tuple:
        return (x.country, normalize_postcode(x.postcode, x.country), x.line1.lower().strip())
    return key(a) == key(b)
