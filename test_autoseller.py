import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autoseller
import bot


class FakeLotFields:
    def __init__(self):
        self.edits = {}

    def edit_fields(self, values):
        self.edits.update(values)


class FakeAccount:
    id = 1

    def __init__(self):
        self.lots = []
        self.saved = []

    def get_user(self, user_id):
        return SimpleNamespace(get_lots=lambda: self.lots)

    def save_lot(self, fields):
        self.saved.append(fields)
        self.lots.append(SimpleNamespace(id=51, description=fields.title_ru))


class AutoSellerTest(unittest.TestCase):
    def test_category_per_item(self):
        acc = SimpleNamespace(subcategories=[
            SimpleNamespace(id=1, name='Гайды', category=SimpleNamespace(name='Roblox')),
            SimpleNamespace(id=2, name='Гайды', category=SimpleNamespace(name='Windows'))])
        self.assertEqual(autoseller.section_id(acc, {'category_keywords': ['windows']}), 2)
        self.assertEqual(autoseller.section_id(acc, {'node_id': 1}), 1)

    def test_publish_and_dedupe(self):
        item = {"sku": "my_guide", "match": "Гайд Roblox: тест", "kind": "guide",
                "delivery_text": "Авторский тестовый материал для авто-выдачи."}
        acc = FakeAccount()
        fields = FakeLotFields()
        with tempfile.TemporaryDirectory() as d, \
                patch.object(autoseller, "STATE", Path(d) / "state.json"), \
                patch.object(bot, "DRY_RUN", False), \
                patch.object(autoseller, "new_lot_fields", return_value=fields):
            state = {}
            autoseller.publish(acc, item, "7.50", 10, state)
            autoseller.publish(acc, item, "7.50", 10, state)
            self.assertEqual(len(acc.saved), 1)
            self.assertEqual(fields.title_ru, item["match"])
            self.assertEqual(fields.price, 7.5)
            self.assertEqual(state["my_guide"], {"status": "published", "lot_id": "51"})

    def test_ambiguous_section_stops(self):
        acc = SimpleNamespace(subcategories=[SimpleNamespace(id=1, name="Предметы",
                                     category=SimpleNamespace(name="Roblox"))])
        with patch.dict("os.environ", {"FUNPAY_GUIDE_NODE_ID": ""}):
            with self.assertRaises(ValueError):
                autoseller.section_id(acc)


if __name__ == "__main__":
    unittest.main()
