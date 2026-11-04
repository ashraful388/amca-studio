"""R2.5 model-backed research planning with a policy validator.

Replaces the fixed-rule planner limitation with an adaptive loop while
keeping every safety boundary intact (manager-reply61):

    Research State -> Planner Model -> Candidate Actions
        -> Policy Validator -> Tool Fabric Approval -> Execution

Frozen constraints:
- The planner model NEVER executes tools directly. It only PROPOSES;
  execution still flows exclusively through the orchestrator's gated
  dispatch (Tool Fabric decides network actions, approvals stay external,
  hard limits are enforced by the orchestrator regardless of proposals).
- Every model candidate is validated against the CLOSED action set, strict
  per-action argument schemas and the run's remaining limits. Rejections
  carry reasons and are retained in an inspectable planning trail.
- The planner cannot create approvals: argument schemas have no field that
  could authorize anything, and forbidden keys are rejected outright.
- When no model is configured, the model fails, or every candidate is
  rejected, the adaptive planner falls back to the accepted deterministic
  baseline and says so truthfully (planning_source = fallback).
"""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from amca.research_orchestrator import (
    InvalidProposal,
    ReasonCode,
    ResearchAction,
    ResearchActionProposal,
    ResearchLimits,
)

MAX_QUERY_LENGTH = 512
MAX_TEXT_LENGTH = 2000
MAX_ACTIONS_PER_TURN = 8
MAX_STATE_CHARS = 60_000

ALLOWED_ACTIONS = frozenset({
    ResearchAction.SEARCH_WEB,
    ResearchAction.FETCH_RESULT,
    ResearchAction.SEARCH_LOCAL,
    ResearchAction.CREATE_OR_REFINE_QUESTION,
    ResearchAction.LINK_EVIDENCE,
    ResearchAction.CREATE_OR_UPDATE_CLAIM,
    ResearchAction.SYNTHESIZE,
    ResearchAction.STOP,
})

#: Per-action STRICT argument allowlists (unknown key => rejection).
ARGUMENT_SCHEMAS: dict[ResearchAction, tuple[tuple[str, type, bool], ...]] = {
    ResearchAction.SEARCH_WEB: (
        ("query", str, True), ("max_results", int, False)),
    ResearchAction.FETCH_RESULT: (
        ("search_result_id", str, True), ("refresh", bool, False),
        ("refresh_reason", str, False)),
    ResearchAction.SEARCH_LOCAL: (
        ("query", str, True), ("retrieval_mode", str, False)),
    ResearchAction.CREATE_OR_REFINE_QUESTION: (
        ("question", str, True), ("purpose", str, False),
        ("parent_question_id", str, False)),
    ResearchAction.LINK_EVIDENCE: (
        ("claim_id", str, True), ("evidence_id", str, True),
        ("relation", str, True)),
    ResearchAction.CREATE_OR_UPDATE_CLAIM: (
        ("claim_text", str, True), ("claim_type", str, False),
        ("question_id", str, False)),
    ResearchAction.SYNTHESIZE: (("plan_id", str, False),),
    ResearchAction.STOP: (),
}

#: Keys that would smuggle authorization into a proposal. A proposal can
#: never grant approvals, pick executions or alter policy.
FORBIDDEN_ARGUMENT_KEYS = frozenset({
    "approve", "approved", "approval", "approval_reference",
    "authorization", "authorized", "decision", "execution_id",
    "policy", "tool_request_id", "bypass", "force",
})

DEFAULT_REASON_BY_ACTION = {
    ResearchAction.SEARCH_WEB: ReasonCode.NO_EVIDENCE,
    ResearchAction.SEARCH_LOCAL: ReasonCode.NO_EVIDENCE,
    ResearchAction.FETCH_RESULT: ReasonCode.NEED_PRIMARY_SOURCE,
    ResearchAction.LINK_EVIDENCE: ReasonCode.NO_EVIDENCE,
    ResearchAction.CREATE_OR_REFINE_QUESTION: ReasonCode.UNANSWERED_SUBQUESTION,
    ResearchAction.CREATE_OR_UPDATE_CLAIM: ReasonCode.NO_EVIDENCE,
    ResearchAction.SYNTHESIZE: ReasonCode.READY_TO_SYNTHESIZE,
    ResearchAction.STOP: ReasonCode.LIMIT_REACHED,
}

VALID_RELATIONS = {"supports", "contradicts", "context", "qualifies"}
VALID_RETRIEVAL_MODES = {"lexical", "semantic", "hybrid"}
VALID_CLAIM_TYPES = {"factual", "definition", "causal", "comparative",
                     "temporal", "quantitative", "procedural",
                     "evaluative", "predictive"}


class PlannerModelError(RuntimeError):
    """Planner model unavailable/malformed; fallback remains usable."""


class PlanningTrailStore:
    """Durable planning-intelligence records in canonical amca.db.

    R2.5.1 (manager-reply62): planning trails become OBSERVABLE and
    REPRODUCIBLE, not just intelligent. Everything here is DERIVED audit
    data - deleting it never touches sources, snapshots, evidence,
    citations, claims or synthesis."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS research_planning_turns (
                    turn_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    model_provider_id TEXT,
                    model_model_id TEXT,
                    planning_source TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, turn_index)
                );
                CREATE TABLE IF NOT EXISTS research_planning_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    step_number INTEGER,
                    candidate_index INTEGER NOT NULL,
                    model_provider_id TEXT,
                    model_model_id TEXT,
                    candidate_json TEXT NOT NULL,
                    validator_result TEXT NOT NULL,
                    rejection_reason TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_retrieval_metrics (
                    project_id TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    value INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (project_id, metric)
                );
            """)
            # R2.6: durable mode per planning turn (mode replay).
            try:
                connection.execute(
                    "ALTER TABLE research_planning_turns ADD COLUMN mode TEXT")
            except sqlite3.OperationalError:
                pass

    def save_turn(self, *, run_id: str, turn_index: int,
                  model: dict[str, Any] | None,
                  planning_source: str, state: dict[str, Any],
                  candidates: list[dict[str, Any]],
                  step_number: int | None = None,
                  mode: str | None = None) -> str:
        """Persist one planning turn + its per-candidate events."""
        now = _now()
        provider_id = (model or {}).get("provider_id")
        model_id = (model or {}).get("model_id")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM research_planning_turns "
                "WHERE run_id = ? AND turn_index = ?",
                (run_id, turn_index)).fetchone()
            if existing is not None:
                return f"turn-{run_id}-{turn_index}"
            connection.execute(
                """INSERT INTO research_planning_turns
                   (turn_id, run_id, turn_index, model_provider_id,
                    model_model_id, planning_source, state_json, created_at,
                    mode)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_uuid("pturn"), run_id, turn_index, provider_id, model_id,
                 planning_source,
                 json.dumps(state, sort_keys=True, default=str), now, mode))
            for index, candidate in enumerate(candidates):
                raw = candidate.get("candidate")
                verdict = str(candidate.get("verdict", "rejected"))
                reason = candidate.get("reason")
                if verdict == "accepted" and isinstance(raw, dict):
                    raw = {**raw, "_validated": True}
                connection.execute(
                    """INSERT INTO research_planning_events
                       (event_id, run_id, turn_index, step_number,
                        candidate_index, model_provider_id, model_model_id,
                        candidate_json, validator_result, rejection_reason,
                        created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (_uuid("pev"), run_id, turn_index, step_number, index,
                     provider_id, model_id,
                     json.dumps(raw if isinstance(raw, dict) else {"raw": raw},
                                sort_keys=True, default=str),
                     verdict, reason, now))
        return f"turn-{run_id}-{turn_index}"

    def turns_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_planning_turns WHERE run_id = ? "
                "ORDER BY turn_index", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def events_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_planning_events WHERE run_id = ? "
                "ORDER BY turn_index, candidate_index", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def delete_run_planning(self, run_id: str) -> int:
        """Derived-data deletion ONLY."""
        with self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM research_planning_events WHERE run_id = ?",
                (run_id,)).rowcount
            connection.execute(
                "DELETE FROM research_planning_turns WHERE run_id = ?",
                (run_id,))
        return deleted

    def _bump(self, project_id: str, metric: str, amount: int = 1) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS research_retrieval_metrics (
                       project_id TEXT NOT NULL,
                       metric TEXT NOT NULL,
                       value INTEGER NOT NULL DEFAULT 0,
                       PRIMARY KEY (project_id, metric))""")
            connection.execute(
                """INSERT INTO research_retrieval_metrics
                   (project_id, metric, value) VALUES (?, ?, ?)
                   ON CONFLICT(project_id, metric)
                   DO UPDATE SET value = value + excluded.value""",
                (project_id, metric, int(amount)))

    def metrics(self, project_id: str) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT metric, value FROM research_retrieval_metrics "
                "WHERE project_id = ?", (project_id,)).fetchall()
        return {row["metric"]: int(row["value"]) for row in rows}


def replay_run(trail_store: PlanningTrailStore, run_id: str) \
        -> list[dict[str, Any]]:
    """Tool-free planner replay for one research run.

    Reconstructs, per turn: the bounded state snapshot the model saw, every
    candidate proposal, its verdict and rejection reason. NOTHING is
    executed - replay only reads persisted audit rows."""
    replay: list[dict[str, Any]] = []
    for turn in trail_store.turns_for_run(run_id):
        candidates = [
            {
                "candidate_index": event["candidate_index"],
                "proposal": json.loads(event["candidate_json"]),
                "validator_result": event["validator_result"],
                "rejection_reason": event["rejection_reason"],
            }
            for event in trail_store.events_for_run(run_id)
            if event["turn_index"] == turn["turn_index"]
        ]
        replay.append({
            "run_id": run_id,
            "turn_index": turn["turn_index"],
            "mode": turn.get("mode"),
            "model_provider_id": turn["model_provider_id"],
            "model_model_id": turn["model_model_id"],
            "planning_source": turn["planning_source"],
            "state": json.loads(turn["state_json"]),
            "candidates": candidates,
        })
    return replay


def format_mode_replay(replay: list[dict[str, Any]]) -> list[str]:
    """R2.6 mode replay: human-readable per-run/per-turn planning history."""
    if not replay:
        return []
    lines = [f"Run {replay[0]['run_id']}: "
             f"mode={replay[0].get('mode') or 'deterministic'}"]
    for turn in replay:
        source = turn["planning_source"]
        if "fallback" in (source or ""):
            label = "fallback"
        elif source == "model":
            label = "model proposal"
        else:
            label = "deterministic completion"
        lines.append(f"Turn {turn['turn_index'] + 1}: {label}")
    return lines


def planner_evaluation_report(trail_store: PlanningTrailStore,
                              run_store, run_id: str,
                              project_id: str) -> dict[str, int]:
    """Planner evaluation telemetry (R2.5.1 item 2/3).

    Reasoning-layer counters are SEPARATE from A3 model-inference economy;
    approval_block_count is derived from the run journal (a model-sourced
    network proposal that ended up WAITING_APPROVAL)."""
    events = trail_store.events_for_run(run_id)
    report = {
        "candidate_count": len(events),
        "accepted_count": sum(1 for e in events
                              if e["validator_result"] == "accepted"),
        "rejected_count": sum(1 for e in events
                              if e["validator_result"] != "accepted"),
        "fallback_count": sum(1 for t in trail_store.turns_for_run(run_id)
                              if t["planning_source"] == "fallback"),
        "limit_rejection_count": sum(1 for e in events
                                     if (e["rejection_reason"] or "")
                                     .startswith("limit_exhausted")),
        "approval_block_count": 0,
        # Planner economy separation: reasoning vs execution vs inference.
        "planning_reasoning_attempts": len(
            trail_store.turns_for_run(run_id)),
        "tool_executions": 0,
        "model_inference_count": 0,
    }
    for step in run_store.steps_for(run_id):
        if step.get("created_by") == "model-proposal" \
                and step["decision"] == "awaiting_approval":
            report["approval_block_count"] += 1
        if step["outcome"] == "executed":
            report["tool_executions"] += 1
    return report


def _uuid(prefix: str) -> str:
    import uuid
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# planner models


class PlannerModel:
    """Provider-neutral PLANNING model interface (proposal-only).

    Implementations receive a bounded, JSON-safe research-state snapshot
    and return CANDIDATE actions. They hold no execution power."""

    provider_id = "abstract"
    version = "0"
    model_id = "abstract"
    is_local = True

    def propose_actions(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError


class DeterministicScriptedPlannerModel(PlannerModel):
    """Offline deterministic stand-in for a planner model (tests/baseline).

    Either a static script of turns or a pure callable over the state -
    both fully reproducible, zero paid calls."""

    provider_id = "deterministic-script"
    version = "r2.5-1.0.0"
    model_id = "scripted"

    def __init__(self, turns: list[list[dict[str, Any]]] | None = None,
                 strategy=None) -> None:
        self.turns = [list(turn) for turn in (turns or [])]
        self.strategy = strategy

    def propose_actions(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        if self.turns:
            return list(self.turns.pop(0))
        if self.strategy is not None:
            return list(self.strategy(state))
        return []


class LocalServicePlannerModel(PlannerModel):
    """Adapter for an ALREADY-RUNNING loopback planning service.

    No weights bundled/downloaded: the operator configures a local
    endpoint; AMCA POSTs the bounded state and expects
    {"actions": [{"action": ..., "arguments": {...}}, ...]}. Tests inject
    a fake opener so zero real network occurs."""

    provider_id = "local-service-planner"

    def __init__(self, *, endpoint: str, model_id: str,
                 version: str = "local-1.0", timeout_seconds: float = 5.0,
                 opener=None) -> None:
        lowered = (endpoint or "").lower()
        if not (lowered.startswith("http://127.0.0.1")
                or lowered.startswith("http://localhost")
                or lowered.startswith("http://[::1]")):
            raise PlannerModelError(
                "local service planner requires a loopback endpoint")
        self.endpoint = endpoint
        self.model_id = model_id
        self.version = version
        self.timeout_seconds = timeout_seconds
        self._opener = opener

    def propose_actions(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        payload = json.dumps({"state": state},
                             sort_keys=True).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint, data=payload,
            headers={"Content-Type": "application/json"})
        if self._opener is not None:
            response = self._opener.open(request,
                                         timeout=self.timeout_seconds)
        else:
            response = urllib.request.urlopen(
                request, timeout=self.timeout_seconds)
        body = json.loads(response.read().decode("utf-8"))
        actions = body.get("actions")
        if not isinstance(actions, list):
            raise PlannerModelError(
                "local planner returned malformed actions payload")
        return [a for a in actions if isinstance(a, dict)]


# ---------------------------------------------------------------------------
# policy validator


class PlannerPolicyValidator:
    """Validates model-proposed candidates against the frozen contract.

    Output is fully inspectable: every candidate yields either an accepted
    proposal or a typed rejection reason."""

    def validate(self, context: dict[str, Any], raw: Any) \
            -> tuple[ResearchActionProposal | None, str | None]:
        if not isinstance(raw, dict):
            return None, "candidate_not_an_object"
        unknown_keys = set(raw) - {"action", "arguments", "reason"}
        if unknown_keys:
            return None, f"forbidden_candidate_keys:{sorted(unknown_keys)}"

        action_raw = raw.get("action")
        try:
            action = ResearchAction(str(action_raw))
        except (ValueError, TypeError):
            return None, f"unknown_action:{action_raw!r}"
        if action not in ALLOWED_ACTIONS:
            return None, f"action_outside_closed_set:{action!r}"

        arguments = raw.get("arguments") or {}
        if not isinstance(arguments, dict):
            return None, "arguments_not_an_object"
        lowered = {str(k).lower() for k in arguments}
        smuggled = lowered & FORBIDDEN_ARGUMENT_KEYS
        if smuggled:
            return None, f"forbidden_approval_field:{sorted(smuggled)}"

        schema = ARGUMENT_SCHEMAS[action]
        allowed = {name for name, _, _ in schema}
        extras = set(arguments) - allowed
        if extras:
            return None, f"unexpected_argument:{sorted(extras)}"
        for name, expected_type, required in schema:
            value = arguments.get(name)
            if required and value is None:
                return None, f"missing_required_argument:{name}"
            if value is not None and not isinstance(value, expected_type):
                return None, f"argument_type_mismatch:{name}"
            if name == "url":
                # Defense-in-depth: fetch targets ALWAYS come from stored
                # durable identities, never from a model proposal.
                return None, "forbidden_argument:url"

        # Value-level bounds and enums.
        query = arguments.get("query")
        if isinstance(query, str) and len(query) > MAX_QUERY_LENGTH:
            return None, "argument_over_bound:query"
        text = arguments.get("question") or arguments.get("claim_text")
        if isinstance(text, str) and len(text) > MAX_TEXT_LENGTH:
            return None, "argument_over_bound:text"
        mode = arguments.get("retrieval_mode")
        if mode is not None and str(mode).lower() not in VALID_RETRIEVAL_MODES:
            return None, f"invalid_retrieval_mode:{mode}"
        relation = arguments.get("relation")
        if relation is not None and str(relation).lower() \
                not in VALID_RELATIONS:
            return None, f"invalid_relation:{relation}"
        max_results = arguments.get("max_results")
        if max_results is not None and not (1 <= int(max_results) <= 50):
            return None, "argument_over_bound:max_results"

        reason_raw = raw.get("reason")
        if reason_raw is None:
            reason = DEFAULT_REASON_BY_ACTION[action]
        else:
            try:
                reason = ReasonCode(str(reason_raw))
            except ValueError:
                return None, f"invalid_reason_code:{reason_raw!r}"

        # Limits: the model may never propose past the remaining budget.
        remaining = context.get("limits_remaining") or {}
        budget_key = {
            ResearchAction.SEARCH_WEB: "searches",
            ResearchAction.FETCH_RESULT: "fetches",
            ResearchAction.SYNTHESIZE: "synthesis_attempts",
        }.get(action)
        if budget_key is not None and remaining.get(budget_key, 0) <= 0:
            return None, f"limit_exhausted:{budget_key}"
        if remaining.get("steps", 1) <= 0 and action \
                is not ResearchAction.STOP:
            return None, "limit_exhausted:steps"

        proposal = ResearchActionProposal(
            action_id=_uuid("act"),
            run_id=context["run_id"],
            step_number=context["step_number"],
            action_type=action,
            arguments=dict(arguments),
            reason_code=reason,
            created_by="model-proposal")
        return proposal, None


class AdaptiveResearchPlanner:
    """Drop-in planner for the R2.1 orchestrator: consults a PlannerModel,
    validates its candidates through PlannerPolicyValidator, and falls back
    to the accepted deterministic baseline when needed.

    Inspectability: `last_planning_trail` retains the model identity, every
    candidate verdict/reason and the chosen planning source after each call.
    """

    def __init__(self, *, model: PlannerModel | None,
                 fallback, validator: PlannerPolicyValidator | None = None,
                 allow_fallback: bool = True,
                 trail_store: PlanningTrailStore | None = None,
                 project_id: str | None = None) -> None:
        if model is not None and not getattr(model, "is_local", False):
            raise PlannerModelError(
                "refusing non-local planner model: no cloud fallback")
        self.model = model
        self.fallback = fallback
        self.validator = validator or PlannerPolicyValidator()
        self.allow_fallback = allow_fallback
        #: Optional R2.5.1 durable audit store.
        self.trail_store = trail_store
        self.project_id = project_id
        self.last_planning_trail: dict[str, Any] = {}

    def _persist_turn(self, context: dict[str, Any],
                      trail: dict[str, Any]) -> None:
        """R2.5.1: durable, inspectable planning history (best-effort)."""
        if self.trail_store is None:
            return
        try:
            run_id = context["run_id"]
            turn_index = len(self.trail_store.turns_for_run(run_id))
            self.trail_store.save_turn(
                run_id=run_id, turn_index=turn_index,
                model=trail.get("model"),
                planning_source=trail["planning_source"],
                state=self._bounded_state(context),
                candidates=trail["candidates"],
                step_number=context.get("step_number"),
                mode=context.get("mode"))
            candidates = trail["candidates"]
            self.trail_store._bump(self.project_id or "",
                                   "planning_candidate_count",
                                   len(candidates))
            self.trail_store._bump(
                self.project_id or "", "planning_accepted_count",
                sum(1 for c in candidates if c.get("verdict") == "accepted"))
            rejected = sum(1 for c in candidates
                           if c.get("verdict") == "rejected")
            self.trail_store._bump(self.project_id or "",
                                   "planning_rejected_count", rejected)
            self.trail_store._bump(
                self.project_id or "", "planning_limit_rejection_count",
                sum(1 for c in candidates
                    if str(c.get("reason", "")).startswith("limit_exhausted")))
            if trail["planning_source"] == "fallback":
                self.trail_store._bump(self.project_id or "",
                                       "planning_fallback_count", 1)
        except Exception:  # noqa: BLE001 - auditing must never break planning
            pass

    def propose_next(self, context: dict[str, Any]) \
            -> ResearchActionProposal:
        trail: dict[str, Any] = {
            "planning_source": "fallback", "model": None,
            "candidates": [], "rejected": []}
        if self.model is not None:
            trail["model"] = {
                "provider_id": self.model.provider_id,
                "model_id": self.model.model_id,
                "version": self.model.version,
            }
            try:
                candidates = self.model.propose_actions(
                    self._bounded_state(context))
                if len(candidates) > MAX_ACTIONS_PER_TURN:
                    candidates = candidates[:MAX_ACTIONS_PER_TURN]
                    trail["candidates"] = [{
                        "verdict": "rejected",
                        "reason": "candidate_over_turn_bound"}]
            except Exception as exc:  # noqa: BLE001 - truthful fallback
                trail["candidates"].append({
                    "verdict": "rejected",
                    "reason": f"model_error:{type(exc).__name__}"})
                candidates = []
            for raw in candidates:
                proposal, rejection = self.validator.validate(context, raw)
                if proposal is None:
                    trail["candidates"].append({
                        "candidate": raw, "verdict": "rejected",
                        "reason": rejection})
                    continue
                trail["candidates"].append({
                    "candidate": raw, "verdict": "accepted"})
                trail["planning_source"] = "model"
                self.last_planning_trail = trail
                self._persist_turn(context, trail)
                return proposal
        if self.model is None:
            trail["candidates"].append({
                "verdict": "rejected", "reason": "no_model_configured"})
        if not self.allow_fallback:
            self.last_planning_trail = trail
            self._persist_turn(context, trail)
            raise PlannerModelError(
                "no valid model candidate and fallback disabled")
        proposal = self.fallback.propose_next(context)
        if proposal.action_type not in ALLOWED_ACTIONS:
            raise InvalidProposal(
                f"fallback produced invalid action: "
                f"{proposal.action_type!r}")
        self.last_planning_trail = trail
        self._persist_turn(context, trail)
        return proposal

    @staticmethod
    def _bounded_state(context: dict[str, Any]) -> dict[str, Any]:
        """Bounded, JSON-safe snapshot handed to the planner model."""
        state = {
            "objective": context.get("objective"),
            "questions": context.get("questions"),
            "claims": context.get("claims"),
            "gaps": context.get("gaps"),
            "executed_queries": context.get("executed_queries"),
            "unfetched_result_count": len(context.get("unfetched_results")
                                          or []),
            "limits_remaining": context.get("limits_remaining"),
            "previous_outcomes": context.get("previous_outcomes"),
            "step_number": context.get("step_number"),
        }
        encoded = json.dumps(state, sort_keys=True, default=str)
        if len(encoded) > MAX_STATE_CHARS:
            raise PlannerModelError("state snapshot exceeds bound")
        return state


class HybridResearchPlanner(AdaptiveResearchPlanner):
    """HYBRID mode profile (R2.6): deterministic-first.

    The accepted deterministic baseline proposal is ALWAYS computed; model
    candidates are advisory and may replace it only after passing the same
    full policy validation. If the model is unavailable, fails, or proposes
    nothing valid, the baseline stands - deterministic fallback by design.
    """

    def propose_next(self, context: dict[str, Any]) \
            -> ResearchActionProposal:
        trail: dict[str, Any] = {
            "planning_source": "deterministic-baseline",
            "model": None,
            "candidates": [],
            "baseline_action": None,
        }
        if self.model is not None:
            trail["model"] = {
                "provider_id": self.model.provider_id,
                "model_id": self.model.model_id,
                "version": self.model.version,
            }
            try:
                candidates = self.model.propose_actions(
                    self._bounded_state(context))
                candidates = candidates[:MAX_ACTIONS_PER_TURN]
            except Exception as exc:  # noqa: BLE001 - advisory only
                trail["candidates"].append({
                    "verdict": "rejected",
                    "reason": f"model_error:{type(exc).__name__}"})
                candidates = []
            for raw in candidates:
                proposal, rejection = self.validator.validate(context, raw)
                if proposal is None:
                    trail["candidates"].append({
                        "candidate": raw, "verdict": "rejected",
                        "reason": rejection})
                    continue
                trail["candidates"].append({
                    "candidate": raw, "verdict": "accepted"})
                trail["planning_source"] = "model"
                self.last_planning_trail = trail
                self._persist_turn(context, trail)
                return proposal
        baseline = None
        try:
            baseline = self.fallback.propose_next(context)
            if baseline.action_type not in ALLOWED_ACTIONS:
                raise InvalidProposal(
                    f"fallback produced invalid action: "
                    f"{baseline.action_type!r}")
        except Exception as exc:  # noqa: BLE001
            if not self.allow_fallback:
                raise
            trail["candidates"].append({
                "verdict": "rejected",
                "reason": f"fallback_error:{type(exc).__name__}"})
        if baseline is None:
            raise PlannerModelError(
                "no valid model candidate and no usable baseline")
        trail["baseline_action"] = str(getattr(
            baseline.action_type, "value", baseline.action_type))
        for post, entry in enumerate(trail["candidates"], start=1):
            entry.setdefault("post_rerank_rank", post)
        self.last_planning_trail = trail
        self._persist_turn(context, trail)
        return baseline
