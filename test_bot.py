import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bot


class FakeAccount:
    id = 999
    username = "seller"
    def __init__(self):
        self.messages = []
        self.raised = []
    def get_chat_by_name(self, buyer, create):
        return SimpleNamespace(id="chat-" + buyer)
    def send_message(self, chat_id, content):
        self.messages.append((str(chat_id), content))
    def raise_lots(self, category_id):
        self.raised.append(category_id)


class BotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "guides").mkdir()
        (root / "stock").mkdir()
        (root / "guides" / "a.txt").write_text("DELIVERY-A", encoding="utf-8")
        (root / "stock" / "b.txt").write_text("CODE-1\nCODE-2\n", encoding="utf-8")
        catalog = [
            {"sku":"a","match":"LOT A","kind":"text","instructions":"Hi","delivery_file":"a.txt"},
            {"sku":"b","match":"LOT B","kind":"stock","instructions":"Take","stock_file":"b.txt"},
            {"sku":"s","match":"LOT S","kind":"service","instructions":"Send details"},
        ]
        (root / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
        (root / "faq.json").write_text(json.dumps([
            {"patterns":["доставка"],"answer":"FAQ DELIVERY"}
        ]), encoding="utf-8")
        self.patches = [
            patch.object(bot, "ROOT", root),
            patch.object(bot, "DB_PATH", root / "data.sqlite3"),
            patch.object(bot, "CATALOG_PATH", root / "catalog.json"),
            patch.object(bot, "FAQ_PATH", root / "faq.json"),
            patch.object(bot, "DRY_RUN", False),
            patch.object(bot, "AI_ENABLED", False),
        ]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)
        self.db = bot.database(); self.addCleanup(self.db.close)
        self.acc = FakeAccount()

    def test_text_delivery_is_idempotent(self):
        order = SimpleNamespace(id="O1", description="LOT A", buyer_username="Buyer")
        bot.receive_order(self.db, self.acc, order)
        bot.receive_order(self.db, self.acc, order)
        self.assertEqual(len(self.acc.messages), 1)
        self.assertIn("DELIVERY-A", self.acc.messages[0][1])
        self.assertEqual(self.db.execute("SELECT status FROM orders WHERE id='O1'").fetchone()[0], "delivered")

    def test_stock_consumes_one_line(self):
        order = SimpleNamespace(id="O2", description="LOT B", buyer_username="Buyer")
        bot.receive_order(self.db, self.acc, order)
        self.assertIn("CODE-1", self.acc.messages[0][1])
        self.assertEqual((bot.ROOT / "stock" / "b.txt").read_text().strip(), "CODE-2")

    def test_service_waits(self):
        order = SimpleNamespace(id="O3", description="LOT S", buyer_username="Buyer")
        bot.receive_order(self.db, self.acc, order)
        self.assertEqual(self.db.execute("SELECT status FROM orders WHERE id='O3'").fetchone()[0], "waiting")

    def test_secret_is_rejected(self):
        event = SimpleNamespace(message=SimpleNamespace(id="M1", chat_id="x", author_id=1, text="пароль: secret"))
        bot.reply_to_message(self.db, self.acc, event)
        self.assertIn("Не присылайте пароли", self.acc.messages[0][1])

    def test_faq_without_ai(self):
        event = SimpleNamespace(message=SimpleNamespace(id="M2", chat_id="x", author_id=1, text="как доставка?"))
        bot.reply_to_message(self.db, self.acc, event)
        self.assertEqual(self.acc.messages[0][1], "FAQ DELIVERY")

    def test_dry_run_does_not_consume_stock(self):
        order = SimpleNamespace(id="O4", description="LOT B", buyer_username="Buyer")
        with patch.object(bot, "DRY_RUN", True):
            bot.receive_order(self.db, self.acc, order)
        self.assertEqual((bot.ROOT / "stock" / "b.txt").read_text().splitlines()[0], "CODE-1")

    def test_raise_ids(self):
        with patch.dict("os.environ", {"RAISE_CATEGORY_IDS":"1, 7"}):
            result = bot.raise_lots_once(self.acc)
        self.assertEqual(result, {1:"ok", 7:"ok"})
        self.assertEqual(self.acc.raised, [1, 7])


if __name__ == "__main__":
    unittest.main()
