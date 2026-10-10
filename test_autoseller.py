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

    def test_groups_once_per_game_and_prefers_guides(self):
        account = SimpleNamespace(subcategories=[
            self.section(1, "Items", 1, "A"),
            self.section(2, "Гайды", 1, "A"),
            self.section(3, "Other", 2, "B"),
        ])
        groups = autoseller.game_sections(account)
        self.assertEqual([(x[0].id, len(x[1])) for x in groups], [(1, 2), (2, 1)])
        self.assertEqual(autoseller.choose_section(groups[0][1]).id, 2)

    def test_title_keeps_bot_marker_and_limit(self):
        title = autoseller.title_for("A" * 100, "B" * 100)
        self.assertTrue(title.endswith("🤖"))
        self.assertLessEqual(len(title), 120)

    def test_guide_rejects_unverified_evidence(self):
        sources = [
            {"title":"a","url":"https://a.example/x","snippet":"A real snippet long enough about gameplay mechanics and beginner controls for testing.","domain":"a.example"},
            {"title":"b","url":"https://b.example/x","snippet":"Another real snippet long enough about useful gameplay tips and progression for testing.","domain":"b.example"},
        ]
        fake = {
            "topic":"Старт",
            "steps":[
                {"text":"Проверяемый практический совет номер один для нового игрока.","source_id":1,"evidence":"invented evidence"},
                {"text":"Проверяемый практический совет номер два для нового игрока.","source_id":2,"evidence":"Another real snippet"},
                {"text":"Проверяемый практический совет номер три для нового игрока.","source_id":1,"evidence":"A real snippet"},
            ],
        }
        with patch.object(autoseller, "ask_qwen", return_value=fake):
            self.assertIsNone(autoseller.make_guide("Game", sources))


if __name__ == "__main__":
    unittest.main()
