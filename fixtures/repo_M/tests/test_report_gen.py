import unittest

from shopkit.common import report_gen


class ReportGenTest(unittest.TestCase):

    def testSalesReport(self):
        orders = [{'orderId': 'A1', 'items': [{'sku': 'X', 'qty': 2, 'price': 5.0}]}]
        rg = report_gen.salesReport(orders)
        self.assertEqual(rg.getRowCount(), 2)
        self.assertIn('<td>$10.00</td>', rg.toHtml())

    def testPaginateList(self):
        self.assertEqual(report_gen.paginateList(list(range(50)), 1, 20), list(range(20, 40)))
        self.assertEqual(report_gen.paginateList([1, 2], 5), [])


if __name__ == '__main__':
    unittest.main()
