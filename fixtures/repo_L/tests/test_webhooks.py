from shopkit.api.webhooks import MAX_ATTEMPTS, Endpoint, WebhookDispatcher, sign, verify
from shopkit.events import EventBus


def test_sign_and_verify():
    sig = sign('{"a": 1}', "s3cret")
    assert sig.startswith("sha256=") and verify('{"a": 1}', "s3cret", sig)
    assert not verify('{"a": 2}', "s3cret", sig)


def test_dispatch_filters_by_event_and_retries():
    calls = []
    def transport(url, body, headers):
        calls.append(url)
        return 500 if url.endswith("/flaky") else 200
    bus = EventBus()
    d = WebhookDispatcher([Endpoint("https://erp/ok", "k", {"order.*"}),
                           Endpoint("https://crm/flaky", "k", {"order.paid"}),
                           Endpoint("https://other/none", "k", {"stock.low"})], transport)
    d.attach(bus)
    bus.publish("order.paid", order_id="o1")
    assert d.flush() == 1 and len(d.queue) == 1
    for _ in range(MAX_ATTEMPTS):
        d.flush()
    assert d.queue == [] and "https://other/none" not in calls
