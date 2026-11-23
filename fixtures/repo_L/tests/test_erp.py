import unittest
from datetime import date

from shopkit.catalog.models import Product
from shopkit.core import erp


class ErpTest(unittest.TestCase):

    def testSkuMapping(self):
        self.assertEqual(erp.toErpSku("MUG-1"), "MUG000000001")
        self.assertEqual(erp.toErpSku("tea-set-12"), "TEASET000012")
        self.assertEqual(erp.toErpSku("GIFTCARD"), "GIFTCARD0000")
        self.assertEqual(erp.fromErpSku("MUG000000001", ["X-1", "MUG-1"]), "MUG-1")
        with self.assertRaises(ValueError):
            erp.toErpSku("ABCDEFGHIJKLM1")

    def testAmounts(self):
        self.assertEqual(erp.erpAmount(123450), "1234.50")
        self.assertEqual(erp.erpAmount(-5), "-0.05")
        self.assertEqual(erp.parseErpAmount("12,5"), 1250)

    def testStockFile(self):
        rec = lambda item, wh, on, bl: item + wh + str(on).rjust(9) + str(bl).rjust(9)
        text = "\n".join(["* header", rec("MUG000000001", "01", 10, 3), rec("MUG000000001", "05", 9, 0),
                          rec("MUG000000001", "02", "4-", 0), rec("ZZZ000000001", "01", 1, 0), "EOF 4"])
        levels, warnings = erp.parseStockFile(text, ["MUG-1"])
        self.assertEqual(levels, {("MAIN", "MUG-1"): 7, ("EAST", "MUG-1"): 0})
        self.assertEqual(len(warnings), 1)
        with self.assertRaises(ValueError):
            erp.parseStockFile(rec("MUG000000001", "01", 1, 0) + "\nEOF 2", ["MUG-1"])

    def testPushInvoiceRetriesThenSucceeds(self):
        replies = ["<Reply status='ERR'><Error code='E500'>busy</Error></Reply>", "<Reply status='OK' ref='R1'/>"]
        sync = erp.ErpSync(lambda body, headers: replies.pop(0), secret="s", sleep=lambda s: None)
        inv = {"number": "INV-1", "date": date(2026, 3, 1), "lines": [{"sku": "MUG-1", "qty": 1, "unitCents": 1000}]}
        self.assertEqual(sync.pushInvoice(inv), (True, "R1"))
        self.assertEqual(sync.pushInvoice(inv), (True, "dup"))

    def testValidationErrorIsNotRetried(self):
        calls = []
        def transport(body, headers):
            calls.append(1)
            return "<Reply status='ERR'><Error code='E101'>bad item</Error></Reply>"
        sync = erp.ErpSync(transport, sleep=lambda s: None)
        ok, err = sync.pushInvoice({"number": "INV-2", "date": date(2026, 3, 1), "lines": [{"sku": "A-1", "qty": 1, "unitCents": 1}]})
        self.assertFalse(ok)
        self.assertEqual(len(calls), 1)

    def testSyncProductsOverwritesTitles(self):
        products = {"MUG-1": Product("MUG-1", "Mug", 1000)}
        n = erp.ErpSync().syncProducts([{"item": "MUG000000001", "desc": "Mug large", "price": "12.00", "active": "Y"}], products)
        self.assertEqual(n, 1)
        self.assertEqual((products["MUG-1"].title, products["MUG-1"].price_cents), ("Mug large", 1200))


if __name__ == "__main__":
    unittest.main()
