import unittest
from datetime import date

from shopkit.core import pricing as p


class CashRoundingTest(unittest.TestCase):

    def testCad(self):
        self.assertEqual([p.cashRoundCents(c, "CAD") for c in (101, 102, 103, 104, 105, 106, 107, 108, 109)],
                         [100, 100, 105, 105, 105, 105, 105, 110, 110])
        self.assertEqual(p.cashRoundCents(-103, "CAD"), -105)
        self.assertEqual(p.cashRoundCents(103, "USD"), 103)


class CouponTest(unittest.TestCase):

    def testRoundTrip(self):
        code = p.makeCouponCode(15, 2023)
        self.assertEqual(p.parseCouponCode(code), 15)
        self.assertEqual(p.parseCouponCode(code.lower()), 15)
        self.assertIsNone(p.parseCouponCode(code, today=date(2025, 1, 1)))
        self.assertIsNone(p.parseCouponCode("SAVE15-2023-AAAA"))


class PriceCalculatorTest(unittest.TestCase):

    def setUp(self):
        self.cat = {"A": {"price": 100.0, "cost": 70.0}, "B": {"price": 10.0, "memberPrice": 8.0}}

    def testDiscountsAddUpButAreCappedAndFloored(self):
        calc = p.PriceCalculator(self.cat, overrides={})
        self.assertEqual(calc.linePrice("A", 20), 95.0)
        self.assertEqual(calc.linePrice("A", 100, member=True, couponPct=20), 70.0)     # capped 40%, floored at cost
        self.assertEqual(calc.linePrice("B", 1, member=True), 8.0)

    def testOverrides(self):
        calc = p.PriceCalculator(self.cat, overrides={"A": 50})
        self.assertEqual(calc.basePrice("A"), 50.0)


if __name__ == "__main__":
    unittest.main()
