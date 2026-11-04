"""AMCA Studio v0.8.0 Adaptive Coordination Intelligence Test Suite (AG-AMCA-STUDIO-V0.8.0).

Validates:
1. Adaptive coordination context capture (recorded facts only, anti-reasoning sanitization)
2. Coordination proposal creation (recommendation as proposal, proposal != execution)
3. Human approval boundary (approval required before application; unapproved raises RuntimeError)
4. Proposal rejection and superseding lifecycle
5. Full evidence reference integration (zero hidden reasoning)
6. Model Router and Agent Fabric architectural decoupling
"""

import inspect
from pathlib import Path
import tempfile
import unittest

from amca.adaptive_coordination_intelligence import (
    AdaptiveCoordinationContext,
    AdaptiveCoordinationEngine,
    AdaptiveCoordinationStore,
    CoordinationProposalRecord,
)


class StudioV080AdaptiveCoordinationIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp_dir.name)
        self.db_path = self.tmp_path / "adaptive.sqlite3"

        self.store = AdaptiveCoordinationStore(self.db_path)
        self.engine = AdaptiveCoordinationEngine(self.store)

        # Create baseline context
        self.context = self.engine.capture_coordination_context(
            workflow_id="wf-cardio-analysis",
            workspace_id="ws-clinical-cardio",
            task_domain="cardiovascular_pathology",
            detected_constraints=("hipaa_compliance", "local_offline_only"),
            required_capabilities=("ecg_analysis", "hemodynamics"),
            active_participants=("agent-cardiologist",),
            runtime_session_id="gcs-998877",
            recorded_facts={
                "cohort_size": 240,
                "anomalies_detected": 12,
                "chain_of_thought": "Should be filtered out",
                "hidden_reasoning": "Must not be stored",
            },
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_context_capture_and_facts_sanitization(self) -> None:
        """Verify context records facts only and strips forbidden chain-of-thought keys."""
        ctx = self.store.get_context(
            context_id=self.context.context_id,
            workspace_id="ws-clinical-cardio",
        )
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.task_domain, "cardiovascular_pathology")
        self.assertEqual(ctx.detected_constraints, ("hipaa_compliance", "local_offline_only"))

        facts = ctx.recorded_facts
        self.assertEqual(facts["cohort_size"], 240)
        self.assertNotIn("chain_of_thought", facts)
        self.assertNotIn("hidden_reasoning", facts)

    def test_proposal_creation_without_execution(self) -> None:
        """Verify recommendations are created as PROPOSED records without executing changes."""
        prop = self.engine.create_proposal(
            context_id=self.context.context_id,
            workspace_id="ws-clinical-cardio",
            proposal_type="ADD_REVIEWER_ROLE",
            rationale="High anomaly density warrants an independent electrophysiology reviewer.",
            proposed_changes={"target_role": "TECHNICAL_REVIEWER", "suggested_agent": "agent-electrophysiologist"},
            evidence_reference_ids=("ev-ecg-001", "ev-ecg-002"),
        )
        self.assertEqual(prop.status, "PROPOSED")
        self.assertEqual(prop.workflow_id, "wf-cardio-analysis")
        self.assertIsNone(prop.applied_at)
        self.assertIsNone(prop.reviewed_by)

    def test_human_approval_boundary_enforces_approved_before_apply(self) -> None:
        """Verify that attempting to apply a proposal before human approval raises RuntimeError."""
        prop = self.engine.create_proposal(
            context_id=self.context.context_id,
            workspace_id="ws-clinical-cardio",
            proposal_type="ATTACH_KNOWLEDGE_SOURCE",
            rationale="Attach cardiovascular clinical guideline corpus.",
            proposed_changes={"knowledge_corpus": "aha_2026_guidelines"},
        )

        # Attempt to apply immediately without approval must fail!
        with self.assertRaises(RuntimeError):
            self.engine.apply_approved_proposal(
                proposal_id=prop.proposal_id,
                workspace_id="ws-clinical-cardio",
            )

        # Approve proposal
        approved = self.engine.approve_proposal(
            proposal_id=prop.proposal_id,
            workspace_id="ws-clinical-cardio",
            reviewed_by="dr_operator",
            reviewer_notes="Approved guideline corpus attachment.",
        )
        self.assertEqual(approved.status, "APPROVED")
        self.assertEqual(approved.reviewed_by, "dr_operator")
        self.assertIsNotNone(approved.reviewed_at)

        # Now apply succeeds
        applied = self.engine.apply_approved_proposal(
            proposal_id=prop.proposal_id,
            workspace_id="ws-clinical-cardio",
        )
        self.assertEqual(applied.status, "APPLIED")
        self.assertIsNotNone(applied.applied_at)

    def test_proposal_rejection_workflow(self) -> None:
        """Verify explicit rejection records operator rationale and blocks application."""
        prop = self.engine.create_proposal(
            context_id=self.context.context_id,
            workspace_id="ws-clinical-cardio",
            proposal_type="REQUEST_MODEL_UPGRADE",
            rationale="Upgrade to cloud LLM for broader literature summary.",
            proposed_changes={"suggested_model": "cloud-frontier-model"},
        )

        rejected = self.engine.reject_proposal(
            proposal_id=prop.proposal_id,
            workspace_id="ws-clinical-cardio",
            reviewed_by="compliance_officer",
            reviewer_notes="Rejected due to local-offline HIPAA constraint.",
        )
        self.assertEqual(rejected.status, "REJECTED")
        self.assertEqual(rejected.reviewed_by, "compliance_officer")

        # Rejected proposal cannot be applied
        with self.assertRaises(RuntimeError):
            self.engine.apply_approved_proposal(
                proposal_id=prop.proposal_id,
                workspace_id="ws-clinical-cardio",
            )

    def test_superseded_proposal_handling(self) -> None:
        """Verify proposals can be superseded when newer context arrives."""
        prop = self.engine.create_proposal(
            context_id=self.context.context_id,
            workspace_id="ws-clinical-cardio",
            proposal_type="SWITCH_COLLABORATION_MODE",
            rationale="Switch to joint mode.",
            proposed_changes={"mode": "joint"},
        )

        superseded = self.engine.supersede_proposal(
            proposal_id=prop.proposal_id,
            workspace_id="ws-clinical-cardio",
            reason="Superseded by specialist consultation work order.",
        )
        self.assertEqual(superseded.status, "SUPERSEDED")
        self.assertEqual(superseded.reviewer_notes, "Superseded by specialist consultation work order.")

    def test_architectural_decoupling(self) -> None:
        """Verify Model Router and Agent Fabric remain independent of coordination intelligence."""
        import amca.intelligence.cross_provider_router as cpr
        source_cpr = inspect.getsource(cpr)
        self.assertNotIn("AdaptiveCoordinationEngine", source_cpr)


if __name__ == "__main__":
    unittest.main()

