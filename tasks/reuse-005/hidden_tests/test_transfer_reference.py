import re

from shopkit.payments.gateway import transfer_reference

REFS = [transfer_reference() for _ in range(300)]


def test_shape():
    assert all(re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", r) for r in REFS)


def test_no_look_alike_characters():
    assert not any(set(r) & set("0O1IL") for r in REFS)


def test_fresh_each_time():
    assert len(set(REFS)) > 295
