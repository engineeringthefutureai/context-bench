import unittest

from shopkit.common import shipping_calc


class ShippingCalcTest(unittest.TestCase):

    def testDomestic(self):
        self.assertEqual(shipping_calc.getShippingCost('us', 0.5), 4.95)
        self.assertEqual(shipping_calc.getShippingCost('USA', 30), 19.95)
        self.assertEqual(shipping_calc.getShippingCost('US', 3, orderTotal=80), 0.0)

    def testInternational(self):
        self.assertEqual(shipping_calc.getShippingCost('DE', 2), 24.95)
        self.assertEqual(shipping_calc.getShippingCost('JP', 2), 79.95)

    def testZonesAndDays(self):
        self.assertEqual(shipping_calc.getZone('ca'), 2)
        self.assertEqual(shipping_calc.estimateDays('FR'), 10)
        self.assertAlmostEqual(shipping_calc.kgToLbs(1), 2.2)


if __name__ == '__main__':
    unittest.main()
