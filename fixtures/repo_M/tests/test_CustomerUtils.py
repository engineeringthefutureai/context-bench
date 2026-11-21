import unittest

from shopkit.common import CustomerUtils, db


class CustomerUtilsTest(unittest.TestCase):

    def setUp(self):
        db.TABLES['customers'].clear()
        CustomerUtils._cache.clear()

    def testRegister(self):
        cid = CustomerUtils.registerCustomer('Ann@Example.com', 'ann lee')
        self.assertTrue(cid > 0)
        self.assertEqual(CustomerUtils.getCustomer(cid)['name'], 'Ann Lee')
        self.assertEqual(CustomerUtils.registerCustomer('ann@example.com', 'x'), -2)
        self.assertEqual(CustomerUtils.registerCustomer('nope', 'x'), -1)

    def testVipAndMask(self):
        cid = CustomerUtils.registerCustomer('bo@example.com', 'bo')
        CustomerUtils.addSpend(cid, 1500.0)
        self.assertTrue(CustomerUtils.isVip(cid))
        self.assertEqual(CustomerUtils.maskCard('4242424242424242'), 'XXXX-XXXX-XXXX-4242')


if __name__ == '__main__':
    unittest.main()
