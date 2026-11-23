"""Wire domain events to customer and staff notifications."""
from shopkit.events import Event, EventBus
from shopkit.money import format_money
from shopkit.notifications.email import Outbox

STAFF_EMAIL = "ops@shopkit.example"


class Notifier:
    def __init__(self, outbox: Outbox, customer_email_for_order):
        self.outbox = outbox
        self._email_for = customer_email_for_order      # order_id -> (email, first_name)

    def attach(self, bus: EventBus) -> None:
        bus.subscribe("order.paid", self.on_paid)
        bus.subscribe("stock.low", self.on_low_stock)

    def on_paid(self, event: Event) -> None:
        email, first_name = self._email_for(event.payload["order_id"])
        total = format_money(event.payload.get("total_cents", 0), event.payload.get("currency", "USD"))
        self.outbox.send(email, "order_confirmation", order_id=event.payload["order_id"],
                         first_name=first_name, total=total)

    def on_low_stock(self, event: Event) -> None:
        self.outbox.send(STAFF_EMAIL, "low_stock", sku=event.payload["sku"],
                         available=event.payload["available"])
