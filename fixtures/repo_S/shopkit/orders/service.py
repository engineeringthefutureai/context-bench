from shopkit.errors import DuplicateOrder, OrderNotFound
from shopkit.orders.models import Order


class OrderBook:
    """In-memory order store used by the API."""

    def __init__(self):
        self._orders: dict[str, Order] = {}

    def add(self, order: Order) -> None:
        if order.id in self._orders:
            raise DuplicateOrder(f"order {order.id} already exists", order_id=order.id)
        self._orders[order.id] = order

    def get(self, order_id: str) -> Order:
        try:
            return self._orders[order_id]
        except KeyError:
            raise OrderNotFound(f"no order {order_id}", order_id=order_id) from None
