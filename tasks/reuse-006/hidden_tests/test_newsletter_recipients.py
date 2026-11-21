from shopkit.customers.models import Customer
from shopkit.marketing.newsletter import recipients


def _ids(*emails, opt_in=True):
    return [c.id for c in recipients([Customer(f"c{i}", e, "Ann", marketing_opt_in=opt_in)
                                      for i, e in enumerate(emails)])]


def test_valid_addresses_kept():
    assert _ids("ann@shop.example", " bob@shop.example ") == ["c0", "c1"]


def test_badly_typed_addresses_skipped():
    assert _ids("ann..b@shop.example", "ann@localhost", "no-at.shop.example", "ann@shop.c") == []


def test_still_requires_opt_in():
    assert _ids("ann@shop.example", opt_in=False) == []
