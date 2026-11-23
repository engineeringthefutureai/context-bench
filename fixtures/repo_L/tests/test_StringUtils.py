import unittest

from shopkit.common import StringUtils


class StringUtilsTest(unittest.TestCase):

    def testMakeSlug(self):
        self.assertEqual(StringUtils.makeSlug('Hello World'), 'hello-world')
        self.assertEqual(StringUtils.makeSlug('a  --  b'), 'a-b')
        self.assertEqual(StringUtils.makeSlug(None), '')
        self.assertEqual(len(StringUtils.makeSlug('x' * 100)), StringUtils.MAX_SLUG_LEN)

    def testCleanSKU(self):
        self.assertEqual(StringUtils.cleanSKU('ab-12 x'), 'AB-12X')
        self.assertEqual(StringUtils.cleanSKU(''), '')

    def testFormatPrice(self):
        self.assertEqual(StringUtils.formatPrice(12.5), '$12.50')
        self.assertEqual(StringUtils.formatPrice('oops'), '$0.00')

    def testIsEmail(self):
        self.assertTrue(StringUtils.isEmail('a@b.com'))
        self.assertFalse(StringUtils.isEmail('@b.com'))
        self.assertFalse(StringUtils.isEmail(None))

    def testTruncateAndTitle(self):
        self.assertEqual(StringUtils.truncateText('abcdefghij', 8), 'abcde...')
        self.assertEqual(StringUtils.toTitleCase('hELLO wORLD'), 'Hello World')
        self.assertEqual(StringUtils.padLeft(7, 3, '0'), '007')


if __name__ == '__main__':
    unittest.main()
