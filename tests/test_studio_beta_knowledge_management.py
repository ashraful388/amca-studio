"""
AMCA Studio Beta B12 Knowledge Intelligence Management Foundation Test Suite
Validates:
1. KnowledgeAssetRecord and KnowledgeProvenance schema and metadata
2. Tag and collection filtering in KnowledgeAssetStore
3. Knowledge sharing safety: blocking shared credentials or private memory
4. Strict workspace isolation in local SQLite
"""

import tempfile
import unittest
from pathlib import Path

from amca.knowledge_management import (
    KnowledgeAssetRecord,
    KnowledgeAssetStore,
    KnowledgeProvenance,
    validate_knowledge_sharing_safety,
)


class StudioBetaKnowledgeManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_knowledge.sqlite3"
        self.store = KnowledgeAssetStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_knowledge_asset_persistence_and_filtering(self) -> None:
        """Verify knowledge asset cataloging, provenance preservation, and tag/collection filtering."""
        prov = KnowledgeProvenance(
            originating_task_id="task-auth-01",
            generating_agent_id="agent.security-auditor",
            model_decision_id="orch-sec-01",
            workflow_id="wf-sec-scan",
            created_at="2026-09-02T08:00:00Z",
        )
        asset = KnowledgeAssetRecord(
            asset_id="k-asset-sec-spec",
            workspace_id="ws-corp-main",
            title="RBAC Security Guidelines",
            asset_type="guide",
            source_ref="/artifacts/art-sec-guidelines.md",
            collections=("security", "architecture"),
            tags=("rbac", "governance", "best_practices"),
            is_shared=False,
            provenance=prov,
        )
        self.store.save_asset(asset)

        # Retrieve all
        all_assets = self.store.list_assets("ws-corp-main")
        self.assertEqual(len(all_assets), 1)
        self.assertEqual(all_assets[0]["asset_id"], "k-asset-sec-spec")
        self.assertEqual(all_assets[0]["provenance"]["generating_agent_id"], "agent.security-auditor")

        # Filter by matching tag
        matched_tag = self.store.list_assets("ws-corp-main", tag="rbac")
        self.assertEqual(len(matched_tag), 1)

        # Filter by non-matching tag
        unmatched_tag = self.store.list_assets("ws-corp-main", tag="database")
        self.assertEqual(len(unmatched_tag), 0)

        # Workspace isolation
        other_ws = self.store.list_assets("ws-other-client")
        self.assertEqual(len(other_ws), 0)

    def test_knowledge_sharing_safety_boundaries(self) -> None:
        """Verify secret tokens and private memories cannot be marked as shared."""
        # Clean guide can be shared
        safe, _ = validate_knowledge_sharing_safety(
            "guide",
            "Public Python Coding Style",
            "/docs/style.md",
            is_shared=True,
        )
        self.assertTrue(safe)

        # Secret credential cannot be shared
        unsafe_cred, reason_cred = validate_knowledge_sharing_safety(
            "credential",
            "Database sk-token",
            "/secrets/db.env",
            is_shared=True,
        )
        self.assertFalse(unsafe_cred)
        self.assertIn("secrets/credentials cannot be shared", reason_cred)

        # Private memory cannot be shared
        unsafe_mem, reason_mem = validate_knowledge_sharing_safety(
            "memory",
            "Private episodic notes",
            "private user feedback",
            is_shared=True,
        )
        self.assertFalse(unsafe_mem)
        self.assertIn("Private episodic/preference memories cannot be shared", reason_mem)


if __name__ == "__main__":
    unittest.main()

