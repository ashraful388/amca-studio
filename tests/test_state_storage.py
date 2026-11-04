from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from amca.domain import (
    CognitiveEvent,
    CollaborationMode,
    ComputePolicy,
    CostState,
    DecisionClass,
    Disagreement,
    EvidenceKind,
    EvidenceStrength,
    EventType,
    GoalContract,
    GoalCriterion,
    ParticipantDisposition,
    ParticipantDispositionStatus,
    PrivacyMode,
    ProjectState,
    Severity,
    SynchronizationCheckpoint,
    SyncMode,
    VerificationStatus,
)
from amca.events import EventBus
from amca.security import SensitiveDataError
from amca.state import StateManager, StateVersionError
from amca.storage import (
    BudgetReservationError,
    ModelCallReservationStatus,
    SQLiteStore,
)


class DomainRoundTripTests(unittest.TestCase):
    def test_nested_project_state_json_round_trip(self) -> None:
        criterion = GoalCriterion(
            id="criterion-1",
            description="All tests pass",
            evidence=["unittest"],
        )
        contract = GoalContract(
            id="contract-1",
            goal="Build the durable state slice",
            criteria=[criterion],
            max_iterations=10,
            max_api_cost_usd=2.5,
        )
        disagreement = Disagreement(
            id="disagreement-1",
            topic="SQLite mode",
            description="Choose a journal mode",
            raised_by="model-a",
            positions={"model-a": "WAL", "model-b": "DELETE"},
            severity=Severity.HIGH,
        )
        event = CognitiveEvent(
            id="event-1",
            event_type=EventType.HYPOTHESIS,
            source="model-a",
            payload={"hypothesis": "WAL improves resilience"},
            state_version=0,
        )
        state = ProjectState(
            project_id="project-1",
            goal_contract=contract,
            current_focus="Persistence",
            active_disagreements=[disagreement],
            recent_events=[event],
            current_collaboration_mode=CollaborationMode.JOINT,
            current_sync_mode=SyncMode.QUORUM,
            privacy_mode=PrivacyMode.OFFLINE,
            compute_policy=ComputePolicy.RESEARCH,
            cost_state=CostState(input_tokens=12, api_cost_usd=0.01),
        )

        restored = ProjectState.from_json(state.to_json(indent=2))

        self.assertEqual(restored, state)
        self.assertEqual(restored.goal, contract.goal)

    def test_goal_contract_requires_at_least_one_required_satisfied_criterion(self) -> None:
        optional = GoalCriterion(description="Nice to have", required=False)
        contract = GoalContract(goal="Goal", criteria=[optional])
        self.assertFalse(contract.is_satisfied())
        self.assertEqual(contract.progress, 0.0)

        required = GoalCriterion(description="Required")
        contract.criteria.append(required)
        required.mark_satisfied("verified")
        self.assertTrue(contract.is_satisfied())
        self.assertEqual(contract.progress, 1.0)

    def test_synchronization_policy_distinguishes_attendance_and_decision_gate(self) -> None:
        async_checkpoint = SynchronizationCheckpoint(
            id="async",
            sync_mode=SyncMode.ASYNC,
            participant_ids=["a", "b"],
            required_participant_ids=[],
            opened_at="2026-01-01T00:00:00Z",
        )
        self.assertTrue(
            async_checkpoint.can_pass(has_critical_objection=False),
            "ASYNC solicits no participant dispositions",
        )
        async_checkpoint.close(
            passed=True,
            reason="integration receipt",
            closed_state_version=1,
            closed_at="2026-01-01T00:00:00Z",
        )
        self.assertTrue(async_checkpoint.permits(DecisionClass.PROVISIONAL))
        self.assertFalse(async_checkpoint.permits(DecisionClass.REVERSIBLE))

        soft = SynchronizationCheckpoint(
            id="soft",
            sync_mode=SyncMode.SOFT,
            participant_ids=["a"],
            required_participant_ids=[],
        )
        self.assertTrue(soft.can_pass(has_critical_objection=False))

        quorum = SynchronizationCheckpoint(
            id="quorum",
            sync_mode=SyncMode.QUORUM,
            participant_ids=["a", "b", "c"],
            required_participant_ids=[],
        )
        quorum.record(
            ParticipantDisposition("a", ParticipantDispositionStatus.SUPPORT)
        )
        quorum.record(
            ParticipantDisposition("b", ParticipantDispositionStatus.ABSTAIN)
        )
        self.assertTrue(
            quorum.can_pass(has_critical_objection=False),
            "abstention counts toward attendance but is not support evidence",
        )

        hard = SynchronizationCheckpoint(
            id="hard",
            sync_mode=SyncMode.HARD,
            participant_ids=["a", "b"],
            required_participant_ids=["a", "b"],
        )
        hard.record(
            ParticipantDisposition("a", ParticipantDispositionStatus.OBJECT)
        )
        hard.record(
            ParticipantDisposition("b", ParticipantDispositionStatus.ABSTAIN)
        )
        self.assertTrue(
            hard.can_pass(has_critical_objection=False),
            "hard sync requires disposition, not agreement as a truth proxy",
        )
        self.assertFalse(hard.can_pass(has_critical_objection=True))

        bounded = SynchronizationCheckpoint(
            id="bounded",
            sync_mode=SyncMode.BOUNDED_STALENESS,
            base_mode=SyncMode.HARD,
            participant_ids=["a"],
            required_participant_ids=["a"],
            max_version_lag=2,
        )
        bounded.record(
            ParticipantDisposition(
                "a",
                ParticipantDispositionStatus.SUPPORT,
                cognitive_lag=3,
            )
        )
        self.assertFalse(bounded.can_pass(has_critical_objection=False))
        bounded.dispositions["a"].rebased = True
        self.assertTrue(bounded.can_pass(has_critical_objection=False))

    def test_checkpoint_release_is_order_equivalent_but_cannot_authorize_critical_decision(
        self,
    ) -> None:
        def closed(order: tuple[str, str]) -> SynchronizationCheckpoint:
            checkpoint = SynchronizationCheckpoint(
                id="release-order",
                sync_mode=SyncMode.HARD,
                participant_ids=["reviewer", "unavailable"],
                required_participant_ids=["reviewer", "unavailable"],
                opened_at="2026-01-01T00:00:00Z",
                deadline_at="2026-01-01T00:01:00Z",
            )
            dispositions = {
                "reviewer": ParticipantDisposition(
                    participant_id="reviewer",
                    status=ParticipantDispositionStatus.SUPPORT,
                    created_at="2026-01-01T00:00:01Z",
                ),
                "unavailable": ParticipantDisposition(
                    participant_id="unavailable",
                    status=ParticipantDispositionStatus.RELEASED,
                    authorized_by="user:owner",
                    created_at="2026-01-01T00:00:01Z",
                ),
            }
            for participant_id in order:
                checkpoint.record(
                    dispositions[participant_id], at="2026-01-01T00:00:01Z"
                )
            self.assertTrue(
                checkpoint.can_pass(
                    has_critical_objection=False,
                    at="2026-01-01T00:00:02Z",
                )
            )
            checkpoint.close(
                passed=True,
                reason="authorized release",
                closed_state_version=3,
                closed_at="2026-01-01T00:00:02Z",
            )
            return checkpoint

        first = closed(("reviewer", "unavailable"))
        second = closed(("unavailable", "reviewer"))
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertTrue(first.permits(DecisionClass.SIGNIFICANT))
        self.assertFalse(first.permits(DecisionClass.CRITICAL))
        self.assertFalse(first.permits(DecisionClass.IRREVERSIBLE))


class EventBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_filtered_fanout_and_unsubscribe(self) -> None:
        bus = EventBus(queue_size=2)
        all_events = bus.subscribe()
        hypotheses = bus.subscribe(EventType.HYPOTHESIS)
        questions = bus.subscribe([EventType.QUESTION])
        event = CognitiveEvent(
            event_type=EventType.HYPOTHESIS,
            source="model-a",
            payload={"hypothesis": "x"},
        )

        delivered = await bus.publish(event)

        self.assertEqual(delivered, 2)
        self.assertIs(await all_events.get(), event)
        self.assertIs(await hypotheses.get(), event)
        self.assertTrue(questions.empty())
        hypotheses.close()
        self.assertEqual(bus.subscriber_count, 2)
        bus.close()
        with self.assertRaises(RuntimeError):
            await bus.publish(event)

    async def test_emit_builds_typed_event(self) -> None:
        bus = EventBus()
        subscription = bus.subscribe(EventType.EVIDENCE)
        event = await bus.emit(
            EventType.EVIDENCE,
            source="tool",
            payload={"evidence": {"result": 42}},
            state_version=3,
        )
        received = await asyncio.wait_for(subscription.get(), timeout=0.5)
        self.assertEqual(received, event)
        self.assertEqual(received.event_type, EventType.EVIDENCE)


class StateManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.criterion = GoalCriterion(id="done", description="Verified")
        contract = GoalContract(goal="Solve", criteria=[self.criterion])
        self.manager = StateManager(
            ProjectState(project_id="p1", goal_contract=contract),
            recent_event_limit=3,
        )

    def event(
        self,
        event_type: EventType,
        payload: dict[str, object],
        *,
        state_version: int | None = None,
        event_id: str | None = None,
        severity: Severity = Severity.INFO,
        source: str = "model-a",
    ) -> CognitiveEvent:
        arguments: dict[str, object] = {
            "event_type": event_type,
            "source": source,
            "payload": payload,
            "state_version": (
                self.manager.version if state_version is None else state_version
            ),
            "severity": severity,
        }
        if event_id is not None:
            arguments["id"] = event_id
        return CognitiveEvent(**arguments)  # type: ignore[arg-type]

    def test_versioned_reduction_is_idempotent_and_input_is_isolated(self) -> None:
        original = self.manager.snapshot()
        event = self.event(
            EventType.HYPOTHESIS,
            {"hypothesis": "Use event sourcing"},
            event_id="fixed-event",
        )

        state = self.manager.apply(event)

        self.assertEqual(state.version, 1)
        self.assertEqual(state.hypotheses, ["Use event sourcing"])
        self.assertEqual(original.version, 0)
        self.assertEqual(original.hypotheses, [])
        duplicate = self.manager.apply(event)
        self.assertEqual(duplicate.version, 1)
        self.assertEqual(duplicate.hypotheses, ["Use event sourcing"])

    def test_conflicting_duplicate_event_id_is_rejected(self) -> None:
        first = self.event(
            EventType.HYPOTHESIS,
            {"hypothesis": "First payload"},
            event_id="fixed-event",
        )
        self.manager.apply(first)
        collision = self.event(
            EventType.HYPOTHESIS,
            {"hypothesis": "Different payload"},
            state_version=first.state_version,
            event_id=first.id,
        )

        with self.assertRaisesRegex(ValueError, "different digest"):
            self.manager.apply(collision)

        self.assertEqual(self.manager.version, 1)
        self.assertEqual(self.manager.state.hypotheses, ["First payload"])

    def test_replaying_same_event_sequence_produces_identical_state(self) -> None:
        criterion = GoalCriterion(id="done", description="Verified")
        initial = ProjectState(
            project_id="deterministic",
            goal_contract=GoalContract(
                id="goal",
                goal="Replay deterministically",
                criteria=[criterion],
                created_at="2026-01-01T00:00:00Z",
            ),
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        conflict = CognitiveEvent(
            id="conflict-event",
            event_type=EventType.CRITICAL_CONFLICT,
            source="reviewer",
            payload={"topic": "Proof", "description": "Check the base case"},
            state_version=0,
            severity=Severity.CRITICAL,
            created_at="2026-01-01T00:00:01Z",
        )
        evidence = CognitiveEvent(
            id="evidence-event",
            event_type=EventType.EVIDENCE,
            source="criterion-evaluator",
            payload={
                "record": {
                    "id": "evidence-1",
                    "summary": "Base-case calculation",
                    "kind": EvidenceKind.CALCULATION.value,
                    "provenance": {
                        "source": "deterministic-calculator",
                        "locator": "proof/base-case",
                        "timestamp": "2026-01-01T00:00:02Z",
                        "content_digest": "sha256:base-case",
                    },
                    "method": "direct substitution",
                    "supports_criterion_ids": ["done"],
                    "addresses_disagreement_ids": ["conflict-event"],
                    "strength": EvidenceStrength.STRONG.value,
                    "verification_status": VerificationStatus.VERIFIED.value,
                }
            },
            state_version=1,
            created_at="2026-01-01T00:00:02Z",
        )
        resolution = CognitiveEvent(
            id="resolution-event",
            event_type=EventType.CONSENSUS,
            source="reviewer",
            payload={
                "disagreement_id": conflict.id,
                "resolution": "Base case verified",
                "evidence_ids": ["evidence-1"],
            },
            state_version=2,
            created_at="2026-01-01T00:00:03Z",
        )
        checkpoint_open = CognitiveEvent(
            id="checkpoint-event",
            event_type=EventType.SYNC_CHECKPOINT_OPENED,
            source="synchronization-controller",
            payload={
                "checkpoint": {
                    "id": "checkpoint-1",
                    "sync_mode": SyncMode.HARD.value,
                    "participant_ids": ["reviewer"],
                    "required_participant_ids": ["reviewer"],
                }
            },
            state_version=3,
            created_at="2026-01-01T00:00:04Z",
        )
        disposition = CognitiveEvent(
            id="disposition-event",
            event_type=EventType.PARTICIPANT_DISPOSITION,
            source="synchronization-controller",
            payload={
                "checkpoint_id": "checkpoint-1",
                "disposition": {
                    "participant_id": "reviewer",
                    "status": ParticipantDispositionStatus.SUPPORT.value,
                },
            },
            state_version=4,
            created_at="2026-01-01T00:00:05Z",
        )
        checkpoint_close = CognitiveEvent(
            id="checkpoint-close-event",
            event_type=EventType.SYNC_CHECKPOINT_CLOSED,
            source="synchronization-controller",
            payload={"checkpoint_id": "checkpoint-1", "passed": True},
            state_version=5,
            created_at="2026-01-01T00:00:06Z",
        )
        progress = CognitiveEvent(
            id="progress-event",
            event_type=EventType.GOAL_PROGRESS,
            source="criterion-evaluator",
            payload={
                "criterion_id": "done",
                "satisfied": True,
                "decision": {
                    "id": "decision-1",
                    "criterion_id": "done",
                    "decision_class": DecisionClass.SIGNIFICANT.value,
                    "checkpoint_id": "checkpoint-1",
                    "evidence_ids": ["evidence-1"],
                    "dispositions": {"reviewer": "support"},
                    "critical_disagreement_dispositions": {
                        "conflict-event": "resolved"
                    },
                    "rationale": "Direct substitution verifies the base case",
                    "evaluator": "deterministic-calculator",
                    "passed": True,
                },
            },
            state_version=6,
            created_at="2026-01-01T00:00:07Z",
        )

        def replay() -> ProjectState:
            manager = StateManager(initial)
            for event in (
                conflict,
                evidence,
                resolution,
                checkpoint_open,
                disposition,
                checkpoint_close,
                progress,
            ):
                manager.apply(event)
            return manager.snapshot()

        first = replay()
        second = replay()

        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual(first.active_disagreements[0].id, conflict.id)
        self.assertEqual(
            first.active_disagreements[0].opened_at, conflict.created_at
        )
        self.assertEqual(
            first.active_disagreements[0].resolved_at, resolution.created_at
        )
        self.assertEqual(
            first.goal_contract.criterion("done").satisfied_at, progress.created_at
        )
        self.assertEqual(first.updated_at, progress.created_at)
        self.assertTrue(first.goal_satisfied)
        self.assertEqual(first.active_checkpoint_id, None)

    def test_semantic_extractor_content_payload_aliases_reduce(self) -> None:
        hypothesis = self.event(
            EventType.HYPOTHESIS, {"content": "Version every accepted change"}
        )
        state = self.manager.apply(hypothesis)
        self.assertIn("Version every accepted change", state.hypotheses)
        alternative = self.event(
            EventType.ALTERNATIVE, {"content": "Store periodic snapshots"}
        )
        state = self.manager.apply(alternative)
        self.assertIn("Store periodic snapshots", state.pending_ideas)
        objection = self.event(
            EventType.OBJECTION,
            {
                "content": "The write is not atomic",
                "disputed_claim": "snapshot durability",
                "proposed_check": "force a rollback",
            },
            severity=Severity.HIGH,
        )
        state = self.manager.apply(objection)
        self.assertEqual(
            state.unresolved_disagreements[-1].description,
            "The write is not atomic",
        )

    def test_late_event_is_marked_stale_but_future_event_is_rejected(self) -> None:
        self.manager.apply(self.event(EventType.QUESTION, {"question": "Why?"}))
        late = self.event(
            EventType.ALTERNATIVE,
            {"alternative": "Try snapshots"},
            state_version=0,
        )
        state = self.manager.apply(late)
        self.assertTrue(state.model_contributions[-1]["stale"])

        future = self.event(
            EventType.HYPOTHESIS,
            {"hypothesis": "future"},
            state_version=state.version + 1,
        )
        with self.assertRaises(StateVersionError):
            self.manager.apply(future)

    def test_strict_manager_rejects_late_event(self) -> None:
        manager = StateManager(self.manager.snapshot(), strict_versions=True)
        first = CognitiveEvent(
            event_type=EventType.QUESTION,
            source="model",
            payload={"question": "one"},
            state_version=0,
        )
        manager.apply(first)
        late = CognitiveEvent(
            event_type=EventType.QUESTION,
            source="model",
            payload={"question": "late"},
            state_version=0,
        )
        with self.assertRaises(StateVersionError):
            manager.apply(late)

    def test_critical_disagreement_blocks_goal_until_evidence_consensus(self) -> None:
        conflict = self.event(
            EventType.CRITICAL_CONFLICT,
            {
                "id": "d1",
                "topic": "Proof validity",
                "description": "Step 3 is unsupported",
            },
            severity=Severity.CRITICAL,
        )
        state = self.manager.apply(conflict)
        self.assertTrue(state.has_critical_objection)

        legacy_progress = self.event(
            EventType.GOAL_PROGRESS,
            {"criterion_id": "done", "satisfied": True, "evidence": "tests"},
            source="goal-manager",
        )
        with self.assertRaisesRegex(ValueError, "decision record"):
            self.manager.apply(legacy_progress)
        state = self.manager.state
        self.assertFalse(state.goal_satisfied)
        self.assertNotEqual(state.status, "complete")

        unsupported_consensus = self.event(
            EventType.CONSENSUS,
            {"disagreement_id": "d1", "resolution": "Models voted 2-1"},
        )
        with self.assertRaises(ValueError):
            self.manager.apply(unsupported_consensus)
        self.assertEqual(self.manager.version, state.version)

        state = self.manager.apply(
            self.event(
                EventType.EVIDENCE,
                {"evidence": "experiment-7"},
            )
        )
        unverified_consensus = self.event(
            EventType.CONSENSUS,
            {
                "disagreement_id": "d1",
                "resolution": "Step 3 verified numerically",
                "evidence_refs": [state.recent_events[-1].id],
            },
        )
        with self.assertRaisesRegex(ValueError, "verified"):
            self.manager.apply(unverified_consensus)

        evidence_id = "experiment-7"
        state = self.manager.apply(
            self.event(
                EventType.EVIDENCE,
                {
                    "record": {
                        "id": evidence_id,
                        "summary": "Numerical boundary experiment",
                        "kind": EvidenceKind.CALCULATION.value,
                        "provenance": {
                            "source": "experiment-runner",
                            "locator": "run/7",
                            "timestamp": "2026-01-01T00:00:00Z",
                            "content_digest": "sha256:experiment-7",
                        },
                        "method": "boundary substitution",
                        "supports_criterion_ids": ["done"],
                        "addresses_disagreement_ids": ["d1"],
                        "strength": EvidenceStrength.STRONG.value,
                        "verification_status": VerificationStatus.VERIFIED.value,
                    }
                },
                source="criterion-evaluator",
            )
        )
        consensus = self.event(
            EventType.CONSENSUS,
            {
                "disagreement_id": "d1",
                "resolution": "Step 3 verified numerically",
                "evidence_ids": [evidence_id],
            },
        )
        state = self.manager.apply(consensus)
        self.assertTrue(state.active_disagreements[0].resolved)

        state = self.manager.apply(
            self.event(
                EventType.SYNC_CHECKPOINT_OPENED,
                {
                    "checkpoint": {
                        "id": "hard-1",
                        "sync_mode": SyncMode.HARD.value,
                        "participant_ids": ["reviewer"],
                        "required_participant_ids": ["reviewer"],
                    }
                },
                source="synchronization-controller",
            )
        )
        state = self.manager.apply(
            self.event(
                EventType.PARTICIPANT_DISPOSITION,
                {
                    "checkpoint_id": "hard-1",
                    "disposition": {
                        "participant_id": "reviewer",
                        "status": ParticipantDispositionStatus.SUPPORT.value,
                    },
                },
                source="synchronization-controller",
            )
        )
        state = self.manager.apply(
            self.event(
                EventType.SYNC_CHECKPOINT_CLOSED,
                {"checkpoint_id": "hard-1", "passed": True},
                source="synchronization-controller",
            )
        )
        progress = self.event(
            EventType.GOAL_PROGRESS,
            {
                "criterion_id": "done",
                "satisfied": True,
                "decision": {
                    "id": "decision-done",
                    "criterion_id": "done",
                    "decision_class": DecisionClass.SIGNIFICANT.value,
                    "checkpoint_id": "hard-1",
                    "evidence_ids": [evidence_id],
                    "dispositions": {"reviewer": "support"},
                    "critical_disagreement_dispositions": {"d1": "resolved"},
                    "rationale": "Verified numerical evidence",
                    "evaluator": "criterion-evaluator",
                    "passed": True,
                },
            },
            source="criterion-evaluator",
        )
        state = self.manager.apply(progress)
        self.assertTrue(state.goal_satisfied)
        self.assertEqual(state.status, "complete")

        state = self.manager.apply(
            self.event(
                EventType.CRITICAL_CONFLICT,
                {"id": "late-critical", "topic": "Late counterexample"},
                severity=Severity.CRITICAL,
            )
        )
        self.assertFalse(state.goal_satisfied)
        self.assertEqual(state.goal_contract.criterion("done").status.value, "blocked")
        state = self.manager.apply(
            self.event(
                EventType.OBJECTION,
                {"id": "late-critical", "topic": "Attempted downgrade"},
                severity=Severity.LOW,
            )
        )
        self.assertEqual(
            next(
                item
                for item in state.active_disagreements
                if item.id == "late-critical"
            ).severity,
            Severity.CRITICAL,
        )

    def test_model_source_cannot_directly_mark_goal_progress(self) -> None:
        progress = self.event(
            EventType.GOAL_PROGRESS,
            {"criterion_id": "done", "satisfied": True, "evidence": "assertion"},
        )
        with self.assertRaisesRegex(ValueError, "authorized evaluator"):
            self.manager.apply(progress)

    def test_model_source_cannot_forge_a_verified_tool_result(self) -> None:
        forged = self.event(
            EventType.TOOL_RESULT,
            {
                "result": {
                    "request_event_id": "made-up",
                    "evidence_id": "tool:critical",
                    "ok": True,
                    "operation": "evaluate",
                    "output": "42",
                    "addresses_disagreement_ids": ["critical"],
                }
            },
        )
        with self.assertRaisesRegex(ValueError, "authorized executor"):
            self.manager.apply(forged)

    def test_critical_resolution_rejects_missing_or_misdirected_provenance(self) -> None:
        self.manager.apply(
            self.event(
                EventType.CRITICAL_CONFLICT,
                {"id": "critical-1", "topic": "Boundary", "content": "Unverified"},
                severity=Severity.CRITICAL,
            )
        )
        self.manager.apply(
            self.event(
                EventType.EVIDENCE,
                {
                    "record": {
                        "id": "no-provenance",
                        "summary": "Claims to be verified but has no lineage",
                        "method": "calculation",
                        "addresses_disagreement_ids": ["critical-1"],
                        "strength": EvidenceStrength.STRONG.value,
                        "verification_status": VerificationStatus.VERIFIED.value,
                    }
                },
                source="criterion-evaluator",
            )
        )
        with self.assertRaisesRegex(ValueError, "provenance-bearing"):
            self.manager.apply(
                self.event(
                    EventType.CONSENSUS,
                    {
                        "disagreement_id": "critical-1",
                        "resolution": "unsupported",
                        "evidence_ids": ["no-provenance"],
                    },
                )
            )

        self.manager.apply(
            self.event(
                EventType.EVIDENCE,
                {
                    "record": {
                        "id": "wrong-conflict",
                        "summary": "Valid evidence for a different question",
                        "provenance": {
                            "source": "test",
                            "timestamp": "2026-01-01T00:00:00Z",
                            "content_digest": "sha256:other",
                        },
                        "method": "calculation",
                        "addresses_disagreement_ids": ["other-conflict"],
                        "strength": EvidenceStrength.STRONG.value,
                        "verification_status": VerificationStatus.VERIFIED.value,
                    }
                },
                source="criterion-evaluator",
            )
        )
        with self.assertRaisesRegex(ValueError, "explicitly addresses"):
            self.manager.apply(
                self.event(
                    EventType.CONSENSUS,
                    {
                        "disagreement_id": "critical-1",
                        "resolution": "wrong evidence",
                        "evidence_ids": ["wrong-conflict"],
                    },
                )
            )

    def test_checkpoint_cannot_forge_pass_and_does_not_advance_cognitive_revision(self) -> None:
        state = self.manager.apply(
            self.event(
                EventType.SYNC_CHECKPOINT_OPENED,
                {
                    "checkpoint": {
                        "id": "hard-forge",
                        "sync_mode": SyncMode.HARD.value,
                        "participant_ids": ["a", "b"],
                        "required_participant_ids": ["a", "b"],
                    }
                },
                source="synchronization-controller",
            )
        )
        self.assertEqual(state.cognitive_revision, 0)
        state = self.manager.apply(
            self.event(
                EventType.PARTICIPANT_DISPOSITION,
                {
                    "checkpoint_id": "hard-forge",
                    "disposition": {
                        "participant_id": "a",
                        "status": ParticipantDispositionStatus.SUPPORT.value,
                    },
                },
                source="synchronization-controller",
            )
        )
        self.assertEqual(state.cognitive_revision, 0)
        version = state.version
        with self.assertRaisesRegex(ValueError, "cannot be marked passed"):
            self.manager.apply(
                self.event(
                    EventType.SYNC_CHECKPOINT_CLOSED,
                    {"checkpoint_id": "hard-forge", "passed": True},
                    source="synchronization-controller",
                )
            )
        self.assertEqual(self.manager.version, version)
        self.assertEqual(
            self.manager.state.synchronization_checkpoints["hard-forge"].status.value,
            "open",
        )

    def test_checkpoint_deadline_expires_and_release_requires_user_event(self) -> None:
        opened = CognitiveEvent(
            id="deadline-open",
            event_type=EventType.SYNC_CHECKPOINT_OPENED,
            source="synchronization-controller",
            payload={
                "checkpoint": {
                    "id": "deadline-gate",
                    "sync_mode": SyncMode.HARD.value,
                    "participant_ids": ["a", "b"],
                    "required_participant_ids": ["a", "b"],
                    "deadline_at": "2026-01-01T00:00:10Z",
                }
            },
            state_version=self.manager.version,
            created_at="2026-01-01T00:00:00Z",
        )
        self.manager.apply(opened)
        forged_release = CognitiveEvent(
            id="forged-release",
            event_type=EventType.PARTICIPANT_DISPOSITION,
            source="synchronization-controller",
            payload={
                "checkpoint_id": "deadline-gate",
                "disposition": {
                    "participant_id": "b",
                    "status": ParticipantDispositionStatus.RELEASED.value,
                    "authorized_by": "claimed-user",
                },
            },
            state_version=self.manager.version,
            created_at="2026-01-01T00:00:01Z",
        )
        with self.assertRaisesRegex(ValueError, "user-origin authorization"):
            self.manager.apply(forged_release)

        authorized_release = CognitiveEvent.from_dict(forged_release.to_dict())
        authorized_release.id = "authorized-release"
        authorized_release.source = "user"
        authorized_release.payload["disposition"]["authorized_by"] = "user:owner"
        self.manager.apply(authorized_release)

        late_support = CognitiveEvent(
            id="late-support",
            event_type=EventType.PARTICIPANT_DISPOSITION,
            source="synchronization-controller",
            payload={
                "checkpoint_id": "deadline-gate",
                "disposition": {
                    "participant_id": "a",
                    "status": ParticipantDispositionStatus.SUPPORT.value,
                },
            },
            state_version=self.manager.version,
            created_at="2026-01-01T00:00:11Z",
        )
        with self.assertRaisesRegex(ValueError, "after the checkpoint deadline"):
            self.manager.apply(late_support)

        expired = self.manager.apply(
            CognitiveEvent(
                id="deadline-close",
                event_type=EventType.SYNC_CHECKPOINT_CLOSED,
                source="synchronization-controller",
                payload={"checkpoint_id": "deadline-gate", "passed": False},
                state_version=self.manager.version,
                created_at="2026-01-01T00:00:11Z",
            )
        ).synchronization_checkpoints["deadline-gate"]
        self.assertEqual(expired.status.value, "expired")
        self.assertEqual(expired.expired_at, "2026-01-01T00:00:11Z")
        self.assertFalse(expired.passed)

    def test_state_patch_mode_cost_and_artifact_reducers(self) -> None:
        state = self.manager.apply(
            self.event(
                EventType.STATE_UPDATE,
                {
                    "set": {"current_focus": "Schema", "progress": 0.25},
                    "append": {"accepted_facts": ["JSON is portable"]},
                },
            )
        )
        self.assertEqual(state.current_focus, "Schema")
        self.assertIn("JSON is portable", state.accepted_facts)
        state = self.manager.apply(
            self.event(
                EventType.MODE_SWITCH,
                {
                    "mode": "joint",
                    "participating_models": ["local", "frontier"],
                },
            )
        )
        self.assertEqual(state.current_collaboration_mode, CollaborationMode.JOINT)
        state = self.manager.apply(
            self.event(
                EventType.COST_UPDATE,
                {
                    "model": "frontier",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "api_cost_usd": 0.03,
                    "latency_seconds": 1.2,
                },
            )
        )
        self.assertEqual(state.cost_state.total_tokens, 15)
        self.assertAlmostEqual(state.cost_state.total_cost_usd, 0.03)
        state = self.manager.apply(
            self.event(
                EventType.ARTIFACT_UPDATE,
                {"artifact_id": "proof", "version": "v0.1", "content": "..."},
            )
        )
        self.assertEqual(state.artifacts["proof"]["version"], "v0.1")
        self.assertEqual(len(state.recent_events), 3)


class SQLiteStoreTests(unittest.TestCase):
    def test_model_call_reservations_are_atomic_across_connections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "concurrent-cost.sqlite3"
            with SQLiteStore(database) as first, SQLiteStore(database) as second:
                first.save_state(
                    ProjectState(project_id="atomic-cost", goal="Stay in budget")
                )

                def reserve(store: SQLiteStore, reservation_id: str) -> str:
                    try:
                        store.reserve_model_call(
                            reservation_id=reservation_id,
                            project_id="atomic-cost",
                            owner_id=reservation_id,
                            adapter_id=reservation_id,
                            is_api=True,
                            estimated_cost_usd=0.6,
                            recorded_api_cost_usd=0.0,
                            budget_usd=1.0,
                        )
                    except BudgetReservationError:
                        return "rejected"
                    return "reserved"

                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = list(
                        executor.map(
                            lambda args: reserve(*args),
                            ((first, "call-a"), (second, "call-b")),
                        )
                    )

                self.assertEqual(sorted(outcomes), ["rejected", "reserved"])
                self.assertAlmostEqual(first.model_call_charged_cost("atomic-cost"), 0.6)

    def test_model_call_restart_reconciliation_is_conservative_and_visible(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "restart-cost.sqlite3"
            with SQLiteStore(database) as store:
                store.save_state(ProjectState(project_id="restart-cost"))
                store.reserve_model_call(
                    reservation_id="crashed-call",
                    project_id="restart-cost",
                    owner_id="dead-process",
                    adapter_id="cloud",
                    is_api=True,
                    estimated_cost_usd=0.4,
                    recorded_api_cost_usd=0.0,
                    budget_usd=0.5,
                    lease_seconds=1.0,
                    reserved_at="2026-01-01T00:00:00Z",
                )

            with SQLiteStore(database) as reopened:
                self.assertEqual(
                    reopened.reconcile_expired_model_calls(
                        project_id="restart-cost", at="2026-01-01T00:00:02Z"
                    ),
                    1,
                )
                recovered = reopened.load_model_call_reservation("crashed-call")
                self.assertIsNotNone(recovered)
                assert recovered is not None
                self.assertEqual(
                    recovered.status, ModelCallReservationStatus.RECONCILED
                )
                self.assertEqual(recovered.charged_cost_usd, 0.4)
                self.assertIn("lease expired", recovered.reconciliation_reason or "")
                with self.assertRaises(BudgetReservationError):
                    reopened.reserve_model_call(
                        reservation_id="restart-call",
                        project_id="restart-cost",
                        owner_id="new-process",
                        adapter_id="cloud",
                        is_api=True,
                        estimated_cost_usd=0.2,
                        recorded_api_cost_usd=0.0,
                        budget_usd=0.5,
                        reserved_at="2026-01-01T00:00:03Z",
                    )

    def test_model_call_settlement_replaces_estimate_and_is_idempotent(self) -> None:
        with SQLiteStore(":memory:") as store:
            store.save_state(ProjectState(project_id="settled-cost"))
            reserved = store.reserve_model_call(
                reservation_id="settle-me",
                project_id="settled-cost",
                owner_id="run",
                adapter_id="cloud",
                is_api=True,
                estimated_cost_usd=0.8,
                recorded_api_cost_usd=0.0,
                budget_usd=1.0,
            )
            self.assertEqual(reserved.status, ModelCallReservationStatus.RESERVED)
            settled = store.settle_model_call(
                "settle-me", actual_cost_usd=0.25
            )
            repeated = store.settle_model_call(
                "settle-me", actual_cost_usd=0.25
            )
            self.assertEqual(settled, repeated)
            self.assertEqual(settled.status, ModelCallReservationStatus.SETTLED)
            self.assertAlmostEqual(store.model_call_charged_cost("settled-cost"), 0.25)

            second = store.reserve_model_call(
                reservation_id="remaining-budget",
                project_id="settled-cost",
                owner_id="run",
                adapter_id="cloud",
                is_api=True,
                estimated_cost_usd=0.7,
                recorded_api_cost_usd=0.25,
                budget_usd=1.0,
            )
            self.assertEqual(second.status, ModelCallReservationStatus.RESERVED)

    def test_rebase_increment_is_reserved_atomically_before_retry(self) -> None:
        with SQLiteStore(":memory:") as store:
            store.save_state(ProjectState(project_id="rebase-reservation"))
            store.reserve_model_call(
                reservation_id="rebase-call",
                project_id="rebase-reservation",
                owner_id="run",
                adapter_id="cloud",
                is_api=True,
                estimated_cost_usd=0.3,
                recorded_api_cost_usd=0.0,
                budget_usd=0.5,
            )
            expanded = store.increase_model_call_reservation(
                "rebase-call",
                additional_estimated_cost_usd=0.2,
                recorded_api_cost_usd=0.0,
                budget_usd=0.5,
            )
            self.assertAlmostEqual(expanded.estimated_cost_usd, 0.5)
            with self.assertRaises(BudgetReservationError):
                store.increase_model_call_reservation(
                    "rebase-call",
                    additional_estimated_cost_usd=0.01,
                    recorded_api_cost_usd=0.0,
                    budget_usd=0.5,
                )

    def test_credential_like_event_keys_are_rejected_recursively(self) -> None:
        sensitive_keys = (
            "api_key",
            "APIKey",
            "Authorization",
            "password",
            "clientSecret",
            "access-token",
            "refresh_token",
            "sessionToken",
            "private_key",
        )
        for key in sensitive_keys:
            with self.subTest(key=key):
                event = CognitiveEvent(
                    event_type=EventType.EVIDENCE,
                    source="security-test",
                    payload={"nested": [{key: "credential-canary"}]},
                )
                with self.assertRaises(SensitiveDataError):
                    event.to_json()
                with self.assertRaises(SensitiveDataError):
                    StateManager(ProjectState(project_id="gate-test")).apply(event)

        # Plural token accounting is telemetry, not credential material.
        event = CognitiveEvent(
            event_type=EventType.COST_UPDATE,
            source="kernel",
            payload={"input_tokens": 12, "output_tokens": 3},
        )
        self.assertEqual(event.payload["input_tokens"], 12)

    def test_secret_canary_never_reaches_raw_sqlite_storage(self) -> None:
        canary = "AMCA-PERSISTENCE-SECRET-CANARY-7f98c0"
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "secret-gate.sqlite3"
            with SQLiteStore(database) as store:
                initial = ProjectState(project_id="secret-gate", goal="Stay clean")
                store.save_state(initial)
                manager = StateManager(initial, store=store)

                # Mutation after construction must not bypass StateManager's
                # pre-digest check or advance its in-memory state.
                mutated_for_manager = CognitiveEvent(
                    id="manager-canary",
                    event_type=EventType.EVIDENCE,
                    source="security-test",
                    payload={"evidence": {"fact": "safe at construction"}},
                )
                mutated_for_manager.payload["transport"] = [
                    {"Authorization": canary}
                ]
                with self.assertRaises(SensitiveDataError) as manager_error:
                    manager.apply(mutated_for_manager)
                self.assertNotIn(canary, str(manager_error.exception))
                self.assertEqual(manager.version, 0)
                self.assertNotIn(
                    mutated_for_manager.id, manager.state.applied_event_digests
                )
                del mutated_for_manager.payload["transport"]
                expected_digest = hashlib.sha256(
                    mutated_for_manager.to_json().encode("utf-8")
                ).hexdigest()
                clean_state = manager.apply(mutated_for_manager)
                self.assertEqual(
                    clean_state.applied_event_digests[mutated_for_manager.id],
                    expected_digest,
                )

                # The durable queue has its own exact-JSON check for callers
                # that use SQLiteStore without going through StateManager.
                mutated_for_store = CognitiveEvent(
                    id="store-canary",
                    event_type=EventType.QUESTION,
                    source="security-test",
                    payload={"question": "safe at construction"},
                )
                mutated_for_store.payload["credentials"] = {"value": canary}
                with self.assertRaises(SensitiveDataError):
                    store.append_event(initial.project_id, mutated_for_store)

                # Direct snapshot writes are guarded too, including arbitrary
                # nested artifact metadata that did not originate in an event.
                unsafe_state = initial.copy()
                unsafe_state.artifacts["settings"] = {
                    "connection_string": canary
                }
                with self.assertRaises(SensitiveDataError):
                    store.save_state(unsafe_state)

                self.assertEqual(
                    store.load_events(initial.project_id), [mutated_for_manager]
                )

            connection = sqlite3.connect(database)
            try:
                persisted_json = "\n".join(
                    str(row[0])
                    for table, column in (
                        ("projects", "state_json"),
                        ("state_snapshots", "state_json"),
                        ("events", "event_json"),
                    )
                    for row in connection.execute(f"SELECT {column} FROM {table}")
                )
            finally:
                connection.close()
            self.assertNotIn(canary, persisted_json)
            for sqlite_file in Path(directory).glob("secret-gate.sqlite3*"):
                self.assertNotIn(canary.encode("utf-8"), sqlite_file.read_bytes())

    def test_restored_state_rejects_conflicting_applied_event_id(self) -> None:
        with SQLiteStore(":memory:") as store:
            initial = ProjectState(project_id="restored-collision")
            manager = StateManager(initial, store=store, recent_event_limit=0)
            applied = CognitiveEvent(
                id="durable-event-id",
                event_type=EventType.HYPOTHESIS,
                source="model",
                payload={"hypothesis": "Persisted payload"},
                state_version=0,
                created_at="2026-01-01T00:00:00Z",
            )
            manager.apply(applied)
            restored = StateManager.restore(
                store, initial.project_id, recent_event_limit=0
            )

            self.assertEqual(restored.apply(applied).version, 1)
            collision = CognitiveEvent(
                id=applied.id,
                event_type=EventType.HYPOTHESIS,
                source="model",
                payload={"hypothesis": "Conflicting payload"},
                state_version=0,
                created_at=applied.created_at,
            )
            with self.assertRaisesRegex(ValueError, "different digest"):
                restored.apply(collision)

    def test_persist_version_history_and_resume_pending_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "amca.sqlite3"
            initial = ProjectState(project_id="project-db", goal="Persist")
            with SQLiteStore(database) as store:
                store.save_state(initial)
                manager = StateManager(initial, store=store)
                event = CognitiveEvent(
                    id="applied-event",
                    event_type=EventType.HYPOTHESIS,
                    source="model",
                    payload={"hypothesis": "SQLite"},
                    state_version=0,
                )
                current = manager.apply(event)
                pending = CognitiveEvent(
                    id="pending-event",
                    event_type=EventType.QUESTION,
                    source="model",
                    payload={"question": "Which journal?"},
                    state_version=1,
                )
                store.append_event(initial.project_id, pending)
                self.assertEqual(store.snapshot_versions(initial.project_id), [0, 1])
                self.assertEqual(store.list_projects(), [initial.project_id])

            with SQLiteStore(database) as reopened:
                resumed = reopened.resume(initial.project_id)
                self.assertIsNotNone(resumed)
                restored, pending_events = resumed  # type: ignore[misc]
                self.assertEqual(restored, current)
                self.assertEqual(pending_events, [pending])
                self.assertEqual(reopened.load_state(initial.project_id, 0), initial)
                events = reopened.load_events(initial.project_id)
                self.assertEqual(events, [event, pending])
                restored_manager = StateManager.restore(reopened, initial.project_id)
                self.assertEqual(restored_manager.version, 1)

    def test_event_and_snapshot_writes_are_idempotent_but_not_mutable(self) -> None:
        with SQLiteStore(":memory:") as store:
            state = ProjectState(project_id="p")
            store.save_state(state)
            store.save_state(state)
            conflicting = state.copy()
            conflicting.goal = "Changed at same version"
            with self.assertRaises(ValueError):
                store.save_state(conflicting)

            pending = CognitiveEvent(
                id="same-id",
                event_type=EventType.QUESTION,
                source="a",
                payload={"question": "q"},
            )
            store.append_event("p", pending)
            store.append_event("p", pending)
            collision = CognitiveEvent(
                id="same-id",
                event_type=EventType.QUESTION,
                source="a",
                payload={"question": "different"},
            )
            with self.assertRaises(ValueError):
                store.append_event("p", collision)

    def test_failed_persistence_does_not_replace_manager_state(self) -> None:
        with SQLiteStore(":memory:") as store:
            state = ProjectState(project_id="p")
            store.save_state(state)
            manager = StateManager(state, store=store)
            # A different payload at an already-used durable ID forces rollback.
            used = CognitiveEvent(
                id="collision",
                event_type=EventType.QUESTION,
                source="a",
                payload={"question": "durable"},
            )
            store.append_event("p", used)
            incoming = CognitiveEvent(
                id="collision",
                event_type=EventType.HYPOTHESIS,
                source="a",
                payload={"hypothesis": "different"},
            )
            with self.assertRaises(ValueError):
                manager.apply(incoming)
            self.assertEqual(manager.version, 0)
            self.assertEqual(manager.state.hypotheses, [])


if __name__ == "__main__":
    unittest.main()
