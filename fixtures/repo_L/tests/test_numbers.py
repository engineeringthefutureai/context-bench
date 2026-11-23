from shopkit.utils.numbers import apply_rate, split_evenly


def test_apply_rate_rounds_half_up():
    assert apply_rate(1999, 15) == 300
    assert apply_rate(-1999, 15) == -300
    assert apply_rate(1000, "7.25") == 73
    assert apply_rate(50, 1) == 1          # 0.5 cent rounds up


def test_split_evenly_sums_exactly():
    assert split_evenly(1000, 3) == [334, 333, 333]
    assert sum(split_evenly(99, 7)) == 99
