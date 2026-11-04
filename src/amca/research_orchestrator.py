"""R2.1 bounded autonomous research orchestrator.

Durable ResearchRun + closed action set + injected planner + Tool Fabric
gating. The orchestrator decides the next bounded research action from R1
coverage metadata, executes it through authoritative repositories, journals
every step, and stops with an explicit reason. It is never an unrestricted
recursive agent: hard limits are mandatory, network actions stay gated by
the Tool Fabric (search approval != fetch approval), duplicate work is
suppressed, and interrupted external actions are never auto-replayed.

Frozen invariants (manager-reply57):
- FETCH_RESULT resolves the STORED url of the durable search result;
  a planner-supplied url can never override that identity.
- SEARCH approval does not authorize FETCH and vice versa (separate tool ids).
- The orchestrator cannot approve its own network work: REQUIRE_APPROVAL
  pauses the run as WAITING_APPROVAL; only an external fabric approval lets
  the already-submitted execution proceed.
- SYNTHESIZE is pure local computation: zero network calls, no self-decided
  re-searching; completion requires satisfied grounding criteria.
- External work whose outcome crossed the process boundary (RUNNING /
  INTERRUPTED_AFTER_START execution) is surfaced as INTERRUPTED with
  stop_reason=interrupted_external_action - never retried automatically.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from amca.research_source import (
    MAX_QUERY_LENGTH,
    ResearchError,
    ResearchStore,
)
from amca.research_semantic import (
    EmbeddingError,
    SemanticBackendUnavailable,
)
from amca.research_context import (
    RerankError,
    build_from_local_search,
)
from amca.research_synthesis import (
    ClaimType,
    Coverage,
    DeterministicSynthesizer,
    EvidenceRelation,
    Grounding,
    SynthesisOutputForm,
    evaluate_grounding,
)
from amca.tool_fabric import Decision, ExecutionStatus, ToolRequest
from amca.web_acquisition import (
    AcquisitionStatus,
    NetworkToolPolicy,
    WebAcquisitionClient,
    WebAcquisitionStore,
)


def _now() -> str:
    from datetime import datetime as _dt
    return _dt.now(UTC).isoformat()


def _uuid(prefix: str) -> str:
    import uuid
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class ResearchAction(str, Enum):
    SEARCH_WEB = "search_web"
    FETCH_RESULT = "fetch_result"
    SEARCH_LOCAL = "search_local"
    CREATE_OR_REFINE_QUESTION = "create_or_refine_question"
    LINK_EVIDENCE = "link_evidence"
    CREATE_OR_UPDATE_CLAIM = "create_or_update_claim"
    SYNTHESIZE = "synthesize"
    STOP = "stop"


class RunStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class RunMode(str, Enum):
    """R2.6 explicit research mode profiles (no free text)."""
    DETERMINISTIC = "deterministic"
    ADAPTIVE = "adaptive"
    HYBRID = "hybrid"


RUN_MODES = frozenset(m.value for m in RunMode)


class StopReason(str, Enum):
    OBJECTIVE_SATISFIED = "objective_satisfied"
    READY_FOR_SYNTHESIS = "ready_for_synthesis"
    STEP_LIMIT = "step_limit"
    SEARCH_LIMIT = "search_limit"
    FETCH_LIMIT = "fetch_limit"
    SOURCE_LIMIT = "source_limit"
    TIME_LIMIT = "time_limit"
    APPROVAL_DENIED = "approval_denied"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNRESOLVED_CONTRADICTION = "unresolved_contradiction"
    INTERRUPTED_EXTERNAL_ACTION = "interrupted_external_action"
    USER_CANCELLED = "user_cancelled"
    ERROR = "error"


class ReasonCode(str, Enum):
    NO_EVIDENCE = "no_evidence"
    INSUFFICIENT_SOURCE_DIVERSITY = "insufficient_source_diversity"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    UNANSWERED_SUBQUESTION = "unanswered_subquestion"
    NEED_PRIMARY_SOURCE = "need_primary_source"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    READY_TO_SYNTHESIZE = "ready_to_synthesize"
    LIMIT_REACHED = "limit_reached"
    OBJECTIVE_SATISFIED = "objective_satisfied"


#: Step outcomes meaning the action was resolved (committed or truthfully
#: refused); awaiting steps are NOT resolved and stay re-proposable.
RESOLVED_OUTCOMES = frozenset({
    "executed", "denied", "failed", "timed_out", "duplicate_suppressed",
    "rejected_unknown_result", "rejected_unknown_entity",
    "rejected_invalid_arguments", "semantic_backend_unavailable",
})

GROUNDING_SATISFIED = frozenset({
    Grounding.GROUNDED.value, Grounding.CONTESTED.value})


@dataclass(slots=True)
class ResearchLimits:
    max_steps: int = 12
    max_searches: int = 3
    max_fetches: int = 3
    max_sources: int = 10
    max_evidence_chunks: int = 25
    max_synthesis_attempts: int = 2
    max_elapsed_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_searches": self.max_searches,
            "max_fetches": self.max_fetches,
            "max_sources": self.max_sources,
            "max_evidence_chunks": self.max_evidence_chunks,
            "max_synthesis_attempts": self.max_synthesis_attempts,
            "max_elapsed_seconds": self.max_elapsed_seconds,
        }


@dataclass(slots=True)
class ResearchActionProposal:
    action_id: str
    run_id: str
    step_number: int
    action_type: ResearchAction
    arguments: dict[str, Any]
    reason_code: ReasonCode
    expected_outcome: str | None = None
    created_by: str = "deterministic-planner"


class InvalidProposal(ValueError):
    """Planner proposed an unknown action/entity - rejected, never fuzzy-matched."""


def _signature(action_type: Any, arguments: dict[str, Any]) -> str:
    action = getattr(action_type, "value", action_type)
    return json.dumps({"action": str(action),
                       "arguments": arguments}, sort_keys=True)


class ResearchRunStore:
    """Durable runs + step journal in canonical amca.db."""

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
                CREATE TABLE IF NOT EXISTS research_runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stop_reason TEXT,
                    limits_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS research_run_steps (
                    step_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
                    ordinal INTEGER NOT NULL,
                    action_type TEXT NOT NULL,
                    arguments_json TEXT NOT NULL DEFAULT '{}',
                    reason_code TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    execution_ref TEXT,
                    outcome TEXT NOT NULL DEFAULT 'pending',
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );
            """)
            # Structured execution payloads (source/snapshot/event refs).
            try:
                connection.execute(
                    "ALTER TABLE research_run_steps "
                    "ADD COLUMN result_json TEXT")
            except sqlite3.OperationalError:
                pass
            # Planner provenance (R2.5): which planner produced the step.
            try:
                connection.execute(
                    "ALTER TABLE research_run_steps "
                    "ADD COLUMN created_by TEXT")
            except sqlite3.OperationalError:
                pass
            # R2.6 durable mode selection + planner evaluation metadata.
            for column in ("mode TEXT", "planner_mode TEXT",
                           "planner_source TEXT", "fallback_reason TEXT"):
                try:
                    connection.execute(
                        f"ALTER TABLE research_runs ADD COLUMN {column}")
                except sqlite3.OperationalError:
                    pass

    def create_run(self, *, project_id: str, session_id: str,
                   objective: str,
                   limits: ResearchLimits,
                   mode: str = RunMode.DETERMINISTIC.value) -> dict[str, Any]:
        if str(mode) not in RUN_MODES:
            raise ValueError(
                f"invalid research mode {mode!r}; allowed: {sorted(RUN_MODES)}")
        run_id = _uuid("run")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_runs
                   (run_id, project_id, session_id, objective, status,
                    stop_reason, limits_json, started_at, completed_at, mode)
                   VALUES (?, ?, ?, ?, 'planned', NULL, ?, ?, NULL, ?)""",
                (run_id, project_id, session_id, objective,
                 json.dumps(limits.to_dict()), _now(), str(mode)))
        return self.get_run(run_id)

    def update_planner_metadata(self, run_id: str, *, planner_mode: str,
                                planner_source: str | None = None,
                                fallback_reason: str | None = None) -> None:
        """R2.6 adaptive-planner evaluation metadata on the durable run."""
        with self._connect() as connection:
            connection.execute(
                """UPDATE research_runs SET planner_mode=?, planner_source=?,
                   fallback_reason=? WHERE run_id=?""",
                (planner_mode, planner_source, fallback_reason, run_id))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_runs WHERE run_id = ?",
                (run_id,)).fetchone()
        record = dict(row) if row else None
        if record:
            record["limits"] = json.loads(record.pop("limits_json"))
            # Legacy rows predate R2.6 durable mode selection.
            record["mode"] = record.get("mode") \
                or RunMode.DETERMINISTIC.value
        return record

    def set_run_status(self, run_id: str, status: RunStatus,
                       stop_reason: StopReason | None = None) -> None:
        completed = (_now()
                     if status in (RunStatus.COMPLETED, RunStatus.PARTIAL,
                                   RunStatus.FAILED, RunStatus.CANCELLED,
                                   RunStatus.INTERRUPTED) else None)
        with self._connect() as connection:
            connection.execute(
                """UPDATE research_runs SET status=?, stop_reason=?,
                   completed_at=COALESCE(?, completed_at) WHERE run_id=?""",
                (status.value,
                 stop_reason.value if stop_reason else None,
                 completed, run_id))

    def cancel_run(self, run_id: str) -> dict[str, Any] | None:
        """User cancellation: terminal; admits no new steps."""
        run = self.get_run(run_id)
        if run is None:
            return None
        if run["status"] in (RunStatus.COMPLETED.value,
                             RunStatus.PARTIAL.value,
                             RunStatus.FAILED.value,
                             RunStatus.CANCELLED.value,
                             RunStatus.INTERRUPTED.value):
            return self.get_run(run_id)
        self.set_run_status(run_id, RunStatus.CANCELLED,
                            StopReason.USER_CANCELLED)
        return self.get_run(run_id)

    def add_step(self, *, run_id: str, ordinal: int,
                 proposal: ResearchActionProposal, decision: str,
                 execution_ref: str | None = None,
                 outcome: str = "pending",
                 result: dict[str, Any] | None = None) -> str:
        step_id = _uuid("step")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_run_steps
                   (step_id, run_id, ordinal, action_type, arguments_json,
                    reason_code, decision, execution_ref, outcome,
                    result_json, created_by, started_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (step_id, run_id, ordinal,
                 getattr(proposal.action_type, "value", proposal.action_type),
                 json.dumps(proposal.arguments, sort_keys=True),
                 proposal.reason_code.value, decision, execution_ref,
                 outcome,
                 json.dumps(result, sort_keys=True) if result else None,
                 getattr(proposal, "created_by", None),
                 _now(), _now() if outcome != "pending" else None))
        return step_id

    def update_step(self, step_id: str, *, outcome: str | None = None,
                    decision: str | None = None,
                    execution_ref: str | None = None,
                    result: dict[str, Any] | None = None) -> None:
        assignments, values = [], []
        for column, value in (("outcome", outcome), ("decision", decision),
                              ("execution_ref", execution_ref)):
            if value is not None:
                assignments.append(f"{column}=?")
                values.append(value)
        if result is not None:
            assignments.append("result_json=?")
            values.append(json.dumps(result, sort_keys=True))
        if not assignments:
            return
        assignments.append("completed_at=COALESCE(completed_at, ?)")
        values.extend([_now(), step_id])
        with self._connect() as connection:
            connection.execute(
                f"UPDATE research_run_steps SET {', '.join(assignments)} "
                "WHERE step_id = ?", tuple(values))

    def steps_for(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_run_steps WHERE run_id = ? "
                "ORDER BY ordinal", (run_id,)).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            record["arguments"] = json.loads(record.pop("arguments_json"))
            raw_result = record.pop("result_json", None)
            record["result"] = json.loads(raw_result) if raw_result else None
            records.append(record)
        return records


class DeterministicResearchPlanner:
    """Non-model baseline policy.

    Proposes the next bounded action from structured coverage metadata:
    unanswered question with no evidence -> search; available unfetched
    result -> fetch (by stored identity only); extracted content -> link
    evidence; supported claims -> synthesize; otherwise stop.
    """

    provider_id = "deterministic-planner"

    def propose_next(self, context: dict[str, Any]) -> ResearchActionProposal:
        limits = context["limits_remaining"]
        if limits["steps"] <= 0 or context.get("force_stop"):
            return self._proposal(context, ResearchAction.STOP,
                                  ReasonCode.LIMIT_REACHED)

        questions = context["questions"]
        claims = context["claims"]

        # 1. A question whose claims have no evidence -> research gap.
        open_question = next(
            (q for q in questions
             if q["status"] in ("open", "unanswered")), None)
        uncovered = [c for c in claims
                     if c["coverage"] == Coverage.NO_EVIDENCE.value]
        if open_question is not None and uncovered:
            query = str(open_question["question"])[:MAX_QUERY_LENGTH]
            if query in context["executed_queries"]:
                # Already researched this exact angle without evidence.
                return self._proposal(context, ResearchAction.STOP,
                                      ReasonCode.INSUFFICIENT_EVIDENCE)
            if limits["searches"] > 0:
                return self._proposal(context, ResearchAction.SEARCH_WEB,
                                      ReasonCode.NO_EVIDENCE,
                                      arguments={"query": query})
            return self._proposal(context, ResearchAction.SEARCH_LOCAL,
                                  ReasonCode.NO_EVIDENCE,
                                  arguments={"query": query})

        # 2. Available unfetched results -> fetch by durable identity.
        pending_fetch = next((r for r in context["unfetched_results"]), None)
        if pending_fetch is not None:
            if limits["fetches"] > 0:
                return self._proposal(
                    context, ResearchAction.FETCH_RESULT,
                    ReasonCode.NEED_PRIMARY_SOURCE,
                    arguments={"search_result_id":
                               pending_fetch["search_result_id"]})

        # 3. Extracted evidence candidates not yet linked -> link to claim.
        pending_links = next(
            (c for c in context["link_candidates"]), None)
        if pending_links is not None:
            return self._proposal(
                context, ResearchAction.LINK_EVIDENCE,
                ReasonCode.NO_EVIDENCE, arguments=pending_links)

        # 4. Supported/contested claims -> synthesis.
        synthesizable = [c for c in claims if c["coverage"] in (
            Coverage.SUPPORT_ONLY.value, Coverage.MIXED.value,
            Coverage.CONTRADICTION_ONLY.value)]
        if synthesizable and limits["synthesis_attempts"] > 0:
            return self._proposal(context, ResearchAction.SYNTHESIZE,
                                  ReasonCode.READY_TO_SYNTHESIZE)

        # 5. Nothing actionable remains.
        if any(c["coverage"] == Coverage.NO_EVIDENCE.value for c in claims):
            return self._proposal(context, ResearchAction.STOP,
                                  ReasonCode.INSUFFICIENT_EVIDENCE)
        return self._proposal(context, ResearchAction.STOP,
                              ReasonCode.OBJECTIVE_SATISFIED)

    @staticmethod
    def _proposal(context: dict[str, Any], action_type: ResearchAction,
                  reason_code: ReasonCode,
                  arguments: dict[str, Any] | None = None) \
            -> ResearchActionProposal:
        from amca.tool_fabric import _uuid  # reuse id helper shape

        return ResearchActionProposal(
            action_id=_uuid("act"),
            run_id=context["run_id"],
            step_number=context["step_number"],
            action_type=action_type,
            arguments=arguments or {},
            reason_code=reason_code)


class ResearchOrchestrator:
    """Executes bounded research runs over R1 repositories."""

    def __init__(self, *, run_store: ResearchRunStore,
                 research_store: ResearchStore,
                 synthesis_store,
                 fabric, acq_store, ingestor: Any,
                 acquisition_client: WebAcquisitionClient | None = None,
                 search_provider=None,
                 semantic_engine=None,
                 context_builder=None,
                 context_store=None) -> None:
        self.run_store = run_store
        self.research_store = research_store
        self.synthesis_store = synthesis_store
        self.fabric = fabric
        self.acq_store = acq_store
        self.ingestor = ingestor
        self.acquisition_client = acquisition_client
        self.search_provider = search_provider
        #: Optional R2.2 hybrid local-retrieval facade (research_semantic).
        self.semantic_engine = semantic_engine
        #: Optional R2.3 ResearchContextBuilder (research_context).
        self.context_builder = context_builder
        #: Optional durable store so journal references stay resolvable.
        self.context_store = context_store

    def set_semantic_engine(self, engine) -> None:
        self.semantic_engine = engine

    def set_context_builder(self, builder) -> None:
        self.context_builder = builder

    def set_context_store(self, store) -> None:
        self.context_store = store

    def set_search_provider(self, provider) -> None:
        self.search_provider = provider

    # -- planning context ----------------------------------------------

    def _remaining(self, run, steps):
        limits = run["limits"]
        searches = sum(1 for s in steps
                       if s["action_type"] == ResearchAction.SEARCH_WEB.value
                       and s["decision"] != "awaiting_approval")
        fetches = sum(1 for s in steps
                      if s["action_type"] == ResearchAction.FETCH_RESULT.value
                      and s["decision"] != "awaiting_approval")
        syntheses = sum(1 for s in steps
                        if s["action_type"] == ResearchAction.SYNTHESIZE.value)
        return {
            "steps": limits["max_steps"] - len(steps),
            "searches": limits["max_searches"] - searches,
            "fetches": limits["max_fetches"] - fetches,
            "sources": limits["max_sources"],
            "synthesis_attempts":
                limits["max_synthesis_attempts"] - syntheses,
        }

    def build_planning_context(self, run, steps, remaining):
        """Structured bounded planner input from R1 authoritative state."""
        session_id = run["session_id"]
        project_id = run["project_id"]

        questions = self.synthesis_store.list_questions(session_id,
                                                        project_id=project_id)
        claims = []
        claim_ids = self.synthesis_store.claim_ids_for_session(
            session_id, project_id=project_id)
        for claim_id in claim_ids:
            coverage = self.synthesis_store.coverage_for(claim_id)
            diversity = self.synthesis_store.diversity_for(claim_id)
            claims.append({"claim_id": claim_id,
                           "coverage": coverage.value,
                           "distinct_sources": diversity["distinct_sources"]})

        executed_queries = []
        fetched_urls = set()
        fetched_result_ids = set()
        for step in steps:
            args = step.get("arguments") or {}
            if step["action_type"] in (ResearchAction.SEARCH_WEB.value,
                                       ResearchAction.SEARCH_LOCAL.value):
                if step["outcome"] in RESOLVED_OUTCOMES:
                    executed_queries.append(args.get("query", ""))
            elif step["action_type"] == ResearchAction.FETCH_RESULT.value:
                if step["outcome"] in ("executed", "duplicate_suppressed"):
                    result_id = str(args.get("search_result_id"))
                    fetched_result_ids.add(result_id)
                    stored = self.research_store.get_search_result(
                        result_id, project_id=project_id)
                    if stored is not None:
                        fetched_urls.add(stored["url"])

        unfetched_results = [
            r for r in self.acq_store.unfetched_results_for(
                session_id=session_id, project_id=project_id,
                exclude_urls=sorted(fetched_urls))
            if str(r["search_result_id"]) not in fetched_result_ids]

        source_count = len(self.research_store.list_sources(
            session_id, project_id=project_id))
        remaining["sources"] = max(0, remaining["sources"] - source_count)

        return {"run_id": run["run_id"], "objective": run["objective"],
                "questions": questions, "claims": claims,
                "gaps": [c["claim_id"] for c in claims
                         if c["coverage"] == Coverage.NO_EVIDENCE.value],
                "conflicts": [], "source_diversity": {},
                "limits_remaining": remaining,
                "previous_outcomes": [s["outcome"] for s in steps],
                "executed_queries": executed_queries,
                "fetched_urls": sorted(fetched_urls),
                "unfetched_results": unfetched_results,
                "link_candidates": [],
                "step_number": len(steps) + 1, "force_stop": False,
                "mode": run.get("mode") or RunMode.DETERMINISTIC.value}

    # -- bounded execute loop ------------------------------------------

    def execute_run(self, run_id: str, planner) -> dict[str, Any]:
        """Bounded execute loop with explicit stop reasons."""
        run = self.run_store.get_run(run_id)
        if run is None:
            raise ValueError(f"unknown run: {run_id!r}")
        if run["status"] in (RunStatus.COMPLETED.value,
                             RunStatus.PARTIAL.value,
                             RunStatus.FAILED.value,
                             RunStatus.CANCELLED.value,
                             RunStatus.INTERRUPTED.value):
            raise ValueError(
                f"run {run_id} already terminal ({run['status']})")
        mode_value = run.get("mode") or RunMode.DETERMINISTIC.value
        if mode_value not in RUN_MODES:
            raise ValueError(f"invalid research mode {mode_value!r}")
        adaptive_capable = (hasattr(planner, "last_planning_trail")
                            and hasattr(planner, "validator"))
        if mode_value == RunMode.DETERMINISTIC.value and adaptive_capable:
            raise ValueError(
                "deterministic mode requires the deterministic planner")
        if mode_value in (RunMode.ADAPTIVE.value, RunMode.HYBRID.value) \
                and not adaptive_capable:
            raise ValueError(
                f"{mode_value} mode requires an adaptive-capable planner "
                "(PlannerModel + validator + planning trail)")
        self.run_store.set_run_status(run_id, RunStatus.RUNNING)

        stop_reason = None
        final_status = RunStatus.COMPLETED
        touched = 0
        while True:
            fresh = self.run_store.get_run(run_id)
            if fresh["status"] == RunStatus.CANCELLED.value:
                stop_reason = StopReason.USER_CANCELLED
                final_status = RunStatus.CANCELLED
                break
            steps = self.run_store.steps_for(run_id)

            # Interruption gate: external work whose outcome crossed the
            # boundary is NEVER auto-replayed.
            uncertain = self._uncertain_execution(steps)
            if uncertain is not None:
                stop_reason = StopReason.INTERRUPTED_EXTERNAL_ACTION
                final_status = RunStatus.INTERRUPTED
                break

            # Resume path: a previously submitted network action that has
            # since been EXTERNALLY approved executes exactly once.
            pending = self._pending_awaiting_step(steps)
            if pending is not None:
                status = self._execution_status(pending["execution_ref"])
                if status == ExecutionStatus.REQUESTED.value:
                    self.run_store.set_run_status(
                        run_id, RunStatus.WAITING_APPROVAL)
                    return {"run_id": run_id,
                            "status": RunStatus.WAITING_APPROVAL.value,
                            "stop_reason": None, "steps_executed": touched,
                            "awaiting_execution": pending["execution_ref"]}
                if status == ExecutionStatus.AUTHORIZED.value:
                    payload = self._execute_network_backend(pending, fresh)
                    resolved_outcome = ("executed" if not payload.get("failed")
                                        else payload["outcome"])
                    self.run_store.update_step(
                        pending["step_id"], outcome=resolved_outcome,
                        decision="authorized", result=payload)
                    touched += 1
                    continue
                # DENIED / terminal pending rows are stale bookkeeping only.
                self.run_store.update_step(pending["step_id"],
                                           decision="denied",
                                           outcome=pending["outcome"]
                                           or "denied")

            remaining = self._remaining(fresh, steps)
            started_at = datetime.fromisoformat(fresh["started_at"])
            budget = fresh["limits"].get("max_elapsed_seconds")
            if remaining["steps"] <= 0:
                stop_reason = StopReason.STEP_LIMIT
                final_status = RunStatus.PARTIAL
                break
            if budget is not None and (
                    datetime.now(UTC) - started_at).total_seconds() > budget:
                stop_reason = StopReason.TIME_LIMIT
                final_status = RunStatus.PARTIAL
                break

            context = self.build_planning_context(fresh, steps, remaining)
            proposal = planner.propose_next(context)
            # R2.6 planner evaluation metadata on the durable run - written
            # for EVERY concluded planning turn, including STOP outcomes.
            trail = getattr(planner, "last_planning_trail", None)
            planner_source = None
            fallback_reason = None
            if trail:
                planner_source = trail.get("planning_source")
                if planner_source == "fallback":
                    fallback_reason = next(
                        (str(c.get("reason")) for c in trail["candidates"]
                         if c.get("verdict") == "rejected"), None)
            self.run_store.update_planner_metadata(
                run_id, planner_mode=mode_value,
                planner_source=planner_source,
                fallback_reason=fallback_reason)
            if proposal.action_type is ResearchAction.STOP:
                stop_reason = (StopReason.INSUFFICIENT_EVIDENCE
                               if proposal.reason_code is ReasonCode.INSUFFICIENT_EVIDENCE
                               else StopReason.OBJECTIVE_SATISFIED)
                final_status = (RunStatus.PARTIAL
                                if stop_reason is StopReason.INSUFFICIENT_EVIDENCE
                                else RunStatus.COMPLETED)
                break

            # No-progress guard: an identical already-resolved proposal is
            # never silently repeated (denials are terminal for that angle).
            # FETCH is exempt: duplicate identity suppression lives in the
            # dispatch layer and must be OBSERVABLE as its own outcome.
            proposal_action = str(getattr(proposal.action_type, "value",
                                          proposal.action_type))
            signature = _signature(proposal_action,
                                   proposal.arguments)
            resolved = next(
                (s for s in steps if s["action_type"] == proposal_action
                 and s["outcome"] in RESOLVED_OUTCOMES
                 and _signature(s["action_type"], s["arguments"]) == signature),
                None)
            if resolved is not None and proposal_action != \
                    ResearchAction.FETCH_RESULT.value:
                if resolved["outcome"] == "denied":
                    stop_reason = StopReason.APPROVAL_DENIED
                else:
                    stop_reason = StopReason.INSUFFICIENT_EVIDENCE
                final_status = RunStatus.PARTIAL
                break

            touched += 1
            try:
                outcome_payload = self._dispatch(proposal, fresh)
            except InvalidProposal as exc:
                # A planner proposal outside the closed action set is an
                # actual error: truthful FAILED, never a fuzzy retry.
                self.run_store.add_step(
                    run_id=run_id, ordinal=len(steps) + 1, proposal=proposal,
                    decision="rejected", outcome="rejected_unknown_action",
                    result={"error": str(exc)})
                self.run_store.set_run_status(run_id, RunStatus.FAILED,
                                              StopReason.ERROR)
                return {"run_id": run_id,
                        "status": RunStatus.FAILED.value,
                        "stop_reason": StopReason.ERROR.value,
                        "steps_executed": touched}
            decision = ("awaiting_approval"
                        if outcome_payload.get("awaiting_approval")
                        else "denied" if outcome_payload["outcome"] == "denied"
                        else "rejected" if outcome_payload.get("failed")
                        else "authorized")
            self.run_store.add_step(
                run_id=run_id, ordinal=len(steps) + 1, proposal=proposal,
                decision=decision,
                execution_ref=outcome_payload.get("execution_ref"),
                outcome=outcome_payload["outcome"], result=outcome_payload)

            if outcome_payload.get("awaiting_approval"):
                self.run_store.set_run_status(run_id,
                                              RunStatus.WAITING_APPROVAL)
                return {"run_id": run_id,
                        "status": RunStatus.WAITING_APPROVAL.value,
                        "stop_reason": None, "steps_executed": touched,
                        "awaiting_execution":
                            outcome_payload.get("execution_ref")}
            if outcome_payload.get("limit"):
                stop_reason = StopReason(outcome_payload["limit"])
                final_status = RunStatus.PARTIAL
                break
            if (proposal.action_type is ResearchAction.SYNTHESIZE
                    and outcome_payload.get("grounding_status")
                    in GROUNDING_SATISFIED
                    and not [c for c in context["claims"]
                             if c["coverage"] == Coverage.NO_EVIDENCE.value]):
                stop_reason = StopReason.OBJECTIVE_SATISFIED
                final_status = RunStatus.COMPLETED
                break

        self.run_store.set_run_status(run_id, final_status, stop_reason)
        return {"run_id": run_id, "status": final_status.value,
                "stop_reason": stop_reason.value if stop_reason else None,
                "steps_executed": touched}

    def cancel(self, run_id: str) -> dict[str, Any] | None:
        return self.run_store.cancel_run(run_id)

    # -- dispatch ------------------------------------------------------

    def _dispatch(self, proposal, run) -> dict[str, Any]:
        """Execute one action through authoritative repositories."""
        action = proposal.action_type
        args = dict(proposal.arguments or {})
        project_id = run["project_id"]

        if action is ResearchAction.SEARCH_WEB:
            return self._dispatch_network(
                NetworkToolPolicy.SEARCH_TOOL_ID, "search",
                {"query": str(args.get("query", ""))[:MAX_QUERY_LENGTH],
                 "max_results": int(args.get("max_results", 10))},
                scope=f"query:{str(args.get('query', ''))[:60]}",
                run=run, action_label=ResearchAction.SEARCH_WEB.value,
                limit_key="searches", limit_stop=StopReason.SEARCH_LIMIT)
        if action is ResearchAction.FETCH_RESULT:
            return self._dispatch_fetch(args, run)
        if action is ResearchAction.SEARCH_LOCAL:
            mode = str(args.get("retrieval_mode", "lexical")).lower()
            if mode != "lexical" and self.semantic_engine is not None:
                return self._dispatch_local_retrieval(args, run)
            hits = self.ingestor.search(args.get("query", ""),
                                        project_id=project_id, limit=10)
            return {"outcome": "executed", "local_hits": len(hits),
                    "requested_mode": "lexical",
                    "effective_mode": "lexical"}
        if action is ResearchAction.CREATE_OR_REFINE_QUESTION:
            question = self.synthesis_store.create_question(
                session_id=run["session_id"], project_id=project_id,
                question=str(args.get("question", ""))[:MAX_QUERY_LENGTH],
                purpose=args.get("purpose"),
                parent_question_id=args.get("parent_question_id"))
            return {"outcome": "executed", "question_id":
                    question["question_id"]}
        if action is ResearchAction.CREATE_OR_UPDATE_CLAIM:
            try:
                claim = self.synthesis_store.create_claim(
                    session_id=run["session_id"], project_id=project_id,
                    claim_text=str(args.get("claim_text", "")),
                    claim_type=ClaimType(str(args["claim_type"]))
                    if not isinstance(args["claim_type"], ClaimType)
                    else args["claim_type"],
                    question_id=args.get("question_id"))
            except (KeyError, ValueError, TypeError) as exc:
                return {"outcome": "rejected_invalid_arguments",
                        "failed": True, "error": str(exc)}
            return {"outcome": "executed", "claim_id": claim["claim_id"]}
        if action is ResearchAction.LINK_EVIDENCE:
            try:
                link = self.synthesis_store.link_evidence(
                    claim_id=args["claim_id"], evidence_id=args["evidence_id"],
                    relation=EvidenceRelation(str(args["relation"])),
                    assessor="deterministic-orchestrator",
                    method="orchestrator-link")
            except (KeyError, ValueError, TypeError, ResearchError) as exc:
                return {"outcome": "rejected_unknown_entity",
                        "failed": True, "error": str(exc)}
            except Exception as exc:
                return {"outcome": "rejected_unknown_entity",
                        "failed": True, "error": str(exc)}
            return {"outcome": "executed", "link_id": link["link_id"]}
        if action is ResearchAction.SYNTHESIZE:
            return self._dispatch_synthesize(args, run)
        raise InvalidProposal(f"unknown action: {action!r}")

    def _dispatch_local_retrieval(self, args, run) -> dict[str, Any]:
        """R2.2: requested/effective retrieval mode with truthful
        degradation; provenance journaled WITHOUT raw vectors."""
        try:
            result = self.semantic_engine.local_search(
                str(args.get("query", "")), project_id=run["project_id"],
                mode=str(args.get("retrieval_mode")),
                session_id=run["session_id"],
                limit=int(args.get("limit", 10)))
        except SemanticBackendUnavailable as exc:
            return {"outcome": "semantic_backend_unavailable",
                    "failed": True, "requested_mode":
                        str(args.get("retrieval_mode")),
                    "effective_mode": None, "error": str(exc)}
        except EmbeddingError as exc:
            return {"outcome": "rejected_invalid_arguments",
                    "failed": True, "error": str(exc)}
        payload = {"outcome": "executed", "local_hits":
                   result.pop("hit_count", 0)}
        payload.update(result)
        if args.get("build_context") and self.context_builder is not None \
                and self.semantic_engine is not None:
            try:
                package = build_from_local_search(
                    self.context_builder,
                    retrieval_store=self.semantic_engine.retrieval_store,
                    research_store=self.research_store,
                    project_id=run["project_id"],
                    session_id=run["session_id"],
                    purpose=str(args.get("context_purpose", "research")),
                    search_result=payload,
                    claim_ids=args.get("claim_ids") or None,
                    context_store=self.context_store)
                payload["context_package_id"] = package["context_id"]
                payload["context_status"] = package["status"]
                payload["context_warnings"] = package["warnings"]
                payload["reranker_used"] = bool(package["rerank_effective"])
                payload["context_items_selected"] = package[
                    "selected_items"]
                payload.pop("hits", None)  # keep the journal bounded
            except RerankError as exc:
                payload["context_error"] = str(exc)
        return payload

    # -- synthesis (pure local, zero network) ---------------------------

    def _dispatch_synthesize(self, args, run) -> dict[str, Any]:
        plan_id = args.get("plan_id")
        if plan_id:
            plan = self.synthesis_store.get_plan(plan_id)
            if plan is None:
                return {"outcome": "rejected_unknown_entity", "failed": True,
                        "error": f"unknown plan {plan_id!r}"}
        else:
            plan = self._default_plan_for(run)
        context = self.build_context_for_plan(plan)
        draft = DeterministicSynthesizer().synthesize(context)
        grounding = evaluate_grounding(self.synthesis_store, context, draft)
        version = self.synthesis_store.save_synthesis_version(
            plan_id=plan["plan_id"], grounding_status=grounding, draft=draft)
        return {"outcome": f"synthesis {grounding.value}",
                "grounding_status": grounding.value,
                "plan_id": plan["plan_id"],
                "version_id": version["version_id"]}

    def _default_plan_for(self, run):
        session_id = run["session_id"]
        project_id = run["project_id"]
        questions = self.synthesis_store.list_questions(
            session_id, project_id=project_id)
        claim_ids = self.synthesis_store.claim_ids_for_session(
            session_id, project_id=project_id)
        return self.synthesis_store.create_plan(
            session_id=session_id, project_id=project_id,
            question_ids=[q["question_id"] for q in questions],
            claim_ids=list(claim_ids),
            output_form=SynthesisOutputForm.BRIEF)

    def build_context_for_plan(self, plan):
        """Assemble bounded synthesis context for one durable plan."""
        return self.synthesis_store.build_context(plan)

    # -- network actions (Tool Fabric gated, two-phase) ------------------

    def submit_network_action(self, proposal, run):
        """Submit a network ToolRequest and report the gate decision."""
        args = proposal.arguments or {}
        tool_id = (NetworkToolPolicy.FETCH_TOOL_ID
                   if proposal.action_type is ResearchAction.FETCH_RESULT
                   else NetworkToolPolicy.SEARCH_TOOL_ID)
        request = ToolRequest(
            request_id=_uuid("treq"),
            organization_id="agent-fabric",
            project_id=run["project_id"],
            tool_id=tool_id,
            operation="acquire"
            if proposal.action_type is ResearchAction.FETCH_RESULT
            else "search",
            normalized_arguments=dict(args),
            requested_resource_scope=args.get("url"),
            actor="research-orchestrator",
        )
        decision, record = self.fabric.submit(request, dict(args))
        return decision, record

    def _dispatch_fetch(self, args, run) -> dict[str, Any]:
        """FETCH_RESULT: authoritative identity resolution + gating.

        The fetch target is ONLY the stored URL of the durable search
        result; a planner-supplied url never overrides it. Duplicate fetches
        of an already-observed result are suppressed unless an explicit
        refresh reason permits a new observation.
        """
        session_id = run["session_id"]
        project_id = run["project_id"]
        result_id = args.get("search_result_id")
        stored = (self.research_store.get_search_result(
            result_id, project_id=project_id) if result_id else None)
        if stored is None:
            return {"outcome": "rejected_unknown_result", "failed": True,
                    "error": f"unknown search_result_id: {result_id!r}"}
        url = stored["url"]
        payload: dict[str, Any] = {}
        if args.get("url") and args["url"] != url:
            payload["url_override_rejected"] = True

        refresh_reason = args.get("refresh_reason")
        refresh_requested = bool(args.get("refresh")) and bool(refresh_reason)
        if not refresh_requested:
            prior_ids, prior_urls = self._fetched_identities(
                run["run_id"], project_id)
            if (str(result_id) in prior_ids or url in prior_urls
                    or self._acquisition_committed(project_id, url)):
                payload.update({"outcome": "duplicate_suppressed"})
                return payload

        limits = run["limits"]
        used_sources = len(self.research_store.list_sources(
            run["session_id"], project_id=project_id))
        if used_sources >= int(limits.get("max_sources", 0)) \
                and not self._acquisition_committed(project_id, url):
            return {"outcome": "source_limit_exhausted", "failed": True,
                    "limit": StopReason.SOURCE_LIMIT.value}

        request_args = {"url": url, "search_result_id": result_id}
        return self._dispatch_network(
            NetworkToolPolicy.FETCH_TOOL_ID, "acquire", request_args,
            scope=url, run=run,
            action_label=ResearchAction.FETCH_RESULT.value,
            extra_payload=payload,
            limit_key="fetches", limit_stop=StopReason.FETCH_LIMIT)

    def _dispatch_network(self, tool_id: str, operation: str,
                          normalized: dict[str, Any], *, scope: str | None,
                          run, action_label: str,
                          extra_payload: dict[str, Any] | None = None,
                          limit_key: str | None = None,
                          limit_stop: StopReason | None = None) \
            -> dict[str, Any]:
        """Phase A: gate one network action. Hard per-action limits are
        enforced BEFORE any transport work; DENY -> zero requests;
        REQUIRE_APPROVAL -> WAITING_APPROVAL pause."""
        if limit_key is not None:
            remaining_budget = self._remaining(
                run, self.run_store.steps_for(run["run_id"]))[limit_key]
            if remaining_budget <= 0:
                return {"outcome": f"{limit_key}_limit_exhausted",
                        "failed": True,
                        "limit": limit_stop.value if limit_stop else None}
        request = ToolRequest(
            request_id=_uuid("treq"),
            organization_id="agent-fabric",
            project_id=run["project_id"],
            tool_id=tool_id,
            operation=operation,
            normalized_arguments=dict(normalized),
            requested_resource_scope=scope,
            actor="research-orchestrator",
        )
        decision_result, record = self.fabric.submit(request, dict(normalized))
        payload: dict[str, Any] = dict(extra_payload or {})
        if decision_result.decision is Decision.DENY:
            payload.update({"outcome": "denied", "failed": True,
                            "execution_ref": record.execution_id})
            return payload
        if decision_result.decision is Decision.REQUIRE_APPROVAL:
            # The orchestrator NEVER approves its own network work.
            payload.update({"outcome": "awaiting_approval",
                            "awaiting_approval": True,
                            "execution_ref": record.execution_id})
            return payload
        payload["execution_ref"] = record.execution_id
        backend_payload = self._network_backend(
            record.tool_id, record.execution_id, normalized, run)
        payload.update(backend_payload)
        receipt_failed = backend_payload.get("failed")
        payload["outcome"] = ("failed" if receipt_failed
                              else backend_payload.get(
                                  "outcome", "executed"))
        return payload

    def _execute_network_backend(self, step, run) -> dict[str, Any]:
        """Phase B: run an ALREADY EXTERNALLY APPROVED execution once."""
        tool_id = (NetworkToolPolicy.FETCH_TOOL_ID
                   if step["action_type"] == ResearchAction.FETCH_RESULT.value
                   else NetworkToolPolicy.SEARCH_TOOL_ID)
        payload = self._network_backend(tool_id, step["execution_ref"],
                                        dict(step["arguments"]), run)
        merged = {**(step.get("result") or {}), **payload}
        merged["outcome"] = ("failed" if merged.get("failed")
                             else merged.get("outcome", "executed"))
        return merged

    def _network_backend(self, tool_id: str, execution_id: str,
                         normalized: dict[str, Any], run) -> dict[str, Any]:
        """Bind the real work as the fabric backend and execute exactly once.

        The actual fetch goes through the accepted R1.3A WebAcquisitionClient
        (SSRF/DNS-pinning/bounds live THERE, not here); the actual search
        goes through the injected provider and lands a durable
        ResearchSearchEvent.
        """
        project_id = run["project_id"]
        session_id = run["session_id"]
        holder: dict[str, Any] = {}

        if tool_id == NetworkToolPolicy.FETCH_TOOL_ID:
            # Authoritative identity AGAIN at execution time: the stored
            # result url wins over anything journaled/planner-supplied.
            result_id = normalized.get("search_result_id")
            stored = (self.research_store.get_search_result(
                result_id, project_id=project_id) if result_id else None)
            url = stored["url"] if stored else normalized.get("url")

            def fetch_backend():
                client = self._require_client()
                outcome = client.fetch(url)
                holder["outcome"] = outcome
                if outcome.status is AcquisitionStatus.FAILED:
                    raise RuntimeError(outcome.error or "acquisition failed")
                if outcome.status is AcquisitionStatus.TIMED_OUT:
                    raise TimeoutError("acquisition timed out")
                return {"summary":
                        f"{outcome.byte_count} bytes from {outcome.final_url}",
                        "side_effect_summary": f"http {outcome.http_status}"}

            self.fabric.set_backend(NetworkToolPolicy.FETCH_TOOL_ID,
                                    fetch_backend)
            receipt = self.fabric.execute(execution_id)
            outcome = holder.get("outcome")
            payload: dict[str, Any] = {
                "execution_ref": execution_id,
                "receipt_status": receipt["status"] if receipt else None}
            if outcome is None or outcome.status not in (
                    AcquisitionStatus.SUCCEEDED,
                    AcquisitionStatus.TRUNCATED):
                payload["failed"] = True
                payload.setdefault(
                    "outcome",
                    (outcome.status.value if outcome else "failed"))
                return payload

            acquisition_id = _uuid("acq")
            self.acq_store.save_acquisition({
                "acquisition_id": acquisition_id,
                "project_id": project_id,
                "session_id": session_id,
                "requested_url": url,
                "final_url": outcome.final_url,
                "redirect_chain": list(outcome.redirect_chain),
                "status": outcome.status.value,
                "http_status": outcome.http_status,
                "content_type": outcome.content_type,
                "byte_count": outcome.byte_count,
                "started_at": (receipt or {}).get("started_at") or _now(),
                "completed_at": (receipt or {}).get("completed_at") or _now(),
                "tool_execution_id": execution_id,
                "etag": outcome.headers.get("etag"),
                "last_modified": outcome.headers.get("last-modified"),
                "warnings": outcome.error,
            })
            created = self.ingestor.ingest_artifact_snapshot(
                session_id=session_id, project_id=project_id,
                artifact_id=url[:80], version=1, content=outcome.body,
                title=outcome.final_url or url,
                content_type=outcome.content_type)
            with self.research_store._connect() as connection:
                connection.execute(
                    "UPDATE research_sources SET source_kind='web',"
                    " canonical_origin=? WHERE source_id=?",
                    (outcome.final_url, created["source"]["source_id"]))
            extraction = self.ingestor.extract(
                created["snapshot"]["snapshot_id"], project_id=project_id)
            payload.update({
                "outcome": "executed",
                "acquisition_id": acquisition_id,
                "source_id": created["source"]["source_id"],
                "snapshot_id": created["snapshot"]["snapshot_id"],
                "final_url": outcome.final_url,
                "extraction_status": extraction["status"],
                "segment_count": extraction["segment_count"],
                "truncated": outcome.status is AcquisitionStatus.TRUNCATED,
            })
            return payload

        # search backend
        provider = self.search_provider
        query = normalized.get("query", "")
        max_results = int(normalized.get("max_results", 10))

        def search_backend():
            started = _now()
            results = provider.search(query, max_results=max_results)
            completed = _now()
            event = self.research_store.record_search_event(
                project_id=project_id, session_id=session_id,
                provider_id=getattr(provider, "provider_id", "unknown"),
                provider_version=getattr(provider, "provider_version", ""),
                query=query, options={"max_results": max_results},
                started_at=started, completed_at=completed,
                status="committed",
                results=[{"rank": r.rank, "title": r.title, "url": r.url,
                          "snippet": r.snippet} for r in results],
                error_reason=None, tool_execution_id=execution_id)
            holder["event"] = event
            return {"summary": f"{len(results)} normalized results"}

        self.fabric.set_backend(NetworkToolPolicy.SEARCH_TOOL_ID,
                                search_backend)
        receipt = self.fabric.execute(execution_id)
        event = holder.get("event")
        payload = {"execution_ref": execution_id,
                   "receipt_status": receipt["status"] if receipt else None}
        if receipt and receipt["status"] == "succeeded" and event:
            payload["outcome"] = "executed"
            payload["event_id"] = event["search_event_id"]
        else:
            payload["failed"] = True
            payload.setdefault("outcome", "provider_call_failed")
        return payload

    # -- helpers ---------------------------------------------------------

    def _require_client(self) -> WebAcquisitionClient:
        if self.acquisition_client is None:
            raise RuntimeError(
                "no acquisition client configured for FETCH_RESULT")
        return self.acquisition_client

    def _fetched_identities(self, run_id: str, project_id: str) \
            -> tuple[set, set]:
        """Result ids / urls with a COMMITTED fetch identity (run steps plus
        any committed acquisition record for the project)."""
        sids: set = set()
        urls: set = set()
        for step in self.run_store.steps_for(run_id):
            if (step["action_type"] == ResearchAction.FETCH_RESULT.value
                    and step["outcome"] in ("executed",
                                            "duplicate_suppressed")):
                result_id = str((step.get("arguments") or {}).get(
                    "search_result_id"))
                sids.add(result_id)
                stored = self.research_store.get_search_result(
                    result_id, project_id=project_id)
                if stored is not None:
                    urls.add(stored["url"])
        with self.acq_store._connect() as connection:
            rows = connection.execute(
                """SELECT requested_url, final_url FROM
                   research_web_acquisitions
                   WHERE project_id = ?
                     AND status IN ('succeeded','truncated')""",
                (project_id,)).fetchall()
        for row in rows:
            urls.add(row["requested_url"])
            urls.add(row["final_url"])
        return sids, urls

    def _acquisition_committed(self, project_id: str, url: str) -> bool:
        with self.acq_store._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM research_web_acquisitions
                   WHERE project_id = ?
                     AND status IN ('succeeded','truncated')
                     AND (requested_url = ? OR final_url = ?) LIMIT 1""",
                (project_id, url, url)).fetchone()
        return row is not None

    def _pending_awaiting_step(self, steps):
        for step in reversed(steps):
            if (step["decision"] == "awaiting_approval"
                    and step.get("execution_ref")):
                return step
        return None

    def _uncertain_execution(self, steps):
        """A step whose execution STARTED but never committed its outcome."""
        for step in steps:
            ref = step.get("execution_ref")
            if not ref:
                continue
            status = self._execution_status(ref)
            if status in (ExecutionStatus.RUNNING.value,
                          ExecutionStatus.INTERRUPTED_AFTER_START.value):
                return step
        return None

    def _execution_status(self, execution_id: str) -> str | None:
        row = self.fabric.store.get_execution(execution_id)
        return row["status"] if row else None
