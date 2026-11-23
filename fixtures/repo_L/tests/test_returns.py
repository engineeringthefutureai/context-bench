from datetime import date

import pytest

from shopkit.orders.models import Order, OrderLine
from shopkit.orders.returns import InvalidReturn, ReturnNotAllowed, open_return

ORDER = Order("o1", [OrderLine("A", 2, 1000), OrderLine("B", 1, 500)])


def test_open_return_and_refund():
    r = open_return(ORDER, date(2026, 11, 1), {"A": 1}, "damaged", today=date(2026, 11, 10))
    assert r.approved and r.refund_cents(ORDER) == 1000
    assert len(r.code) == 9 and r.code[4] == "-"


def test_changed_mind_needs_approval():
    r = open_return(ORDER, date(2026, 11, 1), {"B": 1}, "changed_mind", today=date(2026, 11, 2))
    assert not r.approved


def test_window_and_quantities():
    with pytest.raises(ReturnNotAllowed):
        open_return(ORDER, date(2026, 9, 1), {"A": 1}, "damaged", today=date(2026, 11, 10))
    with pytest.raises(InvalidReturn):
        open_return(ORDER, date(2026, 11, 1), {"A": 3}, "damaged", today=date(2026, 11, 2))
