from shopkit.money import format_money, parse_money


def test_format_money():
    assert format_money(123456) == "$1,234.56"
    assert format_money(-5) == "-$0.05"


def test_round_trip():
    assert parse_money(format_money(987654)) == 987654
