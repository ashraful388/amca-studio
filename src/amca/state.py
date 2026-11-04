"""Deterministic, versioned reduction of cognitive events into project state."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any, Protocol

from .domain import (
    CheckpointStatus,
    CognitiveEvent,
    CollaborationMode,
    CriterionStatus,
    DecisionClass,
    DecisionRecord,
    Disagreement,
    EvidenceKind,
    EvidenceRecord,
    EvidenceStrength,
    EventType,
    ParticipantDisposition,
    ParticipantDispositionStatus,
    ProjectState,
    Severity,
    SynchronizationCheckpoint,
    SyncMode,
    VerificationStatus,
)


class StateVersionError(ValueError):
    """Raised when an event references an impossible or disallowed version."""


_NON_COGNITIVE_EVENT_TYPES = frozenset(
    {
        EventType.COST_UPDATE,
        EventType.MODEL_ERROR,
        EventType.SYNC_CHECKPOINT_OPENED,
        EventType.PARTICIPANT_DISPOSITION,
        EventType.SYNC_CHECKPOINT_CLOSED,
    }
)

_VERIFIED_EVIDENCE_PRODUCERS = frozenset(
    {
        "criterion-evaluator",
        "math-tool",
        "experiment-runner",
        "human-reviewer",
        "user",
    }
)


class StateStore(Protocol):
    def save_transition(self, state: ProjectState, event: CognitiveEvent) -> None: ...

    def load_state(
        self, project_id: str, version: int | None = None
    ) -> ProjectState | None: ...


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _text(payload: Mapping[str, Any], *keys: str) -> str | None:
    value = _first(payload, *keys)
    if value is None:
        return None
    return str(value)


def _append_unique(items: list[Any], value: Any) -> None:
    if value is not None and value not in items:
        items.append(value)


def _event_digest(event: CognitiveEvent) -> str:
    """Return a stable digest of the complete persisted event envelope."""

    return hashlib.sha256(event.to_json().encode("utf-8")).hexdigest()


def _content_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class StateManager:
    """Own and advance one canonical :class:`ProjectState`.

    Events are idempotent by ``event.id``. By default a late event may be
    integrated (its contribution records ``stale=True``), while an event from a
    future state is rejected. Set ``strict_versions=True`` to require an exact
    version match. Every accepted event advances the canonical version once.
    """

    def __init__(
        self,
        state: ProjectState | None = None,
        *,
        project_id: str | None = None,
        goal: str = "",
        store: StateStore | None = None,
        strict_versions: bool = False,
        recent_event_limit: int = 100,
    ) -> None:
        if state is None:
            if not project_id:
                raise ValueError("state or project_id is required")
            state = ProjectState(project_id=project_id, goal=goal)
        if project_id is not None and project_id != state.project_id:
            raise ValueError("project_id does not match the supplied state")
        if recent_event_limit < 0:
            raise ValueError("recent_event_limit must be non-negative")
        self._state = state.copy()
        self.store = store
        self.strict_versions = strict_versions
        self.recent_event_limit = recent_event_limit
        # Backfill digests available in older snapshots without changing their
        # construction contract. Older events can be recovered from a durable
        # store on demand when their IDs are seen again.
        for event in self._state.recent_events:
            if event.id in self._state.applied_event_ids:
                self._state.applied_event_digests.setdefault(
                    event.id, _event_digest(event)
                )

    @property
    def state(self) -> ProjectState:
        """Return an isolated copy so mutation cannot bypass version reduction."""

        return self.snapshot()

    @property
    def version(self) -> int:
        return self._state.version

    def snapshot(self) -> ProjectState:
        return self._state.copy()

    @classmethod
    def restore(
        cls,
        store: StateStore,
        project_id: str,
        *,
        version: int | None = None,
        strict_versions: bool = False,
        recent_event_limit: int = 100,
    ) -> StateManager:
        """Restore a manager from the latest or a selected SQLite snapshot."""

        state = store.load_state(project_id, version)
        if state is None:
            suffix = "latest" if version is None else str(version)
            raise KeyError(f"no {suffix} snapshot for project {project_id!r}")
        return cls(
            state,
            store=store,
            strict_versions=strict_versions,
            recent_event_limit=recent_event_limit,
        )

    def apply(
        self,
        event: CognitiveEvent,
        *,
        persist: bool | None = None,
    ) -> ProjectState:
        """Reduce one event and return an isolated snapshot of the new state."""

        if not isinstance(event, CognitiveEvent):
            raise TypeError("StateManager accepts CognitiveEvent instances only")
        # The digest serialization is also the fail-closed persistence gate;
        # unsafe mutable payloads are rejected before reduction begins.
        event_digest = _event_digest(event)
        if event.id in self._state.applied_event_ids:
            known_digest = self._state.applied_event_digests.get(event.id)
            if known_digest is None and self.store is not None:
                load_event = getattr(self.store, "load_event", None)
                if callable(load_event):
                    stored_event = load_event(event.id)
                    if stored_event is not None:
                        known_digest = _event_digest(stored_event)
            if known_digest is None:
                raise ValueError(
                    f"event id {event.id!r} was already applied, but its legacy "
                    "snapshot has no digest available for safe idempotency checking"
                )
            if known_digest != event_digest:
                raise ValueError(
                    f"event id {event.id!r} was already applied with a different digest"
                )
            return self.snapshot()
        if event.state_version > self._state.version:
            raise StateVersionError(
                f"event starts at future version {event.state_version}; "
                f"current version is {self._state.version}"
            )
        if self.strict_versions and event.state_version != self._state.version:
            raise StateVersionError(
                f"event version {event.state_version} does not match current "
                f"version {self._state.version}"
            )

        candidate = self._state.copy()
        self._reduce(candidate, event)
        next_version = self._state.version + 1
        candidate.model_contributions.append(
            {
                "event_id": event.id,
                "source": event.source,
                "event_type": event.event_type.value,
                "start_state_version": event.state_version,
                "integrated_state_version": next_version,
                "stale": event.state_version < self._state.version,
                "target": event.target,
            }
        )
        candidate.version = next_version
        if event.event_type not in _NON_COGNITIVE_EVENT_TYPES:
            candidate.cognitive_revision = self._state.cognitive_revision + 1
        candidate.updated_at = event.created_at
        candidate.applied_event_ids.append(event.id)
        candidate.applied_event_digests[event.id] = event_digest
        candidate.recent_events.append(CognitiveEvent.from_dict(event.to_dict()))
        if self.recent_event_limit == 0:
            candidate.recent_events.clear()
        elif len(candidate.recent_events) > self.recent_event_limit:
            del candidate.recent_events[: -self.recent_event_limit]
        self._refresh_goal(candidate)

        should_persist = self.store is not None if persist is None else persist
        if should_persist:
            if self.store is None:
                raise RuntimeError("persist=True but no state store is configured")
            self.store.save_transition(candidate, event)
        self._state = candidate
        return self.snapshot()

    apply_event = apply

    def save(self) -> None:
        """Persist the current snapshot without recording a new event."""

        if self.store is None:
            raise RuntimeError("no state store is configured")
        save_state = getattr(self.store, "save_state", None)
        if not callable(save_state):
            raise TypeError("configured store does not implement save_state")
        save_state(self._state)

    @staticmethod
    def reduce(state: ProjectState, event: CognitiveEvent) -> ProjectState:
        """Pure convenience reducer; the input state is not modified."""

        return StateManager(state).apply(event, persist=False)

    def _reduce(self, state: ProjectState, event: CognitiveEvent) -> None:
        payload = event.payload
        kind = event.event_type

        if kind is EventType.HYPOTHESIS:
            _append_unique(
                state.hypotheses,
                _text(payload, "hypothesis", "claim", "text", "content"),
            )
        elif kind is EventType.QUESTION:
            _append_unique(
                state.open_questions, _text(payload, "question", "text", "content")
            )
        elif kind is EventType.ALTERNATIVE:
            _append_unique(
                state.pending_ideas,
                _text(payload, "alternative", "idea", "text", "content"),
            )
        elif kind is EventType.EQUATION_UPDATE:
            _append_unique(
                state.equations, _text(payload, "equation", "text", "content")
            )
        elif kind is EventType.EVIDENCE:
            self._record_evidence(state, event)
        elif kind is EventType.TOOL_RESULT:
            if event.source not in {"math-tool", "experiment-runner"}:
                raise ValueError("tool results require an authorized executor")
            self._record_tool_result(state, event)
        elif kind is EventType.EXPERIMENT_RESULT:
            _append_unique(state.experimental_results, payload.get("result", payload))
        elif kind in (
            EventType.OBJECTION,
            EventType.CRITICAL_CONFLICT,
            EventType.BLOCKER,
        ):
            self._record_disagreement(state, event)
        elif kind is EventType.CONSENSUS:
            self._record_consensus(state, event)
        elif kind is EventType.CORRECTION:
            rejected = _text(payload, "rejected", "old", "incorrect")
            corrected = _text(payload, "corrected", "new", "text", "content")
            _append_unique(state.rejected_ideas, rejected)
            _append_unique(state.accepted_facts, corrected)
        elif kind in (
            EventType.ARCHITECTURE_PROPOSAL,
            EventType.NOVELTY_ALERT,
            EventType.EXPERIMENT_REQUEST,
            EventType.TOOL_REQUEST,
        ):
            _append_unique(
                state.pending_ideas,
                _text(payload, "proposal", "idea", "request", "text", "content")
                or str(payload),
            )
        elif kind is EventType.FOCUS_CHANGE:
            focus = _text(payload, "current_focus", "focus", "text")
            subproblem = _text(payload, "active_subproblem", "subproblem")
            if focus is not None:
                state.current_focus = focus
            if subproblem is not None:
                state.active_subproblem = subproblem
        elif kind is EventType.MODE_SWITCH:
            mode = _first(payload, "mode", "collaboration_mode")
            if mode is None:
                raise ValueError("mode switch event requires a mode")
            state.current_collaboration_mode = CollaborationMode(mode)
            models = payload.get("participating_models")
            if models is not None:
                state.participating_models = [str(model) for model in models]
        elif kind is EventType.SYNC_CHANGE:
            mode = _first(payload, "mode", "sync_mode")
            if mode is None:
                raise ValueError("sync change event requires a mode")
            state.current_sync_mode = SyncMode(mode)
        elif kind is EventType.SYNC_CHECKPOINT_OPENED:
            if event.source not in {"synchronization-controller", "user"}:
                raise ValueError(
                    "synchronization checkpoints require an authorized controller"
                )
            self._open_checkpoint(state, event)
        elif kind is EventType.PARTICIPANT_DISPOSITION:
            if event.source not in {"synchronization-controller", "user"}:
                raise ValueError(
                    "participant dispositions require an authorized controller"
                )
            self._record_participant_disposition(state, event)
        elif kind is EventType.SYNC_CHECKPOINT_CLOSED:
            if event.source not in {"synchronization-controller", "user"}:
                raise ValueError(
                    "synchronization checkpoints require an authorized controller"
                )
            self._close_checkpoint(state, event)
        elif kind is EventType.GOAL_PROGRESS:
            if event.source not in {"criterion-evaluator", "goal-manager", "user"}:
                raise ValueError(
                    "goal progress may only come from an authorized evaluator, "
                    "goal manager, or user"
                )
            self._record_goal_progress(state, event)
        elif kind is EventType.COST_UPDATE:
            allowed = {
                "model",
                "input_tokens",
                "output_tokens",
                "cached_tokens",
                "api_cost_usd",
                "tool_cost_usd",
                "local_compute_cost_usd",
                "local_compute_seconds",
                "latency_seconds",
                "calls",
                "api_calls",
                "local_calls",
            }
            state.cost_state.record_usage(
                **{key: value for key, value in payload.items() if key in allowed}
            )
        elif kind is EventType.USER_INSTRUCTION:
            _append_unique(
                state.user_constraints,
                _text(payload, "instruction", "constraint", "text"),
            )
        elif kind is EventType.ARTIFACT_UPDATE:
            self._record_artifact(state, payload)
        elif kind is EventType.STATE_UPDATE:
            self._apply_state_update(state, payload)
        elif kind is EventType.MODEL_ERROR:
            state.status = "degraded"

    @staticmethod
    def _put_evidence(state: ProjectState, record: EvidenceRecord) -> None:
        existing = state.evidence_records.get(record.id)
        if existing is not None:
            if existing.to_dict() != record.to_dict():
                raise ValueError(
                    f"evidence id {record.id!r} already identifies a different record"
                )
            return
        state.evidence_records[record.id] = record
        _append_unique(state.evidence, record.to_dict())

    @classmethod
    def _record_evidence(cls, state: ProjectState, event: CognitiveEvent) -> None:
        payload = event.payload
        provided = payload.get("record", payload.get("evidence", payload))
        if isinstance(provided, Mapping):
            data = dict(provided)
            if not data.get("summary"):
                summary = _text(data, "content", "result", "claim", "text")
                data["summary"] = summary or str(dict(data))
        else:
            data = {
                "summary": str(provided),
                "kind": EvidenceKind.OBSERVATION.value,
                "value": provided,
            }
        data.setdefault("id", event.id)
        data.setdefault("created_at", event.created_at)
        # A semantic model cannot convert an assertion into verified evidence by
        # writing a field in JSON. Verification authority lives at the reducer
        # boundary and the event source is always assigned by the caller/kernel.
        data["producer"] = event.source
        if event.source not in _VERIFIED_EVIDENCE_PRODUCERS:
            data["verification_status"] = VerificationStatus.UNVERIFIED.value
        else:
            data.setdefault(
                "verification_status", VerificationStatus.UNVERIFIED.value
            )
        record = EvidenceRecord.from_dict(data)
        cls._put_evidence(state, record)

    @classmethod
    def _record_tool_result(cls, state: ProjectState, event: CognitiveEvent) -> None:
        raw_result = event.payload.get("result", event.payload)
        if not isinstance(raw_result, Mapping):
            raise ValueError("tool result must be an object")
        result = dict(raw_result)
        _append_unique(state.experimental_results, result)
        request_event_id = str(result.get("request_event_id") or event.causation_id or "")
        evidence_id = str(result.get("evidence_id") or f"tool:{request_event_id or event.id}")
        ok = result.get("ok") is True
        trusted = event.source in {"math-tool", "experiment-runner"}
        addresses = result.get("addresses_disagreement_ids", [])
        supports = result.get("supports_criterion_ids", [])
        if not isinstance(addresses, list) or not isinstance(supports, list):
            raise ValueError("tool evidence links must be arrays")
        digest = _content_digest(result)
        record = EvidenceRecord(
            id=evidence_id,
            summary=(
                f"Deterministic tool result: {result.get('output')}"
                if ok
                else f"Tool execution failed: {result.get('error', 'unknown error')}"
            ),
            kind=EvidenceKind.TOOL_RESULT,
            provenance={
                "source": event.source,
                "locator": request_event_id or event.id,
                "timestamp": event.created_at,
                "content_digest": digest,
            },
            method=str(result.get("operation") or "tool execution"),
            supports_criterion_ids=[str(item) for item in supports],
            addresses_disagreement_ids=[str(item) for item in addresses],
            strength=(
                EvidenceStrength.STRONG
                if ok and trusted
                else EvidenceStrength.WEAK
            ),
            verification_status=(
                VerificationStatus.VERIFIED
                if ok and trusted
                else (
                    VerificationStatus.UNVERIFIED
                    if ok
                    else VerificationStatus.FAILED
                )
            ),
            value=result.get("output"),
            producer=event.source,
            created_at=event.created_at,
        )
        cls._put_evidence(state, record)

    @staticmethod
    def _open_checkpoint(state: ProjectState, event: CognitiveEvent) -> None:
        if state.active_checkpoint_id is not None:
            active = state.synchronization_checkpoints.get(
                state.active_checkpoint_id
            )
            if active is not None and active.status is CheckpointStatus.OPEN:
                raise ValueError("a synchronization checkpoint is already open")
        raw = event.payload.get("checkpoint", event.payload)
        if not isinstance(raw, Mapping):
            raise ValueError("checkpoint payload must be an object")
        data = dict(raw)
        data.setdefault("id", event.id)
        data.setdefault("opened_at", event.created_at)
        data.setdefault("opened_state_version", state.version + 1)
        data["status"] = CheckpointStatus.OPEN.value
        data["passed"] = False
        data["dispositions"] = {}
        checkpoint = SynchronizationCheckpoint.from_dict(data)
        if checkpoint.id in state.synchronization_checkpoints:
            raise ValueError(f"checkpoint id {checkpoint.id!r} already exists")
        state.synchronization_checkpoints[checkpoint.id] = checkpoint
        state.active_checkpoint_id = checkpoint.id

    @staticmethod
    def _record_participant_disposition(
        state: ProjectState, event: CognitiveEvent
    ) -> None:
        checkpoint_id = _text(event.payload, "checkpoint_id")
        if not checkpoint_id:
            checkpoint_id = state.active_checkpoint_id
        if not checkpoint_id:
            raise ValueError("participant disposition requires a checkpoint")
        checkpoint = state.synchronization_checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise KeyError(f"unknown synchronization checkpoint {checkpoint_id!r}")
        raw = event.payload.get("disposition", event.payload)
        if not isinstance(raw, Mapping):
            raise ValueError("participant disposition must be an object")
        data = dict(raw)
        data.setdefault("created_at", event.created_at)
        disposition = ParticipantDisposition.from_dict(data)
        if (
            disposition.status is ParticipantDispositionStatus.RELEASED
            and event.source != "user"
        ):
            raise ValueError(
                "participant release requires an explicit user-origin authorization event"
            )
        checkpoint.record(disposition, at=event.created_at)

    @staticmethod
    def _close_checkpoint(state: ProjectState, event: CognitiveEvent) -> None:
        checkpoint_id = _text(event.payload, "checkpoint_id")
        if not checkpoint_id:
            checkpoint_id = state.active_checkpoint_id
        if not checkpoint_id:
            raise ValueError("checkpoint close requires a checkpoint ID")
        checkpoint = state.synchronization_checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise KeyError(f"unknown synchronization checkpoint {checkpoint_id!r}")
        expired = checkpoint.is_expired(event.created_at)
        actual = checkpoint.can_pass(
            has_critical_objection=state.has_critical_objection,
            at=event.created_at,
        )
        requested = bool(event.payload.get("passed", actual))
        if requested and not actual:
            raise ValueError(
                "checkpoint cannot be marked passed by its dispositions and objections"
            )
        checkpoint.close(
            passed=requested,
            reason=str(
                event.payload.get(
                    "reason",
                    "checkpoint deadline expired" if expired else "",
                )
            ),
            closed_state_version=state.version + 1,
            closed_at=event.created_at,
            expired=expired,
        )
        if state.active_checkpoint_id == checkpoint_id:
            state.active_checkpoint_id = None

    @staticmethod
    def _record_disagreement(state: ProjectState, event: CognitiveEvent) -> None:
        payload = event.payload
        provided = payload.get("disagreement")
        if isinstance(provided, Mapping):
            data = dict(provided)
        else:
            data = dict(payload)
        data.setdefault("topic", event.target or _text(data, "topic", "claim") or "")
        data.setdefault(
            "description",
            _text(data, "description", "objection", "text", "content") or "",
        )
        data.setdefault("raised_by", event.source)
        if not data.get("id"):
            data["id"] = event.id
        if not data.get("opened_at"):
            data["opened_at"] = event.created_at
        if event.event_type is EventType.CRITICAL_CONFLICT:
            data["severity"] = Severity.CRITICAL.value
        else:
            data.setdefault("severity", event.severity.value)
        disagreement = Disagreement.from_dict(data)
        existing = next(
            (item for item in state.active_disagreements if item.id == disagreement.id),
            None,
        )
        is_critical = disagreement.severity is Severity.CRITICAL
        if existing is None:
            state.active_disagreements.append(disagreement)
        else:
            is_critical = is_critical or existing.severity is Severity.CRITICAL
            existing.description = disagreement.description or existing.description
            existing.positions.update(disagreement.positions)
            for evidence in disagreement.evidence:
                _append_unique(existing.evidence, evidence)
            for evidence_id in disagreement.evidence_ids:
                _append_unique(existing.evidence_ids, evidence_id)
            existing.discriminating_action = (
                disagreement.discriminating_action
                or existing.discriminating_action
            )
            if existing.severity is not Severity.CRITICAL:
                existing.severity = disagreement.severity
            existing.reopen()
        if is_critical and state.goal_contract:
            for criterion in state.goal_contract.criteria:
                if criterion.required and criterion.satisfied:
                    criterion.reopen(status=CriterionStatus.BLOCKED)

    @staticmethod
    def _record_consensus(state: ProjectState, event: CognitiveEvent) -> None:
        payload = event.payload
        disagreement_id = _text(payload, "disagreement_id", "id")
        if disagreement_id:
            disagreement = next(
                (
                    item
                    for item in state.active_disagreements
                    if item.id == disagreement_id
                ),
                None,
            )
            if disagreement is None:
                raise KeyError(f"unknown disagreement {disagreement_id!r}")
            resolution = (
                _text(payload, "resolution", "text", "content") or "Resolved"
            )
            evidence_values: list[str] = []
            evidence = _text(payload, "evidence", "evidence_reference")
            if evidence:
                evidence_values.append(evidence)
            for key in ("evidence_ids", "evidence_refs"):
                references = payload.get(key, [])
                if isinstance(references, list):
                    evidence_values.extend(str(item) for item in references if item)
            evidence_values = list(dict.fromkeys(evidence_values))
            evidence_records = [
                state.evidence_records[item]
                for item in evidence_values
                if item in state.evidence_records
            ]
            if disagreement.severity is Severity.CRITICAL and not any(
                record.usable_for_critical_disagreement(disagreement.id)
                for record in evidence_records
            ):
                raise ValueError(
                    "critical disagreement resolution requires verified, "
                    "provenance-bearing evidence that explicitly addresses it"
                )
            disagreement.resolve(
                resolution,
                evidence_values[0] if evidence_values else None,
                at=event.created_at,
                evidence_ids=[record.id for record in evidence_records],
                decision_id=_text(payload, "decision_id"),
            )
            for item in evidence_values[1:]:
                _append_unique(disagreement.evidence, item)
        accepted_fact = _text(payload, "accepted_fact", "fact")
        _append_unique(state.accepted_facts, accepted_fact)

    @staticmethod
    def _record_goal_progress(
        state: ProjectState,
        event: CognitiveEvent,
    ) -> None:
        payload = event.payload
        if state.goal_contract is None:
            raise ValueError("goal progress requires a goal contract")
        criterion_id = _text(payload, "criterion_id", "id")
        if criterion_id:
            criterion = state.goal_contract.criterion(criterion_id)
            if criterion is None:
                raise KeyError(f"unknown goal criterion {criterion_id!r}")
            if bool(payload.get("satisfied", True)):
                if criterion.satisfied:
                    raise ValueError(f"goal criterion {criterion_id!r} is already satisfied")
                raw_decision = payload.get("decision")
                if not isinstance(raw_decision, Mapping):
                    raise ValueError(
                        "satisfied goal progress requires an evaluator decision record"
                    )
                data = dict(raw_decision)
                data.setdefault("id", event.id)
                data.setdefault("created_at", event.created_at)
                data.setdefault("criterion_id", criterion_id)
                data.setdefault("decision_class", criterion.decision_class.value)
                decision = DecisionRecord.from_dict(data)
                if not decision.passed:
                    raise ValueError("a passing criterion requires a passing decision")
                if decision.criterion_id != criterion_id:
                    raise ValueError("decision criterion does not match goal progress")
                if decision.decision_class is not criterion.decision_class:
                    raise ValueError("decision class does not match the criterion gate")
                checkpoint = state.synchronization_checkpoints.get(
                    decision.checkpoint_id
                )
                if checkpoint is None:
                    raise KeyError(
                        f"unknown synchronization checkpoint {decision.checkpoint_id!r}"
                    )
                if not checkpoint.permits(decision.decision_class):
                    raise ValueError(
                        "synchronization checkpoint does not permit this decision class"
                    )
                actual_dispositions = {
                    participant_id: disposition.status.value
                    for participant_id, disposition in checkpoint.dispositions.items()
                }
                if decision.dispositions != actual_dispositions:
                    raise ValueError(
                        "decision dispositions do not match the checkpoint record"
                    )
                if len(decision.evidence_ids) < criterion.evidence_requirement:
                    raise ValueError("criterion evidence requirement is not met")
                records: list[EvidenceRecord] = []
                for evidence_id in decision.evidence_ids:
                    record = state.evidence_records.get(evidence_id)
                    if record is None:
                        raise KeyError(f"unknown evidence record {evidence_id!r}")
                    if not record.verified or not record.has_provenance:
                        raise ValueError(
                            "criterion evidence must be verified and provenance-bearing"
                        )
                    if record.strength not in {
                        EvidenceStrength.MODERATE,
                        EvidenceStrength.STRONG,
                    }:
                        raise ValueError(
                            "criterion evidence must have at least moderate strength"
                        )
                    if criterion_id not in record.supports_criterion_ids:
                        raise ValueError(
                            "criterion evidence must explicitly support the criterion"
                        )
                    records.append(record)
                critical_disagreements = [
                    item
                    for item in state.active_disagreements
                    if item.severity is Severity.CRITICAL
                ]
                if any(not item.resolved for item in critical_disagreements):
                    raise ValueError(
                        "unresolved critical disagreement blocks a criterion decision"
                    )
                if any(
                    not any(
                        evidence_id in state.evidence_records
                        and state.evidence_records[
                            evidence_id
                        ].usable_for_critical_disagreement(item.id)
                        for evidence_id in item.evidence_ids
                    )
                    for item in critical_disagreements
                ):
                    raise ValueError(
                        "resolved critical disagreements require retained verified evidence"
                    )
                expected_critical = {
                    item.id: "resolved" for item in critical_disagreements
                }
                if decision.critical_disagreement_dispositions != expected_critical:
                    raise ValueError(
                        "decision must record every critical disagreement disposition"
                    )
                if (
                    decision.decision_class is DecisionClass.IRREVERSIBLE
                    and (event.source != "user" or not decision.authorized_by)
                ):
                    raise ValueError(
                        "irreversible decisions require explicit user authorization"
                    )
                if decision.id in state.decision_records:
                    raise ValueError(f"decision id {decision.id!r} already exists")
                state.decision_records[decision.id] = decision
                criterion.mark_satisfied(
                    "; ".join(record.summary for record in records),
                    at=event.created_at,
                    evidence_ids=decision.evidence_ids,
                    decision_id=decision.id,
                )
            else:
                criterion.reopen(status=CriterionStatus.FAIL)
        if "progress" in payload:
            progress = float(payload["progress"])
            if not 0.0 <= progress <= 1.0:
                raise ValueError("progress must be between 0.0 and 1.0")
            state.progress = progress

    @staticmethod
    def _record_artifact(state: ProjectState, payload: Mapping[str, Any]) -> None:
        artifact_id = _text(payload, "artifact_id", "id", "name", "path")
        if not artifact_id:
            raise ValueError("artifact update requires artifact_id, id, name, or path")
        update = dict(payload)
        previous = state.artifacts.get(artifact_id)
        if previous is not None and "previous" not in update:
            update["previous"] = previous
        state.artifacts[artifact_id] = update

    @staticmethod
    def _apply_state_update(state: ProjectState, payload: Mapping[str, Any]) -> None:
        """Apply an explicit allow-listed ``set``/``append`` patch."""

        scalar_fields = {
            "goal",
            "current_focus",
            "active_subproblem",
            "progress",
            "status",
            "complexity",
            "privacy_mode",
            "compute_policy",
            "current_collaboration_mode",
            "current_sync_mode",
        }
        list_fields = {
            "accepted_facts",
            "assumptions",
            "hypotheses",
            "equations",
            "evidence",
            "experimental_results",
            "rejected_ideas",
            "pending_ideas",
            "open_questions",
            "user_constraints",
            "participating_models",
        }
        set_values = payload.get("set", {})
        append_values = payload.get("append", {})
        if not isinstance(set_values, Mapping) or not isinstance(append_values, Mapping):
            raise ValueError("state update set and append values must be objects")
        for field_name, value in set_values.items():
            if field_name not in scalar_fields and field_name not in list_fields:
                raise ValueError(f"state field {field_name!r} cannot be updated")
            if field_name in list_fields:
                if not isinstance(value, list):
                    raise ValueError(f"state field {field_name!r} requires a list")
                setattr(state, field_name, list(value))
            elif field_name == "progress":
                progress = float(value)
                if not 0.0 <= progress <= 1.0:
                    raise ValueError("progress must be between 0.0 and 1.0")
                state.progress = progress
            elif field_name == "current_collaboration_mode":
                state.current_collaboration_mode = CollaborationMode(value)
            elif field_name == "current_sync_mode":
                state.current_sync_mode = SyncMode(value)
            elif field_name == "complexity":
                state.complexity = type(state.complexity)(value)
            elif field_name == "privacy_mode":
                state.privacy_mode = type(state.privacy_mode)(value)
            elif field_name == "compute_policy":
                state.compute_policy = type(state.compute_policy)(value)
            else:
                setattr(state, field_name, str(value))
        for field_name, values in append_values.items():
            if field_name not in list_fields:
                raise ValueError(f"state field {field_name!r} cannot be appended")
            if not isinstance(values, list):
                values = [values]
            target = getattr(state, field_name)
            for value in values:
                _append_unique(target, value)

    @staticmethod
    def _refresh_goal(state: ProjectState) -> None:
        if state.goal_contract is None:
            return
        state.progress = state.goal_contract.progress
        satisfied = state.goal_satisfied
        if satisfied and state.status in {"active", "degraded", "complete"}:
            state.status = "complete"
        elif not satisfied and state.status == "complete":
            state.status = "active"


__all__ = ["StateManager", "StateVersionError"]
