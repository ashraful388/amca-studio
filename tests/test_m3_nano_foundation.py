"""M3 Nano Agent Runtime Foundation tests.

Focused coverage required by the issued M3 work order: contract binding to
the existing Agent Fabric, governor authorization for spawn proposals,
authority-shrink guarantees, depth/count/concurrency/resource bounds, typed
communication, escalation without authority, descendant cancellation, and
all required fail-closed cases. The existing Agent Fabric files are used
read-only (REUSED); no production path is touched.
"""

import unittest

from amca.intelligence import (
    DEFAULT_GATES,
    AgentCompletionDisposition,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    FeatureGateRegistry,
    NANO_LIFECYCLE_TRANSITIONS,
    NanoAssignment,
    NanoEscalationRequest,
    NanoMessage,
    NanoMessageKind,
    NanoResultRecord,
    NanoRunRegistry,
    NanoSpawnProposal,
    Sensitivity,
    require_nano_gate,
)
from amca.agent_fabric import AgentGovernor, ResourceEnvelope
from amca.agent_runtime import AgentTaskStatus

CREATED = "2026-08-27T00:00:00+00:00"


def meta_factory(record_id):
    return ContractMetadata(        record_id=record_id,
        correlation_id="corr-m3",
        producer="m3-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def meta(record_id="rec-m3"):
    return meta_factory(record_id)


def proposal(**overrides):
    fields = dict(
        meta=meta("prop-1"),
        proposal_id="prop-1",
        run_ref="run-1",
        parent_task_id="task:root",
        parent_depth=0,
        task_purpose="verify citation claims",
        task_type="verification",
        requested_child_count=1,
        capabilities=("citation_check",),
        permissions=("local_search",),
        expected_cost=0.0,
        correlation_id="corr-m3",
    )
    fields.update(overrides)
    return NanoSpawnProposal(**fields)


def registry(**kwargs):
    return NanoRunRegistry(
        meta_factory=meta_factory,
        run_ref="run-1",
        envelope=kwargs.pop("envelope", None),
        allowed_capabilities=kwargs.pop(
            "allowed_capabilities", ("citation_check", "summary")
        ),
        **kwargs,
    )


def nano_gates():
    return FeatureGateRegistry(nano_agents=True)


def spawn_one(reg, proposal_id="prop-1", parent_task_id="task:root",
              parent_depth=0, parent_permissions=("local_search",),
              capabilities=("citation_check",), permissions=("local_search",)):
    return reg.authorize_spawn(
        proposal(
            proposal_id=proposal_id,
            parent_task_id=parent_task_id,
            parent_depth=parent_depth,
            capabilities=capabilities,
            permissions=permissions,
        ),
        parent_permissions=parent_permissions,
        gates=nano_gates(),
    )


class ContractBindingTests(unittest.TestCase):
    def test_spawn_proposal_round_trips(self):
        base = proposal()
        from amca.intelligence import NanoSpawnProposal
        restored = NanoSpawnProposal.from_dict(base.to_dict())
        self.assertEqual(restored, base)

    def test_malformed_proposals_rejected(self):
        with self.assertRaises(Exception):
            proposal(requested_child_count=0)
        with self.assertRaises(Exception):
            proposal(expected_cost=-1.0)
        with self.assertRaises(Exception):
            proposal(parent_depth=-1)
        with self.assertRaises(Exception):
            NanoSpawnProposal.from_dict(
                dict(proposal().to_dict(), expected_cost=float("inf"))
            )

    def test_assignment_requires_hierarchy_no_orphans(self):
        with self.assertRaises(Exception) as ctx:
            NanoAssignment(
                meta=meta("assign-x"),
                nano_id="nano:x",
                task_id="task:x",
                run_ref="run-1",
                parent_task_id="",
                depth=1,
                purpose="x",
                task_type="verification",
                capabilities=("citation_check",),
                permissions=(),
                expected_cost=0.0,
                correlation_id="corr-m3",
                proposal_ref="prop-x",
                assignment_version=1,
            )
        # orphan protection: the hierarchy identity fields are mandatory
        self.assertIn("parent_task_id", str(ctx.exception))

    def test_existing_lifecycle_map_reused(self):
        # the nano foundation reuses the EXISTING AgentTaskStatus transitions
        self.assertIs(
            NANO_LIFECYCLE_TRANSITIONS,
            AgentTaskStatus.ALLOWED_TRANSITIONS,
        )
        self.assertIn("nano", __import__(
            "amca.agent_fabric", fromlist=["AgentClass"]
        ).AgentClass.NANO.value)


class GateRefusalTests(unittest.TestCase):
    def test_spawn_while_gate_off_refused(self):
        reg = registry()
        with self.assertRaises(ContractError) as ctx:
            reg.propose_spawn(proposal(), parent_permissions=("local_search",),
                              gates=DEFAULT_GATES)
        self.assertIn("nano_agents gate", str(ctx.exception))
        with self.assertRaises(ContractError):
            require_nano_gate(DEFAULT_GATES)

    def test_gate_on_allows_normal_path(self):
        reg = registry()
        decision = reg.propose_spawn(
            proposal(), parent_permissions=("local_search",), gates=nano_gates()
        )
        self.assertTrue(decision.authorized)


class GovernorAuthorizationTests(unittest.TestCase):
    def test_authorized_spawn_seals_immutable_shrunk_assignment(self):
        reg = registry()
        assignment = spawn_one(reg)
        self.assertEqual(assignment.nano_id, "nano:prop-1")
        self.assertEqual(assignment.parent_task_id, "task:root")
        self.assertEqual(assignment.depth, 1)
        self.assertEqual(assignment.permissions, ("local_search",))
        restored = NanoAssignment.from_dict(assignment.to_dict())
        self.assertEqual(restored, assignment)

    def test_child_requesting_more_authority_rejected(self):
        reg = registry()
        decision = reg.propose_spawn(
            proposal(permissions=("network", "local_search")),
            parent_permissions=("local_search",),
            gates=nano_gates(),
        )
        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason_code, "permission_escalation")

    def test_unauthorized_capability_rejected(self):
        reg = registry(allowed_capabilities=("summary",))
        decision = reg.propose_spawn(
            proposal(capabilities=("citation_check",)),
            parent_permissions=("local_search",),
            gates=nano_gates(),
        )
        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason_code, "unauthorized_capability")

    def test_maximum_children_per_parent_enforced(self):
        reg = registry(max_children_per_parent=1)
        spawn_one(reg, proposal_id="p1")
        decision = reg.propose_spawn(
            proposal(proposal_id="p2"),
            parent_permissions=("local_search",),
            gates=nano_gates(),
        )
        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason_code, "children_per_parent_exceeded")

    def test_total_run_agent_limit_enforced(self):
        reg = registry(
            envelope=ResourceEnvelope(max_nano_tasks=1),
            max_children_per_parent=8,
        )
        spawn_one(reg, proposal_id="p1")
        with self.assertRaises(ContractError) as ctx:
            spawn_one(reg, proposal_id="p2")
        self.assertIn(
            ctx.exception.args[0],
            "spawn proposal 'p2' was not authorized: run_agent_limit_exceeded",
        )

    def test_duplicate_identity_rejected(self):
        reg = registry()
        first = reg.propose_spawn(
            proposal(), parent_permissions=("local_search",), gates=nano_gates()
        )
        self.assertTrue(first.authorized)
        second = reg.propose_spawn(
            proposal(), parent_permissions=("local_search",), gates=nano_gates()
        )
        self.assertFalse(second.authorized)
        self.assertEqual(second.reason_code, "duplicate_identity")

    def test_spawn_depth_exceeded_rejected(self):
        reg = registry(envelope=ResourceEnvelope(max_manager_depth=1))
        decision = reg.propose_spawn(
            proposal(parent_depth=1),
            parent_permissions=("local_search",),
            gates=nano_gates(),
        )
        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason_code, "spawn_depth_exceeded")

    def test_concurrency_bound_respected(self):
        reg = registry(envelope=ResourceEnvelope(max_concurrent_agents=1))
        decision = reg.propose_spawn(
            proposal(requested_child_count=2),
            parent_permissions=("local_search",),
            gates=nano_gates(),
        )
        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason_code, "concurrency_bound")

    def test_budget_ceiling_enforced(self):
        reg = registry(envelope=ResourceEnvelope(max_api_cost_usd=1.0))
        decision = reg.propose_spawn(
            proposal(expected_cost=25.0),
            parent_permissions=("local_search",),
            gates=nano_gates(),
        )
        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason_code, "budget_ceiling")

    def test_existing_governor_decision_layer_reported(self):
        reg = registry()
        decision = reg.propose_spawn(
            proposal(), parent_permissions=("local_search",), gates=nano_gates()
        )
        self.assertEqual(decision.governor_layer, "existing_agent_governor_bridge")
        self.assertTrue(decision.checks["existing_governor"])
        self.assertIsInstance(decision.envelope_snapshot, dict)


class CancellationTests(unittest.TestCase):
    def test_cancelled_parent_prevents_new_spawn(self):
        reg = registry()
        assignment = spawn_one(reg, proposal_id="p1")
        reg.cancel(assignment.nano_id)
        decision = reg.propose_spawn(
            proposal(proposal_id="p2", parent_task_id=assignment.task_id),
            parent_permissions=("local_search",),
            gates=nano_gates(),
        )
        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason_code, "parent_cancelled")

    def test_cancellation_propagates_to_descendants(self):
        from amca.agent_fabric import AgentFabricSettings
        reg = registry(
            envelope=ResourceEnvelope(max_manager_depth=3, max_nano_tasks=8),
            settings=AgentFabricSettings(
                envelope=ResourceEnvelope(max_manager_depth=3, max_nano_tasks=8),
                recursive_spawning=True,
            ),
        )
        parent = spawn_one(reg, proposal_id="p1")
        child = spawn_one(
            reg,
            proposal_id="p2",
            parent_task_id=parent.task_id,
            parent_depth=1,
        )
        cancelled = reg.cancel(parent.nano_id)
        self.assertIn(parent.nano_id, cancelled)
        self.assertIn(child.nano_id, cancelled)
        self.assertEqual(reg.status(child.nano_id), AgentTaskStatus.CANCELLED)

    def test_run_cancel_refuses_spawn_everywhere(self):
        reg = registry()
        spawn_one(reg, proposal_id="p1")
        reg.cancel_run()
        decision = reg.propose_spawn(
            proposal(proposal_id="p2"),
            parent_permissions=("local_search",),
            gates=nano_gates(),
        )
        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason_code, "run_cancelled")

    def test_invalid_lifecycle_transition_rejected(self):
        reg = registry()
        assignment = spawn_one(reg, proposal_id="p1")
        with self.assertRaises(ContractError):
            reg.transition(assignment.nano_id, AgentTaskStatus.COMPLETED)

    def test_legal_lifecycle_path_accepted(self):
        reg = registry()
        assignment = spawn_one(reg, proposal_id="p1")
        for step in (
            AgentTaskStatus.AUTHORIZED,
            AgentTaskStatus.QUEUED,
            AgentTaskStatus.STARTING,
            AgentTaskStatus.RUNNING,
            AgentTaskStatus.VERIFYING,
            AgentTaskStatus.COMPLETED,
            AgentTaskStatus.MERGED,
            AgentTaskStatus.RETIRED,
        ):
            reg.transition(assignment.nano_id, step)
        self.assertEqual(reg.status(assignment.nano_id), AgentTaskStatus.RETIRED)

    def test_unknown_nano_transition_rejected(self):
        reg = registry()
        with self.assertRaises(ContractError):
            reg.transition("nano:ghost", AgentTaskStatus.RUNNING)


class CommunicationTests(unittest.TestCase):
    def test_typed_communication_between_known_agents(self):
        from amca.agent_fabric import AgentFabricSettings
        reg = registry(
            envelope=ResourceEnvelope(max_manager_depth=3, max_nano_tasks=8),
            settings=AgentFabricSettings(
                envelope=ResourceEnvelope(max_manager_depth=3, max_nano_tasks=8),
                recursive_spawning=True,
            ),
        )
        first = spawn_one(reg, proposal_id="p1")
        second = spawn_one(
            reg, proposal_id="p2", parent_task_id=first.task_id, parent_depth=1
        )
        message = reg.send(
            NanoMessage(
                meta=meta("msg-1"),
                sender_id=first.nano_id,
                recipient_id=second.nano_id,
                kind=NanoMessageKind.EVIDENCE,
                correlation_id="corr-m3",
                summary="citation evidence attached",
            )
        )
        self.assertIn(message, reg.messages())

    def test_message_to_unknown_agent_rejected(self):
        reg = registry()
        first = spawn_one(reg, proposal_id="p1")
        with self.assertRaises(ContractError):
            reg.send(
                NanoMessage(
                    meta=meta("msg-2"),
                    sender_id=first.nano_id,
                    recipient_id="nano:ghost",
                    kind=NanoMessageKind.QUESTION,
                    correlation_id="corr-m3",
                    summary="anyone there?",
                )
            )

    def test_escalation_grants_no_authority(self):
        reg = registry()
        assignment = spawn_one(reg, proposal_id="p1")
        request = reg.escalate(
            NanoEscalationRequest(
                meta=meta("esc-1"),
                nano_id=assignment.nano_id,
                reason="need network access",
                requested_authority="network",
            )
        )
        self.assertFalse(request.granted)
        with self.assertRaises(Exception):
            NanoEscalationRequest(
                meta=meta("esc-2"),
                nano_id=assignment.nano_id,
                reason="self-authorize",
                requested_authority="network",
                granted=True,
            )
        self.assertIn(request, reg.escalations())


class EvidenceTests(unittest.TestCase):
    def test_in_scope_result_accepted(self):
        reg = registry()
        assignment = spawn_one(reg, proposal_id="p1")
        reg.transition(assignment.nano_id, AgentTaskStatus.AUTHORIZED)
        record = reg.record_result(
            NanoResultRecord(
                meta=meta("res-1"),
                nano_id=assignment.nano_id,
                disposition=AgentCompletionDisposition.COMPLETE,
                summary="verified 4 citations",
                output_digest="a" * 64,
                capabilities_used=("citation_check",),
                evidence_refs=("ev-1",),
            )
        )
        self.assertEqual(reg.result(assignment.nano_id), record)

    def test_out_of_scope_result_rejected(self):
        reg = registry()
        assignment = spawn_one(reg, proposal_id="p1")
        with self.assertRaises(ContractError) as ctx:
            reg.record_result(
                NanoResultRecord(
                    meta=meta("res-2"),
                    nano_id=assignment.nano_id,
                    disposition=AgentCompletionDisposition.COMPLETE,
                    summary="also browsed the web",
                    output_digest="b" * 64,
                    capabilities_used=("web_browsing",),
                    evidence_refs=(),
                )
            )
        self.assertIn("outside the assignment scope", str(ctx.exception))

    def test_result_for_unknown_agent_rejected(self):
        reg = registry()
        with self.assertRaises(ContractError):
            reg.record_result(
                NanoResultRecord(
                    meta=meta("res-3"),
                    nano_id="nano:ghost",
                    disposition=AgentCompletionDisposition.COMPLETE,
                    summary="x",
                    output_digest="c" * 64,
                    capabilities_used=(),
                    evidence_refs=(),
                )
            )


class FeatureGateTests(unittest.TestCase):
    def test_all_gates_remain_off_after_foundations_use(self):
        reg = registry()
        spawn_one(reg, proposal_id="p1")
        self.assertEqual(
            DEFAULT_GATES.to_dict(),
            {
                "adaptive_planner": False,
                "hybrid_execution": False,
                "nano_agents": False,
                "model_router": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
