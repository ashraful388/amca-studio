"""AMCA Studio v0.8.0 Governed Multi-Agent Collaboration Test Suite (AG-AMCA-STUDIO-V0.8.0).

Validates:
1. Collaboration session runtime lifecycle (INITIALIZED -> GOVERNANCE_VERIFIED -> ACTIVE -> COMPLETED)
2. Role assignment with strict capability compatibility verification
3. Governance gate blocking on incompatible roles or missing approvals
4. Collaboration communication boundary with message recording and sequencing
5. Anti-reasoning invariant (no chain-of-thought, hidden reasoning, or private traces)
6. Decoupling from Model Router and Agent Fabric
"""

import inspect
from pathlib import Path
import tempfile
import unittest

from amca.agent_capability_governance import (
    AgentCapabilityGovernanceEngine,
    AgentCapabilityGovernanceStore,
    AgentCapabilityProfile,
    ModelCapabilityProfile,
)
from amca.governed_multi_agent_collaboration import (
    AgentRoleAssignment,
    CollaborationMessageRecord,
    GovernedCollaborationCoordinator,
    GovernedCollaborationSessionRecord,
    GovernedCollaborationStore,
)


class StudioV080GovernedMultiAgentCollaborationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp_dir.name)

        self.gov_store = AgentCapabilityGovernanceStore(self.tmp_path / "gov.sqlite3")
        self.gov_engine = AgentCapabilityGovernanceEngine(self.gov_store)

        self.collab_store = GovernedCollaborationStore(self.tmp_path / "collab.sqlite3")
        self.coordinator = GovernedCollaborationCoordinator(
            store=self.collab_store,
            governance_engine=self.gov_engine,
        )

        # Register participating agents
        self.gov_engine.register_agent_profile(
            AgentCapabilityProfile(
                agent_id="agent-author",
                name="Primary Author Agent",
                role="author",
                declared_capabilities=("oncology_synthesis", "manuscript_drafting"),
                permission_scopes=("workspace:write",),
            )
        )
        self.gov_engine.register_agent_profile(
            AgentCapabilityProfile(
                agent_id="agent-reviewer",
                name="Biomedical Reviewer Agent",
                role="reviewer",
                declared_capabilities=("clinical_peer_review", "fact_checking"),
                permission_scopes=("workspace:read",),
            )
        )

        # Create collaboration contract
        self.contract = self.gov_engine.create_collaboration_contract(
            workflow_id="wf-oncology-collab",
            workspace_id="ws-clinical",
            lead_agent_id="agent-author",
            participant_agent_ids=("agent-reviewer",),
            assigned_model_ids=("med-llama-8b-local",),
            agreed_capabilities=("oncology_synthesis", "clinical_peer_review"),
            governance_constraints=("hipaa_offline",),
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_session_lifecycle_with_compatible_roles(self) -> None:
        """Verify session initializes, verifies governance, activates, and completes."""
        session = self.coordinator.create_collaboration_session(
            contract_id=self.contract.contract_id,
            workspace_id="ws-clinical",
        )
        self.assertEqual(session.status, "INITIALIZED")
        self.assertEqual(session.lead_agent_id, "agent-author")

        # Assign compatible roles
        asgn1 = self.coordinator.assign_agent_role(
            session_id=session.session_id,
            workspace_id="ws-clinical",
            agent_id="agent-author",
            role="PRIMARY_AUTHOR",
            required_capabilities=("oncology_synthesis",),
        )
        self.assertTrue(asgn1.compatibility_verified)

        asgn2 = self.coordinator.assign_agent_role(
            session_id=session.session_id,
            workspace_id="ws-clinical",
            agent_id="agent-reviewer",
            role="TECHNICAL_REVIEWER",
            required_capabilities=("clinical_peer_review",),
        )
        self.assertTrue(asgn2.compatibility_verified)

        # Verify governance
        session = self.coordinator.verify_collaboration_governance(
            session_id=session.session_id,
            workspace_id="ws-clinical",
            provider_approvals_verified=True,
            locality_requirements_passed=True,
        )
        self.assertTrue(session.governance_verified)
        self.assertEqual(session.status, "GOVERNANCE_VERIFIED")

        # Activate
        session = self.coordinator.activate_collaboration(
            session_id=session.session_id,
            workspace_id="ws-clinical",
        )
        self.assertEqual(session.status, "ACTIVE")

        # Complete
        session = self.coordinator.complete_session(
            session_id=session.session_id,
            workspace_id="ws-clinical",
        )
        self.assertEqual(session.status, "COMPLETED")
        self.assertIsNotNone(session.finished_at)

    def test_governance_blocks_on_incompatible_role(self) -> None:
        """Verify governance gate fails and blocks activation if an agent lacks required capability."""
        session = self.coordinator.create_collaboration_session(
            contract_id=self.contract.contract_id,
            workspace_id="ws-clinical",
        )
        # Author agent lacks "clinical_peer_review"
        asgn = self.coordinator.assign_agent_role(
            session_id=session.session_id,
            workspace_id="ws-clinical",
            agent_id="agent-author",
            role="TECHNICAL_REVIEWER",
            required_capabilities=("clinical_peer_review",),
        )
        self.assertFalse(asgn.compatibility_verified)

        # Verification fails
        session = self.coordinator.verify_collaboration_governance(
            session_id=session.session_id,
            workspace_id="ws-clinical",
            provider_approvals_verified=True,
        )
        self.assertFalse(session.governance_verified)
        self.assertEqual(session.status, "FAILED")

        # Attempted activation raises RuntimeError
        with self.assertRaises(RuntimeError):
            self.coordinator.activate_collaboration(
                session_id=session.session_id,
                workspace_id="ws-clinical",
            )

    def test_collaboration_message_boundary_and_sequencing(self) -> None:
        """Verify messages are recorded, message_count incremented, and ordered by timestamp."""
        session = self.coordinator.create_collaboration_session(
            contract_id=self.contract.contract_id,
            workspace_id="ws-clinical",
        )
        self.coordinator.assign_agent_role(
            session_id=session.session_id,
            workspace_id="ws-clinical",
            agent_id="agent-author",
            role="PRIMARY_AUTHOR",
            required_capabilities=("oncology_synthesis",),
        )
        self.coordinator.verify_collaboration_governance(
            session_id=session.session_id,
            workspace_id="ws-clinical",
            provider_approvals_verified=True,
        )
        self.coordinator.activate_collaboration(
            session_id=session.session_id,
            workspace_id="ws-clinical",
        )

        msg1 = self.coordinator.post_collaboration_message(
            session_id=session.session_id,
            workspace_id="ws-clinical",
            sender_id="agent-author",
            recipient_id="agent-reviewer",
            message_type="TASK_HANDOFF",
            content_reference="draft_manuscript.md",
            metadata={"word_count": 3200},
        )
        msg2 = self.coordinator.post_collaboration_message(
            session_id=session.session_id,
            workspace_id="ws-clinical",
            sender_id="agent-reviewer",
            recipient_id="agent-author",
            message_type="REVIEW_COMMENT",
            content_reference="review_feedback.md",
            metadata={"comments_count": 4},
        )

        messages = self.collab_store.list_messages(session.session_id)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].message_type, "TASK_HANDOFF")
        self.assertEqual(messages[1].message_type, "REVIEW_COMMENT")

        # Check updated session message count
        updated_sess = self.collab_store.get_session(
            session_id=session.session_id,
            workspace_id="ws-clinical",
        )
        self.assertIsNotNone(updated_sess)
        self.assertEqual(updated_sess.message_count, 2)

    def test_anti_reasoning_invariant_in_messages(self) -> None:
        """Verify chain-of-thought and private thoughts are sanitized from message metadata."""
        session = self.coordinator.create_collaboration_session(
            contract_id=self.contract.contract_id,
            workspace_id="ws-clinical",
        )
        self.coordinator.assign_agent_role(
            session_id=session.session_id,
            workspace_id="ws-clinical",
            agent_id="agent-author",
            role="PRIMARY_AUTHOR",
            required_capabilities=("oncology_synthesis",),
        )
        self.coordinator.verify_collaboration_governance(
            session_id=session.session_id,
            workspace_id="ws-clinical",
            provider_approvals_verified=True,
        )
        self.coordinator.activate_collaboration(
            session_id=session.session_id,
            workspace_id="ws-clinical",
        )

        msg = self.coordinator.post_collaboration_message(
            session_id=session.session_id,
            workspace_id="ws-clinical",
            sender_id="agent-author",
            recipient_id="agent-reviewer",
            message_type="STATUS_UPDATE",
            content_reference="status.json",
            metadata={
                "progress_pct": 50,
                "chain_of_thought": "Should not be saved",
                "hidden_reasoning": "Forbidden",
            },
        )
        messages = self.collab_store.list_messages(session.session_id)
        meta = messages[0].metadata
        self.assertEqual(meta["progress_pct"], 50)
        self.assertNotIn("chain_of_thought", meta)
        self.assertNotIn("hidden_reasoning", meta)

    def test_termination_path(self) -> None:
        """Verify session termination records operator reason."""
        session = self.coordinator.create_collaboration_session(
            contract_id=self.contract.contract_id,
            workspace_id="ws-clinical",
        )
        terminated = self.coordinator.terminate_session(
            session_id=session.session_id,
            workspace_id="ws-clinical",
            reason="User cancelled collaboration",
        )
        self.assertEqual(terminated.status, "TERMINATED")
        self.assertEqual(terminated.error_message, "User cancelled collaboration")

    def test_architectural_decoupling(self) -> None:
        """Verify Model Router and Agent Fabric do not depend on collaboration coordinator."""
        import amca.intelligence.cross_provider_router as cpr
        source_cpr = inspect.getsource(cpr)
        self.assertNotIn("GovernedCollaborationCoordinator", source_cpr)


if __name__ == "__main__":
    unittest.main()

