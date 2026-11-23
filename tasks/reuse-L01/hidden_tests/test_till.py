from shopkit.pos.till import amount_due


def test_cash_in_canada_follows_the_rounding_rules():
    assert [amount_due(c, "CAD", "cash") for c in (1001, 1002, 1003, 1004, 1006, 1007, 1008, 1009)] == \
        [1000, 1000, 1005, 1005, 1005, 1005, 1010, 1010]


def test_cards_and_other_currencies_pay_the_exact_total():
    assert amount_due(1003, "CAD", "card") == 1003
    assert amount_due(1003, "USD", "cash") == 1003
