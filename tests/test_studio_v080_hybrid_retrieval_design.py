"""
AMCA Studio v0.8.0 Hybrid Retrieval Architecture Design Test Suite
Validates:
1. Design artifact exists and contains candidates and pipeline specification
2. No runtime semantic retrieval introduced (pure design specification)
3. FTS5 remains primary retrieval layer in pipeline spec
4. Privacy constraints and pre-embedding redaction documented
"""

import inspect
from pathlib import Path
import unittest

from amca.v080_hybrid_retrieval_design import (
    EmbeddingModelCandidate,
    HybridPipelineSpecification,
    HybridRetrievalDesignPackage,
    HybridSecuritySpecification,
    VectorStorageOption,
    assemble_hybrid_retrieval_design,
)


class StudioV080HybridRetrievalDesignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent.parent

    def test_design_package_and_candidates_completeness(self) -> None:
        """Verify presence of local ONNX candidates and vector storage options."""
        pkg = assemble_hybrid_retrieval_design("v0.8.0")
        self.assertTrue(pkg.design_id.startswith("hyb-dsn-"))
        self.assertEqual(pkg.target_version, "v0.8.0")
        self.assertEqual(pkg.overall_status, "HYBRID_DESIGN_SPECIFIED")

        # Verify embedding candidates (offline CPU and GPU tiers)
        self.assertGreaterEqual(len(pkg.candidates), 2)
        model_names = [c.model_name for c in pkg.candidates]
        self.assertIn("all-MiniLM-L6-v2-quantized", model_names)
        for c in pkg.candidates:
            self.assertTrue(c.offline_supported)
            self.assertGreater(c.dimension, 0)
            self.assertGreater(c.parameter_size_mb, 0.0)

        # Verify storage options
        self.assertGreaterEqual(len(pkg.storage_options), 2)

    def test_fts5_remains_primary_retrieval_layer(self) -> None:
        """Verify FTS5 is designated as the primary lexical backbone."""
        pkg = assemble_hybrid_retrieval_design("v0.8.0")
        self.assertEqual(pkg.pipeline.lexical_primary_engine, "SQLite_FTS5")
        self.assertEqual(pkg.pipeline.semantic_secondary_engine, "Local_ONNX")
        self.assertEqual(pkg.pipeline.fusion_algorithm, "RRF")
        self.assertGreater(pkg.pipeline.rrf_k_parameter, 0)

    def test_air_gapped_security_and_privacy_boundaries(self) -> None:
        """Verify strict zero-cloud embedding policies and redaction rules."""
        pkg = assemble_hybrid_retrieval_design("v0.8.0")
        sec = pkg.security
        self.assertFalse(sec.cloud_embedding_permitted)
        self.assertTrue(sec.isolated_by_workspace)
        self.assertTrue(sec.private_memory_exclusion)
        self.assertIn("RED", sec.redaction_policy)

    def test_zero_runtime_semantic_retrieval_introduced(self) -> None:
        """Verify that KnowledgeFtsStore remains purely lexical with zero vector imports."""
        import amca.knowledge_fts as kfts
        source = inspect.getsource(kfts)
        self.assertNotIn("onnxruntime", source)
        self.assertNotIn("torch", source)
        self.assertNotIn("sentence_transformers", source)
        self.assertNotIn("faiss", source)
        self.assertNotIn("chromadb", source)


if __name__ == "__main__":
    unittest.main()

