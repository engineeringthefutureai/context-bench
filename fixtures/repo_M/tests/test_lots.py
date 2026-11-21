from datetime import date

import pytest

from shopkit.inventory.lots import Lot, LotLedger, NotEnoughStock

TODAY = date(2026, 11, 20)


def ledger():
    l = LotLedger()
    l.receive(Lot("MILK", "L2", 5, date(2026, 11, 25)))
    l.receive(Lot("MILK", "L1", 3, date(2026, 11, 22)))
    l.receive(Lot("MILK", "L0", 9, date(2026, 11, 19)))      # expired
    l.receive(Lot("MILK", "L3", 4, None))
    return l


def test_on_hand_ignores_expired():
    assert ledger().on_hand("MILK", TODAY) == 12


def test_pick_first_expired_first_out():
    assert ledger().pick("MILK", 6, TODAY) == [("L1", 3), ("L2", 3)]


def test_pick_too_much_changes_nothing():
    l = ledger()
    with pytest.raises(NotEnoughStock) as e:
        l.pick("MILK", 13, TODAY)
    assert e.value.details == {"sku": "MILK", "requested": 13, "available": 12}
    assert l.on_hand("MILK", TODAY) == 12


def test_expiring():
    assert [x.lot_number for x in ledger().expiring(TODAY, 3)] == ["L1"]
