import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import bot
import leveling_guard as guard

TITLE = next(x['match'] for x in bot.catalog() if x['sku'] == guard.SKU)

class Fake:
    id = 7
    def __init__(self):
        self.status = 'PAID'
        self.refunds = 0
        self.active = True
        self.disconnect = False
        self.delist_error = False
    def get_order(self, oid):
        return NS(seller_id=7, short_description=TITLE, status=NS(name=self.status))
    def get_user(self, uid):
        return NS(get_lots=lambda: [NS(id=1, description=TITLE)])
    def get_lot_fields(self, lid):
        return NS(title_ru=TITLE, active=self.active)
    def save_lot(self, fields):
        if self.delist_error:
            raise OSError('offline')
        self.active = fields.active
    def refund(self, oid):
        self.refunds += 1
        self.status = 'REFUNDED'
        if self.disconnect:
            raise TimeoutError()

class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(bot, 'DB_PATH', Path(self.tmp.name) / 'test.db')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = bot.database()
        self.addCleanup(self.db.close)
        guard.enqueue(self.db, 'A')
        self.acc = Fake()
    def test_success_and_duplicate(self):
        guard.process(self.db, self.acc)
        guard.process(self.db, self.acc)
        self.assertEqual(self.acc.refunds, 1)
        self.assertFalse(self.acc.active)
    def test_lost_reply_reconciles(self):
        self.acc.disconnect = True
        guard.process(self.db, self.acc)
        guard.process(self.db, self.acc)
        self.assertEqual(self.acc.refunds, 1)
        self.assertEqual(self.db.execute('SELECT refund_done FROM leveling_failures').fetchone()[0], 1)
    def test_delisting_error_does_not_block_refund(self):
        self.acc.delist_error = True
        guard.process(self.db, self.acc)
        self.assertEqual(self.acc.status, 'REFUNDED')
        self.acc.delist_error = False
        guard.process(self.db, self.acc)
        self.assertFalse(self.acc.active)
    def test_dry_run(self):
        guard.process(self.db, self.acc, dry_run=True)
        self.assertEqual(self.acc.refunds, 0)
        self.assertTrue(self.acc.active)

if __name__ == '__main__':
    unittest.main()
