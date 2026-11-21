"""Order lifecycle. Every status change goes through `transition` so the rules live in one place."""
from shopkit.errors import Conflict

PENDING, PAID, PACKED, SHIPPED, DELIVERED, CANCELLED, REFUNDED = (
    "pending", "paid", "packed", "shipped", "delivered", "cancelled", "refunded")

ALLOWED = {
    PENDING: {PAID, CANCELLED},
    PAID: {PACKED, CANCELLED, REFUNDED},
    PACKED: {SHIPPED, CANCELLED},
    SHIPPED: {DELIVERED},
    DELIVERED: {REFUNDED},
    CANCELLED: set(),
    REFUNDED: set(),
}
TERMINAL = {CANCELLED, REFUNDED}


class InvalidTransition(Conflict):
    pass


def can_transition(current: str, new: str) -> bool:
    """Whether an order in status `current` may move to `new` (see ALLOWED). Unknown statuses: False."""
    return new in ALLOWED.get(current, set())


def transition(current: str, new: str, order_id: str = "") -> str:
    """Validate and return the new status; raises InvalidTransition (409) otherwise."""
    if not can_transition(current, new):
        raise InvalidTransition(f"cannot move order {order_id} from {current} to {new}",
                                order_id=order_id, current=current, requested=new)
    return new


def is_open(status: str) -> bool:
    return status not in TERMINAL and status != DELIVERED
