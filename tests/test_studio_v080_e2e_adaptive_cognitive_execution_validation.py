"""AMCA Studio v0.8.0 End-to-End Adaptive Cognitive Execution Validation Test Suite.

Milestone: AG-AMCA-STUDIO-V0.8.0-END-TO-END-ADAPTIVE-COGNITIVE-EXECUTION-VALIDATION-01

Comprehensive Integration Scenarios:
1. Scenario A: Research Workflow (capability matching -> contract -> adaptive proposal -> approval -> execution plan -> handoff -> evidence)
2. Scenario B: Software Engineering Workflow (model matching, role assignment, contract, handoff, artifact trace)
3. Scenario C: Document Productivity Workflow (knowledge context, approval, evidence, verification)
4. Scenario D: Failure & Safety Boundary Path (capability mismatch, proposal blocking, handoff blocking, cancellation, failure recording)
5. Scenario E: Governance Audit & Anti-Reasoning Invariant (zero chain-of-thought, zero subjective AI scores, architectural decoupling)
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
from amca.agent_capability_governance import (
    AgentCapabilityGovernanceEngine,
    AgentCapabilityGovernanceStore,
    AgentCapabilityProfile,
    ModelCapabilityProfile,
)
from amca.cognitive_loop_orchestrator import (
    CognitiveWorkflowOrchestrator,
    CognitiveWorkflowStore,
)
from amca.cognitive_runtime_bridge import (
    CognitiveRuntimeBridge,
    CognitiveRuntimeBridgeStore,
)
from amca.cognitive_runtime_observability import (
    CognitiveRuntimeObservabilityStore,
    CognitiveRuntimeObserver,
    RuntimeEventStreamItem,
)
from amca.governed_execution_plan_bridge import (
    GovernedExecutionPlanBridge,
    GovernedExecutionPlanBridgeStore,
)
from amca.governed_multi_agent_collaboration import (
    GovernedCollaborationCoordinator,
    GovernedCollaborationStore,
)


class StudioV080E2EAdaptiveCognitiveExecutionValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp_dir.name)

        # Stores
        self.gov_store = AgentCapabilityGovernanceStore(self.tmp_path / "gov.sqlite3")
        self.gov_engine = AgentCapabilityGovernanceEngine(self.gov_store)

        self.collab_store = GovernedCollaborationStore(self.tmp_path / "collab.sqlite3")
        self.collab_coordinator = GovernedCollaborationCoordinator(
            store=self.collab_store,
            governance_engine=self.gov_engine,
        )

        self.coord_store = AdaptiveCoordinationStore(self.tmp_path / "coord.sqlite3")
        self.coord_engine = AdaptiveCoordinationEngine(self.coord_store)

        self.plan_store = AdaptiveExecutionPlanningStore(self.tmp_path / "plan.sqlite3")
        self.plan_engine = AdaptiveExecutionPlanningEngine(
            store=self.plan_store,
            coordination_store=self.coord_store,
        )

        self.bridge_store = GovernedExecutionPlanBridgeStore(self.tmp_path / "plan_bridge.sqlite3")
        self.plan_bridge = GovernedExecutionPlanBridge(
            store=self.bridge_store,
            planning_store=self.plan_store,
        )

        self.wf_store = CognitiveWorkflowStore(self.tmp_path / "workflows.sqlite3")
        self.orchestrator = CognitiveWorkflowOrchestrator(store=self.wf_store)

        self.runtime_store = CognitiveRuntimeBridgeStore(self.tmp_path / "runtime.sqlite3")
        self.runtime_bridge = CognitiveRuntimeBridge(
            store=self.runtime_store,
            orchestrator=self.orchestrator,
        )

        self.obs_store = CognitiveRuntimeObservabilityStore(self.tmp_path / "obs.sqlite3")
        self.observer = CognitiveRuntimeObserver(
            store=self.obs_store,
            bridge_store=self.runtime_store,
        )

        # Seed standard profiles
        self.gov_engine.register_agent_profile(
            AgentCapabilityProfile(
                agent_id="agent-scientist",
                name="Clinical Scientist",
                role="researcher",
                declared_capabilities=("oncology", "genomics", "protocol_design"),
                permission_scopes=("workspace:write", "knowledge:read"),
                locality_affinity="LOCAL_ONLY",
            )
        )
        self.gov_engine.register_agent_profile(
            AgentCapabilityProfile(
                agent_id="agent-reviewer",
                name="Peer Reviewer",
                role="reviewer",
                declared_capabilities=("peer_review", "compliance_audit"),
                permission_scopes=("workspace:read",),
                locality_affinity="HYBRID",
            )
        )
        self.gov_engine.register_agent_profile(
            AgentCapabilityProfile(
                agent_id="agent-coder",
                name="Systems Engineer",
                role="coder",
                declared_capabilities=("python", "simd", "benchmarking"),
                permission_scopes=("workspace:write", "terminal:exec"),
                locality_affinity="LOCAL_ONLY",
            )
        )

        self.gov_engine.register_model_profile(
            ModelCapabilityProfile(
                model_id="qwen2.5-coder-32b",
                provider="ollama",
                locality="LOCAL_OFFLINE",
                supported_capabilities=("python", "simd", "benchmarking"),
                context_window=32768,
                governance_requirements=("offline_local",),
            )
        )
        self.gov_engine.register_model_profile(
            ModelCapabilityProfile(
                model_id="med-llama-8b-local",
                provider="ollama",
                locality="LOCAL_OFFLINE",
                supported_capabilities=("oncology", "genomics"),
                context_window=16384,
                governance_requirements=("hipaa_offline",),
            )
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_scenario_a_research_workflow(self) -> None:
        """Scenario A: Complete research workflow from capability matching to plan handoff and evidence."""
        # 1. Capability matching
        matches = self.gov_engine.match_agent_candidates(required_capabilities=("oncology", "genomics"))
        self.assertEqual(matches[0].candidate_id, "agent-scientist")
        self.assertTrue(matches[0].exact_match)

        # 2. Collaboration contract
        contract = self.gov_engine.create_collaboration_contract(
            workflow_id="wf-res-001",
            workspace_id="ws-clinical",
            lead_agent_id="agent-scientist",
            participant_agent_ids=("agent-reviewer",),
            assigned_model_ids=("med-llama-8b-local",),
            agreed_capabilities=("oncology", "peer_review"),
        )
        self.assertEqual(contract.status, "AGREED")

        # 3. Collaboration session & roles
        collab_sess = self.collab_coordinator.create_collaboration_session(
            contract_id=contract.contract_id,
            workspace_id="ws-clinical",
        )
        asgn1 = self.collab_coordinator.assign_agent_role(
            session_id=collab_sess.session_id,
            workspace_id="ws-clinical",
            agent_id="agent-scientist",
            role="PRIMARY_AUTHOR",
            required_capabilities=("oncology",),
        )
        self.assertTrue(asgn1.compatibility_verified)

        asgn2 = self.collab_coordinator.assign_agent_role(
            session_id=collab_sess.session_id,
            workspace_id="ws-clinical",
            agent_id="agent-reviewer",
            role="TECHNICAL_REVIEWER",
            required_capabilities=("peer_review",),
        )
        self.assertTrue(asgn2.compatibility_verified)

        collab_sess = self.collab_coordinator.verify_collaboration_governance(
            session_id=collab_sess.session_id,
            workspace_id="ws-clinical",
            provider_approvals_verified=True,
        )
        self.assertTrue(collab_sess.governance_verified)

        # 4. Adaptive context & proposal
        ctx = self.coord_engine.capture_coordination_context(
            workflow_id="wf-res-001",
            workspace_id="ws-clinical",
            task_domain="clinical_oncology",
            detected_constraints=("hipaa_offline",),
            required_capabilities=("oncology", "peer_review"),
            active_participants=("agent-scientist", "agent-reviewer"),
            runtime_session_id=collab_sess.session_id,
            recorded_facts={"cohort_count": 500},
        )
        proposal = self.coord_engine.create_proposal(
            context_id=ctx.context_id,
            workspace_id="ws-clinical",
            proposal_type="REVISE_EXECUTION_STRATEGY",
            rationale="Add statistical power validation step.",
            proposed_changes={"validation_step": "power_analysis"},
            evidence_reference_ids=("ev-stat-01",),
        )
        self.assertEqual(proposal.status, "PROPOSED")

        # 5. Human approval of proposal
        approved_prop = self.coord_engine.approve_proposal(
            proposal_id=proposal.proposal_id,
            workspace_id="ws-clinical",
            reviewed_by="chief_oncologist",
            reviewer_notes="Approved statistical power validation.",
        )
        self.assertEqual(approved_prop.status, "APPROVED")

        # 6. Proposal-to-plan conversion & plan approval
        plan = self.plan_engine.convert_proposal_to_plan(
            proposal_id=approved_prop.proposal_id,
            workspace_id="ws-clinical",
            goal="Clinical oncology study with power analysis",
            steps_spec=[
                {"title": "Genomic Extraction", "assigned_agent_id": "agent-scientist"},
                {"title": "Peer Review", "assigned_agent_id": "agent-reviewer"},
            ],
        )
        self.assertEqual(plan.status, "DRAFT")
        approved_plan = self.plan_engine.approve_plan(
            plan_id=plan.plan_id,
            workspace_id="ws-clinical",
            reviewed_by="chief_oncologist",
        )
        self.assertEqual(approved_plan.status, "APPROVED")

        # 7. Governed execution plan handoff
        handoff = self.plan_bridge.request_plan_execution_handoff(
            plan_id=approved_plan.plan_id,
            workspace_id="ws-clinical",
            requested_by="operator_dr",
            runtime_session_id=collab_sess.session_id,
            resources_valid=True,
            capabilities_satisfied=True,
            locality_satisfied=True,
            provider_approvals_satisfied=True,
        )
        self.assertEqual(handoff.status, "ACCEPTED")
        dispatched = self.plan_bridge.dispatch_plan_handoff(
            handoff_id=handoff.handoff_id,
            workspace_id="ws-clinical",
        )
        self.assertEqual(dispatched.status, "DISPATCHED")

        # 8. Observability event record
        self.obs_store.append_event(
            RuntimeEventStreamItem(
                event_id="evs-res-001",
                session_id=collab_sess.session_id,
                workflow_id="wf-res-001",
                workspace_id="ws-clinical",
                seq_num=0,
                event_type="GOVERNED_PLAN_DISPATCHED",
                payload={"handoff_id": dispatched.handoff_id, "plan_id": approved_plan.plan_id},
                timestamp="2026-09-02T22:00:00.000000Z",
            )
        )
        events = self.obs_store.list_events(session_id=collab_sess.session_id, workspace_id="ws-clinical")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "GOVERNED_PLAN_DISPATCHED")

    def test_scenario_b_software_engineering_workflow(self) -> None:
        """Scenario B: Software engineering workflow with coder and reviewer collaboration."""
        # Model matching for code generation
        model_matches = self.gov_engine.match_model_candidates(required_capabilities=("python", "simd"))
        self.assertEqual(model_matches[0].candidate_id, "qwen2.5-coder-32b")
        self.assertTrue(model_matches[0].exact_match)

        contract = self.gov_engine.create_collaboration_contract(
            workflow_id="wf-swe-002",
            workspace_id="ws-engineering",
            lead_agent_id="agent-coder",
            participant_agent_ids=("agent-reviewer",),
            assigned_model_ids=("qwen2.5-coder-32b",),
            agreed_capabilities=("python", "peer_review"),
        )
        collab_sess = self.collab_coordinator.create_collaboration_session(
            contract_id=contract.contract_id,
            workspace_id="ws-engineering",
        )
        self.collab_coordinator.assign_agent_role(
            session_id=collab_sess.session_id,
            workspace_id="ws-engineering",
            agent_id="agent-coder",
            role="PRIMARY_AUTHOR",
            required_capabilities=("python",),
        )
        self.collab_coordinator.assign_agent_role(
            session_id=collab_sess.session_id,
            workspace_id="ws-engineering",
            agent_id="agent-reviewer",
            role="TECHNICAL_REVIEWER",
            required_capabilities=("peer_review",),
        )
        collab_sess = self.collab_coordinator.verify_collaboration_governance(
            session_id=collab_sess.session_id,
            workspace_id="ws-engineering",
            provider_approvals_verified=True,
        )
        self.collab_coordinator.activate_collaboration(
            session_id=collab_sess.session_id,
            workspace_id="ws-engineering",
        )

        # Communication handoff
        msg = self.collab_coordinator.post_collaboration_message(
            session_id=collab_sess.session_id,
            workspace_id="ws-engineering",
            sender_id="agent-coder",
            recipient_id="agent-reviewer",
            message_type="TASK_HANDOFF",
            content_reference="simd_kernels.py",
            metadata={"lines_of_code": 450},
        )
        self.assertEqual(msg.content_reference, "simd_kernels.py")

        # Complete collaboration
        collab_sess = self.collab_coordinator.complete_session(
            session_id=collab_sess.session_id,
            workspace_id="ws-engineering",
        )
        self.assertEqual(collab_sess.status, "COMPLETED")

    def test_scenario_c_document_productivity_workflow(self) -> None:
        """Scenario C: Document productivity with step status audit progression."""
        ctx = self.coord_engine.capture_coordination_context(
            workflow_id="wf-doc-003",
            workspace_id="ws-office",
            task_domain="technical_documentation",
            detected_constraints=("local_offline_only",),
            required_capabilities=("protocol_design",),
            active_participants=("agent-scientist",),
            recorded_facts={"doc_type": "sop_manual"},
        )
        prop = self.coord_engine.create_proposal(
            context_id=ctx.context_id,
            workspace_id="ws-office",
            proposal_type="ATTACH_KNOWLEDGE_SOURCE",
            rationale="Attach laboratory SOP knowledge corpus.",
            proposed_changes={"corpus": "iso_9001_sops"},
        )
        self.coord_engine.approve_proposal(
            proposal_id=prop.proposal_id,
            workspace_id="ws-office",
            reviewed_by="qa_director",
        )

        plan = self.plan_engine.convert_proposal_to_plan(
            proposal_id=prop.proposal_id,
            workspace_id="ws-office",
            goal="Generate ISO-compliant lab SOP",
            steps_spec=[
                {"title": "Draft SOP Sections", "assigned_agent_id": "agent-scientist"},
                {"title": "QA Audit", "assigned_agent_id": "agent-reviewer"},
            ],
        )
        self.plan_engine.approve_plan(
            plan_id=plan.plan_id,
            workspace_id="ws-office",
            reviewed_by="qa_director",
        )
        ready_plan = self.plan_engine.prepare_for_execution(
            plan_id=plan.plan_id,
            workspace_id="ws-office",
        )
        self.assertEqual(ready_plan.status, "READY_FOR_EXECUTION")

        # Advance step 1: PENDING -> IN_PROGRESS -> COMPLETED
        step1_id = ready_plan.steps[0].step_id
        plan = self.plan_engine.update_step_status(
            plan_id=plan.plan_id,
            workspace_id="ws-office",
            step_id=step1_id,
            status="IN_PROGRESS",
        )
        self.assertEqual(plan.steps[0].status, "IN_PROGRESS")

        plan = self.plan_engine.update_step_status(
            plan_id=plan.plan_id,
            workspace_id="ws-office",
            step_id=step1_id,
            status="COMPLETED",
        )
        self.assertEqual(plan.steps[0].status, "COMPLETED")

    def test_scenario_d_failure_and_safety_boundary_path(self) -> None:
        """Scenario D: Complete validation of safety boundaries, error paths, and non-autonomous recovery."""
        # 1. Capability mismatch in collaboration role assignment
        contract = self.gov_engine.create_collaboration_contract(
            workflow_id="wf-fail-004",
            workspace_id="ws-fail",
            lead_agent_id="agent-scientist",
            participant_agent_ids=(),
            assigned_model_ids=(),
            agreed_capabilities=(),
        )
        sess = self.collab_coordinator.create_collaboration_session(
            contract_id=contract.contract_id,
            workspace_id="ws-fail",
        )
        # scientist lacks "simd"
        asgn = self.collab_coordinator.assign_agent_role(
            session_id=sess.session_id,
            workspace_id="ws-fail",
            agent_id="agent-scientist",
            role="COORDINATOR",
            required_capabilities=("simd",),
        )
        self.assertFalse(asgn.compatibility_verified)

        sess = self.collab_coordinator.verify_collaboration_governance(
            session_id=sess.session_id,
            workspace_id="ws-fail",
            provider_approvals_verified=True,
        )
        self.assertFalse(sess.governance_verified)
        self.assertEqual(sess.status, "FAILED")

        # 2. Plan conversion strictly blocked on unapproved proposal
        ctx = self.coord_engine.capture_coordination_context(
            workflow_id="wf-fail-004",
            workspace_id="ws-fail",
            task_domain="failure_testing",
        )
        prop = self.coord_engine.create_proposal(
            context_id=ctx.context_id,
            workspace_id="ws-fail",
            proposal_type="REQUEST_MODEL_UPGRADE",
            rationale="Test failure",
            proposed_changes={},
        )
        with self.assertRaises(RuntimeError):
            self.plan_engine.convert_proposal_to_plan(
                proposal_id=prop.proposal_id,
                workspace_id="ws-fail",
                goal="Should fail",
                steps_spec=[],
            )

        # 3. Plan handoff strictly blocked when safety gate fails
        self.coord_engine.approve_proposal(
            proposal_id=prop.proposal_id,
            workspace_id="ws-fail",
            reviewed_by="admin",
        )
        plan = self.plan_engine.convert_proposal_to_plan(
            proposal_id=prop.proposal_id,
            workspace_id="ws-fail",
            goal="Plan to test safety gate",
            steps_spec=[],
        )
        self.plan_engine.approve_plan(
            plan_id=plan.plan_id,
            workspace_id="ws-fail",
            reviewed_by="admin",
        )
        handoff = self.plan_bridge.request_plan_execution_handoff(
            plan_id=plan.plan_id,
            workspace_id="ws-fail",
            requested_by="admin",
            runtime_session_id="rts-999",
            resources_valid=True,
            capabilities_satisfied=True,
            locality_satisfied=True,
            provider_approvals_satisfied=False,  # Unapproved provider!
        )
        self.assertEqual(handoff.status, "REJECTED")
        self.assertIn("failed", handoff.rejection_reason)

        # 4. Operator cancellation records reason faithfully
        cancelled = self.plan_bridge.cancel_plan_handoff(
            handoff_id=handoff.handoff_id,
            workspace_id="ws-fail",
            reason="Operator aborted test run.",
        )
        self.assertEqual(cancelled.status, "CANCELLED")
        self.assertEqual(cancelled.cancellation_reason, "Operator aborted test run.")

    def test_scenario_e_governance_audit_and_anti_reasoning_invariant(self) -> None:
        """Scenario E: Governance audit across all stores ensuring zero chain-of-thought or hidden reasoning."""
        # Verify sanitization in adaptive coordination context
        ctx = self.coord_engine.capture_coordination_context(
            workflow_id="wf-audit-005",
            workspace_id="ws-audit",
            task_domain="security_audit",
            recorded_facts={
                "fact_a": 123,
                "chain_of_thought": "secret reasoning",
                "hidden_reasoning": "forbidden thought",
                "private_deliberation": "internal thoughts",
            },
        )
        retrieved_ctx = self.coord_store.get_context(
            context_id=ctx.context_id,
            workspace_id="ws-audit",
        )
        self.assertIsNotNone(retrieved_ctx)
        self.assertNotIn("chain_of_thought", retrieved_ctx.recorded_facts)
        self.assertNotIn("hidden_reasoning", retrieved_ctx.recorded_facts)
        self.assertNotIn("private_deliberation", retrieved_ctx.recorded_facts)

        # Verify decoupling: Model Router and Agent Fabric remain independent
        import amca.intelligence.cross_provider_router as cpr
        src = inspect.getsource(cpr)
        self.assertNotIn("GovernedExecutionPlanBridge", src)
        self.assertNotIn("AdaptiveExecutionPlanningEngine", src)
        self.assertNotIn("GovernedCollaborationCoordinator", src)


if __name__ == "__main__":
    unittest.main()

