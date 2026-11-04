from __future__ import annotations

import asyncio
import json
import unittest

from amca.domain import (
    CognitiveEvent,
    CollaborationMode,
    ComplexityLevel,
    ComputePolicy,
    Disagreement,
    EventType,
    GoalContract,
    PrivacyMode,
    ProjectState,
    Severity,
    SyncMode,
)
from amca.kernel import CollaborationKernel, KernelConfig, StopReason
from amca.modes import (
    AdaptiveFeatures,
    DecompositionPlan,
    SubmissionStatus,
    WorkUnit,
    WorkUnitOutputContract,
)
from amca.models.scripted import ScriptedAdapter
from amca.routing import AdaptiveRouter
from amca.state import StateManager


def structured(
    summary: str,
    *,
    event_type: str = "alternative",
    focus_resolved: bool = False,
) -> str:
    return json.dumps(
        {
            "summary": summary,
            "events": [
                {
                    "type": event_type,
                    "content": summary,
                    "severity": "info",
                    "confidence": 0.7,
                    "evidence_refs": [],
                }
            ],
            "criterion_evidence": [],
            "focus_resolved": focus_resolved,
        }
    )


class ProbeAdapter(ScriptedAdapter):
    def __init__(
        self,
        scripts: list[str],
        *,
        state_manager: StateManager,
        probe_delay: float = 0.0,
        **kwargs: object,
    ) -> None:
        super().__init__(scripts, is_local=True, **kwargs)
        self.state_manager = state_manager
        self.probe_delay = probe_delay
        self.observed_pending_before_first_response: list[str] | None = None
        self.prompts: list[str] = []

    async def stream(self, request):
        self.prompts.append(self._request_text(request))
        if self.observed_pending_before_first_response is None:
            if self.probe_delay:
                await asyncio.sleep(self.probe_delay)
            self.observed_pending_before_first_response = list(
                self.state_manager.state.pending_ideas
            )
        async for chunk in super().stream(request):
            yield chunk


class ConcurrencyProbeAdapter(ScriptedAdapter):
    def __init__(self, scripts: list[str], *, tracker: dict[str, int], **kwargs: object) -> None:
        super().__init__(scripts, is_local=True, **kwargs)
        self.tracker = tracker

    async def stream(self, request):
        self.tracker["active"] += 1
        self.tracker["peak"] = max(self.tracker["peak"], self.tracker["active"])
        try:
            async for chunk in super().stream(request):
                yield chunk
        finally:
            self.tracker["active"] -= 1


class FixedCostAdapter(ScriptedAdapter):
    def __init__(self, scripts: list[str], *, fixed_cost: float, **kwargs: object) -> None:
        super().__init__(scripts, **kwargs)
        self.fixed_cost = fixed_cost

    def estimate_cost(self, input_tokens: int, output_tokens: int = 0) -> float:
        return self.fixed_cost


class ModeSchemaTests(unittest.TestCase):
    def test_work_unit_inputs_are_isolated_and_json_only(self) -> None:
        inputs = {"nested": {"values": [1, 2]}}
        unit = WorkUnit(
            "unit",
            "Focus",
            inputs,
            WorkUnitOutputContract(),
            (),
            ("worker",),
            "owner",
        )
        inputs["nested"]["values"].append(3)
        self.assertEqual(unit.to_dict()["inputs"], {"nested": {"values": [1, 2]}})
        with self.assertRaises(TypeError):
            WorkUnit(
                "invalid",
                "Focus",
                {"value": object()},
                WorkUnitOutputContract(),
                (),
                ("worker",),
                "owner",
            )

    def test_decomposition_graph_rejects_cycles_and_unowned_shared_artifacts(self) -> None:
        contract = WorkUnitOutputContract(artifact_ids=("report",))
        with self.assertRaisesRegex(ValueError, "cycle"):
            DecompositionPlan(
                work_units=(
                    WorkUnit(
                        "a",
                        "A",
                        {},
                        contract,
                        ("b",),
                        ("one",),
                        "integrator",
                        artifact_owners={"report": "one"},
                    ),
                    WorkUnit(
                        "b",
                        "B",
                        {},
                        contract,
                        ("a",),
                        ("two",),
                        "integrator",
                        artifact_owners={"report": "two"},
                    ),
                ),
                artifact_merge_policies={"report": "three-way merge"},
            )

        with self.assertRaisesRegex(ValueError, "merge policy"):
            DecompositionPlan(
                work_units=(
                    WorkUnit(
                        "a",
                        "A",
                        {},
                        contract,
                        (),
                        ("one",),
                        "integrator",
                        artifact_owners={"report": "one"},
                    ),
                    WorkUnit(
                        "b",
                        "B",
                        {},
                        contract,
                        (),
                        ("two",),
                        "integrator",
                        artifact_owners={"report": "two"},
                    ),
                )
            )

    def test_adaptive_transition_is_reproducible_and_safety_overrides_lock(self) -> None:
        state = ProjectState(
            project_id="adaptive",
            goal="Integrate",
            complexity=ComplexityLevel.MEDIUM,
        )
        features = AdaptiveFeatures(
            weakly_coupled=True,
            estimated_cost_usd=0.25,
            estimated_latency_seconds=1.5,
        )
        router = AdaptiveRouter()
        first = router.decide(state, features, plan_available=True)
        second = router.decide(state.copy(), features, plan_available=True)
        self.assertEqual(first.selected_mode, CollaborationMode.PARALLEL)
        self.assertEqual(first.feature_digest, second.feature_digest)
        self.assertEqual(first.to_dict(), second.to_dict())

        override = router.decide(
            state,
            AdaptiveFeatures(parallel_conflict=True),
            user_lock=CollaborationMode.SOLO,
        )
        self.assertEqual(override.selected_mode, CollaborationMode.JOINT)
        self.assertEqual(override.selected_sync_mode, SyncMode.HARD)
        self.assertTrue(override.safety_override)
        self.assertEqual(override.policy_rule, "safety.conflict_to_joint")

        constrained = router.decide(
            state,
            AdaptiveFeatures(novelty_required=True, cost_pressure=True),
        )
        self.assertEqual(constrained.selected_mode, CollaborationMode.SOLO)
        self.assertEqual(constrained.policy_rule, "static.resource_pressure_to_solo")
        disagreement = router.decide(
            state,
            AdaptiveFeatures(high_disagreement=True, time_pressure=True),
        )
        self.assertEqual(disagreement.selected_mode, CollaborationMode.JOINT)
        self.assertEqual(disagreement.selected_sync_mode, SyncMode.QUORUM)

    def test_adaptive_manual_transition_and_static_lock_are_auditable(self) -> None:
        state = ProjectState(project_id="manual", complexity=ComplexityLevel.HIGH)
        router = AdaptiveRouter()
        locked = router.decide(
            state,
            AdaptiveFeatures(novelty_required=True),
            user_lock=CollaborationMode.SOLO,
        )
        self.assertEqual(locked.selected_mode, CollaborationMode.SOLO)
        self.assertEqual(locked.policy_rule, "user.mode_lock")
        manual = router.decide(
            state,
            AdaptiveFeatures(),
            manual_instruction=CollaborationMode.INDEPENDENT_DISCUSS,
        )
        self.assertEqual(
            manual.selected_mode, CollaborationMode.INDEPENDENT_DISCUSS
        )
        self.assertEqual(manual.policy_rule, "user.manual_transition")


class ExecutableModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_submissions_are_peer_isolated_until_reveal(self) -> None:
        manager = StateManager(
            ProjectState(
                project_id="sealed",
                goal="Generate alternatives without anchoring",
                current_focus="Find two independent mechanisms",
                privacy_mode=PrivacyMode.OFFLINE,
            )
        )
        fast = ProbeAdapter(
            [structured("fast-private"), structured("fast-synthesis")],
            adapter_id="fast",
            state_manager=manager,
        )
        slow = ProbeAdapter(
            [structured("slow-private"), structured("slow-synthesis")],
            adapter_id="slow",
            state_manager=manager,
            probe_delay=0.03,
        )
        result = await CollaborationKernel(
            manager,
            [fast, slow],
            config=KernelConfig(max_rounds=1),
        ).run(
            mode=CollaborationMode.INDEPENDENT_DISCUSS,
            sync_mode=SyncMode.HARD,
        )

        self.assertEqual(slow.observed_pending_before_first_response, [])
        self.assertEqual(fast.prompts[0], slow.prompts[0])
        phase = result.mode_trace[0]
        self.assertEqual(phase["type"], "independent_phase")
        receipts = phase["receipts"]
        self.assertEqual(
            {receipt["status"] for receipt in receipts},
            {SubmissionStatus.REVEALED.value},
        )
        self.assertTrue(
            all(len(receipt["content_digest"]) == 64 for receipt in receipts)
        )
        self.assertEqual(
            {item["summary"] for item in phase["alternatives"]},
            {"fast-private", "slow-private"},
        )
        self.assertTrue(
            all(item["points"][0]["kind"] == "alternative" for item in phase["alternatives"])
        )
        event_types = [event.event_type for event in result.state.recent_events]
        reveal_index = next(
            index
            for index, event in enumerate(result.state.recent_events)
            if event.event_type is EventType.ARTIFACT_UPDATE
            and str(event.payload.get("artifact_id", "")).startswith(
                "independent-reveal:"
            )
        )
        first_model_content = next(
            index
            for index, event in enumerate(result.state.recent_events)
            if event.source in {"fast", "slow"}
            and event.event_type
            in {EventType.ALTERNATIVE, EventType.HYPOTHESIS}
        )
        self.assertLess(reveal_index, first_model_content)
        self.assertEqual(result.state.current_collaboration_mode, CollaborationMode.JOINT)
        self.assertEqual(len(result.call_records), 4)
        self.assertIn(EventType.MODE_SWITCH, event_types)
        # 09F semantic contract: after the reveal barrier, the synthesis
        # round-2 prompt must contain BOTH peers' revealed submissions (each
        # participant sees every alternative via canonical context). The
        # internal addendum heading is prompt decoration, not contract.
        self.assertIn("fast-private", fast.prompts[1])
        self.assertIn("slow-private", fast.prompts[1])
        self.assertIn("fast-private", slow.prompts[1])
        self.assertIn("slow-private", slow.prompts[1])
        json.dumps(result.to_dict())

    async def test_independent_timeout_and_release_are_explicit(self) -> None:
        state = ProjectState(
            project_id="independent-timeout",
            goal="Explore",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        good = ScriptedAdapter(
            [structured("good"), structured("synthesis")],
            adapter_id="good",
            is_local=True,
        )
        late = ScriptedAdapter(
            [structured("late"), structured("late synthesis")],
            adapter_id="late",
            is_local=True,
            delay_seconds=0.08,
        )
        released = ScriptedAdapter(
            [structured("must not be consumed"), structured("can discuss")],
            adapter_id="released",
            is_local=True,
        )
        result = await CollaborationKernel(
            StateManager(state),
            [good, late, released],
            config=KernelConfig(max_rounds=1, model_timeout_seconds=0.02),
        ).run(
            mode=CollaborationMode.INDEPENDENT_DISCUSS,
            sync_mode=SyncMode.SOFT,
            released_participant_ids={"released"},
            release_authorized_by="user:test-owner",
        )

        receipts = {
            receipt["participant_id"]: receipt
            for receipt in result.mode_trace[0]["receipts"]
        }
        self.assertEqual(receipts["good"]["status"], "revealed")
        self.assertEqual(receipts["late"]["status"], "timed_out")
        self.assertEqual(receipts["released"]["status"], "released")
        # Released means no call in the sealed phase; it may participate after reveal.
        self.assertGreaterEqual(released.calls_consumed, 1)

    async def test_same_focus_independent_restart_requires_logged_reason(self) -> None:
        state = ProjectState(
            project_id="independent-restart",
            goal="Generate alternatives",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter = ScriptedAdapter(
            [structured("alternative"), structured("synthesis")],
            adapter_id="local",
            is_local=True,
            repeat_last=True,
        )
        kernel = CollaborationKernel(
            StateManager(state), [adapter], config=KernelConfig(max_rounds=1)
        )
        await kernel.run(
            mode=CollaborationMode.INDEPENDENT_DISCUSS,
            sync_mode=SyncMode.SOFT,
        )
        calls_before_rejection = adapter.calls_consumed
        with self.assertRaisesRegex(ValueError, "anti_anchoring_reason"):
            await kernel.run(
                mode=CollaborationMode.INDEPENDENT_DISCUSS,
                sync_mode=SyncMode.SOFT,
            )
        self.assertEqual(adapter.calls_consumed, calls_before_rejection)
        restarted = await kernel.run(
            mode=CollaborationMode.INDEPENDENT_DISCUSS,
            sync_mode=SyncMode.SOFT,
            anti_anchoring_reason="A second isolated sample checks anchoring bias.",
        )
        self.assertEqual(
            restarted.mode_trace[0]["anti_anchoring_reason"],
            "A second isolated sample checks anchoring bias.",
        )

    async def test_independent_and_synthesis_share_one_atomic_api_budget(self) -> None:
        state = ProjectState(
            project_id="independent-budget",
            goal="Stay under budget",
            goal_contract=GoalContract(goal="Stay under budget", max_api_cost_usd=0.9),
            privacy_mode=PrivacyMode.FULL_FRONTIER,
            compute_policy=ComputePolicy.RESEARCH,
        )
        adapters = [
            FixedCostAdapter(
                [structured("first")],
                adapter_id="cloud-a",
                provider="cloud",
                fixed_cost=0.4,
            ),
            FixedCostAdapter(
                [structured("second")],
                adapter_id="cloud-b",
                provider="cloud",
                fixed_cost=0.4,
            ),
        ]
        result = await CollaborationKernel(
            StateManager(state), adapters, config=KernelConfig(max_rounds=1)
        ).run(
            mode=CollaborationMode.INDEPENDENT_DISCUSS,
            sync_mode=SyncMode.HARD,
        )
        self.assertEqual(sum(adapter.calls_consumed for adapter in adapters), 2)
        self.assertEqual(result.state.cost_state.api_model_calls, 2)
        self.assertAlmostEqual(result.state.cost_state.api_cost_usd, 0.8)
        self.assertFalse(result.synchronization_passed)

    async def test_parallel_ready_units_run_concurrently_and_share_pinned_parent(self) -> None:
        state = ProjectState(
            project_id="parallel",
            goal="Execute independent checks",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        tracker = {"active": 0, "peak": 0}
        # 09F: the accepted scheduler launch-staggers admitted calls (15s per
        # additional branch). Use probe delays longer than the stagger so the
        # branches genuinely overlap and peak concurrency remains a meaningful
        # assertion; zero-delay simultaneous launch is NOT required.
        left = ConcurrencyProbeAdapter(
            [structured("left", event_type="hypothesis", focus_resolved=True)],
            adapter_id="left",
            tracker=tracker,
            delay_seconds=20.0,
        )
        right = ConcurrencyProbeAdapter(
            [structured("right", event_type="hypothesis", focus_resolved=True)],
            adapter_id="right",
            tracker=tracker,
            delay_seconds=20.0,
        )
        contract = WorkUnitOutputContract(
            require_focus_resolved=True,
            minimum_events=1,
            required_event_types=("hypothesis",),
        )
        plan = DecompositionPlan(
            work_units=(
                WorkUnit("left-unit", "Check left", {"side": "left"}, contract, (), ("left",), "owner"),
                WorkUnit("right-unit", "Check right", {"side": "right"}, contract, (), ("right",), "owner"),
            )
        )
        result = await CollaborationKernel(
            StateManager(state),
            [left, right],
            config=KernelConfig(max_rounds=2),
        ).run(
            mode=CollaborationMode.PARALLEL,
            sync_mode=SyncMode.HARD,
            decomposition_plan=plan,
        )
        self.assertEqual(tracker["peak"], 2, "ready work units should overlap")
        self.assertTrue(result.synchronization_passed)
        record = result.mode_trace[0]
        self.assertTrue(record["integration"]["passed"])
        base_versions = {
            item["base_state_version"] for item in record["results"]
        }
        self.assertEqual(base_versions, {record["pinned_parent_state_version"]})
        self.assertEqual(set(result.state.hypotheses), {"left", "right"})

    async def test_all_children_complete_but_conflict_blocks_parent_integration(self) -> None:
        state = ProjectState(
            project_id="parallel-conflict",
            goal="Integrate incompatible calculations",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        first = ScriptedAdapter(
            [structured("first", event_type="hypothesis", focus_resolved=True)],
            adapter_id="first",
            is_local=True,
        )
        second = ScriptedAdapter(
            [structured("second", event_type="hypothesis", focus_resolved=True)],
            adapter_id="second",
            is_local=True,
        )
        plan = DecompositionPlan(
            work_units=(
                WorkUnit(
                    "first-unit",
                    "First",
                    {},
                    WorkUnitOutputContract(),
                    (),
                    ("first",),
                    "owner",
                    assumptions={"coordinate_system": "Cartesian"},
                ),
                WorkUnit(
                    "second-unit",
                    "Second",
                    {},
                    WorkUnitOutputContract(),
                    (),
                    ("second",),
                    "owner",
                    assumptions={"coordinate_system": "polar"},
                ),
            )
        )
        result = await CollaborationKernel(
            StateManager(state), [first, second], config=KernelConfig(max_rounds=1)
        ).run(
            mode=CollaborationMode.PARALLEL,
            sync_mode=SyncMode.HARD,
            decomposition_plan=plan,
        )

        self.assertEqual(result.stop_reason, StopReason.INTEGRATION_BLOCKED)
        results = result.mode_trace[0]["results"]
        self.assertEqual({item["status"] for item in results}, {"completed"})
        self.assertFalse(result.mode_trace[0]["integration"]["passed"])
        self.assertFalse(result.synchronization_passed)
        self.assertEqual(result.state.hypotheses, [])
        self.assertEqual(result.state.current_collaboration_mode, CollaborationMode.JOINT)
        self.assertTrue(
            all(not checkpoint.passed for checkpoint in result.state.synchronization_checkpoints.values())
        )

    async def test_parallel_dependencies_dispatch_only_after_successful_inputs(self) -> None:
        manager = StateManager(
            ProjectState(
                project_id="parallel-dependencies",
                goal="Respect the DAG",
                privacy_mode=PrivacyMode.OFFLINE,
            )
        )
        producer = ProbeAdapter(
            [structured("dependency-result", focus_resolved=True)],
            adapter_id="producer",
            state_manager=manager,
        )
        consumer = ProbeAdapter(
            [structured("consumer-result", focus_resolved=True)],
            adapter_id="consumer",
            state_manager=manager,
        )
        plan = DecompositionPlan(
            work_units=(
                WorkUnit("produce", "Produce", {}, WorkUnitOutputContract(), (), ("producer",), "owner"),
                WorkUnit("consume", "Consume", {}, WorkUnitOutputContract(), ("produce",), ("consumer",), "owner"),
            )
        )
        result = await CollaborationKernel(
            manager, [producer, consumer], config=KernelConfig(max_rounds=2)
        ).run(
            mode=CollaborationMode.PARALLEL,
            decomposition_plan=plan,
            sync_mode=SyncMode.SOFT,
        )
        self.assertIn('"produce":"dependency-result"', consumer.prompts[0])
        record = result.mode_trace[0]
        self.assertEqual(record["topological_levels"], [["produce"], ["consume"]])
        self.assertEqual(
            {item["base_state_version"] for item in record["results"]},
            {record["pinned_parent_state_version"]},
        )

        failed_state = ProjectState(
            project_id="parallel-dependency-failure",
            goal="Do not dispatch a blocked child",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        failed_producer = ScriptedAdapter(
            ["not structured JSON"], adapter_id="producer", is_local=True
        )
        untouched_consumer = ScriptedAdapter(
            [structured("must not run")], adapter_id="consumer", is_local=True
        )
        blocked = await CollaborationKernel(
            StateManager(failed_state),
            [failed_producer, untouched_consumer],
            config=KernelConfig(max_rounds=2),
        ).run(
            mode=CollaborationMode.PARALLEL,
            decomposition_plan=plan,
            sync_mode=SyncMode.SOFT,
        )
        statuses = {
            item["work_unit_id"]: item["status"]
            for item in blocked.mode_trace[0]["results"]
        }
        self.assertEqual(statuses, {"produce": "failed", "consume": "blocked"})
        self.assertEqual(untouched_consumer.calls_consumed, 0)

    async def test_optional_work_unit_failure_does_not_weaken_required_hard_gate(self) -> None:
        state = ProjectState(
            project_id="parallel-optional",
            goal="Complete required work",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        required = ScriptedAdapter(
            [structured("required result", event_type="hypothesis", focus_resolved=True)],
            adapter_id="required",
            is_local=True,
        )
        optional = ScriptedAdapter(
            ["invalid optional response"], adapter_id="optional", is_local=True
        )
        plan = DecompositionPlan(
            work_units=(
                WorkUnit("required-unit", "Required", {}, WorkUnitOutputContract(), (), ("required",), "owner"),
                WorkUnit("optional-unit", "Optional", {}, WorkUnitOutputContract(), (), ("optional",), "owner", required=False),
            )
        )
        result = await CollaborationKernel(
            StateManager(state), [required, optional], config=KernelConfig(max_rounds=1)
        ).run(
            mode=CollaborationMode.PARALLEL,
            decomposition_plan=plan,
            sync_mode=SyncMode.HARD,
        )
        self.assertTrue(result.synchronization_passed)
        checkpoint = next(iter(result.state.synchronization_checkpoints.values()))
        self.assertEqual(set(checkpoint.required_participant_ids), {"required"})
        self.assertEqual(set(checkpoint.dispositions), {"required"})
        self.assertEqual(result.state.hypotheses, ["required result"])

    async def test_parallel_artifact_contract_is_not_satisfied_from_model_prose(self) -> None:
        state = ProjectState(
            project_id="artifact-contract",
            goal="Create report",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter = ScriptedAdapter(
            [structured("I claim the report exists", focus_resolved=True)],
            adapter_id="worker",
            is_local=True,
        )
        plan = DecompositionPlan(
            work_units=(
                WorkUnit(
                    "report",
                    "Write report",
                    {},
                    WorkUnitOutputContract(artifact_ids=("report.md",)),
                    (),
                    ("worker",),
                    "owner",
                    artifact_owners={"report.md": "worker"},
                ),
            )
        )
        result = await CollaborationKernel(
            StateManager(state), [adapter], config=KernelConfig(max_rounds=1)
        ).run(
            mode=CollaborationMode.PARALLEL,
            decomposition_plan=plan,
            sync_mode=SyncMode.SOFT,
        )
        self.assertEqual(result.stop_reason, StopReason.INTEGRATION_BLOCKED)
        integration = result.mode_trace[0]["integration"]
        self.assertFalse(integration["criterion_results"]["output_contracts_pass"])
        self.assertEqual(result.state.hypotheses, [])

    async def test_custom_integration_evaluator_cannot_waive_mandatory_gate(self) -> None:
        state = ProjectState(
            project_id="custom-cannot-waive",
            goal="Create typed artifact",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter = ScriptedAdapter(
            [structured("unsupported artifact claim", focus_resolved=True)],
            adapter_id="worker",
            is_local=True,
        )
        plan = DecompositionPlan(
            work_units=(
                WorkUnit(
                    "artifact",
                    "Create artifact",
                    {},
                    WorkUnitOutputContract(artifact_ids=("result.bin",)),
                    (),
                    ("worker",),
                    "owner",
                    artifact_owners={"result.bin": "worker"},
                ),
            )
        )
        result = await CollaborationKernel(
            StateManager(state),
            [adapter],
            integration_evaluator=lambda _state, _plan, _results: True,
            config=KernelConfig(max_rounds=1),
        ).run(
            mode=CollaborationMode.PARALLEL,
            decomposition_plan=plan,
            sync_mode=SyncMode.SOFT,
        )
        self.assertEqual(result.stop_reason, StopReason.INTEGRATION_BLOCKED)
        integration = result.mode_trace[0]["integration"]
        self.assertFalse(integration["passed"])
        self.assertFalse(integration["criterion_results"]["output_contracts_pass"])
        self.assertTrue(integration["criterion_results"]["custom_integration_evaluator"])

    async def test_custom_integration_evaluator_exception_fails_closed(self) -> None:
        state = ProjectState(
            project_id="custom-evaluator-error",
            goal="Integrate safely",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter = ScriptedAdapter(
            [structured("valid child", focus_resolved=True)],
            adapter_id="worker",
            is_local=True,
        )
        plan = DecompositionPlan(
            work_units=(
                WorkUnit(
                    "unit",
                    "Work",
                    {},
                    WorkUnitOutputContract(),
                    (),
                    ("worker",),
                    "owner",
                ),
            )
        )

        def broken_evaluator(_state, _plan, _results):
            raise RuntimeError("evaluator unavailable")

        result = await CollaborationKernel(
            StateManager(state),
            [adapter],
            integration_evaluator=broken_evaluator,
            config=KernelConfig(max_rounds=1),
        ).run(
            mode=CollaborationMode.PARALLEL,
            decomposition_plan=plan,
            sync_mode=SyncMode.SOFT,
        )
        self.assertEqual(result.stop_reason, StopReason.INTEGRATION_BLOCKED)
        self.assertFalse(
            result.mode_trace[0]["integration"]["criterion_results"][
                "custom_integration_evaluator"
            ]
        )
        self.assertEqual(result.state.hypotheses, [])

    async def test_parallel_offline_policy_rejects_cloud_before_transmission(self) -> None:
        state = ProjectState(
            project_id="offline-parallel",
            goal="Keep data local",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        cloud = ScriptedAdapter(
            [structured("must not run")], adapter_id="cloud", provider="cloud"
        )
        plan = DecompositionPlan(
            work_units=(
                WorkUnit(
                    "unit",
                    "Private unit",
                    {"private": "data"},
                    WorkUnitOutputContract(),
                    (),
                    ("cloud",),
                    "owner",
                ),
            )
        )
        result = await CollaborationKernel(
            StateManager(state), [cloud], config=KernelConfig(max_rounds=1)
        ).run(
            mode=CollaborationMode.PARALLEL,
            decomposition_plan=plan,
            sync_mode=SyncMode.SOFT,
        )
        self.assertEqual(cloud.calls_consumed, 0)
        self.assertEqual(result.stop_reason, StopReason.INTEGRATION_BLOCKED)

    async def test_parallel_stale_artifact_version_blocks_dispatch(self) -> None:
        state = ProjectState(
            project_id="artifact-version",
            goal="Update artifact",
            privacy_mode=PrivacyMode.OFFLINE,
            artifacts={"design.md": {"artifact_id": "design.md", "version": 2}},
        )
        adapter = ScriptedAdapter(
            [structured("must not run")], adapter_id="worker", is_local=True
        )
        plan = DecompositionPlan(
            work_units=(
                WorkUnit(
                    "update",
                    "Update design",
                    {},
                    WorkUnitOutputContract(),
                    (),
                    ("worker",),
                    "owner",
                    artifact_owners={"design.md": "worker"},
                    artifact_base_versions={"design.md": 1},
                ),
            )
        )
        result = await CollaborationKernel(
            StateManager(state), [adapter], config=KernelConfig(max_rounds=1)
        ).run(
            mode=CollaborationMode.PARALLEL,
            decomposition_plan=plan,
            sync_mode=SyncMode.SOFT,
        )
        self.assertEqual(adapter.calls_consumed, 0)
        self.assertEqual(result.stop_reason, StopReason.INTEGRATION_BLOCKED)
        integration = result.mode_trace[0]["integration"]
        self.assertFalse(integration["criterion_results"]["artifact_versions_current"])
        self.assertTrue(
            any("expected version 1" in conflict for conflict in integration["conflicts"])
        )

    async def test_adaptive_kernel_logs_static_selection_and_executes_base_mode(self) -> None:
        state = ProjectState(
            project_id="adaptive-kernel",
            goal="Routine residual",
            complexity=ComplexityLevel.HIGH,
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter = ScriptedAdapter(
            [structured("done")], adapter_id="local", is_local=True
        )
        result = await CollaborationKernel(
            StateManager(state), [adapter], config=KernelConfig(max_rounds=1)
        ).run(
            mode=CollaborationMode.ADAPTIVE_HYBRID,
            adaptive_features=AdaptiveFeatures(routine_residual=True),
            mode_lock=CollaborationMode.SOLO,
        )
        transition = result.mode_trace[0]
        self.assertEqual(transition["selected_mode"], "solo")
        self.assertEqual(transition["policy_rule"], "user.mode_lock")
        self.assertEqual(result.state.current_collaboration_mode, CollaborationMode.SOLO)
        self.assertEqual(adapter.calls_consumed, 1)

    async def test_adaptive_kernel_dispatches_parallel_and_independent_base_modes(self) -> None:
        parallel_state = ProjectState(
            project_id="adaptive-parallel",
            goal="Split work",
            complexity=ComplexityLevel.MEDIUM,
            privacy_mode=PrivacyMode.OFFLINE,
        )
        worker = ScriptedAdapter(
            [structured("unit result", focus_resolved=True)],
            adapter_id="worker",
            is_local=True,
        )
        plan = DecompositionPlan(
            work_units=(
                WorkUnit("unit", "Unit", {}, WorkUnitOutputContract(), (), ("worker",), "owner"),
            )
        )
        parallel = await CollaborationKernel(
            StateManager(parallel_state), [worker], config=KernelConfig(max_rounds=1)
        ).run(
            mode=CollaborationMode.ADAPTIVE_HYBRID,
            adaptive_features=AdaptiveFeatures(weakly_coupled=True),
            decomposition_plan=plan,
            sync_mode=SyncMode.SOFT,
        )
        self.assertEqual(
            [record["type"] for record in parallel.mode_trace],
            ["adaptive_transition", "parallel_decomposition"],
        )
        self.assertEqual(parallel.mode_trace[0]["selected_mode"], "parallel")

        independent_state = ProjectState(
            project_id="adaptive-independent",
            goal="Explore novelty",
            complexity=ComplexityLevel.HIGH,
            privacy_mode=PrivacyMode.OFFLINE,
        )
        explorer = ScriptedAdapter(
            [structured("novel option"), structured("joint synthesis")],
            adapter_id="explorer",
            is_local=True,
        )
        independent = await CollaborationKernel(
            StateManager(independent_state),
            [explorer],
            config=KernelConfig(max_rounds=1),
        ).run(
            mode=CollaborationMode.ADAPTIVE_HYBRID,
            adaptive_features=AdaptiveFeatures(novelty_required=True),
            sync_mode=SyncMode.SOFT,
        )
        self.assertEqual(
            [record["type"] for record in independent.mode_trace],
            ["adaptive_transition", "independent_phase"],
        )
        self.assertEqual(
            independent.mode_trace[0]["selected_mode"], "independent_discuss"
        )
        self.assertEqual(
            independent.state.current_collaboration_mode, CollaborationMode.JOINT
        )

    async def test_adaptive_supervisor_ambiguous_alignment_to_joint(self) -> None:
        state = ProjectState(
            project_id="adaptive-ambiguous",
            goal="Resolve mixed decomposition signals",
            complexity=ComplexityLevel.MEDIUM,
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter1 = ScriptedAdapter(
            [structured("model 1 alignment")], adapter_id="m1", is_local=True
        )
        adapter2 = ScriptedAdapter(
            [structured("model 2 alignment")], adapter_id="m2", is_local=True
        )
        plan = DecompositionPlan(
            work_units=(
                WorkUnit("u1", "U1", {}, WorkUnitOutputContract(), (), ("m1",), "owner"),
            )
        )
        result = await CollaborationKernel(
            StateManager(state),
            [adapter1, adapter2],
            config=KernelConfig(max_rounds=1, max_adaptive_phases=2),
        ).run(
            mode=CollaborationMode.ADAPTIVE_HYBRID,
            adaptive_features=AdaptiveFeatures(
                ambiguous_mixed_signals=True,
                weakly_coupled=True,
            ),
            decomposition_plan=plan,
            sync_mode=SyncMode.SOFT,
        )
        transitions = [r for r in result.mode_trace if r.get("type") == "adaptive_transition"]
        self.assertTrue(len(transitions) >= 1)
        self.assertEqual(transitions[0]["selected_mode"], "joint")
        self.assertEqual(
            transitions[0]["policy_rule"], "adaptive.ambiguous_alignment_to_joint"
        )

    async def test_adaptive_supervisor_joint_alignment_then_parallel(self) -> None:
        state = ProjectState(
            project_id="adaptive-joint-to-parallel",
            goal="Align then execute in parallel",
            complexity=ComplexityLevel.MEDIUM,
            privacy_mode=PrivacyMode.OFFLINE,
        )
        # Round 1 (Joint): m1 + m2 align; Round 2 (Parallel): m1 executes work unit
        m1 = ScriptedAdapter(
            [structured("alignment consensus"), structured("unit output", focus_resolved=True)],
            adapter_id="m1",
            is_local=True,
        )
        m2 = ScriptedAdapter(
            [structured("agreed to decompose")],
            adapter_id="m2",
            is_local=True,
        )
        plan = DecompositionPlan(
            work_units=(
                WorkUnit("u1", "U1", {}, WorkUnitOutputContract(), (), ("m1",), "owner"),
            )
        )
        result = await CollaborationKernel(
            StateManager(state),
            [m1, m2],
            config=KernelConfig(max_rounds=1, max_adaptive_phases=3),
        ).run(
            mode=CollaborationMode.ADAPTIVE_HYBRID,
            adaptive_features=AdaptiveFeatures(
                ambiguous_mixed_signals=True,
                weakly_coupled=True,
            ),
            decomposition_plan=plan,
            sync_mode=SyncMode.SOFT,
        )
        transitions = [r for r in result.mode_trace if r.get("type") == "adaptive_transition"]
        self.assertEqual(len(transitions), 2)
        self.assertEqual(transitions[0]["selected_mode"], "joint")
        self.assertEqual(transitions[1]["selected_mode"], "parallel")
        self.assertEqual(
            transitions[1]["policy_rule"], "static.weak_coupling_with_plan_to_parallel"
        )

    async def test_adaptive_supervisor_parallel_conflict_transitions_to_joint_hard(self) -> None:
        state = ProjectState(
            project_id="adaptive-conflict",
            goal="Detect parallel conflict and escalate to joint deliberation",
            complexity=ComplexityLevel.MEDIUM,
            privacy_mode=PrivacyMode.OFFLINE,
            artifacts={"shared_spec": {"type": "code", "version": 2}},
        )
        worker = ScriptedAdapter(
            [
                structured("stale unit attempt"),
                structured("joint conflict resolution deliberation"),
            ],
            adapter_id="w1",
            is_local=True,
        )
        plan = DecompositionPlan(
            work_units=(
                WorkUnit(
                    "u1",
                    "U1",
                    {},
                    WorkUnitOutputContract(),
                    (),
                    ("w1",),
                    "owner",
                    artifact_owners={"shared_spec": "owner"},
                    artifact_base_versions={"shared_spec": 1},  # pinned version 1 vs state version 2 triggers conflict
                ),
            )
        )
        result = await CollaborationKernel(
            StateManager(state),
            [worker],
            config=KernelConfig(max_rounds=1, max_adaptive_phases=3),
        ).run(
            mode=CollaborationMode.ADAPTIVE_HYBRID,
            adaptive_features=AdaptiveFeatures(weakly_coupled=True),
            decomposition_plan=plan,
            sync_mode=SyncMode.SOFT,
        )
        transitions = [r for r in result.mode_trace if r.get("type") == "adaptive_transition"]
        self.assertTrue(len(transitions) >= 2)
        self.assertEqual(transitions[0]["selected_mode"], "parallel")
        self.assertEqual(transitions[1]["selected_mode"], "joint")
        self.assertEqual(transitions[1]["selected_sync_mode"], "hard")
        self.assertTrue(transitions[1]["safety_override"])
        self.assertEqual(transitions[1]["policy_rule"], "safety.conflict_to_joint")

    async def test_adaptive_supervisor_stagnation_halts(self) -> None:
        state = ProjectState(
            project_id="adaptive-stagnation",
            goal="Stagnant progress",
            complexity=ComplexityLevel.HIGH,
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter = ScriptedAdapter(
            ['{"summary": "stagnant", "events": []}', '{"summary": "stagnant", "events": []}'],
            adapter_id="local",
            is_local=True,
        )
        result = await CollaborationKernel(
            StateManager(state),
            [adapter],
            config=KernelConfig(max_rounds=1, max_adaptive_phases=4),
        ).run(
            mode=CollaborationMode.ADAPTIVE_HYBRID,
            adaptive_features=AdaptiveFeatures(high_disagreement=True),
            sync_mode=SyncMode.SOFT,
        )
        self.assertEqual(result.stop_reason, StopReason.STAGNATION)
        self.assertEqual(result.rounds_completed, 2)

    async def test_adaptive_supervisor_critical_objection_persists_hard_sync(self) -> None:
        state = ProjectState(
            project_id="adaptive-critical",
            goal="Safety persistence",
            complexity=ComplexityLevel.HIGH,
            privacy_mode=PrivacyMode.OFFLINE,
        )
        state.active_disagreements.append(
            Disagreement(
                id="crit-1",
                topic="Safety critical",
                positions={"m1": "unsafe"},
                severity=Severity.CRITICAL,
                resolved=False,
            )
        )
        adapter = ScriptedAdapter(
            [structured("addressing objection")],
            adapter_id="m1",
            is_local=True,
        )
        result = await CollaborationKernel(
            StateManager(state),
            [adapter],
            config=KernelConfig(max_rounds=1, max_adaptive_phases=2),
        ).run(
            mode=CollaborationMode.ADAPTIVE_HYBRID,
            adaptive_features=AdaptiveFeatures(routine_residual=True),
            mode_lock=CollaborationMode.SOLO,
            sync_mode=SyncMode.SOFT,
        )
        transitions = [r for r in result.mode_trace if r.get("type") == "adaptive_transition"]
        self.assertEqual(transitions[0]["selected_mode"], "joint")
        self.assertEqual(transitions[0]["selected_sync_mode"], "hard")
        self.assertTrue(transitions[0]["safety_override"])

    async def test_adaptive_supervisor_phase_ceiling_terminates_deterministically(self) -> None:
        state = ProjectState(
            project_id="adaptive-ceiling",
            goal="Phase ceiling test",
            complexity=ComplexityLevel.HIGH,
            privacy_mode=PrivacyMode.OFFLINE,
        )
        # Responses advance state hypotheses each time with different hypotheses
        adapter = ScriptedAdapter(
            [
                structured("pass 1", event_type="hypothesis"),
                structured("pass 2", event_type="hypothesis"),
                structured("pass 3", event_type="hypothesis"),
                structured("pass 4", event_type="hypothesis"),
            ],
            adapter_id="m1",
            is_local=True,
        )
        result = await CollaborationKernel(
            StateManager(state),
            [adapter],
            config=KernelConfig(max_rounds=1, max_adaptive_phases=3),
        ).run(
            mode=CollaborationMode.ADAPTIVE_HYBRID,
            adaptive_features=AdaptiveFeatures(high_disagreement=True),
            sync_mode=SyncMode.SOFT,
        )
        self.assertEqual(result.stop_reason, StopReason.ROUND_LIMIT)
        self.assertEqual(result.rounds_completed, 3)

    async def test_adaptive_supervisor_persists_recovery_metadata(self) -> None:
        """Verify that Adaptive phase transitions persist durable metadata artifacts.

        Note: Tests metadata persistence and state replay recovery; mid-run
        Adaptive execution continuation after kernel re-instantiation is not yet implemented.
        """
        state = ProjectState(
            project_id="adaptive-recovery",
            goal="Recovery metadata persistence",
            complexity=ComplexityLevel.HIGH,
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter = ScriptedAdapter(
            [structured("completed work")],
            adapter_id="m1",
            is_local=True,
        )
        manager = StateManager(state)
        result = await CollaborationKernel(
            manager,
            [adapter],
            config=KernelConfig(max_rounds=1, max_adaptive_phases=2),
        ).run(
            mode=CollaborationMode.ADAPTIVE_HYBRID,
            adaptive_features=AdaptiveFeatures(routine_residual=True),
            mode_lock=CollaborationMode.SOLO,
        )
        transitions = [r for r in result.mode_trace if r.get("type") == "adaptive_transition"]
        self.assertEqual(len(transitions), 1)
        record = transitions[0]
        self.assertEqual(record["phase_index"], 0)
        self.assertGreaterEqual(record["state_version_before"], 0)
        self.assertIsNotNone(record["state_version_after"])
        self.assertEqual(record["phase_stop_reason"], "round_limit")

        # Verify cognitive artifact was persisted
        artifacts = manager.state.artifacts
        transition_artifacts = [
            v for k, v in artifacts.items() if k.startswith("adaptive-transition:")
        ]
        self.assertEqual(len(transition_artifacts), 1)
        self.assertEqual(transition_artifacts[0]["selected_mode"], "solo")


if __name__ == "__main__":
    unittest.main()

