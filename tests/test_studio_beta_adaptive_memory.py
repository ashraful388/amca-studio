"""
AMCA Studio Beta B3 Adaptive Memory Intelligence Foundation Test Suite
Validates:
1. Memory classification metadata (factual, procedural, episodic, preference) and confidence
2. Privacy governance: strict refusal of raw keys/tokens (sk-, ghp-, xoxb-, aws_secret, private keys)
3. Confidence-weighted context retrieval ranking
4. User deletion guarantees and project isolation
"""

import tempfile
import unittest
from pathlib import Path

from amca.studio_memory import StudioMemoryStore


class StudioBetaAdaptiveMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_memory.sqlite3"
        self.store = StudioMemoryStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_memory_classification_and_confidence_metadata(self) -> None:
        """Verify memory items persist and return classification and confidence scores."""
        item = self.store.save_memory(
            project_id="proj-delta",
            title="Coding Standard",
            content="Always write pure functions when formatting data.",
            memory_type="guideline",
            classification="procedural",
            confidence=0.95,
        )
        self.assertEqual(item["classification"], "procedural")
        self.assertEqual(item["confidence"], 0.95)

        retrieved = self.store.get_memory(item["memory_id"], project_id="proj-delta")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["classification"], "procedural")
        self.assertEqual(retrieved["confidence"], 0.95)

    def test_privacy_governance_credential_rejection(self) -> None:
        """Verify that attempted storage of private keys and API tokens is strictly rejected."""
        with self.assertRaises(ValueError) as cm:
            self.store.save_memory(
                project_id="proj-delta",
                title="OpenAI Key Leak",
                content="Use sk-proj-1234567890abcdef for calls.",
            )
        self.assertIn("must not contain raw authentication tokens", str(cm.exception))

        with self.assertRaises(ValueError) as cm2:
            self.store.save_memory(
                project_id="proj-delta",
                title="SSH Key Leak",
                content="-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASC",
            )
        self.assertIn("must not contain raw authentication tokens", str(cm2.exception))

    def test_context_retrieval_confidence_ranking(self) -> None:
        """Verify that higher confidence memories rank above lower confidence ones when query matches."""
        self.store.save_memory(
            project_id="proj-delta",
            title="Database Schema Doc",
            content="SQLite database WAL mode explanation.",
            confidence=0.5,
        )
        self.store.save_memory(
            project_id="proj-delta",
            title="Database Optimization Guide",
            content="SQLite database WAL mode optimization guide.",
            confidence=0.99,
        )

        res = self.store.retrieve_context(project_id="proj-delta", query="SQLite database WAL")
        self.assertEqual(res["selected_count"], 2)
        # Higher confidence item should be ranked first
        self.assertEqual(res["memories"][0]["title"], "Database Optimization Guide")

    def test_user_deletion_guarantees_and_isolation(self) -> None:
        """Verify explicit deletion guarantees and zero leak across projects."""
        m1 = self.store.save_memory(
            project_id="proj-x",
            title="Item 1",
            content="Content in X",
        )
        self.store.save_memory(
            project_id="proj-y",
            title="Item 2",
            content="Content in Y",
        )

        # Confirm isolation
        self.assertEqual(len(self.store.list_memories("proj-x")), 1)
        self.assertEqual(len(self.store.list_memories("proj-y")), 1)

        # Delete in X
        deleted = self.store.delete_memory(m1["memory_id"], project_id="proj-x")
        self.assertTrue(deleted)
        self.assertEqual(len(self.store.list_memories("proj-x")), 0)
        # Y is untouched
        self.assertEqual(len(self.store.list_memories("proj-y")), 1)


if __name__ == "__main__":
    unittest.main()

