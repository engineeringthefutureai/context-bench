import json

from shopkit.api.webhooks import Endpoint, WebhookDispatcher
from shopkit.events import Event


def _sent(payload):
    d = WebhookDispatcher([Endpoint("https://partner/hook", "k", {"payment.*"})])
    d.on_event(Event("payment.captured", payload))
    return json.loads(d.queue[0].body)["data"]


def test_card_number_masked_like_everywhere_else():
    assert _sent({"card_number": "4242-4242-4242-4242", "amount": 100}) == {
        "card_number": "************4242", "amount": 100}


def test_short_numbers_fully_masked():
    assert _sent({"card_number": "123"})["card_number"] == "***"


def test_other_fields_and_original_event_untouched():
    payload = {"order_id": "o1", "amount": 5}
    assert _sent(payload) == {"order_id": "o1", "amount": 5}
