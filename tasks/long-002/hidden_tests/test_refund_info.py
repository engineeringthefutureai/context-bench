from datetime import date, datetime

from shopkit.payments.models import CAPTURED, Payment
from shopkit.support.refund_info import refund_destinations

TODAY = date(2026, 3, 10)


def _pay(pid, amount, created, method="card", refunded=0):
    p = Payment(pid, "O1", amount, "USD", method, status=CAPTURED, created=created)
    p.refunded_cents = refunded
    return p


def test_recent_card_goes_back_to_the_card_and_old_card_to_store_credit():
    assert refund_destinations("O1", 1_000, [_pay("P1", 5_000, datetime(2026, 3, 1))], TODAY) == \
        {"card": 1_000, "store_credit": 0, "gift_card": 0}
    assert refund_destinations("O1", 1_000, [_pay("P1", 5_000, datetime(2025, 8, 1))], TODAY) == \
        {"card": 0, "store_credit": 1_000, "gift_card": 0}


def test_split_payments_in_proportion_with_leftover_cents_to_the_card():
    pays = [_pay("G1", 1_000, datetime(2026, 3, 1), "gift_card"), _pay("P1", 3_000, datetime(2026, 3, 1))]
    assert refund_destinations("O1", 1_001, pays, TODAY) == {"card": 751, "store_credit": 0, "gift_card": 250}
    partly = [_pay("P1", 3_000, datetime(2026, 3, 1), refunded=2_000), _pay("G1", 1_000, datetime(2026, 3, 1), "gift_card")]
    assert refund_destinations("O1", 1_000, partly, TODAY) == {"card": 500, "store_credit": 0, "gift_card": 500}


def test_nothing_is_refunded():
    p = _pay("P1", 5_000, datetime(2026, 3, 1))
    refund_destinations("O1", 1_000, [p], TODAY)
    assert p.refunded_cents == 0
