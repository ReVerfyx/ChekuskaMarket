import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import autoseller


class AutoSellerTest(unittest.TestCase):
    def section(self, sid, name, cid=1, game="Game"):
        return SimpleNamespace(
            id=sid,
            name=name,
            type=SimpleNamespace(name="COMMON"),
            category=SimpleNamespace(id=cid, name=game),
        )

    def test_search_game_name_strips_funpay_region(self):
        self.assertEqual(autoseller.search_game_name("World of Warcraft (RU, EU)"), "World of Warcraft")
        self.assertEqual(autoseller.search_game_name("Lineage 2 (RU)"), "Lineage 2")
        self.assertEqual(autoseller.search_game_name("Counter-Strike 2"), "Counter-Strike 2")

    def test_groups_once_per_game_and_prefers_guides(self):
        account = SimpleNamespace(subcategories=[
            self.section(1, "Items", 1, "A"),
            self.section(2, "Гайды", 1, "A"),
            self.section(3, "Other", 2, "B"),
        ])
        groups = autoseller.game_sections(account)
        self.assertEqual([(x[0].id, len(x[1])) for x in groups], [(1, 2), (2, 1)])
        self.assertEqual(autoseller.choose_section(groups[0][1]).id, 2)

    def test_bilingual_titles_keep_marker_and_limit(self):
        ru = autoseller.title_ru_for("A" * 100, "B" * 100)
        en = autoseller.title_en_for("A" * 100, "B" * 100)
        self.assertTrue(ru.endswith("🤖"))
        self.assertTrue(en.endswith("🤖"))
        self.assertIn("Автовыдача", ru)
        self.assertIn("Auto-delivery", en)
        self.assertLessEqual(len(ru), 120)
        self.assertLessEqual(len(en), 120)

    def test_guide_rejects_unverified_evidence(self):
        sources = [
            {"title":"a","url":"https://a.example/x","snippet":"A real snippet long enough about gameplay mechanics and beginner controls for testing.","domain":"a.example"},
            {"title":"b","url":"https://b.example/x","snippet":"Another real snippet long enough about useful gameplay tips and progression for testing.","domain":"b.example"},
        ]
        fake = {
            "topic_ru":"Старт",
            "topic_en":"Getting started",
            "steps":[
                {"text_ru":"Проверяемый практический совет номер один для нового игрока.",
                 "text_en":"A verifiable practical tip number one for a new player.",
                 "source_id":1,"evidence":"invented evidence"},
                {"text_ru":"Проверяемый практический совет номер два для нового игрока.",
                 "text_en":"A verifiable practical tip number two for a new player.",
                 "source_id":2,"evidence":"Another real snippet"},
                {"text_ru":"Проверяемый практический совет номер три для нового игрока.",
                 "text_en":"A verifiable practical tip number three for a new player.",
                 "source_id":1,"evidence":"A real snippet"},
            ],
        }
        with patch.object(autoseller, "ask_qwen", return_value=fake):
            self.assertIsNone(autoseller.make_guide("Game", sources))

    def test_auto_delivery_item_is_one_physical_line(self):
        encoded = autoseller.encode_auto_delivery_item("RU line 1\nRU line 2\nEN line 1")
        self.assertNotIn("\n", encoded)
        self.assertIn("\\n", encoded)
        self.assertEqual(encoded.count("\\n"), 2)

    def test_auto_delivery_stock_has_repeated_single_item(self):
        with patch.dict(os.environ, {"AUTOPUBLISH_STOCK_COPIES":"5"}):
            stock = autoseller.auto_delivery_stock("a\nb")
        self.assertEqual(len(stock), 5)
        self.assertEqual(len(set(stock)), 1)
        self.assertEqual(stock[0], "a\\nb")


if __name__ == "__main__":
    unittest.main()
