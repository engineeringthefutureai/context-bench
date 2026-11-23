from datetime import date, datetime

from shopkit.catalog.importer import apply_updates, diff_sheet, parse_feed
from shopkit.catalog.models import Product
from shopkit.inventory.reorder import SupplierTerms, round_to_packs, suggest_with_terms
from shopkit.inventory.stock import Stock
from shopkit.shipping.carriers import cheapest_meeting, delivery_options, ship_date
from shopkit.shipping.rates import is_remote, shipping_quote


def test_feed_updates_hold_big_price_jumps_and_never_touch_titles():
    products = {"MUG-1": Product("MUG-1", "Blue mug", 2000), "TEA-9": Product("TEA-9", "Tea", 1000)}
    items = parse_feed('{"items": [{"sku": "mug-1", "price_cents": 2100, "available": false},'
                       ' {"sku": "TEA9", "price_cents": 199}, {"sku": "X1", "price_cents": 5}, {"sku": "Y"}]}')
    held = []
    report = apply_updates(items, products, on_hold=lambda *a: held.append(a))
    assert report.updated == ["MUG-1"] and products["MUG-1"].price_cents == 2100 and not products["MUG-1"].active
    assert report.held == [("TEA-9", 1000, 199)] and held == [("TEA-9", 1000, 199)]
    assert products["TEA-9"].price_cents == 1000 and report.unknown == ["X-1"] and len(report.errors) == 1
    dry = apply_updates([{"sku": "tea 9", "price_cents": 900}], products, dry_run=True)
    assert dry.updated == ["TEA-9"] and products["TEA-9"].price_cents == 1000
    diff = diff_sheet("sku,title,price\nMUG1,Blue mug XL,21.00\nNEW1,New,1.00\n", products)
    assert diff == {"new": ["NEW-1"], "price": [], "title": ["MUG-1"]}


def test_reorder_rounds_to_packs_and_meets_supplier_minimum():
    t = SupplierTerms("acme", lead_time_days=10, min_order_qty=12, pack_size=6, min_order_cents=50_000)
    assert round_to_packs(1, t) == 12 and round_to_packs(13, t) == 18
    stock = Stock()
    stock.receive("A", 2)
    out = suggest_with_terms(stock, {"A": 30}, {"A": t}, {"A": 1000})
    row = out["acme"][0]
    assert row.order_qty % 6 == 0 and row.order_qty * 1000 >= 50_000


def test_cutoffs_and_carrier_choice():
    friday_late = datetime(2026, 3, 6, 17, 0)
    assert ship_date("ups", friday_late) == date(2026, 3, 9)
    assert ship_date("ups", datetime(2026, 3, 6, 15, 0)) == date(2026, 3, 6)
    opts = delivery_options("US", datetime(2026, 3, 2, 9, 0), {"ups": 900, "post": 500})
    assert [o[0] for o in opts] == ["ups", "post"]
    assert cheapest_meeting("US", datetime(2026, 3, 2, 9, 0), {"ups": 900, "post": 500}, date(2026, 3, 6)) == "post"
    assert cheapest_meeting("US", datetime(2026, 3, 2, 9, 0), {"ups": 900, "post": 500}, date(2026, 3, 4)) == "ups"


def test_shipping_quote_surcharges():
    assert is_remote("US", "99501") and not is_remote("US", "10001")
    q = shipping_quote("US", "99501", 400, order_total_cents=0)
    assert q == {"band": 495, "fuel": 42, "remote": 1500, "total": 2037}
    free = shipping_quote("US", "99501", 400, order_total_cents=20_000)
    assert free["band"] == 0 and free["total"] == 1500
