import unittest
from datetime import date, datetime

from shopkit.core import reporting as r


class FiscalCalendarTest(unittest.TestCase):

    def testWeeks(self):
        self.assertEqual(r.fiscalWeek(date(2026, 2, 1)), "FY2026-W01")
        self.assertEqual(r.fiscalWeek(date(2026, 2, 2)), "FY2026-W02")
        self.assertEqual(r.fiscalWeek(date(2027, 1, 31)), "FY2026-W53")
        self.assertEqual(r.fiscalWeek(datetime(2026, 1, 31, 23, 0)), "FY2025-W53")

    def testPeriodsAndQuarters(self):
        self.assertEqual(r.fiscalPeriod(date(2026, 2, 15)), 1)
        self.assertEqual(r.fiscalPeriod(date(2026, 1, 15)), 12)
        self.assertEqual(r.fiscalQuarter(date(2026, 5, 1)), "FY2026-Q2")
        self.assertEqual(r.periodBounds(2026, 12), (date(2027, 1, 1), date(2027, 1, 31)))


class SalesTaxTest(unittest.TestCase):

    def testNexusTotalsOnly(self):
        rows = [{"date": date(2026, 3, 1), "state": "ca", "taxableCents": 10000, "taxCents": 725},
                {"date": date(2026, 3, 2), "state": "OR", "taxableCents": 5000, "taxCents": 0},
                {"date": date(2026, 3, 3), "state": "CA", "taxableCents": 2000, "taxCents": 145, "refundedTaxCents": 145},
                {"date": date(2026, 5, 1), "state": "CA", "taxableCents": 1, "taxCents": 1}]
        rep = r.salesTaxByState(rows, date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(rep["CA"]["net"], 7.25)
        self.assertFalse(rep["OR"]["nexus"])
        self.assertEqual(rep["TOTAL"]["orders"], 2)
        self.assertIn("CA,Y,2,120.00,8.70,1.45,7.25", r.salesTaxCsv(rep))


class FifoTest(unittest.TestCase):

    def testOldestLayersFirst(self):
        receipts = [(date(2026, 1, 1), "A", 10, 100), (date(2026, 2, 1), "A", 10, 150)]
        issues = [(date(2026, 2, 15), "A", 12), (date(2026, 2, 16), "B", 1)]
        val, neg = r.inventoryValuationFifo(receipts, issues)
        self.assertEqual(val["A"], {"qty": 8, "valueCents": 1200, "avgCostCents": 150.0})
        self.assertEqual(neg, {"B": 1})


class ReportTest(unittest.TestCase):

    def testGenerateText(self):
        orders = [{"id": "1", "date": datetime(2026, 3, 3, 10), "customer": "C1", "total": 50.0,
                   "items": [{"sku": "A", "qty": 2, "price": 25.0}], "channel": "web", "status": "paid",
                   "country": "US"}]
        r.clearCache()
        text = r.ReportGenerator(orders, {"A": {"title": "Apple"}}).generate(date(2026, 3, 3))
        self.assertIn("WEEKLY REPORT FY2026-W06", text)
        self.assertIn("Apple", text)
        self.assertIn("$50.00", text)


if __name__ == "__main__":
    unittest.main()
