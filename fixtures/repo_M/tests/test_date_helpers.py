import time
import unittest

from shopkit.common import date_helpers


class DateHelpersTest(unittest.TestCase):

    def testAddWorkDays(self):
        friday = time.mktime((2026, 11, 20, 12, 0, 0, 0, 0, -1))
        monday = date_helpers.addWorkDays(friday, 1)
        self.assertEqual(date_helpers.formatDate(monday), '11/23/2026')

    def testParseAndDays(self):
        a = date_helpers.parseDate('01/01/2026')
        b = date_helpers.parseDate('01/11/2026')
        self.assertEqual(date_helpers.daysBetween(a, b), 10)
        self.assertEqual(date_helpers.parseDate('nonsense'), None)


if __name__ == '__main__':
    unittest.main()
