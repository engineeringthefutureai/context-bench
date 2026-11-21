import unittest

from shopkit.common import TaxHelper


class TaxHelperTest(unittest.TestCase):

    def testRates(self):
        self.assertEqual(TaxHelper.getTaxRate('CA'), 0.0725)
        self.assertEqual(TaxHelper.getTaxRate('ZZ'), 0.0)
        self.assertTrue(TaxHelper.isTaxFree('OR'))

    def testCalc(self):
        self.assertEqual(TaxHelper.calcTax(100.0, 'NY'), 4.0)
        self.assertEqual(TaxHelper.addTax(10.0, 'TX'), 10.62)  # float rounding: 0.625 -> 0.62


if __name__ == '__main__':
    unittest.main()
