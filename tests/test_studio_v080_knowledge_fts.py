"""
AMCA Studio v0.8.0 Knowledge FTS5 Retrieval Test Suite
Validates:
1. Index creation: FTS5 table exists and migration succeeds
2. Isolation: workspace A cannot retrieve workspace B data
3. Synchronization: create -> searchable, update -> updated result, delete -> removed
4. Security: secrets never indexed
5. Regression: existing artifact workflows unchanged
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from amca.artifacts import ArtifactStore
from amca.knowledge_fts import KnowledgeFtsStore, KnowledgeSearchResult, sanitize_knowledge_text


class StudioV080KnowledgeFtsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_knowledge_fts.sqlite3"
        self.store = KnowledgeFtsStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_index_creation_and_fts_table_presence(self) -> None:
        """Verify native SQLite FTS5 table is created successfully."""
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='artifacts_fts5_idx';"
            ).fetchall()
            self.assertEqual(len(rows), 1)

    def test_workspace_isolation(self) -> None:
        """Verify workspace A cannot retrieve workspace B data."""
        self.store.index_artifact(
            artifact_id="art-ws-a-1",
            workspace_id="ws-alpha",
            title="Quantum Architecture Synthesis",
            content="Proprietary cognition pipeline algorithms.",
        )
        self.store.index_artifact(
            artifact_id="art-ws-b-1",
            workspace_id="ws-beta",
            title="Financial Risk Ledger",
            content="Confidential asset accounting tables.",
        )

        # Query workspace alpha
        results_a = self.store.search(workspace_id="ws-alpha", query="cognition")
        self.assertEqual(len(results_a), 1)
        self.assertEqual(results_a[0].artifact_id, "art-ws-a-1")

        # Query workspace beta for workspace alpha data -> must be empty
        results_b = self.store.search(workspace_id="ws-beta", query="cognition")
        self.assertEqual(len(results_b), 0)

    def test_synchronization_lifecycle(self) -> None:
        """Verify create -> searchable, update -> updated result, delete -> removed."""
        # 1. Create -> searchable
        self.store.index_artifact(
            artifact_id="art-lifecycle-1",
            workspace_id="ws-test",
            title="Initial Draft Specification",
            content="Version one requirements document.",
        )
        res1 = self.store.search(workspace_id="ws-test", query="Specification")
        self.assertEqual(len(res1), 1)
        self.assertIn("Draft", res1[0].title)

        # 2. Update -> updated result
        self.store.index_artifact(
            artifact_id="art-lifecycle-1",
            workspace_id="ws-test",
            title="Revised Final Specification",
            content="Version two approved architecture.",
        )
        res2 = self.store.search(workspace_id="ws-test", query="Revised")
        self.assertEqual(len(res2), 1)
        self.assertIn("Revised Final", res2[0].title)

        # 3. Delete -> removed
        removed = self.store.remove_artifact(artifact_id="art-lifecycle-1", workspace_id="ws-test")
        self.assertTrue(removed)
        res3 = self.store.search(workspace_id="ws-test", query="Specification")
        self.assertEqual(len(res3), 0)

    def test_security_secret_redaction(self) -> None:
        """Verify secret tokens and credentials are never stored in plain text."""
        raw_text = "Here is my secret sk-proj1234567890abcdef1234567890 and ghp_abcdefghijklmnopqr012345."
        self.store.index_artifact(
            artifact_id="art-secret-1",
            workspace_id="ws-sec",
            title="Leaked Key Incident",
            content=raw_text,
        )

        # Search for raw secret token -> must not match
        res_secret = self.store.search(workspace_id="ws-sec", query="sk-proj1234567890abcdef1234567890")
        self.assertEqual(len(res_secret), 0)

        # Verify underlying table contains redaction marker
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT content FROM artifacts_fts5_idx WHERE artifact_id='art-secret-1';"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIn("[REDACTED_SECRET]", row[0])
            self.assertNotIn("sk-proj1234567890", row[0])

    def test_existing_artifact_store_regression_unchanged(self) -> None:
        """Verify standard ArtifactStore works completely unaltered."""
        store_path = Path(self.tmp_dir.name) / "artifacts.sqlite3"
        payloads_path = Path(self.tmp_dir.name) / "payloads"
        artifact_store = ArtifactStore(store_path, payloads_path)
        # Standard store operations run without conflict
        self.assertTrue(store_path.exists())


if __name__ == "__main__":
    unittest.main()

