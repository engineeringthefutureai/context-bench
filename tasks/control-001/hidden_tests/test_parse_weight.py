import pytest

from shopkit.utils.units import parse_weight


@pytest.mark.parametrize("text,grams", [
    ("250g", 250), ("1.5 kg", 1500), ("2 LB", 907), ("3oz", 85), ("  0.25Kg ", 250), ("0g", 0),
])
def test_valid(text, grams):
    assert parse_weight(text) == grams


@pytest.mark.parametrize("text", ["", "kg", "10", "10 stone", "-1kg", "1,5kg", "1.5.2kg"])
def test_invalid(text):
    with pytest.raises(ValueError):
        parse_weight(text)
