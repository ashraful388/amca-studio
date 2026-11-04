from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.automations import AutomationStore


class AutomationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = AutomationStore(Path(self.temporary.name) / "state.sqlite3")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_definitions_are_durable_and_disabled_by_default(self) -> None:
        saved = self.store.save({
            "name": "Weekly review", "project_id": "project-1",
            "goal": "Review the project and report blockers.",
            "automation_type": "recurring", "schedule": "Monday 09:00",
        })
        self.assertFalse(saved["enabled"])
        reopened = AutomationStore(self.store.database)
        self.assertEqual(reopened.list()[0]["id"], saved["id"])

    def test_update_mark_run_and_delete(self) -> None:
        saved = self.store.save({
            "name": "Monitor", "project_id": "project-2", "goal": "Check status.",
            "automation_type": "monitor", "schedule": "every hour", "enabled": True,
        })
        updated = self.store.save({**saved, "schedule": "every two hours", "enabled": False})
        self.assertEqual(updated["schedule"], "every two hours")
        self.assertFalse(updated["enabled"])
        marked = self.store.mark_run(saved["id"], "run-1")
        self.assertEqual(marked["last_run_id"], "run-1")
        self.store.delete(saved["id"])
        self.assertEqual(self.store.list(), [])

    def test_rejects_unknown_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported"):
            self.store.save({
                "name": "Bad", "project_id": "p", "goal": "g",
                "automation_type": "arbitrary", "schedule": "manual",
            })


if __name__ == "__main__":
    unittest.main()
