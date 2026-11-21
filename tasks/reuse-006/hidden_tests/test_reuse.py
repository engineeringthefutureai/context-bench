from reuse_spy import spy

from shopkit.customers.models import Customer
from shopkit.marketing.newsletter import recipients


def test_uses_existing_helper(monkeypatch):
    calls = spy(monkeypatch, "shopkit.utils.validation.is_valid_email")
    recipients([Customer("c1", "ann@shop.example", "Ann", marketing_opt_in=True)])
    assert calls, "addresses not checked with the shop's email validation"
