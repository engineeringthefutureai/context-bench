import pytest

from shopkit.catalog.reviews import Review, average_rating, moderate, rating_histogram


def test_moderation():
    assert moderate(Review("A", "c1", 5, "Lovely mug, great glaze", verified_buyer=True)).approved
    assert not moderate(Review("A", "c2", 1, "This is a scam honestly", verified_buyer=True)).approved
    assert not moderate(Review("A", "c3", 4, "Nice but not bought here")).approved
    with pytest.raises(ValueError):
        moderate(Review("A", "c4", 6, "Out of range rating"))


def test_average_and_histogram():
    rs = [Review("A", "c", 5, "x" * 10, True, True), Review("A", "d", 2, "x" * 10, True, True),
          Review("A", "e", 1, "x" * 10, False, True)]
    assert average_rating(rs) == 3.5
    assert average_rating([]) is None
    assert rating_histogram(rs) == {1: 1, 2: 1, 3: 0, 4: 0, 5: 1}
