import unittest

from shopkit.common.OrderManager import OrderManager, STATUS_CANCELLED, STATUS_NEW, STATUS_PAID


class OrderManagerTest(unittest.TestCase):

    def setUp(self):
        self.mgr = OrderManager({})
        self.mgr.createOrder('A1', [])
        self.mgr.addItem('A1', 'ab-1', 2, 10.0)

    def testCreateAndDuplicate(self):
        self.assertEqual(self.mgr.getOrder('A1')['status'], STATUS_NEW)
        self.assertFalse(self.mgr.createOrder('A1', []))
        self.assertEqual(self.mgr.lastError, 'duplicate order')

    def testTotals(self):
        self.assertEqual(self.mgr.calcSubtotal('A1'), 20.0)
        self.assertEqual(self.mgr.calcTotal('A1'), 21.6)
        self.mgr.applyCoupon('A1', 10)
        self.assertEqual(self.mgr.calcTotal('A1'), 19.44)

    def testCouponCapped(self):
        self.mgr.applyCoupon('A1', 80)
        self.assertEqual(self.mgr.getOrder('A1')['coupon'], 50)

    def testStatus(self):
        self.assertTrue(self.mgr.changeStatus('A1', STATUS_PAID))
        self.assertEqual(len(self.mgr.getOrdersByStatus(STATUS_PAID)), 1)
        self.mgr.changeStatus('A1', STATUS_CANCELLED)
        self.assertFalse(self.mgr.changeStatus('A1', STATUS_PAID))

    def testMissingOrder(self):
        self.assertEqual(self.mgr.getOrder('nope'), None)
        self.assertEqual(self.mgr.orderSummary('nope'), '')


if __name__ == '__main__':
    unittest.main()
