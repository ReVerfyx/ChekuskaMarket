import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import farm


class FarmTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.patch_db = patch.object(farm, "DB_PATH", root / "farm.sqlite3")
        self.patch_profiles = patch.object(farm, "PROFILES", root / "profiles.json")
        self.patch_db.start()
        self.patch_profiles.start()
        self.addCleanup(self.patch_db.stop)
        self.addCleanup(self.patch_profiles.stop)
        self.db = farm.db_open()
        self.addCleanup(self.db.close)
        with self.db:
            self.db.execute("INSERT INTO accounts(alias, game) VALUES ('mine', 'game')")
        self.worker = root / "worker.py"
        self.worker.write_text("#!/usr/bin/env python3\nimport json,sys\np=json.load(sys.stdin)\nprint(json.dumps({'observed_progress':p['target'],'account_alias':p['account_alias'],'game':p['game'],'session_closed':True}))\n")
        self.worker.chmod(0o700)
        farm.PROFILES.write_text(json.dumps({"game": {"command": [str(self.worker)]}}))

    def test_job_reaches_observed_target(self):
        job_id = farm.enqueue(self.db, "mine", "game", 100)
        with self.assertRaises(ValueError):
            farm.enqueue(self.db, "mine", "game", 100)
        self.assertTrue(farm.process_one(self.db))
        row = self.db.execute("SELECT status, progress FROM jobs WHERE id=?", (job_id,)).fetchone()
        self.assertEqual(tuple(row), ("done", 100))

    def test_no_progress_fails_instead_of_looping(self):
        self.worker.write_text("#!/usr/bin/env python3\nprint('{\"observed_progress\":0}')\n")
        job_id = farm.enqueue(self.db, "mine", "game", 100)
        farm.process_one(self.db)
        self.assertEqual(self.db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0], "failed")

    def test_progress_without_logout_confirmation_fails(self):
        self.worker.write_text("#!/usr/bin/env python3\nimport json,sys\np=json.load(sys.stdin)\nprint(json.dumps({'observed_progress':p['target'],'account_alias':p['account_alias'],'game':p['game']}))\n")
        job_id = farm.enqueue(self.db, "mine", "game", 100)
        farm.process_one(self.db)
        self.assertEqual(self.db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0], "failed")

    def test_gift_requires_matching_receipt(self):
        self.worker.write_text("#!/usr/bin/env python3\nimport json,sys\np=json.load(sys.stdin)\nprint(json.dumps({'confirmed':True,'recipient':p['recipient'],'item':p['item'],'receipt':'game-42'}))\n")
        farm.enqueue_gift(self.db, order_id="F-42", alias="mine", game="game",
                          item="Енот", recipient="Buyer_1", method="friend")
        farm.enqueue_gift(self.db, order_id="F-42", alias="mine", game="game",
                          item="Енот", recipient="Buyer_1", method="friend")
        self.assertTrue(farm.process_gift(self.db))
        row = self.db.execute("SELECT status, confirmation FROM gifts WHERE order_id='F-42'").fetchone()
        self.assertEqual(tuple(row), ("done", "game-42"))

    def test_mismatched_recipient_is_not_delivered(self):
        self.worker.write_text("#!/usr/bin/env python3\nprint('{\"confirmed\":true,\"recipient\":\"Other\",\"item\":\"Енот\",\"receipt\":\"x\"}')\n")
        farm.enqueue_gift(self.db, order_id="F-43", alias="mine", game="game",
                          item="Енот", recipient="Buyer_1", method="friend")
        farm.process_gift(self.db)
        self.assertEqual(self.db.execute("SELECT status FROM gifts WHERE order_id='F-43'").fetchone()[0], "failed")

    def test_private_link_host_validation(self):
        with self.assertRaises(ValueError):
            farm.enqueue_gift(self.db, order_id="F-44", alias="mine", game="game", item="Енот",
                              recipient="Buyer_1", method="private", server_url="https://roblox.com.evil.example/x")


if __name__ == "__main__":
    unittest.main()
