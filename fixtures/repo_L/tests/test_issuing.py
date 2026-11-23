from datetime import date

import pytest

from shopkit.customers.models import Address
from shopkit.invoices.issuing import InvoiceRegister
from shopkit.orders.models import Order, OrderLine

BILL_TO = Address("Ann Lee", "1 Main St", "Austin", "78701", "US", region="TX")


def test_issue_and_credit():
    reg = InvoiceRegister()
    a = reg.issue(Order("o1", [OrderLine("mug 1", 2, 1250)]), BILL_TO, date(2026, 11, 20))
    b = reg.issue(Order("o2", [OrderLine("cup 2", 1, 500)]), BILL_TO, date(2026, 11, 21))
    assert (a.number, b.number) == ("INV-2026-000001", "INV-2026-000002")
    assert "MUG-1" in a.text and "Austin, TX 78701" in a.text and "Due: 2026-12-10" in a.text
    note = reg.credit("o1", 1250, "USD", date(2026, 11, 25), "damaged")
    assert note.number == "CN-2026-000001" and note.amount_cents == -1250
    with pytest.raises(ValueError):
        reg.issue(Order("o1"), BILL_TO, date(2026, 11, 22))
