"""
AMCA Studio v0.8.0 Knowledge Context Integration Design Test Suite
Validates:
1. Design artifact exists and contains valid context contract schemas and store boundary definitions
2. No automatic agent injection implemented (pure explicit tool-call design)
3. Permissions and role-based policies documented for agent fabric
4. Existing workflows and memory systems unchanged
"""

import inspect
from pathlib import Path
import unittest

from amca.v080_context_integration_design import (
    AgentKnowledgeAccessPolicy,
    ContextIntegrationDesignPackage,
    KnowledgeContextItem,
    StoreBoundaryDefinition,
    assemble_context_integration_design,
)


class StudioV080ContextIntegrationDesignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent.parent

    def test_design_package_and_store_boundaries_completeness(self) -> None:
        """Verify presence of 3 store boundaries: MemoryStore, KnowledgeStore, ArtifactStore."""
        pkg = assemble_context_integration_design("v0.8.0")
        self.assertTrue(pkg.design_id.startswith("ctx-dsn-"))
        self.assertEqual(pkg.target_version, "v0.8.0")
        self.assertEqual(pkg.overall_status, "CONTEXT_INTEGRATION_DESIGN_APPROVED")

        # 3-store partition verified
        self.assertEqual(len(pkg.store_boundaries), 3)
        store_names = {s.store_name for s in pkg.store_boundaries}
        self.assertTrue(any("MemoryStore" in name for name in store_names))
        self.assertTrue(any("KnowledgeStore" in name for name in store_names))
        self.assertTrue(any("ArtifactStore" in name for name in store_names))

    def test_zero_hidden_injection_policy(self) -> None:
        """Verify policy strictly bans hidden automatic context injection."""
        pkg = assemble_context_integration_design("v0.8.0")
        self.assertFalse(pkg.hidden_injection_permitted)
        for policy in pkg.agent_access_policies:
            self.assertTrue(policy.requires_explicit_user_prompt)
            self.assertTrue(policy.audit_trail_logging)

    def test_agent_access_policies_documented(self) -> None:
        """Verify roles and action scopes are defined for Agent Fabric."""
        pkg = assemble_context_integration_design("v0.8.0")
        roles = {p.agent_role for p in pkg.agent_access_policies}
        expected_roles = {"researcher", "coder", "reviewer"}
        self.assertTrue(expected_roles.issubset(roles))

        for p in pkg.agent_access_policies:
            self.assertIn("knowledge.search", p.allowed_actions)
            self.assertGreater(p.max_context_tokens, 0)

    def test_existing_agent_fabric_and_memory_runtime_untouched(self) -> None:
        """Verify existing agent_fabric.py and context execution modules are unmodified."""
        import amca.agent_fabric as af
        source = inspect.getsource(af)
        # Ensure AgentFabric has not been modified to inject hybrid retrieval automatically
        self.assertNotIn("HybridRetrievalEngine", source)
        self.assertNotIn("artifacts_fts5_idx", source)


if __name__ == "__main__":
    unittest.main()

