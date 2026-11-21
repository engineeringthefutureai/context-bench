import pytest

from shopkit.utils.durations import parse_duration


@pytest.mark.parametrize("text,minutes", [("2h", 120), ("45m", 45), ("1h30m", 90), ("1h 30m", 90),
                                          (" 1H5M ", 65), ("0m", 0), ("90m", 90)])
def test_valid(text, minutes):
    assert parse_duration(text) == minutes


@pytest.mark.parametrize("text", ["", "   ", "5", "h", "1d", "30m1h", "-5m", "1h1h", "1.5h", "m30"])
def test_invalid(text):
    with pytest.raises(ValueError):
        parse_duration(text)
