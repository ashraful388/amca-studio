from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Mapping

from amca.domain import (
    CognitiveEvent,
    CollaborationMode,
    ComputePolicy,
    CostState,
    DecisionClass,
    EvaluatorKind,
    EventType,
    GoalContract,
    GoalCriterion,
    PrivacyMode,
    ProjectState,
    SyncMode,
)
from amca.kernel import (
    CollaborationKernel,
    KernelConfig,
    StalenessDisposition,
    StopReason,
)
from amca.models.base import ModelCapabilities, ModelResponse, TokenUsage
from amca.models.scripted import ScriptedAdapter
from amca.state import StateManager
from amca.storage import ModelCallReservationStatus, SQLiteStore


def structured(
    summary: str,
    *,
    events: list[dict[str, object]] | None = None,
    criterion_id: str | None = None,
    focus_resolved: bool = True,
) -> str:
    return json.dumps(
        {
            "summary": summary,
            "events": events or [],
            "criterion_evidence": (
                [{"criterion_id": criterion_id, "evidence": summary}]
                if criterion_id
                else []
            ),
            "focus_resolved": focus_resolved,
        }
    )


class LocalScriptedAdapter(ScriptedAdapter):
    def __init__(self, scripts: list[str], **kwargs: object) -> None:
        super().__init__(scripts, **kwargs)
        self._capabilities = ModelCapabilities(
            supports_streaming=True,
            supports_tools=True,
            supports_json=True,
            is_local=True,
        )


class FixedCostScriptedAdapter(ScriptedAdapter):
    def __init__(self, scripts: list[str], *, fixed_cost_usd: float, **kwargs: object) -> None:
        super().__init__(scripts, **kwargs)
        self.fixed_cost_usd = fixed_cost_usd

    def estimate_cost(self, input_tokens: int, output_tokens: int = 0) -> float:
        return self.fixed_cost_usd


class FailingFixedCostAdapter(FixedCostScriptedAdapter):
    async def stream(self, request):
        del request
        if False:
            yield None
        raise RuntimeError("provider outcome unknown")


class ConcurrencyProbeAdapter(LocalScriptedAdapter):
    def __init__(self, scripts: list[str], **kwargs: object) -> None:
        super().__init__(scripts, **kwargs)
        self.active_streams = 0
        self.peak_active_streams = 0
        self.stream_started = asyncio.Event()

    async def stream(self, request):
        self.active_streams += 1
        self.peak_active_streams = max(self.peak_active_streams, self.active_streams)
        self.stream_started.set()
        try:
            async for chunk in super().stream(request):
                yield chunk
        finally:
            self.active_streams -= 1


def accept_attested_criterion(
    state: ProjectState,
    criterion: GoalCriterion,
    attestations: Mapping[str, str],
) -> str | None:
    return "test evaluator verified synchronized evidence" if attestations else None


class KernelTests(unittest.IsolatedAsyncioTestCase):
    async def test_length_finish_reason_fails_before_semantic_extraction(self) -> None:
        state = ProjectState(
            project_id="truncated-output",
            goal="Reject a truncated response",
            goal_contract=GoalContract(
                goal="Reject a truncated response",
                max_iterations=1,
                max_api_cost_usd=0.01,
            ),
            privacy_mode=PrivacyMode.FULL_FRONTIER,
        )
        adapter = ScriptedAdapter(
            [
                ModelResponse(
                    text='{"summary":"unfinished',
                    model="local-test",
                    provider="scripted",
                    usage=TokenUsage(
                        input_tokens=10,
                        output_tokens=32,
                        cost_usd=0.002,
                    ),
                    finish_reason="length",
                )
            ],
            adapter_id="truncated",
        )
        manager = StateManager(state)
        result = await CollaborationKernel(
            manager,
            [adapter],
            config=KernelConfig(max_rounds=1),
        ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.HARD)
        call = result.call_records[0]
        self.assertEqual(call.status, "failed")
        self.assertEqual(call.output_tokens, 32)
        self.assertEqual(call.cost_usd, 0.002)
        self.assertEqual(result.state.cost_state.api_cost_usd, 0.002)
        self.assertEqual(
            call.error,
            "ModelOutputTruncatedError: provider finish_reason=length",
        )
        errors = [
            event
            for event in manager.state.recent_events
            if event.event_type is EventType.MODEL_ERROR
        ]
        self.assertEqual(errors[-1].payload["stage"], "generation")

    async def test_joint_config_enforces_per_call_output_cap(self) -> None:
        class RecordingAdapter(LocalScriptedAdapter):
            seen_max_output_tokens = None

            async def stream(self, request):
                type(self).seen_max_output_tokens = request.max_output_tokens
                async for chunk in super().stream(request):
                    yield chunk

        state = ProjectState(
            project_id="output-cap",
            goal="Bound one round",
            goal_contract=GoalContract(goal="Bound one round", max_iterations=1),
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter = RecordingAdapter(
            [structured("bounded", focus_resolved=False)], adapter_id="bounded"
        )
        kernel = CollaborationKernel(
            StateManager(state),
            [adapter],
            config=KernelConfig(max_rounds=1, max_output_tokens_per_call=128),
        )
        await kernel.run(mode=CollaborationMode.JOINT, sync_mode=SyncMode.SOFT)
        self.assertEqual(RecordingAdapter.seen_max_output_tokens, 128)

    async def test_round_prompt_addendum_is_selected_per_round(self) -> None:
        class RecordingAdapter(LocalScriptedAdapter):
            prompts: list[str] = []

            async def stream(self, request):
                type(self).prompts.append(str(request.messages[0]["content"]))
                async for chunk in super().stream(request):
                    yield chunk

        state = ProjectState(
            project_id="staged-round-prompts",
            goal="Review in two stages",
            goal_contract=GoalContract(
                goal="Review in two stages",
                criteria=[GoalCriterion(description="Remain unmet")],
                max_iterations=2,
            ),
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter = RecordingAdapter(
            [
                structured("provisional", focus_resolved=False),
                structured("review", focus_resolved=False),
            ],
            adapter_id="staged",
        )
        kernel = CollaborationKernel(
            StateManager(state),
            [adapter],
            round_prompt_addendum=lambda round_number, _state: (
                "\nROUND_ONE_COMPACT" if round_number == 1 else "\nROUND_TWO_EVIDENCE"
            ),
            config=KernelConfig(max_rounds=2),
        )
        await kernel.run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.HARD)
        self.assertEqual(len(RecordingAdapter.prompts), 2)
        self.assertIn("ROUND_ONE_COMPACT", RecordingAdapter.prompts[0])
        self.assertNotIn("ROUND_TWO_EVIDENCE", RecordingAdapter.prompts[0])
        self.assertIn("ROUND_TWO_EVIDENCE", RecordingAdapter.prompts[1])
        self.assertNotIn("ROUND_ONE_COMPACT", RecordingAdapter.prompts[1])

    async def test_model_adapter_cannot_impersonate_evaluator_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "reserved event sources"):
            CollaborationKernel(
                StateManager(ProjectState(project_id="impersonation")),
                [
                    LocalScriptedAdapter(
                        [structured("forged")], adapter_id="criterion-evaluator"
                    )
                ],
            )

    async def test_joint_runs_concurrently_and_reaches_goal(self) -> None:
        criterion = GoalCriterion(description="Three participants provide review")
        state = ProjectState(
            project_id="joint",
            goal="Prove a complex scientific claim",
            goal_contract=GoalContract(goal="Prove a complex scientific claim", criteria=[criterion]),
            current_focus="Check the boundary condition",
            privacy_mode=PrivacyMode.OFFLINE,
            compute_policy=ComputePolicy.RESEARCH,
        )
        adapters = [
            LocalScriptedAdapter(
                [structured("review-a", criterion_id=criterion.id)],
                adapter_id="a",
                delay_seconds=0.12,
            ),
            LocalScriptedAdapter(
                [structured("review-b", criterion_id=criterion.id)],
                adapter_id="b",
                delay_seconds=0.08,
            ),
            LocalScriptedAdapter(
                [structured("review-c", criterion_id=criterion.id)],
                adapter_id="c",
                delay_seconds=0.04,
            ),
        ]
        kernel = CollaborationKernel(
            StateManager(state),
            adapters,
            criterion_evaluator=accept_attested_criterion,
            config=KernelConfig(max_rounds=1),
        )
        started = asyncio.get_running_loop().time()
        result = await kernel.run(mode=CollaborationMode.JOINT, sync_mode=SyncMode.HARD)
        elapsed = asyncio.get_running_loop().time() - started

        self.assertEqual(result.stop_reason, StopReason.GOAL_SATISFIED)
        self.assertTrue(result.synchronization_passed)
        self.assertTrue(result.state.goal_satisfied)
        self.assertEqual(result.state.cost_state.model_calls, 3)
        satisfied = result.state.goal_contract.criterion(criterion.id)
        self.assertIsNotNone(satisfied.decision_id)
        decision = result.state.decision_records[satisfied.decision_id]
        self.assertEqual(decision.decision_class, DecisionClass.SIGNIFICANT)
        checkpoint = result.state.synchronization_checkpoints[
            decision.checkpoint_id
        ]
        self.assertTrue(checkpoint.passed)
        self.assertEqual(set(checkpoint.dispositions), {"a", "b", "c"})
        self.assertEqual(decision.evidence_ids, satisfied.evidence_ids)
        self.assertTrue(
            all(result.state.evidence_records[item].verified for item in decision.evidence_ids)
        )
        self.assertLess(elapsed, 0.20, "model calls should overlap rather than serialize")
        self.assertTrue(
            all(call.staleness is StalenessDisposition.CURRENT for call in result.call_records),
            "cost bookkeeping alone must not make peer responses stale",
        )

    async def test_critical_objection_blocks_goal_even_with_majority_support(self) -> None:
        criterion = GoalCriterion(description="Claim verified")
        state = ProjectState(
            project_id="dissent",
            goal="Prove a complex theorem",
            goal_contract=GoalContract(goal="Prove a complex theorem", criteria=[criterion]),
            current_focus="Verify the claim",
            privacy_mode=PrivacyMode.OFFLINE,
            compute_policy=ComputePolicy.RESEARCH,
        )
        supporter = structured("support", criterion_id=criterion.id)
        dissenter = structured(
            "counterexample remains",
            events=[
                {
                    "type": "critical_conflict",
                    "content": "n=0 contradicts the claimed base case.",
                    "severity": "critical",
                    "confidence": 1.0,
                    "evidence_refs": ["direct substitution n=0"],
                    "disputed_claim": "the theorem holds for all n",
                    "proposed_check": "evaluate n=0",
                }
            ],
            focus_resolved=False,
        )
        adapters = [
            LocalScriptedAdapter([supporter], adapter_id="support-a"),
            LocalScriptedAdapter([supporter], adapter_id="support-b"),
            LocalScriptedAdapter([dissenter], adapter_id="dissenter"),
        ]
        result = await CollaborationKernel(
            StateManager(state), adapters, config=KernelConfig(max_rounds=1)
        ).run(mode=CollaborationMode.JOINT, sync_mode=SyncMode.QUORUM)

        self.assertEqual(result.stop_reason, StopReason.SYNCHRONIZATION_BLOCKED)
        self.assertFalse(result.state.goal_satisfied)
        self.assertTrue(result.state.has_critical_objection)
        self.assertFalse(criterion.satisfied, "input criterion must stay isolated")

    async def test_persists_and_restores_final_state(self) -> None:
        criterion = GoalCriterion(description="Answer recorded")
        state = ProjectState(
            project_id="resume",
            goal="Answer a routine question",
            goal_contract=GoalContract(goal="Answer a routine question", criteria=[criterion]),
            privacy_mode=PrivacyMode.OFFLINE,
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite3"
            with SQLiteStore(database) as store:
                manager = StateManager(state, store=store)
                result = await CollaborationKernel(
                    manager,
                    [LocalScriptedAdapter([structured("answer", criterion_id=criterion.id)], adapter_id="local")],
                    criterion_evaluator=accept_attested_criterion,
                    config=KernelConfig(max_rounds=1),
                ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.HARD)
                restored = StateManager.restore(store, "resume").snapshot()

        self.assertEqual(result.stop_reason, StopReason.GOAL_SATISFIED)
        self.assertEqual(restored.version, result.state.version)
        self.assertTrue(restored.goal_satisfied)

    async def test_kernel_settles_durable_model_call_cost_across_restart(self) -> None:
        state = ProjectState(
            project_id="durable-call",
            goal="Use one cloud call",
            goal_contract=GoalContract(
                goal="Use one cloud call", max_api_cost_usd=1.0
            ),
            privacy_mode=PrivacyMode.FULL_FRONTIER,
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "durable-call.sqlite3"
            with SQLiteStore(database) as store:
                result = await CollaborationKernel(
                    StateManager(state, store=store),
                    [
                        FixedCostScriptedAdapter(
                            [structured("done")],
                            adapter_id="cloud",
                            fixed_cost_usd=0.4,
                        )
                    ],
                    config=KernelConfig(max_rounds=1),
                ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT)
                reservations = store.list_model_call_reservations("durable-call")
                self.assertEqual(len(reservations), 1)
                self.assertEqual(
                    reservations[0].status, ModelCallReservationStatus.SETTLED
                )
                self.assertAlmostEqual(reservations[0].actual_cost_usd or 0.0, 0.4)
                self.assertEqual(
                    result.call_records[0].reservation_id, reservations[0].id
                )

            with SQLiteStore(database) as reopened:
                persisted = reopened.list_model_call_reservations("durable-call")
                self.assertEqual(persisted, reservations)
                self.assertAlmostEqual(
                    reopened.model_call_charged_cost("durable-call"), 0.4
                )

    async def test_provider_failure_reconciles_estimate_instead_of_reusing_budget(
        self,
    ) -> None:
        state = ProjectState(
            project_id="unknown-provider-cost",
            goal="Fail conservatively",
            goal_contract=GoalContract(
                goal="Fail conservatively", max_api_cost_usd=0.5
            ),
            privacy_mode=PrivacyMode.FULL_FRONTIER,
        )
        with SQLiteStore(":memory:") as store:
            await CollaborationKernel(
                StateManager(state, store=store),
                [
                    FailingFixedCostAdapter(
                        ["unused"], adapter_id="cloud", fixed_cost_usd=0.4
                    )
                ],
                config=KernelConfig(max_rounds=1),
            ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT)
            reservation = store.list_model_call_reservations(
                "unknown-provider-cost"
            )[0]
            self.assertEqual(
                reservation.status, ModelCallReservationStatus.RECONCILED
            )
            self.assertAlmostEqual(reservation.charged_cost_usd, 0.4)
            self.assertIn(
                "without authoritative usage",
                reservation.reconciliation_reason or "",
            )

    async def test_joint_release_requires_authorization_and_is_audited_in_checkpoint(
        self,
    ) -> None:
        state = ProjectState(
            project_id="authorized-release",
            goal="Continue safely",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        active = LocalScriptedAdapter([structured("reviewed")], adapter_id="active")
        released = LocalScriptedAdapter(
            [structured("must not run")], adapter_id="released"
        )
        kernel = CollaborationKernel(
            StateManager(state),
            [active, released],
            config=KernelConfig(max_rounds=1),
        )
        with self.assertRaisesRegex(ValueError, "release_authorized_by"):
            await kernel.run(
                mode=CollaborationMode.JOINT,
                sync_mode=SyncMode.HARD,
                released_participant_ids={"released"},
            )

        result = await kernel.run(
            mode=CollaborationMode.JOINT,
            sync_mode=SyncMode.HARD,
            released_participant_ids={"released"},
            release_authorized_by="user:project-owner",
        )
        checkpoint = next(iter(result.state.synchronization_checkpoints.values()))
        release = checkpoint.dispositions["released"]
        self.assertTrue(checkpoint.passed)
        self.assertEqual(release.status.value, "released")
        self.assertEqual(release.authorized_by, "user:project-owner")
        self.assertIsNotNone(checkpoint.deadline_at)
        self.assertEqual(released.calls_consumed, 0)
        self.assertFalse(checkpoint.permits(DecisionClass.CRITICAL))

    async def test_restart_closes_expired_checkpoint_before_new_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "checkpoint-recovery.sqlite3"
            state = ProjectState(
                project_id="checkpoint-recovery",
                goal="Resume after expiry",
                privacy_mode=PrivacyMode.OFFLINE,
            )
            with SQLiteStore(database) as store:
                manager = StateManager(state, store=store)
                manager.save()
                manager.apply(
                    CognitiveEvent(
                        id="abandoned-checkpoint",
                        event_type=EventType.SYNC_CHECKPOINT_OPENED,
                        source="synchronization-controller",
                        payload={
                            "checkpoint": {
                                "id": "abandoned-checkpoint",
                                "sync_mode": SyncMode.HARD.value,
                                "participant_ids": ["local"],
                                "required_participant_ids": ["local"],
                                "deadline_at": "2020-01-01T00:00:01Z",
                            }
                        },
                        state_version=manager.version,
                        created_at="2020-01-01T00:00:00Z",
                    )
                )

            with SQLiteStore(database) as reopened:
                restored = StateManager.restore(reopened, "checkpoint-recovery")
                result = await CollaborationKernel(
                    restored,
                    [LocalScriptedAdapter([structured("resumed")], adapter_id="local")],
                    config=KernelConfig(max_rounds=1),
                ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT)

                abandoned = result.state.synchronization_checkpoints[
                    "abandoned-checkpoint"
                ]
                self.assertEqual(abandoned.status.value, "expired")
                self.assertIn("restart recovery", abandoned.reason)
                self.assertIsNone(result.state.active_checkpoint_id)

    async def test_model_attestation_cannot_satisfy_goal_without_evaluator(self) -> None:
        criterion = GoalCriterion(description="Must be independently verified")
        state = ProjectState(
            project_id="attestation-only",
            goal="Do not trust self-attestation",
            goal_contract=GoalContract(
                goal="Do not trust self-attestation", criteria=[criterion]
            ),
            privacy_mode=PrivacyMode.OFFLINE,
        )
        result = await CollaborationKernel(
            StateManager(state),
            [
                LocalScriptedAdapter(
                    [structured("I assert this is correct", criterion_id=criterion.id)],
                    adapter_id="local",
                )
            ],
            config=KernelConfig(max_rounds=1),
        ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.HARD)

        self.assertFalse(result.state.goal_satisfied)
        self.assertFalse(result.state.goal_contract.criteria[0].satisfied)
        self.assertEqual(
            result.criterion_attestations[criterion.id]["local"],
            "I assert this is correct",
        )
        self.assertEqual(result.state.decision_records, {})
        self.assertEqual(result.state.evidence_records, {})
        self.assertEqual(len(result.state.synchronization_checkpoints), 1)

    async def test_unresolved_model_evidence_is_not_an_attestation(self) -> None:
        criterion = GoalCriterion(description="Require resolved verified evidence")
        state = ProjectState(
            project_id="unresolved-attestation",
            goal="Do not evaluate an unresolved participant claim",
            goal_contract=GoalContract(
                goal="Do not evaluate an unresolved participant claim",
                criteria=[criterion],
            ),
            privacy_mode=PrivacyMode.OFFLINE,
        )
        result = await CollaborationKernel(
            StateManager(state),
            [
                LocalScriptedAdapter(
                    [
                        structured(
                            "Evidence is supplied but the focus remains unresolved",
                            criterion_id=criterion.id,
                            focus_resolved=False,
                        )
                    ],
                    adapter_id="objector",
                )
            ],
            criterion_evaluator=accept_attested_criterion,
            config=KernelConfig(max_rounds=1),
        ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.HARD)

        checkpoint = next(iter(result.state.synchronization_checkpoints.values()))
        self.assertTrue(checkpoint.passed)
        self.assertEqual(
            checkpoint.dispositions["objector"].status.value,
            "object",
        )
        self.assertEqual(result.criterion_attestations, {})
        self.assertFalse(result.state.goal_satisfied)
        self.assertFalse(result.state.goal_contract.criteria[0].satisfied)
        self.assertEqual(result.state.evidence_records, {})
        self.assertEqual(result.state.decision_records, {})

    async def test_async_records_no_solicited_participant_dispositions(self) -> None:
        state = ProjectState(
            project_id="async-receipt",
            goal="Integrate provisionally",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        result = await CollaborationKernel(
            StateManager(state),
            [LocalScriptedAdapter([structured("provisional")], adapter_id="local")],
            config=KernelConfig(max_rounds=1),
        ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.ASYNC)

        checkpoint = next(iter(result.state.synchronization_checkpoints.values()))
        self.assertTrue(checkpoint.passed)
        self.assertEqual(checkpoint.dispositions, {})

    async def test_quorum_is_attendance_gate_not_majority_truth_vote(self) -> None:
        state = ProjectState(
            project_id="quorum-attendance",
            goal="Review a reversible change",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapters = [
            LocalScriptedAdapter([structured("support")], adapter_id="support"),
            LocalScriptedAdapter(
                [structured("noncritical objection", focus_resolved=False)],
                adapter_id="objector",
            ),
            LocalScriptedAdapter(["not structured JSON"], adapter_id="failed"),
        ]
        result = await CollaborationKernel(
            StateManager(state), adapters, config=KernelConfig(max_rounds=1)
        ).run(mode=CollaborationMode.JOINT, sync_mode=SyncMode.QUORUM)

        checkpoint = next(iter(result.state.synchronization_checkpoints.values()))
        self.assertTrue(checkpoint.passed)
        self.assertEqual(checkpoint.dispositions["support"].status.value, "support")
        self.assertEqual(checkpoint.dispositions["objector"].status.value, "object")
        self.assertEqual(checkpoint.dispositions["failed"].status.value, "failed")
        self.assertEqual(result.state.decision_records, {})

    async def test_stale_response_is_rebased_against_current_state(self) -> None:
        state = ProjectState(
            project_id="rebase",
            goal="Review a complex scientific claim",
            current_focus="Check the claim",
            privacy_mode=PrivacyMode.OFFLINE,
            compute_policy=ComputePolicy.RESEARCH,
        )
        fast = LocalScriptedAdapter(
            [
                structured(
                    "fast update",
                    events=[
                        {
                            "type": "hypothesis",
                            "content": "The fast provisional claim.",
                            "severity": "info",
                            "confidence": 0.6,
                            "evidence_refs": [],
                        }
                    ],
                )
            ],
            adapter_id="fast",
        )
        slow = LocalScriptedAdapter(
            [
                structured(
                    "old slow update",
                    events=[
                        {
                            "type": "hypothesis",
                            "content": "This old claim should not be integrated.",
                            "severity": "info",
                            "confidence": 0.5,
                            "evidence_refs": [],
                        }
                    ],
                ),
                structured(
                    "rebased update",
                    events=[
                        {
                            "type": "hypothesis",
                            "content": "The slow claim after reviewing the new delta.",
                            "severity": "info",
                            "confidence": 0.8,
                            "evidence_refs": [],
                        }
                    ],
                ),
            ],
            adapter_id="slow",
            delay_seconds=0.03,
        )
        result = await CollaborationKernel(
            StateManager(state),
            [fast, slow],
            config=KernelConfig(max_rounds=1, max_staleness_versions=0),
        ).run(
            mode=CollaborationMode.JOINT,
            sync_mode=SyncMode.BOUNDED_STALENESS,
        )

        slow_record = next(call for call in result.call_records if call.adapter_id == "slow")
        self.assertEqual(slow_record.staleness, StalenessDisposition.REBASED)
        self.assertEqual(slow_record.rebase_calls, 1)
        self.assertIn("The slow claim after reviewing the new delta.", result.state.hypotheses)
        self.assertNotIn("This old claim should not be integrated.", result.state.hypotheses)
        checkpoint = next(iter(result.state.synchronization_checkpoints.values()))
        self.assertTrue(checkpoint.passed)
        self.assertTrue(checkpoint.dispositions["slow"].rebased)

    async def test_structured_math_tool_request_produces_local_result(self) -> None:
        state = ProjectState(
            project_id="tool",
            goal="Calculate safely",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        script = structured(
            "requested deterministic evidence",
            events=[
                {
                    "type": "tool_request",
                    "content": "Evaluate the expression.",
                    "severity": "info",
                    "confidence": 1.0,
                    "evidence_refs": [],
                    "tool": "math",
                    "operation": "evaluate",
                    "expression": "sqrt(81) + 2 ** 3",
                    "disagreement_id": "d1",
                }
            ],
        )
        result = await CollaborationKernel(
            StateManager(state),
            [LocalScriptedAdapter([script], adapter_id="local")],
            config=KernelConfig(max_rounds=1),
        ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT)

        tool_result = result.state.experimental_results[0]
        self.assertTrue(tool_result["ok"])
        self.assertEqual(tool_result["output"], "17.0")
        evidence = result.state.evidence_records["tool:d1"]
        self.assertTrue(evidence.usable_for_critical_disagreement("d1"))

    async def test_builtin_numeric_evaluator_completes_from_tool_evidence(self) -> None:
        criterion = GoalCriterion(
            id="numeric",
            description="The deterministic result is 17",
            evaluator_kind=EvaluatorKind.NUMERIC_TOLERANCE,
            expected_value=17,
            tolerance=0,
        )
        state = ProjectState(
            project_id="builtin-evaluator",
            goal="Evaluate deterministically",
            goal_contract=GoalContract(
                goal="Evaluate deterministically", criteria=[criterion]
            ),
            privacy_mode=PrivacyMode.OFFLINE,
        )
        script = structured(
            "run deterministic check",
            events=[
                {
                    "type": "tool_request",
                    "content": "Evaluate the expression.",
                    "severity": "info",
                    "confidence": 1.0,
                    "evidence_refs": [],
                    "tool": "math",
                    "operation": "evaluate",
                    "expression": "sqrt(81) + 2 ** 3",
                    "criterion_id": criterion.id,
                }
            ],
        )
        result = await CollaborationKernel(
            StateManager(state),
            [LocalScriptedAdapter([script], adapter_id="local")],
            config=KernelConfig(max_rounds=1),
        ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.HARD)

        self.assertEqual(result.stop_reason, StopReason.GOAL_SATISFIED)
        self.assertTrue(result.state.goal_satisfied)
        satisfied = result.state.goal_contract.criterion(criterion.id)
        decision = result.state.decision_records[satisfied.decision_id]
        self.assertEqual(decision.evaluator, EvaluatorKind.NUMERIC_TOLERANCE.value)
        self.assertEqual(len(decision.evidence_ids), 1)

    async def test_model_cannot_request_unisolated_symbolic_work(self) -> None:
        state = ProjectState(
            project_id="symbolic-tool-boundary",
            goal="Stay bounded",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        script = structured(
            "requested symbolic work",
            events=[
                {
                    "type": "tool_request",
                    "content": "Simplify this expression.",
                    "severity": "info",
                    "confidence": 1.0,
                    "evidence_refs": [],
                    "tool": "math",
                    "operation": "simplify",
                    "expression": "x + x",
                }
            ],
        )
        result = await CollaborationKernel(
            StateManager(state),
            [LocalScriptedAdapter([script], adapter_id="local")],
            config=KernelConfig(max_rounds=1),
        ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT)

        # 07C: the unauthorized symbolic request is dropped (never executed)
        # and audited via dropped_event_count on the contribution; the rest of
        # the model's semantic batch remains usable. The boundary holds: no
        # experimental result is produced from the refused request.
        self.assertEqual(result.call_records[0].status, "complete")
        self.assertEqual(result.state.experimental_results, [])

    async def test_zero_runtime_contract_starts_no_model_call(self) -> None:
        state = ProjectState(
            project_id="runtime-limit",
            goal="Do not start",
            goal_contract=GoalContract(goal="Do not start", max_runtime_seconds=0),
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter = LocalScriptedAdapter([structured("unused")], adapter_id="local")
        result = await CollaborationKernel(
            StateManager(state), [adapter], config=KernelConfig(max_rounds=1)
        ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT)

        self.assertEqual(result.stop_reason, StopReason.RUNTIME_LIMIT)
        self.assertEqual(adapter.calls_consumed, 0)

    async def test_runtime_contract_cancels_an_inflight_model_call(self) -> None:
        state = ProjectState(
            project_id="inflight-runtime-limit",
            goal="Stop promptly",
            goal_contract=GoalContract(
                goal="Stop promptly", max_runtime_seconds=0.02
            ),
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter = LocalScriptedAdapter(
            [structured("too late")],
            adapter_id="local",
            delay_seconds=0.2,
        )
        started = asyncio.get_running_loop().time()
        result = await CollaborationKernel(
            StateManager(state), [adapter], config=KernelConfig(max_rounds=1)
        ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT)
        elapsed = asyncio.get_running_loop().time() - started

        self.assertEqual(result.stop_reason, StopReason.RUNTIME_LIMIT)
        self.assertLess(elapsed, 0.15)
        self.assertEqual(result.call_records[0].status, "cancelled")
        self.assertIn("runtime limit", result.call_records[0].error)

    async def test_concurrent_runs_are_serialized_and_call_records_are_isolated(self) -> None:
        state = ProjectState(
            project_id="serialized-runs",
            goal="Review twice",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter = ConcurrencyProbeAdapter(
            [structured("first"), structured("second")],
            adapter_id="local",
            delay_seconds=0.02,
        )
        kernel = CollaborationKernel(
            StateManager(state), [adapter], config=KernelConfig(max_rounds=1)
        )

        first, second = await asyncio.gather(
            kernel.run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT),
            kernel.run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT),
        )

        self.assertEqual(adapter.peak_active_streams, 1)
        self.assertEqual(len(first.call_records), 1)
        self.assertEqual(len(second.call_records), 1)
        self.assertEqual(first.call_records[0].summary, "first")
        self.assertEqual(second.call_records[0].summary, "second")

    async def test_bookkeeping_events_do_not_trigger_rebase(self) -> None:
        state = ProjectState(
            project_id="bookkeeping-staleness",
            goal="Review together",
            privacy_mode=PrivacyMode.OFFLINE,
            compute_policy=ComputePolicy.RESEARCH,
        )
        adapters = [
            LocalScriptedAdapter([structured("fast")], adapter_id="fast"),
            LocalScriptedAdapter(
                [structured("slow")],
                adapter_id="slow",
                delay_seconds=0.02,
            ),
        ]
        result = await CollaborationKernel(
            StateManager(state),
            adapters,
            config=KernelConfig(max_rounds=1, max_staleness_versions=0),
        ).run(mode=CollaborationMode.JOINT, sync_mode=SyncMode.SOFT)

        self.assertEqual(sum(call.rebase_calls for call in result.call_records), 0)
        self.assertTrue(
            all(call.staleness is StalenessDisposition.CURRENT for call in result.call_records)
        )
        self.assertGreater(result.state.version, result.state.cognitive_revision)

    async def test_cancelled_run_cleans_up_provider_tasks_before_unlocking(self) -> None:
        state = ProjectState(
            project_id="cancelled-run-cleanup",
            goal="Review twice",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        adapter = ConcurrencyProbeAdapter(
            [structured("cancelled"), structured("second")],
            adapter_id="local",
            delay_seconds=0.05,
        )
        kernel = CollaborationKernel(
            StateManager(state), [adapter], config=KernelConfig(max_rounds=1)
        )

        cancelled = asyncio.create_task(
            kernel.run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT)
        )
        await adapter.stream_started.wait()
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled

        result = await kernel.run(
            mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT
        )

        self.assertEqual(adapter.peak_active_streams, 1)
        self.assertEqual(adapter.active_streams, 0)
        self.assertEqual(len(result.call_records), 1)
        self.assertEqual(result.call_records[0].summary, "second")

    async def test_atomic_round_enforces_frontier_call_limit(self) -> None:
        state = ProjectState(
            project_id="frontier-limit",
            goal="Review together",
            privacy_mode=PrivacyMode.FULL_FRONTIER,
            compute_policy=ComputePolicy.ECONOMY,
        )
        adapters = [
            ScriptedAdapter([structured("first")], adapter_id="cloud-a"),
            ScriptedAdapter([structured("second")], adapter_id="cloud-b"),
        ]
        result = await CollaborationKernel(
            StateManager(state), adapters, config=KernelConfig(max_rounds=1)
        ).run(mode=CollaborationMode.JOINT, sync_mode=SyncMode.SOFT)

        self.assertEqual(sum(adapter.calls_consumed for adapter in adapters), 1)
        self.assertEqual(result.state.cost_state.api_model_calls, 1)
        self.assertEqual(result.state.cost_state.local_model_calls, 0)

    async def test_desktop_telemetry_run_succeeds_despite_exhausted_lifetime_calls(
        self,
    ) -> None:
        """Inherited lifetime cloud calls must not block a new desktop run.

        A telemetry contract (budget_is_telemetry=True) with an exhausted
        lifetime ledger must still admit fresh calls under the per-run
        baseline, without erasing lifetime telemetry.
        """
        state = ProjectState(
            project_id="desktop-recovery",
            goal="New desktop Chat message",
            privacy_mode=PrivacyMode.FULL_FRONTIER,
            compute_policy=ComputePolicy.BALANCED,
            goal_contract=GoalContract(goal="New desktop Chat message"),
            cost_state=CostState(api_model_calls=2),
        )
        state.goal_contract.budget_is_telemetry = True
        adapters = [ScriptedAdapter([structured("fresh desktop reply")], adapter_id="glm")]

        result = await CollaborationKernel(
            StateManager(state),
            adapters,
            config=KernelConfig(max_rounds=1),
        ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT)

        self.assertIs(result.stop_reason, StopReason.ROUND_LIMIT)
        self.assertEqual(len(result.call_records), 1)
        self.assertEqual(sum(adapter.calls_consumed for adapter in adapters), 1)
        # Lifetime telemetry preserved and still accumulating.
        self.assertEqual(result.state.cost_state.api_model_calls, 3)
        self.assertEqual(
            result.state.goal_contract.budget_is_telemetry,
            True,
        )

    async def test_desktop_joint_planned_allowance_completes_both_rounds(self) -> None:
        for telemetry, limit, rounds, expected_calls in (
            (True, None, 2, 2),
            (True, 4, 2, 4),
            (True, 4, 3, 4),
            (False, 4, 2, 2),
        ):
            with self.subTest(telemetry=telemetry, limit=limit, rounds=rounds):
                state = ProjectState(
                    project_id="joint-planned-allowance",
                    goal="Review together",
                    privacy_mode=PrivacyMode.FULL_FRONTIER,
                    compute_policy=ComputePolicy.BALANCED,
                    goal_contract=GoalContract(
                        goal="Review together", budget_is_telemetry=telemetry
                    ),
                )
                adapters = [
                    ScriptedAdapter(
                        [structured("ok", focus_resolved=False)],
                        adapter_id=name,
                        repeat_last=True,
                    )
                    for name in ("cloud-a", "cloud-b")
                ]
                result = await CollaborationKernel(
                    StateManager(state),
                    adapters,
                    config=KernelConfig(
                        max_rounds=rounds,
                        desktop_cloud_call_limit=limit,
                        rebase_stale_responses=False,
                    ),
                ).run(mode=CollaborationMode.JOINT, sync_mode=SyncMode.SOFT)
                self.assertEqual(len(result.call_records), expected_calls)
                self.assertEqual(
                    [adapter.calls_consumed for adapter in adapters],
                    [expected_calls // 2] * 2,
                )
                complete = telemetry and limit == 4 and rounds == 2
                self.assertEqual(result.synchronization_passed, complete)
                self.assertIs(
                    result.stop_reason,
                    StopReason.ROUND_LIMIT if complete else StopReason.BUDGET_OR_POLICY,
                )

    async def test_desktop_rebase_uses_fresh_per_run_allowance(self) -> None:
        """A stale-response rebase counts current-run calls, not lifetime.

        With the lifetime ledger exhausted and the policy limit reached, the
        lifetime-based rebase comparison would block every rebase in a fresh
        desktop run.  The rebase must be admitted inside the fresh per-run
        allowance.
        """
        state = ProjectState(
            project_id="desktop-rebase",
            goal="Review a complex scientific claim",
            current_focus="Check the claim",
            privacy_mode=PrivacyMode.FULL_FRONTIER,
            compute_policy=ComputePolicy.RESEARCH,
            goal_contract=GoalContract(goal="Review a complex scientific claim"),
            cost_state=CostState(api_model_calls=4),
        )
        state.goal_contract.budget_is_telemetry = True
        fast = ScriptedAdapter(
            [structured("fast update", events=[{
                "type": "hypothesis",
                "content": "The fast provisional claim.",
                "severity": "info",
                "confidence": 0.6,
                "evidence_refs": [],
            }])],
            adapter_id="fast",
        )
        slow = ScriptedAdapter(
            [
                structured(
                    "old slow update",
                    events=[{
                        "type": "hypothesis",
                        "content": "This old claim should not be integrated.",
                        "severity": "info",
                        "confidence": 0.5,
                        "evidence_refs": [],
                    }],
                ),
                structured(
                    "rebased update",
                    events=[{
                        "type": "hypothesis",
                        "content": "The slow claim after reviewing the new delta.",
                        "severity": "info",
                        "confidence": 0.8,
                        "evidence_refs": [],
                    }],
                ),
            ],
            adapter_id="slow",
            delay_seconds=0.03,
        )
        result = await CollaborationKernel(
            StateManager(state),
            [fast, slow],
            config=KernelConfig(max_rounds=1, max_staleness_versions=0),
        ).run(mode=CollaborationMode.JOINT, sync_mode=SyncMode.BOUNDED_STALENESS)

        slow_record = next(
            call for call in result.call_records if call.adapter_id == "slow"
        )
        self.assertIs(slow_record.staleness, StalenessDisposition.REBASED)
        self.assertEqual(slow_record.rebase_calls, 1)
        self.assertIn(
            "The slow claim after reviewing the new delta.",
            result.state.hypotheses,
        )
        # 4 lifetime + 2 current-run calls + 1 rebase call.
        self.assertEqual(result.state.cost_state.api_model_calls, 7)

    async def test_desktop_run_still_enforces_within_run_frontier_limit(self) -> None:
        """The fresh per-run allowance is bounded, not unlimited.

        A desktop telemetry run on a balanced policy may make exactly two
        current-run cloud calls; the third in the same run must still be
        denied at preflight.
        """
        state = ProjectState(
            project_id="desktop-run-limit",
            goal="Review together",
            privacy_mode=PrivacyMode.FULL_FRONTIER,
            compute_policy=ComputePolicy.BALANCED,
            goal_contract=GoalContract(goal="Review together"),
            cost_state=CostState(api_model_calls=2),
        )
        state.goal_contract.budget_is_telemetry = True
        adapters = [
            ScriptedAdapter([structured("first")], adapter_id="cloud-a"),
            ScriptedAdapter([structured("second")], adapter_id="cloud-b"),
            ScriptedAdapter([structured("third")], adapter_id="cloud-c"),
        ]
        result = await CollaborationKernel(
            StateManager(state),
            adapters,
            config=KernelConfig(max_rounds=1),
        ).run(mode=CollaborationMode.JOINT, sync_mode=SyncMode.SOFT)

        consumed = {adapter.adapter_id: adapter.calls_consumed for adapter in adapters}
        self.assertEqual(consumed["cloud-a"], 1)
        self.assertEqual(consumed["cloud-b"], 1)
        self.assertEqual(consumed["cloud-c"], 0)
        self.assertEqual(len(result.call_records), 2)
        self.assertEqual(result.state.cost_state.api_model_calls, 4)
        rejected = next(
            event
            for event in result.state.recent_events
            if event.source == "cloud-c" and event.event_type is EventType.MODEL_ERROR
        )
        self.assertIn("frontier-call limit", rejected.payload["error"])
        # Round completed via the two admitted participants, so the run ends
        # ROUND_LIMIT (synchronization passed) rather than BUDGET_OR_POLICY;
        # the third participant's preflight rejection is the within-run bound.
        self.assertIs(result.stop_reason, StopReason.ROUND_LIMIT)

    async def test_desktop_frontier_allowance_resets_between_runs_not_rounds(self) -> None:
        state = ProjectState(
            project_id="desktop-reused-kernel",
            goal="Continue review",
            privacy_mode=PrivacyMode.FULL_FRONTIER,
            compute_policy=ComputePolicy.BALANCED,
            goal_contract=GoalContract(
                goal="Continue review", budget_is_telemetry=True
            ),
            cost_state=CostState(api_model_calls=2),
        )
        adapter = ScriptedAdapter(
            [structured("review", focus_resolved=False)], repeat_last=True
        )
        kernel = CollaborationKernel(
            StateManager(state), [adapter], config=KernelConfig(max_rounds=3)
        )
        for expected_lifetime in (4, 6):
            result = await kernel.run(
                mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT
            )
            self.assertEqual(len(result.call_records), 2)
            self.assertIs(result.stop_reason, StopReason.BUDGET_OR_POLICY)
            self.assertEqual(result.state.cost_state.api_model_calls, expected_lifetime)
        self.assertEqual(adapter.calls_consumed, 4)

    async def test_generic_budget_frontier_limit_still_uses_lifetime_calls(
        self,
    ) -> None:
        """Non-telemetry contracts keep the strict lifetime comparison."""
        state = ProjectState(
            project_id="generic-lifetime-limit",
            goal="Review together",
            privacy_mode=PrivacyMode.FULL_FRONTIER,
            compute_policy=ComputePolicy.BALANCED,
            goal_contract=GoalContract(goal="Review together"),
            cost_state=CostState(api_model_calls=2),
        )
        adapters = [
            ScriptedAdapter([structured("first")], adapter_id="cloud-a"),
            ScriptedAdapter([structured("second")], adapter_id="cloud-b"),
        ]
        result = await CollaborationKernel(
            StateManager(state),
            adapters,
            config=KernelConfig(max_rounds=1),
        ).run(mode=CollaborationMode.JOINT, sync_mode=SyncMode.SOFT)

        self.assertEqual(sum(adapter.calls_consumed for adapter in adapters), 0)
        self.assertEqual(len(result.call_records), 0)
        self.assertEqual(result.state.cost_state.api_model_calls, 2)
        rejected = next(
            event
            for event in result.state.recent_events
            if event.source == "cloud-a" and event.event_type is EventType.MODEL_ERROR
        )
        self.assertIn(
            "The atomic round would exceed the compute policy frontier-call limit.",
            rejected.payload["error"],
        )
        self.assertIs(result.stop_reason, StopReason.BUDGET_OR_POLICY)


    async def test_rebase_cannot_oversubscribe_frontier_call_reservation(self) -> None:
        state = ProjectState(
            project_id="frontier-rebase",
            goal="Review together",
            privacy_mode=PrivacyMode.FULL_FRONTIER,
            compute_policy=ComputePolicy.RESEARCH,
        )
        adapters = [
            ScriptedAdapter([structured(f"review-{index}")], adapter_id=f"cloud-{index}")
            for index in range(4)
        ]
        result = await CollaborationKernel(
            StateManager(state),
            adapters,
            config=KernelConfig(max_rounds=1, max_staleness_versions=0),
        ).run(mode=CollaborationMode.JOINT, sync_mode=SyncMode.SOFT)

        self.assertEqual(result.state.cost_state.api_model_calls, 4)
        self.assertEqual(sum(call.rebase_calls for call in result.call_records), 0)
        self.assertEqual(sum(adapter.calls_consumed for adapter in adapters), 4)

    async def test_rebase_cannot_oversubscribe_atomic_api_budget(self) -> None:
        state = ProjectState(
            project_id="budget-rebase",
            goal="Review together",
            goal_contract=GoalContract(goal="Review together", max_api_cost_usd=0.9),
            privacy_mode=PrivacyMode.FULL_FRONTIER,
            compute_policy=ComputePolicy.RESEARCH,
        )
        adapters = [
            FixedCostScriptedAdapter(
                [structured("review-a")],
                adapter_id="cloud-a",
                fixed_cost_usd=0.4,
            ),
            FixedCostScriptedAdapter(
                [structured("review-b")],
                adapter_id="cloud-b",
                fixed_cost_usd=0.4,
            ),
        ]
        result = await CollaborationKernel(
            StateManager(state),
            adapters,
            config=KernelConfig(max_rounds=1, max_staleness_versions=0),
        ).run(mode=CollaborationMode.JOINT, sync_mode=SyncMode.SOFT)

        self.assertEqual(result.state.cost_state.api_model_calls, 2)
        self.assertAlmostEqual(result.state.cost_state.api_cost_usd, 0.8)
        self.assertEqual(sum(call.rebase_calls for call in result.call_records), 0)

    async def test_invalid_later_event_does_not_partially_commit_batch(self) -> None:
        state = ProjectState(
            project_id="atomic-events",
            goal="Integrate atomically",
            privacy_mode=PrivacyMode.OFFLINE,
        )
        script = structured(
            "one valid event followed by an invalid reference",
            events=[
                {
                    "type": "hypothesis",
                    "content": "This must not be partially committed.",
                    "severity": "info",
                    "confidence": 0.5,
                    "evidence_refs": [],
                },
                {
                    "type": "consensus",
                    "content": "Invalid consensus target.",
                    "severity": "info",
                    "confidence": 0.5,
                    "evidence_refs": ["claim only"],
                    "disagreement_id": "missing-disagreement",
                },
            ],
        )
        result = await CollaborationKernel(
            StateManager(state),
            [LocalScriptedAdapter([script], adapter_id="local")],
            config=KernelConfig(max_rounds=1),
        ).run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT)

        self.assertNotIn(
            "This must not be partially committed.", result.state.hypotheses
        )
        self.assertEqual(result.call_records[0].status, "failed")


if __name__ == "__main__":
    unittest.main()
