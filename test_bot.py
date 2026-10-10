import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bot
import farm


class FakeAccount:
    def __init__(self):
        self.messages = []
        self.id = 999

    def get_chat_by_name(self, name, create):
        return SimpleNamespace(id="chat-" + name)

    def send_message(self, chat_id, message):
        self.messages.append((chat_id, message))


class BotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.db_patch = patch.object(bot, "DB_PATH", root / "bot.sqlite3")
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.dry_patch = patch.object(bot, "DRY_RUN", False)
        self.dry_patch.start()
        self.addCleanup(self.dry_patch.stop)
        self.price_patch = patch.object(bot, "PRICES_PATH", root / "prices.json")
        self.price_patch.start()
        self.addCleanup(self.price_patch.stop)
        self.db = bot.database()
        self.addCleanup(self.db.close)
        self.acc = FakeAccount()

    def test_order_idempotence_and_completion(self):
        order = SimpleNamespace(id="F-1", description="Гайд Roblox: приватный сервер и доступ друзьям", buyer_username="Buyer")
        bot.receive_order(self.db, self.acc, order)
        bot.receive_order(self.db, self.acc, order)
        self.assertEqual(len(self.acc.messages), 0)
        self.assertEqual(self.db.execute("SELECT status FROM orders WHERE id='F-1'").fetchone()[0], "waiting")

    def test_unknown_order_not_sent(self):
        bot.receive_order(self.db, self.acc, SimpleNamespace(id="F-2", description="Неизвестный лот", buyer_username="Buyer"))
        self.assertEqual(self.acc.messages, [])

    def test_unavailable_leveling_is_not_processed_as_working_service(self):
        order = SimpleNamespace(id="F-L", description="Roblox Steal An Egg: прокачка Speed на дорожке",
                                buyer_username="Buyer")
        bot.receive_order(self.db, self.acc, order)
        self.assertIsNone(self.db.execute("SELECT 1 FROM orders WHERE id='F-L'").fetchone())
        self.assertEqual(self.acc.messages, [])

    def test_dry_run_does_not_mark_delivered(self):
        order = SimpleNamespace(id="F-3", description="Гайд Roblox: защита аккаунта и завершение сеансов", buyer_username="Buyer")
        with patch.object(bot, "DRY_RUN", True):
            bot.receive_order(self.db, self.acc, order)
        self.assertEqual(self.acc.messages, [])
        self.assertEqual(self.db.execute("SELECT status FROM orders WHERE id='F-3'").fetchone()[0], "new")
        bot.receive_order(self.db, self.acc, order)
        self.assertEqual(len(self.acc.messages), 0)

    def test_private_feed_rejected(self):
        for url in ("http://example.org/rss", "https://127.0.0.1/rss", "file:///etc/passwd"):
            with self.assertRaises(ValueError):
                bot.valid_public_feed(url)

    def test_redaction(self):
        text = bot.redact("пароль: hunter2 token=abc123")
        self.assertNotIn("hunter2", text)
        self.assertNotIn("abc123", text)

    def test_chat_arithmetic_only(self):
        self.assertEqual(bot.solve_chat_challenge("капча: 7 + 9?"), "16")
        self.assertEqual(bot.solve_chat_challenge("Captcha 3×8="), "24")
        self.assertIsNone(bot.solve_chat_challenge("код почты: 123456"))
        self.assertIsNone(bot.solve_chat_challenge("капча на картинке"))

    def test_pre_purchase_chat_arithmetic_once(self):
        event = SimpleNamespace(message=SimpleNamespace(id="m-1", chat_id="chat-Buyer",
                            author_id=123, text="капча: 7 + 9?"))
        bot.reply_to_message(self.db, self.acc, event)
        bot.reply_to_message(self.db, self.acc, event)
        self.assertEqual(self.acc.messages, [("chat-Buyer", "16")])

    def test_paid_guides_have_real_content_and_drafts(self):
        guides = [x for x in bot.catalog() if x["kind"] == "guide"]
        self.assertTrue(all("price_rub" not in x for x in guides))
        self.assertTrue(all("https://" in bot.guide_text(x) for x in guides))
        self.assertIn("бесплатной официальной документации", bot.lot_drafts())
        self.assertIn("не задана", bot.lot_drafts())

    def test_individual_prices_and_validation(self):
        bot.set_price("roblox_security_guide", "2,50")
        bot.set_price("studio_luau_first_script", "19")
        self.assertEqual(bot.prices()["roblox_security_guide"], "2.50")
        self.assertEqual(bot.prices()["studio_luau_first_script"], "19.00")
        self.assertIn("2.50 ₽", bot.lot_drafts())
        for invalid in ("0", "-3", "nan", "1.234", "abc"):
            with self.assertRaises(ValueError):
                bot.set_price("roblox_security_guide", invalid)

    def test_notifier_only_sends_confirmed_gift_once(self):
        root = Path(self.tmp.name)
        with patch.object(farm, "DB_PATH", root / "farm.sqlite3"):
            fdb = farm.db_open()
            with fdb:
                fdb.execute("INSERT INTO gifts(order_id,alias,game,item,recipient,method,status,confirmation,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            ("F-7", "mine", "demo", "Енот", "Buyer", "friend", "done", "receipt-7", "now", "now"))
            fdb.close()
            with self.db:
                self.db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
                                ("F-7", "Buyer", "chat-Buyer", "demo_gift_raccoon", "gift", "waiting", "now", "now"))
            bot.sync_gifts(self.db, self.acc)
            bot.sync_gifts(self.db, self.acc)
        self.assertEqual(len(self.acc.messages), 1)
        self.assertIn("receipt-7", self.acc.messages[0][1])
        self.assertEqual(self.db.execute("SELECT status FROM orders WHERE id='F-7'").fetchone()[0], "done")


if __name__ == "__main__":
    unittest.main()
