import unittest

from shopkit.common import inventory_sync


class InventorySyncTest(unittest.TestCase):

    def setUp(self):
        inventory_sync.stockLevels.clear()

    def testSyncFile(self):
        n = inventory_sync.syncFile('# header\nab-1|2|A\nbad line\ncd-2|10|B\n')
        self.assertEqual(n, 2)
        self.assertEqual(inventory_sync.stockLevels['AB-1'], 2)
        self.assertEqual(len(inventory_sync.syncErrors), 1)
        self.assertEqual(inventory_sync.getLowStock(), ['AB-1'])

    def testBatches(self):
        self.assertEqual(inventory_sync.splitIntoBatches([1, 2, 3], 2), [[1, 2], [3]])


if __name__ == '__main__':
    unittest.main()
