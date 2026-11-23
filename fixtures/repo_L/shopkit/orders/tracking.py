"""Status per order, kept beside the OrderBook so the Order dataclass stays a plain value."""
from shopkit.events import BUS, EventBus
from shopkit.orders.status import PENDING, transition


class StatusBoard:
    def __init__(self, bus: EventBus = BUS):
        self._status: dict[str, str] = {}
        self._history: dict[str, list[str]] = {}
        self._bus = bus

    def open(self, order_id: str) -> None:
        self._status[order_id] = PENDING
        self._history[order_id] = [PENDING]

    def status(self, order_id: str) -> str:
        return self._status[order_id]

    def move(self, order_id: str, new: str) -> str:
        status = transition(self._status[order_id], new, order_id)
        self._status[order_id] = status
        self._history[order_id].append(status)
        self._bus.publish(f"order.{status}", order_id=order_id)
        return status

    def history(self, order_id: str) -> list[str]:
        return list(self._history[order_id])

    def in_status(self, status: str) -> list[str]:
        return sorted(o for o, s in self._status.items() if s == status)
