from shopkit.reports.formatting import fmt_amount, fmt_percent, pad_table


def test_fmt_amount_is_spreadsheet_friendly():
    assert fmt_amount(123456) == "1234.56"
    assert fmt_amount(-5) == "-0.05"


def test_fmt_percent_and_table():
    assert fmt_percent(0.1234) == "12.3%"
    assert pad_table([["a", "bb"], ["ccc", "d"]]) == "a    bb\n---  --\nccc  d"
