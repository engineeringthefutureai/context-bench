from reuse_spy import spy

from shopkit.payments.gateway import transfer_reference


def test_uses_existing_helper(monkeypatch):
    calls = spy(monkeypatch, "shopkit.utils.ids.short_code")
    transfer_reference()
    assert calls, "reference not generated with the shop's code helper"
