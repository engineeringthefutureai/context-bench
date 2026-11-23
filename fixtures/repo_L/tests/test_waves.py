from datetime import date, datetime

from shopkit.config import Settings
from shopkit.core.order_engine import OrderRecord, splitForShipping
from shopkit.events import EventBus
from shopkit.orders.models import Order, OrderLine
from shopkit.warehouse.sites import Lot, Site, SiteStock
from shopkit.warehouse.waves import EXPRESS, STANDARD, WaveOrder, allocate_wave, pick_list_text, release

TODAY = date(2026, 3, 10)
MAIN = Site("MAIN", "Main", priority=0)
EAST = Site("EAST", "East", priority=1, services=frozenset({"air"}))


def _stock(*lots):
    return SiteStock([Lot(*l) for l in lots])


def _o(oid, *lines, **kw):
    return WaveOrder(Order(oid, [OrderLine(s, q, 100) for s, q in lines]), **kw)


def test_single_site_fefo_and_waves_per_service():
    stock = _stock(("MUG", "MAIN", "A1", 5, date(2026, 6, 1)), ("MUG", "MAIN", "A2", 5, date(2026, 4, 1)),
                   ("MUG", "EAST", "B1", 5))
    bus = EventBus()
    res = allocate_wave([_o("O1", ("MUG", 6), service=EXPRESS, paid_at=datetime(2026, 3, 9)),
                         _o("O2", ("MUG", 1), paid_at=datetime(2026, 3, 8))], [MAIN, EAST], stock, today=TODAY, bus=bus)
    express = next(w for w in res.waves if w.service == EXPRESS)
    assert [(a.bin, a.qty) for a in express.allocations] == [("A2", 5), ("A1", 1)]
    assert {w.service for w in res.waves} == {EXPRESS, STANDARD} and res.stats["single_site"] == 2


def test_hazmat_only_from_ground_sites_and_never_express():
    stock = _stock(("BATT", "EAST", "B1", 5), ("BATT", "MAIN", "A1", 1))
    bus = EventBus()
    res = allocate_wave([_o("O1", ("BATT", 1), service=EXPRESS)], [MAIN, EAST], stock, today=TODAY,
                        hazmat_skus=frozenset({"BATT"}), bus=bus)
    assert [(w.site, w.service) for w in res.waves] == [("MAIN", STANDARD)]
    assert any(e.name == "order.service_downgraded" for e in bus.history)
    res2 = allocate_wave([_o("O2", ("BATT", 3))], [MAIN, EAST], stock, today=TODAY, hazmat_skus=frozenset({"BATT"}),
                         bus=EventBus())
    assert res2.backordered == {"O2": {"BATT": 3}}            # EAST has 5 but only ships by air


def test_split_backorder_gift_and_safety_stock():
    stock = _stock(("MUG", "MAIN", "A1", 2), ("MUG", "EAST", "B1", 2), ("TEA", "MAIN", "A9", 1))
    res = allocate_wave([_o("O1", ("MUG", 3)), _o("O2", ("TEA", 2))], [MAIN, EAST], stock, today=TODAY, bus=EventBus())
    assert res.stats["split"] == 1 and res.backordered == {"O2": {"TEA": 1}}
    gift = allocate_wave([_o("G1", ("MUG", 1), gift=True)], [MAIN, EAST], _stock(("MUG", "MAIN", "A1", 1)),
                         today=TODAY, bus=EventBus())
    assert gift.stats["single_site"] == 1
    g2 = allocate_wave([_o("G2", ("MUG", 3), gift=True)], [MAIN, EAST], _stock(("MUG", "MAIN", "A1", 2), ("MUG", "EAST", "B", 2)),
                       today=TODAY, bus=EventBus())
    assert "G2" in g2.backordered and g2.waves == []
    safe = Site("MAIN", "Main", safety_stock=2)
    res3 = allocate_wave([_o("O3", ("MUG", 2))], [safe], _stock(("MUG", "MAIN", "A1", 3)), today=TODAY, bus=EventBus())
    assert res3.backordered == {"O3": {"MUG": 1}}


def test_expired_lots_holds_and_shadow_split():
    stock = _stock(("MILK", "MAIN", "C1", 4, date(2026, 3, 1)), ("MILK", "MAIN", "C2", 1, date(2026, 3, 20)))
    calls = []
    def shadow(order, avail):
        calls.append(order.id)
        return splitForShipping(OrderRecord(order.id, lines=[{"sku": l.sku, "qty": l.qty} for l in order.lines]), avail)
    res = allocate_wave([_o("O1", ("MILK", 2)), _o("O2", ("MILK", 1), hold="fraud review")], [MAIN], stock,
                        today=TODAY, bus=EventBus(), shadow_split=shadow)
    assert res.backordered == {"O1": {"MILK": 1}} and res.skipped == {"O2": "hold: fraud review"}


def test_release_and_pick_list():
    stock = _stock(("MUG", "MAIN", "A1", 5))
    res = allocate_wave([_o("O1", ("MUG", 2))], [MAIN], stock, today=TODAY, bus=EventBus(), metrics={})
    w = res.waves[0]
    payload = release(w, bus=EventBus())
    assert payload["picks"] == [{"order": "O1", "sku": "MUG", "bin": "A1", "qty": 2}]
    assert "A1" in pick_list_text(w) and "1 orders, 2 units" in pick_list_text(w)
