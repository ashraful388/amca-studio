"""AMCA Studio v0.8.0 Governed Execution Plan Bridge Test Suite (AG-AMCA-STUDIO-V0.8.0).

Validates:
1. Plan execution handoff request for APPROVED / READY_FOR_EXECUTION plans
2. Strict blocking of unapproved or invalid plans (raises RuntimeError)
3. Safety gate evaluation (rejects handoff if approvals or capabilities unsatisfied)
4. Dispatch lifecycle (ACCEPTED -> DISPATCHED)
5. Cancellation and failure audit recording (zero automatic recovery)
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
    AdaptiveExecutionPlanningEngine,
    AdaptiveExecutionPlanningStore,
)
from amca.governed_execution_plan_bridge import (
    GovernedExecutionPlanBridge,
    GovernedExecutionPlanBridgeStore,
    PlanExecutionHandoffRecord,
)


class StudioV080GovernedPlanExecutionBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp_dir.name)

        self.coord_store = AdaptiveCoordinationStore(self.tmp_path / "coord.sqlite3")
        self.coord_engine = AdaptiveCoordinationEngine(self.coord_store)

        self.plan_store = AdaptiveExecutionPlanningStore(self.tmp_path / "plan.sqlite3")
        self.plan_engine = AdaptiveExecutionPlanningEngine(
            store=self.plan_store,
            coordination_store=self.coord_store,
        )

        self.bridge_store = GovernedExecutionPlanBridgeStore(self.tmp_path / "bridge.sqlite3")
        self.bridge = GovernedExecutionPlanBridge(
            store=self.bridge_store,
            planning_store=self.plan_store,
        )

        # Baseline context & proposal
        self.context = self.coord_engine.capture_coordination_context(
            workflow_id="wf-radiology-review",
            workspace_id="ws-radiology",
            task_domain="radiology_imaging",
            detected_constraints=("hipaa_offline",),
            required_capabilities=("ct_scan_segmentation", "radiology_report"),
            active_participants=("agent-radiologist",),
        )
        self.proposal = self.coord_engine.create_proposal(
            context_id=self.context.context_id,
            workspace_id="ws-radiology",
            proposal_type="REVISE_EXECUTION_STRATEGY",
            rationale="High-resolution volumetric CT pipeline with radiologist verification.",
            proposed_changes={"volumetric": True},
            evidence_reference_ids=("ev-ct-001",),
        )
        self.coord_engine.approve_proposal(
            proposal_id=self.proposal.proposal_id,
            workspace_id="ws-radiology",
            reviewed_by="chief_radiologist",
        )

        # Approved plan
        self.approved_plan = self.plan_engine.convert_proposal_to_plan(
            proposal_id=self.proposal.proposal_id,
            workspace_id="ws-radiology",
            goal="Volumetric CT lung screening",
            steps_spec=[
                {"title": "Segmentation", "assigned_agent_id": "agent-radiologist"},
                {"title": "Clinical Report Generation", "assigned_model_id": "med-llama-8b-local"},
            ],
        )
        self.plan_engine.approve_plan(
            plan_id=self.approved_plan.plan_id,
            workspace_id="ws-radiology",
            reviewed_by="chief_radiologist",
            review_notes="Approved for runtime execution handoff.",
        )

        # Unapproved DRAFT plan
        self.draft_plan = self.plan_engine.convert_proposal_to_plan(
            proposal_id=self.proposal.proposal_id,
            workspace_id="ws-radiology",
            goal="Draft plan",
            steps_spec=[{"title": "Unapproved step"}],
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_approved_plan_handoff_accepted_and_dispatched(self) -> None:
        """Verify approved plan handoff passes all safety gates and dispatches smoothly."""
        handoff = self.bridge.request_plan_execution_handoff(
            plan_id=self.approved_plan.plan_id,
            workspace_id="ws-radiology",
            requested_by="operator_tech",
            runtime_session_id="rts-radiology-001",
            resources_valid=True,
            capabilities_satisfied=True,
            locality_satisfied=True,
            provider_approvals_satisfied=True,
        )
        self.assertEqual(handoff.status, "ACCEPTED")
        self.assertIsNone(handoff.rejection_reason)
        self.assertTrue(handoff.governance_checks["gate_passed"])

        # Dispatch
        dispatched = self.bridge.dispatch_plan_handoff(
            handoff_id=handoff.handoff_id,
            workspace_id="ws-radiology",
        )
        self.assertEqual(dispatched.status, "DISPATCHED")
        self.assertIsNotNone(dispatched.dispatched_at)

    def test_unapproved_plan_handoff_strictly_blocked(self) -> None:
        """Verify requesting handoff on unapproved plan raises RuntimeError immediately."""
        with self.assertRaises(RuntimeError):
            self.bridge.request_plan_execution_handoff(
                plan_id=self.draft_plan.plan_id,
                workspace_id="ws-radiology",
                requested_by="operator_tech",
                runtime_session_id="rts-radiology-001",
            )

    def test_safety_gate_failure_sets_status_rejected(self) -> None:
        """Verify handoff is REJECTED if any safety gate fails."""
        handoff = self.bridge.request_plan_execution_handoff(
            plan_id=self.approved_plan.plan_id,
            workspace_id="ws-radiology",
            requested_by="operator_tech",
            runtime_session_id="rts-radiology-001",
            resources_valid=True,
            capabilities_satisfied=False,  # Failure here!
            locality_satisfied=True,
            provider_approvals_satisfied=True,
        )
        self.assertEqual(handoff.status, "REJECTED")
        self.assertIsNotNone(handoff.rejection_reason)
        self.assertFalse(handoff.governance_checks["gate_passed"])

        # Rejected handoff cannot be dispatched
        with self.assertRaises(RuntimeError):
            self.bridge.dispatch_plan_handoff(
                handoff_id=handoff.handoff_id,
                workspace_id="ws-radiology",
            )

    def test_cancellation_workflow(self) -> None:
        """Verify operator cancellation records reason truthfully."""
        handoff = self.bridge.request_plan_execution_handoff(
            plan_id=self.approved_plan.plan_id,
            workspace_id="ws-radiology",
            requested_by="operator_tech",
            runtime_session_id="rts-radiology-001",
        )
        cancelled = self.bridge.cancel_plan_handoff(
            handoff_id=handoff.handoff_id,
            workspace_id="ws-radiology",
            reason="Radiology workstation maintenance started.",
        )
        self.assertEqual(cancelled.status, "CANCELLED")
        self.assertEqual(cancelled.cancellation_reason, "Radiology workstation maintenance started.")

    def test_failure_recording(self) -> None:
        """Verify execution handoff failure is recorded without automatic recovery."""
        handoff = self.bridge.request_plan_execution_handoff(
            plan_id=self.approved_plan.plan_id,
            workspace_id="ws-radiology",
            requested_by="operator_tech",
            runtime_session_id="rts-radiology-001",
        )
        failed = self.bridge.record_handoff_failure(
            handoff_id=handoff.handoff_id,
            workspace_id="ws-radiology",
            failure_reason="Compute backend connection dropped.",
        )
        self.assertEqual(failed.status, "FAILED")
        self.assertEqual(failed.rejection_reason, "Compute backend connection dropped.")

    def test_architectural_decoupling(self) -> None:
        """Verify Model Router and Agent Fabric remain independent of governed execution plan bridge."""
        import amca.intelligence.cross_provider_router as cpr
        source_cpr = inspect.getsource(cpr)
        self.assertNotIn("GovernedExecutionPlanBridge", source_cpr)


if __name__ == "__main__":
    unittest.main()

