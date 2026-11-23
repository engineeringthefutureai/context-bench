import unittest

from shopkit.utils.strings import amount_str, sku_code, slugify


class StringsTest(unittest.TestCase):

    def test_slugify(self):
        self.assertEqual(slugify("Summer Sale 2024"), "summer_sale_2024")
        self.assertEqual(slugify("Café"), "café")

    def test_sku_code(self):
        self.assertEqual(sku_code("ab 12-x"), "AB12-X")

    def test_amount_str(self):
        self.assertEqual(amount_str(1999), "19.99")
        self.assertEqual(amount_str(5), "0.05")


if __name__ == "__main__":
    unittest.main()
