from datetime import date

from shopkit.customers.segments import Activity, segment

TODAY = date(2026, 11, 20)


def test_segments():
    assert segment(Activity("c", None, 0, 0), TODAY) == "new"
    assert segment(Activity("c", date(2025, 1, 1), 1, 100), TODAY) == "lapsed"
    assert segment(Activity("c", date(2026, 6, 1), 4, 100), TODAY) == "at_risk"
    assert segment(Activity("c", date(2026, 11, 1), 8, 60_000), TODAY) == "champion"
    assert segment(Activity("c", date(2026, 11, 1), 3, 1_000), TODAY) == "loyal"
