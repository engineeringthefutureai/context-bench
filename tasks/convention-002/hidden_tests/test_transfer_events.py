import pytest

from shopkit.audit_log import AuditLog
from shopkit.events import BUS
from shopkit.inventory.transfers import TransferFailed, transfer
from shopkit.inventory.warehouses import Network, Warehouse


def _network():
    east, west = Warehouse("east", "US"), Warehouse("west", "US")
    east.stock.receive("MUG", 10)
    return Network([east, west])


def test_successful_transfer_is_announced_and_audited_under_the_sku():
    before = len(BUS.history)
    transfer(_network(), "MUG", 4, "east", "west")
    new = BUS.history[before:]
    assert len(new) == 1
    event = new[0]
    assert "." in event.name
    assert {k: event.payload[k] for k in ("sku", "qty", "source", "destination")} == {
        "sku": "MUG", "qty": 4, "source": "east", "destination": "west"}
    log = AuditLog()
    log.on_event(event)
    assert [e.target for e in log.for_target("MUG")] == ["MUG"]


def test_failed_transfer_announces_nothing():
    before = len(BUS.history)
    with pytest.raises(TransferFailed):
        transfer(_network(), "MUG", 40, "east", "west")
    assert len(BUS.history) == before
