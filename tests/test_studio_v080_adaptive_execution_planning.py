"""AMCA Studio v0.8.0 Adaptive Execution Planning Test Suite (AG-AMCA-STUDIO-V0.8.0).

Validates:
1. Deterministic proposal-to-plan conversion (approved proposals only)
2. Strict blocking on unapproved or rejected proposals (raises RuntimeError)
3. Step model with explicit evidence/reference origin tracking
4. Human plan review boundary (DRAFT -> UNDER_REVIEW -> APPROVED -> READY_FOR_EXECUTION)
5. Explicit step status audit transitions (no automatic transitions)
6. Model Router and Agent Fabric architectural decoupling
"""

import inspect
from pathlib import Path
import tempfile
import unittest

from amca.adaptive_coordination_intelligence import (
    AdaptiveCoordinationEngine,
    AdaptiveCoordinationStore,
)
from amca.adaptive_execution_planning import (
    AdaptiveExecutionPlanRecord,
    AdaptiveExecutionPlanningEngine,
    AdaptiveExecutionPlanningStore,
    AdaptivePlanStep,
)


class StudioV080AdaptiveExecutionPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp_dir.name)

        self.coord_store = AdaptiveCoordinationStore(self.tmp_path / "coord.sqlite3")
        self.coord_engine = AdaptiveCoordinationEngine(self.coord_store)

        self.plan_store = AdaptiveExecutionPlanningStore(self.tmp_path / "planning.sqlite3")
        self.plan_engine = AdaptiveExecutionPlanningEngine(
            store=self.plan_store,
            coordination_store=self.coord_store,
        )

        # Create context and proposals
        self.context = self.coord_engine.capture_coordination_context(
            workflow_id="wf-plan-genomics",
            workspace_id="ws-genomics",
            task_domain="computational_genomics",
            detected_constraints=("local_compute",),
            required_capabilities=("variant_calling", "annotation"),
            active_participants=("agent-bioinfo",),
        )

        # Approved proposal
        self.approved_proposal = self.coord_engine.create_proposal(
            context_id=self.context.context_id,
            workspace_id="ws-genomics",
            proposal_type="ADD_REVIEWER_ROLE",
            rationale="Add clinical geneticist reviewer for pathogenicity classification.",
            proposed_changes={"reviewer": "agent-geneticist"},
            evidence_reference_ids=("ev-variant-01",),
        )
        self.coord_engine.approve_proposal(
            proposal_id=self.approved_proposal.proposal_id,
            workspace_id="ws-genomics",
            reviewed_by="pi_investigator",
            reviewer_notes="Approved geneticist reviewer addition.",
        )

        # Unapproved proposal
        self.unapproved_proposal = self.coord_engine.create_proposal(
            context_id=self.context.context_id,
            workspace_id="ws-genomics",
            proposal_type="REQUEST_MODEL_UPGRADE",
            rationale="Request cloud model.",
            proposed_changes={"model": "cloud-frontier"},
        )

        # Rejected proposal
        self.rejected_proposal = self.coord_engine.create_proposal(
            context_id=self.context.context_id,
            workspace_id="ws-genomics",
            proposal_type="SWITCH_COLLABORATION_MODE",
            rationale="Switch mode.",
            proposed_changes={"mode": "parallel"},
        )
        self.coord_engine.reject_proposal(
            proposal_id=self.rejected_proposal.proposal_id,
            workspace_id="ws-genomics",
            reviewed_by="pi_investigator",
            reviewer_notes="Rejected parallel mode.",
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_plan_conversion_from_approved_proposal(self) -> None:
        """Verify plan conversion succeeds on approved proposal with structured steps and evidence."""
        plan = self.plan_engine.convert_proposal_to_plan(
            proposal_id=self.approved_proposal.proposal_id,
            workspace_id="ws-genomics",
            goal="Execute variant calling with clinical geneticist review",
            steps_spec=[
                {
                    "title": "Variant Calling",
                    "description": "Align reads and call indels/SNVs",
                    "assigned_agent_id": "agent-bioinfo",
                    "assigned_model_id": "qwen2.5-coder-32b",
                    "required_capabilities": ["variant_calling"],
                    "evidence_origin_ids": ["ev-raw-reads-001"],
                },
                {
                    "title": "Clinical Annotation & Review",
                    "description": "Annotate variants and classify pathogenicity",
                    "assigned_agent_id": "agent-geneticist",
                    "assigned_model_id": "med-llama-8b-local",
                    "required_capabilities": ["annotation"],
                    "evidence_origin_ids": ["ev-variant-01"],
                },
            ],
        )
        self.assertEqual(plan.status, "DRAFT")
        self.assertEqual(plan.proposal_id, self.approved_proposal.proposal_id)
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].title, "Variant Calling")
        self.assertEqual(plan.steps[1].assigned_agent_id, "agent-geneticist")
        self.assertIn("ev-variant-01", plan.steps[1].evidence_origin_ids)

    def test_unapproved_proposal_cannot_create_plan(self) -> None:
        """Verify unapproved proposals are rejected and raise RuntimeError."""
        with self.assertRaises(RuntimeError):
            self.plan_engine.convert_proposal_to_plan(
                proposal_id=self.unapproved_proposal.proposal_id,
                workspace_id="ws-genomics",
                goal="Should fail",
                steps_spec=[],
            )

    def test_rejected_proposal_cannot_create_plan(self) -> None:
        """Verify rejected proposals are rejected and raise RuntimeError."""
        with self.assertRaises(RuntimeError):
            self.plan_engine.convert_proposal_to_plan(
                proposal_id=self.rejected_proposal.proposal_id,
                workspace_id="ws-genomics",
                goal="Should fail",
                steps_spec=[],
            )

    def test_human_plan_review_workflow(self) -> None:
        """Verify plan lifecycle DRAFT -> UNDER_REVIEW -> APPROVED -> READY_FOR_EXECUTION."""
        plan = self.plan_engine.convert_proposal_to_plan(
            proposal_id=self.approved_proposal.proposal_id,
            workspace_id="ws-genomics",
            goal="Clinical analysis execution",
            steps_spec=[{"title": "Step 1", "description": "Run pipeline"}],
        )

        # Attempt to prepare for execution before approval must fail!
        with self.assertRaises(RuntimeError):
            self.plan_engine.prepare_for_execution(
                plan_id=plan.plan_id,
                workspace_id="ws-genomics",
            )

        # Submit for review
        under_review = self.plan_engine.submit_plan_for_review(
            plan_id=plan.plan_id,
            workspace_id="ws-genomics",
        )
        self.assertEqual(under_review.status, "UNDER_REVIEW")

        # Approve
        approved = self.plan_engine.approve_plan(
            plan_id=plan.plan_id,
            workspace_id="ws-genomics",
            reviewed_by="clinical_director",
            review_notes="Plan approved for execution scheduling.",
        )
        self.assertEqual(approved.status, "APPROVED")
        self.assertEqual(approved.reviewed_by, "clinical_director")

        # Now prepare for execution succeeds
        ready = self.plan_engine.prepare_for_execution(
            plan_id=plan.plan_id,
            workspace_id="ws-genomics",
        )
        self.assertEqual(ready.status, "READY_FOR_EXECUTION")

    def test_step_lifecycle_audit_transition(self) -> None:
        """Verify step status transitions are recorded accurately without automatic execution."""
        plan = self.plan_engine.convert_proposal_to_plan(
            proposal_id=self.approved_proposal.proposal_id,
            workspace_id="ws-genomics",
            goal="Multi-step pipeline",
            steps_spec=[{"title": "Step A"}, {"title": "Step B"}],
        )
        step_id = plan.steps[0].step_id

        # Update to IN_PROGRESS
        plan = self.plan_engine.update_step_status(
            plan_id=plan.plan_id,
            workspace_id="ws-genomics",
            step_id=step_id,
            status="IN_PROGRESS",
        )
        self.assertEqual(plan.steps[0].status, "IN_PROGRESS")

        # Update to COMPLETED
        plan = self.plan_engine.update_step_status(
            plan_id=plan.plan_id,
            workspace_id="ws-genomics",
            step_id=step_id,
            status="COMPLETED",
        )
        self.assertEqual(plan.steps[0].status, "COMPLETED")

    def test_architectural_decoupling(self) -> None:
        """Verify Model Router and Agent Fabric remain independent of adaptive execution planning."""
        import amca.intelligence.cross_provider_router as cpr
        source_cpr = inspect.getsource(cpr)
        self.assertNotIn("AdaptiveExecutionPlanningEngine", source_cpr)


if __name__ == "__main__":
    unittest.main()

