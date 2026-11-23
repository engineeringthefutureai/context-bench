import pytest

from shopkit.customers.addresses import format_address, normalize_postcode, same_address
from shopkit.customers.models import Address


def test_normalize_postcode():
    assert normalize_postcode("sw1a1aa", "GB") == "SW1A 1AA"
    assert normalize_postcode("94103-1234", "US") == "94103"
    assert normalize_postcode(" 75008  paris ", "FR") == "75008 PARIS"
    with pytest.raises(ValueError):
        normalize_postcode("ABC", "GB")


def test_format_address_us():
    a = Address("Ann Lee", "1 Main St", "Austin", "78701", "US", region="TX")
    assert format_address(a) == "Ann Lee\n1 Main St\nAustin, TX 78701\nUS"


def test_format_address_gb_skips_empty_parts():
    a = Address("Bo Chen", "10 Downing St", "London", "sw1a2aa", "GB")
    assert format_address(a) == "Bo Chen\n10 Downing St\nLondon\nSW1A 2AA\nGB"


def test_same_address():
    a = Address("A", "1 High St", "Leeds", "LS1 1AA", "GB")
    b = Address("B", "1 high st ", "Leeds", "ls11aa", "GB")
    assert same_address(a, b)
