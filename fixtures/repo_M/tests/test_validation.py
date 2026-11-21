import pytest

from shopkit.utils.validation import is_valid_email, normalize_phone


@pytest.mark.parametrize("address", ["a@b.co", "first.last+tag@mail.example.org"])
def test_valid_emails(address):
    assert is_valid_email(address)


@pytest.mark.parametrize("address", ["", "no-at.example", "a@b", "a b@c.de", "a..b@c.de"])
def test_invalid_emails(address):
    assert not is_valid_email(address)


def test_normalize_phone():
    assert normalize_phone("0044 20 7946 0958") == "+442079460958"
    with pytest.raises(ValueError):
        normalize_phone("12")
