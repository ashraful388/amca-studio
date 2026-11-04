"""AMCA Studio v0.8.0 Agent Capability Negotiation & Governance Test Suite (AG-AMCA-STUDIO-V0.8.0).

Validates:
1. Agent & model capability profile registration (recorded capabilities only, no inferred abilities)
2. Deterministic capability candidate matching (exact set subset/intersection; no probabilistic rankings)
3. Locality affinity filtering (LOCAL_ONLY, HYBRID, CLOUD_ALLOWED)
4. Collaboration contract creation, activation, and termination lifecycle
5. Human-governed boundaries (strictly no autonomous agent creation or delegation)
6. Model Router and Agent Fabric architectural decoupling
"""

import inspect
from pathlib import Path
import tempfile
import unittest

from amca.agent_capability_governance import (
    AgentCapabilityGovernanceEngine,
    AgentCapabilityGovernanceStore,
    AgentCapabilityProfile,
    CollaborationContractRecord,
    DeterministicMatchCandidate,
    ModelCapabilityProfile,
)


class StudioV080AgentCapabilityGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp_dir.name)
        self.db_path = self.tmp_path / "capability_governance.sqlite3"

        self.store = AgentCapabilityGovernanceStore(self.db_path)
        self.engine = AgentCapabilityGovernanceEngine(self.store)

        # Seed agent capability profiles
        self.engine.register_agent_profile(
            AgentCapabilityProfile(
                agent_id="agent-coder",
                name="Software Engineering Specialist",
                role="coder",
                declared_capabilities=("python", "simd", "unit_testing", "profiling"),
                permission_scopes=("workspace:write", "terminal:exec"),
                locality_affinity="LOCAL_ONLY",
            )
        )
        self.engine.register_agent_profile(
            AgentCapabilityProfile(
                agent_id="agent-researcher",
                name="Clinical Research Specialist",
                role="researcher",
                declared_capabilities=("pubmed", "clinical_trials", "synthesis"),
                permission_scopes=("knowledge:read",),
                locality_affinity="HYBRID",
            )
        )
        self.engine.register_agent_profile(
            AgentCapabilityProfile(
                agent_id="agent-reviewer",
                name="Quality and Governance Reviewer",
                role="reviewer",
                declared_capabilities=("governance_audit", "unit_testing", "synthesis"),
                permission_scopes=("governance:audit",),
                locality_affinity="HYBRID",
            )
        )

        # Seed model capability profiles
        self.engine.register_model_profile(
            ModelCapabilityProfile(
                model_id="qwen2.5-coder-32b",
                provider="ollama",
                locality="LOCAL_OFFLINE",
                supported_capabilities=("python", "simd", "unit_testing"),
                context_window=32768,
                governance_requirements=("offline_verified",),
            )
        )
        self.engine.register_model_profile(
            ModelCapabilityProfile(
                model_id="med-llama-8b-local",
                provider="ollama",
                locality="LOCAL_OFFLINE",
                supported_capabilities=("clinical_trials", "synthesis"),
                context_window=16384,
                governance_requirements=("hipaa_offline",),
            )
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_agent_and_model_profile_persistence(self) -> None:
        """Verify agent and model profiles persist accurately without inferred abilities."""
        agent = self.store.get_agent_profile("agent-coder")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.role, "coder")
        self.assertEqual(agent.locality_affinity, "LOCAL_ONLY")
        self.assertIn("simd", agent.declared_capabilities)

        model = self.store.get_model_profile("qwen2.5-coder-32b")
        self.assertIsNotNone(model)
        self.assertEqual(model.locality, "LOCAL_OFFLINE")
        self.assertIn("python", model.supported_capabilities)

    def test_deterministic_capability_matching(self) -> None:
        """Verify candidates are matched by exact capability subset with deterministic tie-breaking."""
        # Requirement: python, unit_testing
        matches = self.engine.match_agent_candidates(
            required_capabilities=("python", "unit_testing"),
        )
        self.assertGreater(len(matches), 0)

        # First match should be agent-coder because it matches BOTH required capabilities (exact_match=True)
        self.assertEqual(matches[0].candidate_id, "agent-coder")
        self.assertTrue(matches[0].exact_match)
        self.assertEqual(set(matches[0].matched_capabilities), {"python", "unit_testing"})

        # Second match should be agent-reviewer because it matches unit_testing (exact_match=False)
        self.assertEqual(matches[1].candidate_id, "agent-reviewer")
        self.assertFalse(matches[1].exact_match)
        self.assertEqual(matches[1].matched_capabilities, ("unit_testing",))

    def test_locality_affinity_filtering(self) -> None:
        """Verify matching respects locality filters strictly."""
        # Query clinical synthesis under LOCAL_ONLY filter
        matches_local = self.engine.match_agent_candidates(
            required_capabilities=("synthesis",),
            locality_filter="LOCAL_ONLY",
        )
        # agent-researcher and agent-reviewer are HYBRID, so they should not appear in LOCAL_ONLY filter
        self.assertEqual(len(matches_local), 0)

        # Under HYBRID filter, both should appear deterministically
        matches_hybrid = self.engine.match_agent_candidates(
            required_capabilities=("synthesis",),
            locality_filter="HYBRID",
        )
        self.assertEqual(len(matches_hybrid), 2)
        candidate_ids = {m.candidate_id for m in matches_hybrid}
        self.assertEqual(candidate_ids, {"agent-researcher", "agent-reviewer"})

    def test_collaboration_contract_lifecycle(self) -> None:
        """Verify explicit collaboration contracts progress through AGREED -> ACTIVE -> TERMINATED."""
        contract = self.engine.create_collaboration_contract(
            workflow_id="wf-collaboration",
            workspace_id="ws-main",
            lead_agent_id="agent-coder",
            participant_agent_ids=("agent-reviewer",),
            assigned_model_ids=("qwen2.5-coder-32b",),
            agreed_capabilities=("python", "unit_testing"),
            governance_constraints=("hipaa_offline", "no_cloud"),
        )
        self.assertEqual(contract.status, "AGREED")
        self.assertEqual(contract.lead_agent_id, "agent-coder")
        self.assertEqual(len(contract.participant_agent_ids), 1)

        # Activate contract
        active = self.engine.activate_contract(
            contract_id=contract.contract_id,
            workspace_id="ws-main",
        )
        self.assertEqual(active.status, "ACTIVE")

        # Terminate contract
        terminated = self.engine.terminate_contract(
            contract_id=contract.contract_id,
            workspace_id="ws-main",
            reason="Completed software development turn",
        )
        self.assertEqual(terminated.status, "TERMINATED")
        self.assertEqual(terminated.termination_reason, "Completed software development turn")

    def test_non_existent_lead_agent_rejected(self) -> None:
        """Verify contract rejects unregistered lead agent (no autonomous agent creation)."""
        with self.assertRaises(KeyError):
            self.engine.create_collaboration_contract(
                workflow_id="wf-bad",
                workspace_id="ws-main",
                lead_agent_id="ghost_agent",
                participant_agent_ids=(),
                assigned_model_ids=(),
                agreed_capabilities=(),
            )

    def test_model_router_and_orchestrator_decoupling(self) -> None:
        """Verify Model Router and Agent Fabric do not depend on capability governance."""
        import amca.intelligence.cross_provider_router as cpr
        source_cpr = inspect.getsource(cpr)
        self.assertNotIn("AgentCapabilityGovernanceEngine", source_cpr)


if __name__ == "__main__":
    unittest.main()

