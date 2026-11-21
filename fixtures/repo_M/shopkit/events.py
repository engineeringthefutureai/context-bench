"""In-process event bus. Services publish domain events; notifications and audit subscribe."""
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from shopkit.utils.dates import utc_now


@dataclass(frozen=True)
class Event:
    name: str                       # e.g. "order.paid", "stock.low"
    payload: dict
    at: datetime = field(default_factory=utc_now)


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self):
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self.history: list[Event] = []

    def subscribe(self, name: str, handler: Handler) -> None:
        """name may end with ".*" to receive every event under a prefix, e.g. "order.*"."""
        self._handlers[name].append(handler)

    def publish(self, name: str, **payload) -> Event:
        event = Event(name, payload)
        self.history.append(event)
        prefix = name.split(".", 1)[0] + ".*"
        for handler in self._handlers.get(name, []) + self._handlers.get(prefix, []):
            handler(event)
        return event


BUS = EventBus()
