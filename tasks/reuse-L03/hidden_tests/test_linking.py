from shopkit.support.linking import order_refs


def test_references_are_found_normalised_and_deduplicated():
    text = "Hi, where is so 2019 123? I also ordered SO-2019-000123 and ORD_0123456789AB, and sob-2020-45."
    assert order_refs(text) == ["SO-2019-000123", "ord_0123456789ab", "SOB-2020-000045"]


def test_invalid_references_are_left_out():
    assert order_refs("old order SO-2015-000001, ticket 12345, so 2019") == []
