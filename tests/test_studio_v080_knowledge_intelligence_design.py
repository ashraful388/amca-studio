"""
AMCA Studio v0.8.0 Knowledge Intelligence Design Test Suite
Validates:
1. Architecture document integrity and schema completeness
2. Non-destructive migration boundary validation (shadow table strategy)
3. Zero premature runtime modification detection (existing ArtifactStore untouched)
"""

import inspect
from pathlib import Path
import unittest

from amca.v080_knowledge_intelligence_design import (
    FTS5IndexSpecification,
    HybridRetrievalSpecification,
    KnowledgeFieldMapping,
    KnowledgeIntelligenceDesignPackage,
    KnowledgeSecurityModel,
    assemble_knowledge_intelligence_design,
)


class StudioV080KnowledgeIntelligenceDesignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent.parent

    def test_architecture_design_package_integrity(self) -> None:
        """Verify design package contains valid FTS5, Hybrid, and Security specifications."""
        pkg = assemble_knowledge_intelligence_design("v0.8.0")
        self.assertTrue(pkg.design_id.startswith("know-dsn-"))
        self.assertEqual(pkg.target_version, "v0.8.0")
        self.assertEqual(pkg.design_verdict, "DESIGN_SPECIFICATION_APPROVED")

        # FTS5 specification integrity
        fts5 = pkg.fts5_spec
        self.assertEqual(fts5.table_name, "artifacts_fts5_idx")
        self.assertIn("CREATE VIRTUAL TABLE", fts5.virtual_table_sql)
        self.assertEqual(len(fts5.update_triggers_sql), 2)
        indexed_fields = [f.field_name for f in fts5.searchable_fields if f.indexed_in_fts5]
        self.assertIn("title", indexed_fields)
        self.assertIn("content", indexed_fields)

        # Security model integrity
        sec = pkg.security_model
        self.assertEqual(sec.isolation_scope, "STRICT_WORKSPACE_ISOLATION")
        self.assertFalse(sec.cloud_vector_allowed)

    def test_migration_boundary_and_shadow_table_strategy(self) -> None:
        """Verify non-destructive shadow table migration strategy preserves Alpha/Beta data."""
        pkg = assemble_knowledge_intelligence_design("v0.8.0")
        self.assertEqual(pkg.fts5_spec.migration_strategy, "NON_DESTRUCTIVE_SHADOW_TABLE")

        # Ensure triggers reference existing artifacts schema fields without destructive alter table
        trigger_sql = " ".join(pkg.fts5_spec.update_triggers_sql)
        self.assertNotIn("DROP TABLE", trigger_sql)
        self.assertNotIn("ALTER TABLE", trigger_sql)
        self.assertIn("INSERT INTO artifacts_fts5_idx", trigger_sql)

    def test_zero_runtime_store_modifications(self) -> None:
        """Verify that existing ArtifactStore implementation remains unmodified and intact."""
        from amca.artifacts import ArtifactStore
        source = inspect.getsource(ArtifactStore)
        # Verify no premature FTS5 virtual tables were added to the v0.7.0 runtime ArtifactStore
        self.assertNotIn("CREATE VIRTUAL TABLE", source)
        self.assertNotIn("artifacts_fts5_idx", source)


if __name__ == "__main__":
    unittest.main()

