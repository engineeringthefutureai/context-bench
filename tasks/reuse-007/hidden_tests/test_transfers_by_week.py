from datetime import datetime, timezone

from shopkit.inventory.transfers import Transfer, transfers_by_week


def _t(qty, y, m, d):
    return Transfer("SKU", qty, "east", "west", at=datetime(y, m, d, 9, 0, tzinfo=timezone.utc))


def test_one_week():
    assert transfers_by_week([_t(2, 2026, 11, 16), _t(3, 2026, 11, 18)]) == {"2026-W47": 5}


def test_year_end_week_and_order():
    weeks = transfers_by_week([_t(1, 2027, 1, 4), _t(2, 2026, 12, 31), _t(3, 2027, 1, 1)])
    assert weeks == {"2026-W53": 5, "2027-W01": 1}
    assert list(weeks) == ["2026-W53", "2027-W01"]
