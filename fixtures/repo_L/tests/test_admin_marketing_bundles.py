from datetime import date

import pytest

from shopkit.accounting.journal import Journal
from shopkit.admin.cli import Context, main
from shopkit.catalog.bundles import Bundle, BundleError, Component
from shopkit.catalog.models import Product
from shopkit.customers.models import Customer
from shopkit.customers.segments import Activity
from shopkit.db import connect
from shopkit.inventory.stock import Stock
from shopkit.marketing.audiences import MAX_SENDS_PER_WEEK, SendLog, build_audience, in_holdout, lift
from shopkit.marketing.campaigns import Campaign
from shopkit.payouts.models import Earning, Seller

TODAY = date(2026, 3, 10)


def test_admin_commands(tmp_path):
    ctx = Context(db=connect(migrate=False))
    assert main(["migrate"], ctx) == 0 and "applied" in ctx.out[-1]
    assert main(["migrate", "--status"], ctx) == 0 and ctx.out[-1].endswith("applied")
    f = tmp_path / "p.csv"
    f.write_text("sku,title,price\nmug1,Mug,12.00\n,No sku,1.00\n")
    assert main(["import-products", str(f), "--dry-run"], ctx) == 1 and ctx.products == {}
    main(["import-products", str(f)], ctx)
    assert "MUG-1" in ctx.products and ctx.search.search("mug")[0][0] == "MUG-1"
    s = tmp_path / "s.txt"
    s.write_text("MUG000000001" + "01" + "        4" + "        0" + "\nEOF 1\n")
    main(["stock-file", str(s)], ctx)
    assert ctx.stock["MAIN"].available("MUG-1") == 4
    ctx.sellers = [Seller("S1", "x", tax_id="1")]
    ctx.earnings = [Earning("S1", "O1", "MUG-1", 10_000, date(2026, 1, 1))]
    assert main(["payouts", "--start", "2026-03-02", "--end", "2026-03-08", "--approve-by", "fin"], ctx) == 0
    assert any(" approved 8500" in line for line in ctx.out)
    assert main(["close-month", "2026-3"], ctx) == 0 and main(["close-month", "march"], ctx) == 2
    assert main(["stock-file", str(tmp_path / "nope")], ctx) == 2


def test_audiences_consent_suppression_caps_and_holdout():
    camp = Campaign("Spring Sale", date(2026, 3, 1), date(2026, 3, 31))
    customers = [Customer(f"C{i}", f"c{i}@example.com", f"C {i}", marketing_opt_in=i != 0) for i in range(40)]
    log = SendLog()
    for _ in range(MAX_SENDS_PER_WEEK):
        log.record("C1", date(2026, 3, 8))
    aud = build_audience(camp, customers, {}, TODAY, suppressed={"c2@example.com"}, send_log=log, holdout_pct=10)
    assert aud.excluded["C0"] == "no consent" and aud.excluded["C2"] == "suppressed" and aud.excluded["C1"] == "frequency cap"
    held = [c.id for c in customers if aud.excluded.get(c.id) == "holdout"]
    assert held and all(in_holdout(cid, camp.handle, 10) for cid in held)
    seg = build_audience(camp, customers[:5], {"C3": Activity("C3", date(2025, 1, 1), 1, 100)}, TODAY, segments={"lapsed"},
                         holdout_pct=0)
    assert [c.id for c in seg.recipients] == ["C3"]
    assert build_audience(camp, customers, {}, date(2026, 5, 1)).size == 0
    assert lift(30, 1000, 2, 100) == 0.5 and lift(1, 1, 0, 10) is None


def test_bundles():
    products = {"MUG": Product("MUG", "Mug", 1500), "TEA": Product("TEA", "Tea", 500)}
    b = Bundle("KIT", "Tea kit", 1800, [Component("MUG", 1), Component("TEA", 2)])
    b.validate(products)
    assert b.savings_cents(products) == 700
    alloc = b.allocate_revenue(products, qty=3)
    assert sum(alloc.values()) == 5400 and alloc == {"MUG": 3240, "TEA": 2160}
    stock = Stock()
    stock.receive("MUG", 3)
    stock.receive("TEA", 5)
    assert b.available(stock) == 2 and b.explode(2) == [("MUG", 2), ("TEA", 4)]
    with pytest.raises(BundleError):
        Bundle("X", "x", 1, [Component("X", 1)]).validate(products)
