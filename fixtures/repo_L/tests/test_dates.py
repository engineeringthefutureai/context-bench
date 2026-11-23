from datetime import date

import pytest

from shopkit.utils.dates import add_business_days, iso_week_label, month_bounds, parse_iso_date


def test_add_business_days_skips_weekend():
    friday = date(2026, 11, 20)
    assert add_business_days(friday, 1) == date(2026, 11, 23)
    assert add_business_days(friday, 0) == friday


def test_add_business_days_skips_holidays():
    assert add_business_days(date(2026, 12, 24), 1) == date(2026, 12, 28)


def test_add_business_days_rejects_negative():
    with pytest.raises(ValueError):
        add_business_days(date(2026, 1, 5), -1)


def test_iso_week_label():
    assert iso_week_label(date(2026, 11, 18)) == "2026-W47"
    assert iso_week_label(date(2027, 1, 1)) == "2026-W53"


def test_month_bounds_and_parse():
    assert month_bounds(date(2028, 2, 10)) == (date(2028, 2, 1), date(2028, 2, 29))
    assert parse_iso_date(" 2026-03-04 ") == date(2026, 3, 4)
