import pytest

from shopkit.customers.models import Customer
from shopkit.reports.customers_report import by_email


def test_lookup_by_normalised_email():
    ann, bob = Customer("c1", " Ann@Shop.Example", "Ann"), Customer("c2", "bob@shop.example", "Bob")
    assert by_email([ann, bob]) == {"ann@shop.example": ann, "bob@shop.example": bob}


def test_two_customers_with_one_address_is_an_error():
    with pytest.raises(ValueError):
        by_email([Customer("c1", "ann@shop.example", "Ann"), Customer("c2", "ANN@shop.example ", "Ann B")])
