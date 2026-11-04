from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.chat_history import ConversationStore
from amca.studio_projects import StudioProjectStore


class StudioProjectStoreTests(unittest.TestCase):
    def test_project_groups_many_chats_and_delete_only_unlinks_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state.sqlite3"
            conversations = ConversationStore(database)
            projects = StudioProjectStore(database)
            project = projects.save({
                "name": "AMCA 0.2.1",
                "description": "Durable product workspace",
                "instructions": "Verify changes before reporting completion.",
                "template": "software",
            })
            first = conversations.create(workspace_project_id=project["project_id"])
            second = conversations.create(workspace_project_id=project["project_id"])

            reopened = StudioProjectStore(database).get(project["project_id"])
            assert reopened is not None
            self.assertEqual(reopened["chat_count"], 2)
            self.assertEqual(reopened["instructions"], "Verify changes before reporting completion.")

            projects.delete(project["project_id"])
            self.assertIsNone(projects.get(project["project_id"]))
            self.assertIsNone(conversations.get(first["conversation_id"])["conversation"]["workspace_project_id"])
            self.assertIsNone(conversations.get(second["conversation_id"])["conversation"]["workspace_project_id"])

