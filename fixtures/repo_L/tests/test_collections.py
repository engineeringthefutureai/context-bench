import pytest

from shopkit.utils.collections import chunked, dedupe, group_by, index_by


def test_chunked():
    assert list(chunked(range(5), 2)) == [[0, 1], [2, 3], [4]]
    assert list(chunked([], 3)) == []
    with pytest.raises(ValueError):
        list(chunked([1], 0))


def test_index_by_rejects_duplicates():
    assert index_by(["a", "bb"], len) == {1: "a", 2: "bb"}
    with pytest.raises(ValueError):
        index_by(["a", "b"], len)


def test_group_by_and_dedupe():
    assert group_by([1, 2, 3, 4], lambda n: n % 2) == {1: [1, 3], 0: [2, 4]}
    assert dedupe(["b", "a", "b"]) == ["b", "a"]
