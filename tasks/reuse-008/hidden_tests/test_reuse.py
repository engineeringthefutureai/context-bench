from reuse_spy import spy

from shopkit.api.webhooks import Endpoint, WebhookDispatcher
from shopkit.events import Event


def test_uses_existing_helper(monkeypatch):
    calls = spy(monkeypatch, "shopkit.payments.reconciliation.redact_pan")
    WebhookDispatcher([Endpoint("u", "k", {"payment.*"})]).on_event(Event("payment.captured", {"card_number": "4242424242424242"}))
    assert calls, "card number not masked with the shop's masking helper"
