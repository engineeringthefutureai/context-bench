import pytest

from shopkit.events import EventBus
from shopkit.orders.status import InvalidTransition, can_transition, transition
from shopkit.orders.tracking import StatusBoard


def test_can_transition():
    assert can_transition("pending", "paid")
    assert not can_transition("shipped", "cancelled")
    assert not can_transition("nonsense", "paid")


def test_transition_raises_409():
    with pytest.raises(InvalidTransition) as e:
        transition("delivered", "packed", "o1")
    assert e.value.http_status == 409 and e.value.details["requested"] == "packed"


def test_status_board_publishes_events():
    bus = EventBus()
    board = StatusBoard(bus)
    board.open("o1")
    board.move("o1", "paid")
    assert board.history("o1") == ["pending", "paid"]
    assert [e.name for e in bus.history] == ["order.paid"]
