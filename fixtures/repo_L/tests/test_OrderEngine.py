import unittest
from datetime import datetime, timedelta

from shopkit.core import order_engine as oe


class FakePayments(object):
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def charge(self, orderId, amountCents, token):
        self.calls.append((orderId, amountCents))
        return (True, "pay-1") if self.ok else (False, "declined")


class FakeWms(object):
    def __init__(self, results):
        self.results = list(results)
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)
        return self.results.pop(0) if self.results else (True, "W1")


ADDR = {"name": "Ann", "line1": "1 Main St", "city": "Springfield", "postcode": "12345", "country": "US",
        "email": "ann@example.com"}


class OrderEngineTest(unittest.TestCase):

    def setUp(self):
        oe.clearRetryQueue()
        self.catalog = {"MUG-1": {"price": 19.99, "title": "Mug"}, "TEA-9": {"price": 5.0, "title": "Tea"}}
        self.stock = {"MUG-1": 10, "TEA-9": 1}

    def testParseOrderRef(self):
        self.assertEqual(oe.parseOrderRef("SO-2019-000123"), ("SO", 2019, 123))
        self.assertEqual(oe.parseOrderRef(" sob 2020 45 "), ("SOB", 2020, 45))
        self.assertEqual(oe.parseOrderRef("ORD_1A2B3C4D5E6F"), ("ord", None, "ord_1a2b3c4d5e6f"))
        self.assertIsNone(oe.parseOrderRef("SO-2015-000001"))
        self.assertIsNone(oe.parseOrderRef("hello"))
        self.assertEqual(oe.formatOrderRef("SO", 2019, 123), "SO-2019-000123")

    def testWmsMapping(self):
        self.assertEqual(oe.wmsStatusToShopkit("dsp"), "shipped")
        self.assertEqual(oe.wmsStatusToShopkit("SHT"), "paid")
        self.assertIsNone(oe.wmsStatusToShopkit("XXX"))
        self.assertEqual(oe.wmsStatusToCode("PKD"), oe.STATUS_PACKED)

    def testTotals(self):
        t = oe.calcOrderTotals([{"sku": "a", "qty": 3, "price": 19.99}], taxRate=0.1)
        self.assertEqual(t["subtotal"], 59.97)
        self.assertEqual(t["tax"], 6.0)
        self.assertEqual(t["shipping"], 7.95)
        t2 = oe.calcOrderTotals([{"sku": "a", "qty": 6, "price": 19.99}], taxRate=0.0, discountPct=10)
        self.assertEqual(t2["shipping"], 0.0)          # free shipping looks at the subtotal before discount

    def testProcessOrderHappyPath(self):
        eng = oe.OrderEngine(self.catalog, self.stock, FakePayments(), FakeWms([(True, "W9")]))
        o = oe.OrderRecord("SO-2026-000001", "C1", [{"sku": "MUG-1", "qty": 2}], dict(ADDR))
        ok, code = eng.processOrder(o)
        self.assertTrue(ok)
        self.assertEqual(o.status, oe.STATUS_SENT_TO_WMS)
        self.assertEqual(self.stock["MUG-1"], 8)
        self.assertEqual(o.wmsRef, "W9")

    def testDeclinedPaymentRestoresStock(self):
        eng = oe.OrderEngine(self.catalog, self.stock, FakePayments(ok=False))
        o = oe.OrderRecord("SO-2026-000002", "C1", [{"sku": "MUG-1", "qty": 2}], dict(ADDR))
        ok, code = eng.processOrder(o)
        self.assertFalse(ok)
        self.assertEqual(code, oe.ERR_PAYMENT_DECLINED)
        self.assertEqual(self.stock["MUG-1"], 10)

    def testWmsFailureQueuesRetry(self):
        eng = oe.OrderEngine(self.catalog, self.stock, FakePayments(), FakeWms([(False, "timeout")] * 3))
        o = oe.OrderRecord("SO-2026-000003", "C1", [{"sku": "MUG-1", "qty": 1}], dict(ADDR))
        ok, code = eng.processOrder(o)
        self.assertTrue(ok)
        self.assertEqual(oe.retryQueueSize(), 1)
        self.assertEqual(eng.retryWms(), 1)

    def testValidation(self):
        eng = oe.OrderEngine(self.catalog, self.stock)
        o = oe.OrderRecord("X", lines=[{"sku": "TEA-9", "qty": 2}], address=dict(ADDR))
        self.assertEqual(eng.validateOrder(o), (False, oe.ERR_OUT_OF_STOCK))
        o2 = oe.OrderRecord("Y", lines=[{"sku": "MUG-1", "qty": 1}], address={"name": "x"})
        self.assertEqual(eng.validateOrder(o2)[1], oe.ERR_BAD_ADDRESS)

    def testCallbackShortPick(self):
        eng = oe.OrderEngine(self.catalog, self.stock, FakePayments(), FakeWms([]))
        o = oe.OrderRecord("SO-2026-000004", "C1", [{"sku": "MUG-1", "qty": 3}], dict(ADDR))
        eng.processOrder(o)
        code, msg = eng.handleWmsCallback({"ref": o.orderId, "status": "SHT", "items": [{"sku": "MUG1", "picked": 1}]})
        self.assertEqual(code, 200)
        self.assertEqual(o.shortItems, {"MUG1": 2})
        self.assertEqual(eng.handleWmsCallback({"ref": "nope", "status": "PKD"}), (202, "unknown order, accepted"))

    def testCancelAndRefund(self):
        eng = oe.OrderEngine(self.catalog, self.stock, FakePayments())
        o = oe.OrderRecord("SO-2026-000005", "C1", [{"sku": "MUG-1", "qty": 1}], dict(ADDR))
        eng.processOrder(o, sendToWms=False)
        ok, code, amt = eng.refundOrder(o.orderId, 10.0)
        self.assertTrue(ok)
        self.assertEqual(eng.cancelOrder(o.orderId), (True, oe.ERR_NONE))
        self.assertEqual(eng.cancelOrder(o.orderId), (False, oe.ERR_ALREADY_CANCELLED))

    def testCreditAccounts(self):
        oe.setCreditAccount("B1", 100)
        self.assertEqual(oe.chargeToAccount("B1", 60), (True, oe.ERR_NONE))
        self.assertEqual(oe.chargeToAccount("B1", 60)[0], False)
        self.assertEqual(oe.creditAvailable("B1"), 40.0)

    def testSplitForShipping(self):
        o = oe.OrderRecord("Z", lines=[{"sku": "A", "qty": 2}, {"sku": "B", "qty": 5}])
        plan, missing = oe.splitForShipping(o, {"W1": {"A": 2, "B": 2}, "W2": {"B": 2}})
        self.assertEqual(plan["W1"][0], {"sku": "A", "qty": 2})
        self.assertEqual(missing, {"B": 1})

    def testCsvImportDryRun(self):
        eng = oe.OrderEngine(self.catalog, self.stock)
        csvText = ("order,customer,sku,qty,price,name,line1,city,postcode,country\n"
                   "T1,B1,mug-1,2,\"12,50\",Ann,1 Main,X,123,us\n"
                   "T2,B1,MUG-1,x,,Ann,1 Main,X,123,us\n")
        created, errors = oe.importOrdersCsv(csvText, eng, dryRun=True)
        self.assertEqual([o.orderId for o in created], ["T1"])
        self.assertEqual(created[0].lines[0]["price"], 12.5)
        # the bad row is reported, and its order (created before the row failed) shows up as empty
        self.assertEqual(errors, ["line 3: invalid literal for int() with base 10: 'x'", "order T2: Order has no items"])


if __name__ == "__main__":
    unittest.main()
