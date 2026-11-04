from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from amca.chat_history import ConversationStore


class ConversationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "amca.sqlite3"
        self.store = ConversationStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_conversation_messages_preferences_search_and_delete_are_durable(self) -> None:
        created = self.store.create(
            mode="joint", participant_ids=("ollama:a", "api:b")
        )
        conversation_id = created["conversation_id"]
        first = self.store.add_message(
            conversation_id,
            message_id="message-1",
            role="user",
            content="Compare durable chat histories.",
            meta="ollama:a · api:b",
            project_id="project-1",
        )
        replay = self.store.add_message(
            conversation_id,
            message_id="message-1",
            role="user",
            content="Compare durable chat histories.",
            meta="ollama:a · api:b",
            project_id="project-1",
        )
        self.assertEqual(first, replay)

        self.store.rename(conversation_id, "Durable comparison")
        self.store.update_preferences(
            conversation_id, mode="solo", participant_ids=("ollama:a",)
        )

        reopened = ConversationStore(self.database).get(conversation_id)
        assert reopened is not None
        self.assertEqual(reopened["conversation"]["title"], "Durable comparison")
        self.assertEqual(reopened["conversation"]["mode"], "solo")
        self.assertEqual(reopened["conversation"]["participant_ids"], ["ollama:a"])
        self.assertEqual(reopened["messages"][0]["content"], "Compare durable chat histories.")
        self.assertEqual(
            self.store.list(search="histories")[0]["conversation_id"],
            conversation_id,
        )

        self.store.delete(conversation_id)
        self.assertIsNone(self.store.get(conversation_id))
        connection = sqlite3.connect(self.database)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM desktop_conversation_messages"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_active_run_blocks_delete_and_terminal_result_restores_idle_chat(self) -> None:
        conversation = self.store.create()
        conversation_id = conversation["conversation_id"]
        self.store.attach_run(
            conversation_id, run_id="run-1", project_id="project-1"
        )
        with self.assertRaisesRegex(ValueError, "active run"):
            self.store.delete(conversation_id)

        self.store.finish_run(
            conversation_id,
            run_id="run-1",
            role="assistant",
            content="The bounded run finished.",
            meta="model · Partial · Offline Joint",
            status="idle",
            project_id="project-1",
        )
        document = self.store.get(conversation_id)
        assert document is not None
        self.assertIsNone(document["conversation"]["active_run_id"])
        self.assertEqual(document["conversation"]["status"], "idle")
        self.assertEqual(document["messages"][-1]["run_id"], "run-1")

    def test_message_id_collision_is_rejected(self) -> None:
        conversation_id = self.store.create()["conversation_id"]
        self.store.add_message(
            conversation_id,
            message_id="same-id",
            role="user",
            content="First message",
        )
        with self.assertRaisesRegex(ValueError, "collision"):
            self.store.add_message(
                conversation_id,
                message_id="same-id",
                role="user",
                content="Different message",
            )

    def test_credential_shaped_chat_text_is_rejected_before_sqlite_write(self) -> None:
        conversation_id = self.store.create()["conversation_id"]
        canary = "gsk_abcdefghijklmnopqrstuvwxyz1234567890"
        with self.assertRaisesRegex(ValueError, "credential material"):
            self.store.add_message(
                conversation_id,
                message_id="unsafe",
                role="user",
                content=f"Please use {canary}",
            )
        self.assertNotIn(canary.encode(), self.database.read_bytes())

    def test_existing_databases_gain_architecture_modes_without_data_loss(self) -> None:
        """A pre-C1.1 binary-mode database migrates in place, rows intact."""

        legacy_database = Path(self.temporary.name) / "legacy.sqlite3"
        legacy_sql = """
            CREATE TABLE desktop_conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                mode TEXT NOT NULL CHECK (mode IN ('solo', 'joint')),
                participant_ids_json TEXT NOT NULL,
                workspace_project_id TEXT,
                project_id TEXT,
                active_run_id TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_message_at TEXT
            )
        """
        connection = sqlite3.connect(legacy_database)
        try:
            connection.execute(legacy_sql)
            connection.execute(
                "INSERT INTO desktop_conversations VALUES "
                "('legacy-id', 'Legacy chat', 'joint', '[\"ollama:a\"]', "
                "NULL, 'project-1', NULL, 'idle', '2026-01-01T00:00:00Z', "
                "'2026-01-01T00:00:00Z', NULL)"
            )
            connection.commit()
        finally:
            connection.close()

        migrated = ConversationStore(legacy_database)

        stored = migrated.get("legacy-id")
        assert stored is not None
        self.assertEqual(stored["conversation"]["mode"], "joint")
        self.assertEqual(stored["conversation"]["participant_ids"], ["ollama:a"])
        widened = migrated.create(
            mode="adaptive_hybrid", participant_ids=("ollama:a", "ollama:b")
        )
        self.assertEqual(widened["mode"], "adaptive_hybrid")

        # The messages foreign key and standard indexes survive the rebuild.
        migrated.add_message(
            "legacy-id",
            message_id="after-migration",
            role="user",
            content="History intact.",
        )
        connection = sqlite3.connect(legacy_database)
        try:
            names = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()
        self.assertIn("idx_desktop_conversations_updated", names)
        self.assertIn("idx_desktop_messages_conversation", names)
        self.assertNotIn("desktop_conversations_new", tables)

    def test_migration_is_idempotent_on_already_widened_databases(self) -> None:
        legacy_database = Path(self.temporary.name) / "idempotent.sqlite3"
        connection = sqlite3.connect(legacy_database)
        try:
            connection.execute(
                """
                CREATE TABLE desktop_conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK (mode IN ('solo', 'joint')),
                    participant_ids_json TEXT NOT NULL,
                    workspace_project_id TEXT,
                    project_id TEXT,
                    active_run_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_at TEXT
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        first = ConversationStore(legacy_database)
        second = ConversationStore(legacy_database)
        widened = second.create(
            mode="independent_discuss", participant_ids=("ollama:a", "ollama:b")
        )
        self.assertEqual(widened["mode"], "independent_discuss")


if __name__ == "__main__":
    unittest.main()
