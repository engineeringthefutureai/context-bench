from datetime import date, datetime, timezone

from shopkit.payments.refunds import Refund, expected_arrival


def _issued(y, m, d):
    return Refund("pay_1", 500, "damaged", at=datetime(y, m, d, 15, 0, tzinfo=timezone.utc))


def test_plain_week():
    assert expected_arrival(_issued(2026, 11, 20)) == date(2026, 11, 27)     # Fri -> next Fri


def test_issued_on_a_weekend():
    assert expected_arrival(_issued(2026, 11, 21)) == date(2026, 11, 27)     # Sat -> Fri


def test_holidays_do_not_count():
    assert expected_arrival(_issued(2026, 12, 23)) == date(2026, 12, 31)     # skips Dec 25/26
