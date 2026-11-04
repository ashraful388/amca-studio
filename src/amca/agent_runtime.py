"""Live Agent Fabric runtime (milestone A1).

Turns the deterministic planning layer's ``task_graph`` into real bounded
executions with persisted lifecycle records.  Every visible agent count and
status derives from these records - the runtime never reports fictitious
workers.  The execution cascade is deterministic-first: a task marked
``deterministic_tool`` completes without any model call, ``nano_local_model``
tasks call exactly one configured local adapter, and escalation beyond that
stays out of this slice until measured evidence justifies it.

Governor limits are enforced mechanically: hard concurrency ceiling, per-task
retry bound, and an absolute USD budget after which remaining work is
cancelled rather than silently overrun.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import math
import re
from typing import Any
from uuid import uuid4

from amca.agent_fabric import AgentFabricSettings, AgentGovernor, ResourceEnvelope
from amca.domain import (
    CollaborationMode,
    ComputePolicy,
    PrivacyMode,
    ProjectState,
    SyncMode,
)
from amca.kernel import CollaborationKernel, KernelConfig
from amca.models.base import ModelRequest
from amca.models.tool_protocol import (
    ToolCall,
    ToolResult,
    ToolResultState,
    effective_tool_support,
    ensure_tool_definition,
)
from amca.modes import DecompositionPlan, WorkUnit
from amca.state import StateManager


def _is_adapter_local(adapter: Any) -> bool:
    """Classify adapter as local compute vs cloud/API using capabilities or attribute."""
    if hasattr(adapter, "capabilities") and callable(adapter.capabilities):
        try:
            return bool(getattr(adapter.capabilities(), "is_local", False))
        except Exception:
            pass
    return bool(getattr(adapter, "is_local", True))


def parse_verifier_payload(text: str) -> dict[str, Any]:
    """Parse strictly-structured verifier payload.

    Requires a valid JSON object with:
    - "verdict": "pass" | "fail" | "uncertain"
    - "summary": non-empty string <= 400 chars
    - "evidence_refs": optional list of strings <= 100 chars (max 20)

    Unstructured text, missing summary, or unknown verdict enums are treated
    strictly as uncertain/malformed and can never produce a verified outcome.
    """
    trimmed = str(text or "").strip()
    if not trimmed:
        return {"verdict": "uncertain", "summary": "empty verifier response", "evidence_refs": []}

    json_candidate = None
    if trimmed.startswith("{") and trimmed.endswith("}"):
        json_candidate = trimmed
    else:
        match = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", trimmed, re.DOTALL)
        if match:
            json_candidate = match.group(0)

    if json_candidate:
        try:
            doc = json.loads(json_candidate)
            if isinstance(doc, Mapping):
                raw_verdict = str(doc.get("verdict", "")).strip().casefold()
                summary_raw = str(doc.get("summary", "")).strip()
                if not summary_raw:
                    return {
                        "verdict": "uncertain",
                        "summary": "malformed verifier response: missing required summary",
                        "evidence_refs": [],
                    }
                if raw_verdict not in {"pass", "fail", "uncertain"}:
                    return {
                        "verdict": "uncertain",
                        "summary": f"malformed verifier response: invalid verdict enum {raw_verdict!r}",
                        "evidence_refs": [],
                    }
                refs_raw = doc.get("evidence_refs", [])
                evidence_refs: list[str] = []
                if isinstance(refs_raw, (list, tuple)):
                    for r in refs_raw[:20]:
                        ref_str = str(r).strip()
                        if ref_str:
                            evidence_refs.append(ref_str[:100])

                return {
                    "verdict": raw_verdict,
                    "summary": summary_raw[:400],
                    "evidence_refs": evidence_refs,
                }
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Raw prose / non-structured text can NEVER become a valid pass or fail verdict.
    return {
        "verdict": "uncertain",
        "summary": f"non-structured or unparseable verifier output: {trimmed[:200]}",
        "evidence_refs": [],
    }


class AgentTaskStatus:
    """Lifecycle states with the single allowed transition map."""

    PROPOSED = "proposed"
    AUTHORIZED = "authorized"
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    MERGED = "merged"
    RETIRED = "retired"
    FAILED = "failed"
    CANCELLED = "cancelled"

    ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
        PROPOSED: frozenset({AUTHORIZED}),
        # authorized -> completed is taken ONLY by the duplicate-reuse path,
        # where a previously verified result removes any need for dispatch.
        AUTHORIZED: frozenset({QUEUED, COMPLETED, CANCELLED}),
        QUEUED: frozenset({STARTING, CANCELLED}),
        STARTING: frozenset({RUNNING, FAILED, CANCELLED}),
        RUNNING: frozenset({WAITING, VERIFYING, COMPLETED, FAILED, CANCELLED}),
        WAITING: frozenset({RUNNING, CANCELLED}),
        # PS1-13D (PS1-13B-FUNC-01): VERIFYING is cancellable through the
        # EXISTING canonical cancel path (no parallel mechanism).
        VERIFYING: frozenset({COMPLETED, FAILED, CANCELLED}),
        COMPLETED: frozenset({MERGED}),
        MERGED: frozenset({RETIRED}),
        RETIRED: frozenset(),
        FAILED: frozenset({QUEUED}),
        CANCELLED: frozenset(),
    }


#: Agent-to-agent handoff.  A task whose ``depends_on`` has completed receives
#: a bounded, structured envelope describing its dependencies' results, so the
#: downstream agent works from the upstream output instead of starting blind.
#:
#: The envelope is deliberately a *reference plus a bounded excerpt* rather than
#: the upstream text verbatim.  Concatenating every upstream result into every
#: downstream prompt grows the context without bound as a graph widens, which is
#: both a cost problem and a correctness one: the agent cannot tell which part
#: came from where.  ``HANDOFF_TOTAL_BUDGET`` caps the whole block across all
#: dependencies, not each one, so a task with ten dependencies is not ten times
#: as expensive as a task with one.
HANDOFF_HEADER = "Results from the agents that ran before you"
MAX_HANDOFF_CHARS = 4000
HANDOFF_TOTAL_BUDGET = 6000
HANDOFF_EXCERPT_CHARS = 900




def build_handoff_envelope(task: "AgentTaskRecord", org: "OrganizationRun") -> str:
    """A bounded, structured description of what this task's dependencies produced.

    Each dependency contributes a labelled block carrying its identity, status,
    a content digest so the downstream agent can tell whether it is looking at
    the same result, and a bounded excerpt.  The whole envelope is capped by
    ``HANDOFF_TOTAL_BUDGET`` across every dependency, so a wide graph cannot
    inflate the prompt without limit.

    Returns an empty string when nothing upstream produced a result, so a caller
    never appends an empty section.
    """
    blocks: list[str] = []
    remaining = HANDOFF_TOTAL_BUDGET
    for dependency in task.depends_on:
        upstream = org.tasks.get(dependency)
        if upstream is None or not upstream.result_summary:
            continue
        if remaining <= 0:
            blocks.append(
                f"[{dependency}] {upstream.agent_id}: result withheld — "
                "handoff budget exhausted; read it from shared state if needed"
            )
            continue
        text = upstream.result_summary
        excerpt = text[: min(HANDOFF_EXCERPT_CHARS, remaining)]
        remaining -= len(excerpt)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        truncated = " (excerpt)" if len(excerpt) < len(text) else ""
        blocks.append(
            f"[{dependency}] {upstream.agent_id}\n"
            f"  status: {upstream.status}\n"
            f"  result_digest: {digest}  chars: {len(text)}\n"
            f"  result{truncated}:\n{excerpt}"
        )
    if not blocks:
        return ""
    return "\n\n".join(blocks)


class ExecutorKind:
    DETERMINISTIC_TOOL = "deterministic_tool"
    NANO_LOCAL_MODEL = "nano_local_model"
    NANO_CLOUD_MODEL = "nano_cloud_model"
    VERIFICATION_CELL = "verification_cell"
    MANAGER = "manager"
    COLLABORATION = "collaboration"


def parse_manager_proposal_payload(
    text: str,
    *,
    manager_id: str,
    organization_id: str,
    project_id: str,
    manager_depth: int = 0,
    max_children: int = 8,
    max_focus_length: int = 400,
    max_payload_size: int = 8192,
    existing_task_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Strictly parse and validate manager child-task proposal payload.

    Returns a structured dictionary:
    {
        "valid": bool,
        "version": int,
        "manager_id": str,
        "reason": str,
        "children": list[AgentTaskRecord],
        "error": str | None,
        "raw_payload": str,
    }
    """
    raw_text = str(text or "").strip()
    if len(raw_text.encode("utf-8")) > max_payload_size:
        return {
            "valid": False,
            "version": 1,
            "manager_id": manager_id,
            "reason": "",
            "children": [],
            "error": f"proposal payload size ({len(raw_text.encode('utf-8'))} bytes) exceeds limit ({max_payload_size} bytes)",
            "raw_payload": raw_text[:500],
        }

    if not raw_text:
        return {
            "valid": False,
            "version": 1,
            "manager_id": manager_id,
            "reason": "",
            "children": [],
            "error": "empty manager proposal output",
            "raw_payload": "",
        }

    json_str = None
    if raw_text.startswith("{") and raw_text.endswith("}"):
        json_str = raw_text
    else:
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            json_str = match.group(0)

    if not json_str:
        return {
            "valid": False,
            "version": 1,
            "manager_id": manager_id,
            "reason": "",
            "children": [],
            "error": f"non-structured or prose output cannot be parsed into proposals: {raw_text[:200]}",
            "raw_payload": raw_text[:500],
        }

    try:
        doc = json.loads(json_str)
    except Exception as exc:
        return {
            "valid": False,
            "version": 1,
            "manager_id": manager_id,
            "reason": "",
            "children": [],
            "error": f"invalid JSON in proposal: {exc}",
            "raw_payload": raw_text[:500],
        }

    if not isinstance(doc, Mapping):
        return {
            "valid": False,
            "version": 1,
            "manager_id": manager_id,
            "reason": "",
            "children": [],
            "error": "proposal root must be a JSON object",
            "raw_payload": raw_text[:500],
        }

    version = doc.get("version", 1)
    if not isinstance(version, int) or version < 1:
        return {
            "valid": False,
            "version": 1,
            "manager_id": manager_id,
            "reason": "",
            "children": [],
            "error": f"unsupported proposal version {version!r}",
            "raw_payload": raw_text[:500],
        }

    reason = str(doc.get("reason", "")).strip()[:400]
    children_raw = doc.get("children", [])
    if not isinstance(children_raw, (list, tuple)):
        return {
            "valid": False,
            "version": version,
            "manager_id": manager_id,
            "reason": reason,
            "children": [],
            "error": "proposal 'children' must be a list",
            "raw_payload": raw_text[:500],
        }

    if len(children_raw) > max_children:
        return {
            "valid": False,
            "version": version,
            "manager_id": manager_id,
            "reason": reason,
            "children": [],
            "error": f"proposed child count {len(children_raw)} exceeds manager limit of {max_children}",
            "raw_payload": raw_text[:500],
        }

    known_ids = set(existing_task_ids or ())
    proposed_records: list[AgentTaskRecord] = []
    seen_digests: set[str] = set()
    declared_child_ids: set[str] = set()

    for i, c_raw in enumerate(children_raw):
        if not isinstance(c_raw, Mapping):
            return {
                "valid": False,
                "version": version,
                "manager_id": manager_id,
                "reason": reason,
                "children": [],
                "error": f"child [{i}] must be an object",
                "raw_payload": raw_text[:500],
            }

        focus = str(c_raw.get("focus", "")).strip()
        if not focus:
            return {
                "valid": False,
                "version": version,
                "manager_id": manager_id,
                "reason": reason,
                "children": [],
                "error": f"child [{i}] missing required focus",
                "raw_payload": raw_text[:500],
            }
        if len(focus) > max_focus_length:
            return {
                "valid": False,
                "version": version,
                "manager_id": manager_id,
                "reason": reason,
                "children": [],
                "error": f"child [{i}] focus length {len(focus)} exceeds limit {max_focus_length}",
                "raw_payload": raw_text[:500],
            }

        ex_kind = str(c_raw.get("executor_kind", ExecutorKind.NANO_LOCAL_MODEL)).strip()
        if ex_kind not in {
            ExecutorKind.DETERMINISTIC_TOOL,
            ExecutorKind.NANO_LOCAL_MODEL,
            ExecutorKind.VERIFICATION_CELL,
            ExecutorKind.MANAGER,
            ExecutorKind.COLLABORATION,
        }:
            return {
                "valid": False,
                "version": version,
                "manager_id": manager_id,
                "reason": reason,
                "children": [],
                "error": f"child [{i}] has unknown or unpermitted executor_kind {ex_kind!r}",
                "raw_payload": raw_text[:500],
            }

        child_id = str(c_raw.get("id", f"{manager_id}.c{i+1}")).strip()
        if not child_id:
            child_id = f"{manager_id}.c{i+1}"

        if child_id in declared_child_ids or child_id in known_ids:
            child_id = f"{manager_id}.c{i+1}_{uuid4().hex[:6]}"

        declared_child_ids.add(child_id)

        deps_raw = c_raw.get("depends_on", [])
        if not isinstance(deps_raw, (list, tuple)):
            deps_raw = []
        deps = tuple(str(d).strip() for d in deps_raw if str(d).strip())

        # Check self-dependency
        if child_id in deps or (manager_id in deps and manager_id == child_id):
            return {
                "valid": False,
                "version": version,
                "manager_id": manager_id,
                "reason": reason,
                "children": [],
                "error": f"child [{child_id}] contains self-dependency {child_id!r}",
                "raw_payload": raw_text[:500],
            }

        # Check unknown dependency
        for dep in deps:
            if dep not in known_ids and dep not in declared_child_ids:
                return {
                    "valid": False,
                    "version": version,
                    "manager_id": manager_id,
                    "reason": reason,
                    "children": [],
                    "error": f"child [{child_id}] depends on unknown or future dependency {dep!r}",
                    "raw_payload": raw_text[:500],
                }

        # Verification Cell check
        v_ids: tuple[str, ...] = ()
        if ex_kind == ExecutorKind.VERIFICATION_CELL:
            raw_v = c_raw.get("verifier_ids", c_raw.get("requested_verifier_ids", []))
            if not isinstance(raw_v, (list, tuple)) or len([v for v in raw_v if str(v).strip()]) < 2:
                return {
                    "valid": False,
                    "version": version,
                    "manager_id": manager_id,
                    "reason": reason,
                    "children": [],
                    "error": f"verification cell child [{child_id}] requires at least 2 explicit verifier_ids",
                    "raw_payload": raw_text[:500],
                }
            v_ids = tuple(str(v).strip() for v in raw_v if str(v).strip())

        # Collaboration check
        p_ids: tuple[str, ...] = ()
        collab_arch = None
        collab_sync = None
        collab_payload = None
        max_rounds = 1
        max_adaptive_phases = 4
        decomp_plan = None
        if ex_kind == ExecutorKind.COLLABORATION:
            raw_p = c_raw.get("participant_ids", c_raw.get("participants", []))
            if not isinstance(raw_p, (list, tuple)) or len([p for p in raw_p if str(p).strip()]) < 2:
                return {
                    "valid": False,
                    "version": version,
                    "manager_id": manager_id,
                    "reason": reason,
                    "children": [],
                    "error": f"collaboration child [{child_id}] requires at least 2 explicit participant_ids",
                    "raw_payload": raw_text[:500],
                }
            p_ids = tuple(str(p).strip() for p in raw_p if str(p).strip())
            collab_arch = str(c_raw.get("collaboration_architecture", c_raw.get("architecture", "joint"))).strip().lower()
            collab_sync = str(c_raw.get("sync_mode", "bounded_staleness")).strip().lower()
            if isinstance(c_raw.get("collaboration_payload"), Mapping):
                collab_payload = dict(c_raw["collaboration_payload"])
            try:
                max_rounds = max(1, int(c_raw.get("max_rounds", c_raw.get("rounds", 1))))
            except Exception:
                max_rounds = 1
            try:
                max_adaptive_phases = max(1, int(c_raw.get("max_adaptive_phases", 4)))
            except Exception:
                max_adaptive_phases = 4
            decomp_plan = c_raw.get("decomposition_plan") or (collab_payload.get("decomposition_plan") if collab_payload else None)

        # Duplicate child detection
        digest_key = f"{focus}|{','.join(sorted(deps))}|{ex_kind}"
        if digest_key in seen_digests:
            return {
                "valid": False,
                "version": version,
                "manager_id": manager_id,
                "reason": reason,
                "children": [],
                "error": f"child [{child_id}] is a duplicate proposal in the same manager proposal set",
                "raw_payload": raw_text[:500],
            }
        seen_digests.add(digest_key)

        agent_id = str(c_raw.get("agent_id", "worker")).strip() or "worker"
        child_reason = str(c_raw.get("reason", "")).strip()[:400] or None

        record = AgentTaskRecord(
            id=child_id,
            organization_id=organization_id,
            project_id=project_id,
            agent_id=agent_id,
            focus=focus,
            status=AgentTaskStatus.PROPOSED,
            depends_on=deps,
            executor_kind=ex_kind,
            verifier_ids=v_ids,
            participant_ids=p_ids,
            collaboration_architecture=collab_arch,
            sync_mode=collab_sync,
            max_rounds=max_rounds,
            max_adaptive_phases=max_adaptive_phases,
            decomposition_plan=decomp_plan,
            collaboration_payload=collab_payload,
            proposed_by_manager_id=manager_id,
            manager_depth=manager_depth + 1,
            proposal_reason=child_reason,
            proposal_version=version,
        )
        proposed_records.append(record)

    return {
        "valid": True,
        "version": version,
        "manager_id": manager_id,
        "reason": reason,
        "children": proposed_records,
        "error": None,
        "raw_payload": raw_text[:500],
    }


@dataclass(slots=True)
class ManagerResponse:
    text: str
    cost_usd: float = 0.0

    @property
    def usage(self) -> Any:
        return type("Usage", (), {"cost_usd": self.cost_usd})()


class DeterministicManagerAdapter:
    """Scripted manager adapter producing deterministic structured proposals with zero model cost."""

    def __init__(
        self,
        proposal_payload: dict[str, Any] | str | None = None,
        cost_usd: float = 0.0,
        is_local: bool = True,
        is_conservative_bound: bool = True,
    ) -> None:
        self.cost_usd = cost_usd
        self.is_local = is_local
        self.is_conservative_bound = is_conservative_bound
        self.calls = 0
        if proposal_payload is None:
            self.payload_str = json.dumps({
                "version": 1,
                "reason": "deterministic task decomposition",
                "children": [
                    {
                        "id": "c1",
                        "focus": "collect metrics",
                        "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                        "agent_id": "tool_worker",
                        "depends_on": [],
                        "reason": "gather input metrics",
                    },
                    {
                        "id": "c2",
                        "focus": "analyze collected metrics",
                        "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                        "agent_id": "analyzer_worker",
                        "depends_on": ["c1"],
                        "reason": "evaluate gathered metrics",
                    },
                ],
            })
        elif isinstance(proposal_payload, str):
            self.payload_str = proposal_payload
        else:
            self.payload_str = json.dumps(proposal_payload)

    async def generate(self, request: Any) -> Any:
        self.calls += 1
        return ManagerResponse(self.payload_str, cost_usd=self.cost_usd)


def _now() -> str:
    return datetime.now(UTC).isoformat()


_MANAGER_PROPOSAL_CALL_KEY = "manager:proposal"


def _primary_call_key(task: "AgentTaskRecord", attempt: int) -> str:
    """Stable durable identity for a task attempt's primary paid call.

    The Manager proposal is its own stable sub-call (never a child-task
    identity); every other attempt anchors on the canonical task/attempt key.
    """

    if task.executor_kind == ExecutorKind.MANAGER:
        return _MANAGER_PROPOSAL_CALL_KEY
    return f"task:{task.id}:attempt:{attempt}"


def _ensure_durable_baseline(store: Any, org: "OrganizationRun", task: "AgentTaskRecord") -> None:
    """Idempotently mirror organization/task identity into the recovery store.

    Call-attempt and reservation rows carry foreign keys into the
    organization/task tables, so the baseline rows must exist before the
    first journal write. Works inside ``store.transaction()`` views as well.
    """

    if store is None:
        return

    def _insert_guarded(insert):
        # Concurrent sibling tasks can race the existence check; a duplicate
        # INSERT from that race means the row already exists - which is fine.
        import sqlite3 as _sqlite3

        try:
            insert()
        except _sqlite3.IntegrityError:
            pass

    if store.get_organization(org.organization_id) is None:
        _insert_guarded(lambda: store.create_organization(
            organization_id=org.organization_id,
            project_id=org.project_id,
            goal=org.goal,
            budget_usd=org.budget_usd,
            max_concurrent=org.max_concurrent,
            max_attempts=org.max_attempts,
        ))
    if store.get_task(task.id) is None:
        _insert_guarded(lambda: store.create_task(
            task_id=task.id,
            organization_id=org.organization_id,
            project_id=task.project_id,
            agent_id=task.agent_id,
            focus=task.focus,
            executor_kind=str(task.executor_kind),
            depends_on=list(task.depends_on),
            verifier_ids=list(task.verifier_ids),
            participant_ids=list(task.participant_ids),
            collaboration_architecture=task.collaboration_architecture,
            manager_depth=int(getattr(task, "manager_depth", 0) or 0),
            proposed_by_manager_id=getattr(task, "proposed_by_manager_id", None),
            authorization_record=getattr(task, "authorization_record", None),
            max_rounds=int(getattr(task, "max_rounds", 1) or 1),
            max_adaptive_phases=int(getattr(task, "max_adaptive_phases", 4) or 4),
            sync_mode=getattr(task, "sync_mode", None),
        ))


def _record_provenance_model_binding(
    recovery_store: Any,
    *,
    organization_id: str,
    project_id: str,
    task: "AgentTaskRecord",
    attempt: int,
    call_key: str,
    adapter: Any,
    response: Any | None,
    outcome: str,
    cost_usd: float = 0.0,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    response_text: str | None = None,
) -> None:
    """PS1-13I: best-effort durable ACTUAL-executed-model binding.

    Persistence/provenance only: shares the canonical database file behind
    ``recovery_store`` (no parallel DB, no routing/state-machine change).
    Never raises: provenance must never break dispatch. Never fabricates:
    callers pass only adapters actually invoked and their real outcome.
    """
    if recovery_store is None:
        return
    database = getattr(recovery_store, "database", None)
    if database is None:
        return
    try:
        from amca.agent_provenance import record_actual_model_binding, AgentProvenanceStore

        provenance = AgentProvenanceStore(database)
        record_actual_model_binding(
            provenance,
            recovery_store,
            organization_id=str(organization_id),
            project_id=str(project_id),
            task_id=str(task.id),
            task_attempt=int(attempt),
            call_key=str(call_key),
            agent_id=str(task.agent_id),
            executor_kind=str(task.executor_kind),
            adapter=adapter,
            response=response,
            outcome=str(outcome),
            cost_usd=float(cost_usd or 0.0),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            response_text=response_text,
        )
    except Exception:
        pass


def _finite_non_negative(value: object, name: str) -> float:
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return number


class CostBoundKind:
    """Classification of pricing/cost source bounds."""

    CONSERVATIVE_BOUND = "conservative_bound"
    ESTIMATE_ONLY = "estimate_only"
    ZERO_COST = "zero_cost"
    UNKNOWN = "unknown"


def _resolve_adapter_bound_classification(adapter: Any) -> tuple[str, float]:
    """Classify adapter cost source as CONSERVATIVE_BOUND, ESTIMATE_ONLY, ZERO_COST, or UNKNOWN.

    Returns:
        (CostBoundKind, bound_usd)
    """
    if adapter is None:
        return CostBoundKind.UNKNOWN, 0.0

    if _is_adapter_local(adapter):
        if hasattr(adapter, "max_cost_usd"):
            return CostBoundKind.CONSERVATIVE_BOUND, float(getattr(adapter, "max_cost_usd"))
        if getattr(adapter, "is_conservative_bound", False) and hasattr(adapter, "cost_usd"):
            return CostBoundKind.CONSERVATIVE_BOUND, float(getattr(adapter, "cost_usd"))
        if hasattr(adapter, "cost_usd"):
            val = float(getattr(adapter, "cost_usd"))
            if val == 0.0:
                return CostBoundKind.ZERO_COST, 0.0
            # If adapter has non-zero cost_usd and is explicitly marked conservative, handle it above; otherwise estimate
            if getattr(adapter, "is_conservative_bound", False):
                return CostBoundKind.CONSERVATIVE_BOUND, val
            # For test FakeAdapters or adapters with cost_usd without is_conservative_bound:
            return CostBoundKind.ESTIMATE_ONLY, val
        return CostBoundKind.ZERO_COST, 0.0

    # Paid / Cloud / Remote Adapter:
    # 1. Explicit conservative maximum
    if hasattr(adapter, "max_cost_usd"):
        return CostBoundKind.CONSERVATIVE_BOUND, float(getattr(adapter, "max_cost_usd"))
    if hasattr(adapter, "conservative_cost_bound_usd"):
        return CostBoundKind.CONSERVATIVE_BOUND, float(getattr(adapter, "conservative_cost_bound_usd"))
    if getattr(adapter, "is_conservative_bound", False) and hasattr(adapter, "cost_bound_usd"):
        return CostBoundKind.CONSERVATIVE_BOUND, float(getattr(adapter, "cost_bound_usd"))
    if getattr(adapter, "is_conservative_bound", False) and hasattr(adapter, "cost_usd"):
        return CostBoundKind.CONSERVATIVE_BOUND, float(getattr(adapter, "cost_usd"))

    # 2. Estimate-only sources
    if hasattr(adapter, "cost_bound_usd"):
        return CostBoundKind.ESTIMATE_ONLY, float(getattr(adapter, "cost_bound_usd"))
    if hasattr(adapter, "estimated_cost_usd"):
        return CostBoundKind.ESTIMATE_ONLY, float(getattr(adapter, "estimated_cost_usd"))
    if hasattr(adapter, "estimate_cost") and callable(adapter.estimate_cost):
        return CostBoundKind.ESTIMATE_ONLY, float(adapter.estimate_cost())
    if hasattr(adapter, "cost_usd"):
        return CostBoundKind.ESTIMATE_ONLY, float(getattr(adapter, "cost_usd"))

    return CostBoundKind.UNKNOWN, 0.0


@dataclass(slots=True)
class CostReservation:
    """One conservative budget reservation for a task execution attempt."""

    reservation_id: str
    organization_id: str
    task_id: str
    attempt: int
    reserved_usd: float
    status: str = "reserved"  # "reserved", "settled", "released"
    settled_actual_usd: float = 0.0
    integrity_violation: bool = False
    created_at: str = field(default_factory=_now)
    settled_at: str | None = None

    def __post_init__(self) -> None:
        self.reserved_usd = _finite_non_negative(self.reserved_usd, "reserved_usd")
        self.settled_actual_usd = _finite_non_negative(self.settled_actual_usd, "settled_actual_usd")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "organization_id": self.organization_id,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "reserved_usd": self.reserved_usd,
            "status": self.status,
            "settled_actual_usd": self.settled_actual_usd,
            "integrity_violation": self.integrity_violation,
            "created_at": self.created_at,
            "settled_at": self.settled_at,
        }


@dataclass(slots=True)
class AgentTaskRecord:
    """One persisted logical work item with its audited lifecycle."""

    id: str
    organization_id: str
    project_id: str
    agent_id: str
    focus: str
    status: str = AgentTaskStatus.PROPOSED
    depends_on: tuple[str, ...] = ()
    executor_kind: str = ExecutorKind.DETERMINISTIC_TOOL
    verifier_ids: tuple[str, ...] = ()
    participant_ids: tuple[str, ...] = ()
    collaboration_architecture: str | None = None
    sync_mode: str | None = None
    max_rounds: int = 1
    max_adaptive_phases: int = 4
    decomposition_plan: Any | None = None
    collaboration_payload: dict[str, Any] | None = None
    collaboration_result: dict[str, Any] | None = None
    requires_verified: bool = False
    tool_config: Any | None = None
    output_schema: Any | None = None
    resolved_model_config: dict[str, Any] | None = None
    input_payload: dict[str, Any] | None = None
    dependency_digests: dict[str, str] | None = None
    attempts: int = 0
    result_summary: str | None = None
    verification_result: dict[str, Any] | None = None
    error: str | None = None
    cost_usd: float = 0.0
    local_calls: int = 0
    api_calls: int = 0
    reused_from: str | None = None
    reservation_id: str | None = None
    max_cost_usd: float | None = None
    proposed_children: tuple[dict[str, Any], ...] = ()
    proposed_by_manager_id: str | None = None
    manager_depth: int = 0
    proposal_reason: str | None = None
    proposal_version: int = 1
    authorization_record: dict[str, Any] | None = None
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    completed_at: str | None = None
    # PS1-13H additive: crash-recovery truthfulness (default = certain).
    external_outcome_uncertain: bool = False
    recovery_disposition: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.agent_id or not self.focus.strip():
            raise ValueError("agent tasks require id, agent_id, and focus")
        self.cost_usd = _finite_non_negative(self.cost_usd, "cost_usd")
        if self.max_cost_usd is not None:
            self.max_cost_usd = _finite_non_negative(self.max_cost_usd, "max_cost_usd")
        self.depends_on = tuple(self.depends_on)
        self.verifier_ids = tuple(self.verifier_ids)
        self.participant_ids = tuple(self.participant_ids)
        self.proposed_children = tuple(self.proposed_children)

    @property
    def focus_digest(self) -> str:
        payload = json.dumps(
            {"focus": self.focus, "depends_on": sorted(self.depends_on)},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def execution_identity(
        self,
        version: str = "v2",
        *,
        dependency_results: Mapping[str, str] | None = None,
        adapters: Mapping[str, Any] | None = None,
    ) -> str:
        """Deterministic execution-significant identity.

        v1: Legacy focus digest (focus + sorted depends_on).
        v2: Comprehensive execution-significant identity capturing:
            - prompt / focus
            - expectations: requires_verified, verifier_ids
            - models: agent_id, participant_ids, resolved model identity & provider config
            - provider routing: executor_kind
            - model configuration: collaboration_architecture, sync_mode, max_rounds, max_adaptive_phases
            - tool & decomposition plan configuration
            - output schema & contract
            - hard budget: max_cost_usd
            - dependency identity + cryptographic digests of authoritative dependency results consumed

            Strictly excludes volatile runtime metadata:
            - timestamps (created_at, started_at, completed_at)
            - task/run/process/organization/project IDs, reservation IDs
            - volatile attempt counters, transient errors, memory addresses
            - credential material, tokens, API keys
        """
        if version == "v1":
            return f"v1:{self.focus_digest}"

        if version == "v2":
            # 1. Resolved model identity
            resolved_agent_model = None
            if self.resolved_model_config:
                resolved_agent_model = {
                    k: v for k, v in sorted(self.resolved_model_config.items())
                    if not any(s in k.lower() for s in ("secret", "token", "key", "password", "auth", "credential"))
                }
            elif adapters and self.agent_id in adapters:
                ad = adapters[self.agent_id]
                resolved_agent_model = {
                    "model_id": str(getattr(ad, "model_id", getattr(ad, "name", type(ad).__name__))),
                    "provider": getattr(ad, "provider", None),
                    "is_local": bool(getattr(ad, "is_local", True)),
                }

            resolved_participants = []
            for p_id in sorted(self.participant_ids):
                p_cfg = None
                if adapters and p_id in adapters:
                    p_ad = adapters[p_id]
                    p_cfg = {
                        "agent_id": p_id,
                        "model_id": str(getattr(p_ad, "model_id", getattr(p_ad, "name", type(p_ad).__name__))),
                        "provider": getattr(p_ad, "provider", None),
                    }
                else:
                    p_cfg = {"agent_id": p_id}
                resolved_participants.append(p_cfg)

            # 2. Dependency results digest
            deps_identity = []
            for dep in sorted(self.depends_on):
                if self.dependency_digests and dep in self.dependency_digests:
                    dep_digest = self.dependency_digests[dep]
                elif dependency_results is not None:
                    dep_res = dependency_results.get(dep)
                    if dep_res is not None and dep_res.strip():
                        dep_digest = hashlib.sha256(dep_res.strip().encode("utf-8")).hexdigest()
                    else:
                        dep_digest = "unresolved"
                else:
                    dep_digest = "unresolved"
                deps_identity.append({"dependency_id": dep, "result_digest": dep_digest})

            # 3. Canonical tool, decomposition, output, and input projection
            decomp = (
                self.decomposition_plan.to_dict()
                if hasattr(self.decomposition_plan, "to_dict")
                else (
                    self.decomposition_plan
                    if isinstance(self.decomposition_plan, dict)
                    else None
                )
            )
            collab_payload = (
                dict(self.collaboration_payload)
                if isinstance(self.collaboration_payload, dict)
                else None
            )
            tool_cfg = None
            if isinstance(self.tool_config, dict):
                tool_cfg = {
                    k: v for k, v in sorted(self.tool_config.items())
                    if not any(s in k.lower() for s in ("secret", "token", "key", "password", "auth", "credential"))
                }
            elif isinstance(self.tool_config, (list, set, tuple)):
                tool_cfg = {"tools": sorted(str(t) for t in self.tool_config)}

            out_schema = (
                dict(self.output_schema)
                if isinstance(self.output_schema, dict)
                else (str(self.output_schema) if self.output_schema is not None else None)
            )
            in_payload = (
                dict(self.input_payload)
                if isinstance(self.input_payload, dict)
                else None
            )

            canonical_spec = {
                "version": "v2",
                "focus": self.focus.strip(),
                "agent_id": self.agent_id.strip(),
                "resolved_model": resolved_agent_model,
                "resolved_participants": resolved_participants,
                "dependencies": deps_identity,
                "executor_kind": str(self.executor_kind),
                "verifier_ids": sorted(self.verifier_ids),
                "requires_verified": bool(self.requires_verified),
                "collaboration_architecture": (
                    str(self.collaboration_architecture)
                    if self.collaboration_architecture
                    else None
                ),
                "sync_mode": str(self.sync_mode) if self.sync_mode else None,
                "max_rounds": int(self.max_rounds),
                "max_adaptive_phases": int(self.max_adaptive_phases),
                "max_cost_usd": (
                    round(float(self.max_cost_usd), 6)
                    if self.max_cost_usd is not None
                    else None
                ),
                "decomposition_plan": decomp,
                "collaboration_payload": collab_payload,
                "tool_config": tool_cfg,
                "output_schema": out_schema,
                "input_payload": in_payload,
            }
            payload = json.dumps(
                canonical_spec,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            return f"v2:{hashlib.sha256(payload).hexdigest()}"

        raise ValueError(f"unsupported identity version: {version!r}")

    def transition(self, new_status: str) -> None:
        allowed = AgentTaskStatus.ALLOWED_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise ValueError(
                f"invalid lifecycle transition {self.status!r} -> {new_status!r} "
                f"for task {self.id!r}"
            )
        self.status = new_status

    def to_dict(self) -> dict[str, Any]:
        # PS1-13H: existing keys/semantics preserved; new fields additive only.
        # Safe serialization: tool_config sanitized recursively, output_schema
        # as-is if safe, resolved_model allowlist only, input_summary without
        # raw input_payload, nano timing from existing lifecycle info.
        base: dict[str, Any] = {
            "id": self.id,
            "organization_id": self.organization_id,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "focus": self.focus,
            "status": self.status,
            "depends_on": list(self.depends_on),
            "executor_kind": self.executor_kind,
            "verifier_ids": list(self.verifier_ids),
            "participant_ids": list(self.participant_ids),
            "collaboration_architecture": self.collaboration_architecture,
            "sync_mode": self.sync_mode,
            "max_rounds": self.max_rounds,
            "max_adaptive_phases": self.max_adaptive_phases,
            "decomposition_plan": (
                self.decomposition_plan.to_dict()
                if hasattr(self.decomposition_plan, "to_dict")
                else self.decomposition_plan
            ),
            "collaboration_payload": self.collaboration_payload,
            "collaboration_result": self.collaboration_result,
            "requires_verified": self.requires_verified,
            "attempts": self.attempts,
            "result_summary": self.result_summary,
            "verification_result": self.verification_result,
            "error": self.error,
            "cost_usd": self.cost_usd,
            "local_calls": self.local_calls,
            "api_calls": self.api_calls,
            "reused_from": self.reused_from,
            "reservation_id": self.reservation_id,
            "max_cost_usd": self.max_cost_usd,
            "proposed_children": [
                c.to_dict() if hasattr(c, "to_dict") else c
                for c in self.proposed_children
            ],
            "proposed_by_manager_id": self.proposed_by_manager_id,
            "manager_depth": self.manager_depth,
            "proposal_reason": self.proposal_reason,
            "proposal_version": self.proposal_version,
            "authorization_record": self.authorization_record,
            "focus_digest": self.focus_digest,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
        try:
            from amca.agent_observability import (
                build_timing,
                sanitize_output_schema,
                sanitize_resolved_model,
                sanitize_tool_config,
                sanitize_value,
                summarize_input,
            )
            from amca.agent_observability import _redact_string_patterns as _redact

            # Redact secret patterns in free-text paths (truthful otherwise).
            if isinstance(base.get("error"), str):
                base["error"] = _redact(str(base["error"]))
            elif base.get("error") is not None:
                base["error"] = sanitize_value(base.get("error"))
            if isinstance(base.get("result_summary"), str):
                base["result_summary"] = _redact(str(base["result_summary"]))
            if isinstance(base.get("verification_result"), str):
                base["verification_result"] = _redact(str(base["verification_result"]))
            elif isinstance(base.get("verification_result"), (dict, list)):
                base["verification_result"] = sanitize_value(base.get("verification_result"))
            if isinstance(base.get("collaboration_result"), (dict, list)):
                base["collaboration_result"] = sanitize_value(base.get("collaboration_result"))

            base["tool_config"] = sanitize_tool_config(self.tool_config)
            base["output_schema"] = sanitize_output_schema(self.output_schema)
            base["resolved_model"] = sanitize_resolved_model(self.resolved_model_config)
            base["input_summary"] = summarize_input(self.input_payload)
            base["timing"] = build_timing(self.created_at, self.started_at, self.completed_at)
        except Exception:
            # Observability must never break lifecycle serialization.
            base.setdefault("tool_config", None)
            base.setdefault("output_schema", None)
            base.setdefault("resolved_model", None)
            base.setdefault("input_summary", None)
            base.setdefault("timing", {
                "created_at": self.created_at,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "elapsed_seconds": None,
                "monotonic": None,
            })
        # Never expose raw input payload, even if a future field adds it.
        base.pop("input_payload", None)
        return base


@dataclass(slots=True)
class OrganizationRun:
    """The live organization: actual tasks in their actual states."""

    organization_id: str
    project_id: str
    goal: str
    budget_usd: float
    max_concurrent: int
    max_attempts: int = 2
    retired: bool = False
    tasks: dict[str, AgentTaskRecord] = field(default_factory=dict)
    reservations: dict[str, CostReservation] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        self.budget_usd = _finite_non_negative(self.budget_usd, "budget_usd")

    def active_count(self) -> int:
        # PS1-13H: authoritative active set; crash-uncertain work never active.
        active = {
            AgentTaskStatus.QUEUED,
            AgentTaskStatus.STARTING,
            AgentTaskStatus.RUNNING,
            AgentTaskStatus.WAITING,
            AgentTaskStatus.VERIFYING,
        }
        total = 0
        for task in self.tasks.values():
            if task.status not in active:
                continue
            if bool(getattr(task, "external_outcome_uncertain", False)):
                continue
            if getattr(task, "recovery_disposition", None) == "BLOCKED_UNCERTAIN_EXTERNAL_OUTCOME":
                continue
            total += 1
        return total

    def completed_count(self) -> int:
        return sum(1 for task in self.tasks.values() if task.status == AgentTaskStatus.COMPLETED)

    def total_cost_usd(self) -> float:
        return sum(task.cost_usd for task in self.tasks.values())

    def reserved_cost_usd(self) -> float:
        return sum(res.reserved_usd for res in self.reservations.values() if res.status == "reserved")

    def available_budget_usd(self) -> float:
        return max(0.0, self.budget_usd - self.total_cost_usd() - self.reserved_cost_usd())

    def to_dict(self) -> dict[str, Any]:
        # PS1-13H: existing keys preserved; lifecycle_counts additive.
        payload: dict[str, Any] = {
            "organization_id": self.organization_id,
            "project_id": self.project_id,
            "goal": self.goal,
            "budget_usd": self.budget_usd,
            "max_concurrent": self.max_concurrent,
            "retired": self.retired,
            "active_count": self.active_count(),
            "completed_count": self.completed_count(),
            "incurred_cost_usd": self.total_cost_usd(),
            "total_cost_usd": self.total_cost_usd(),
            "reserved_cost_usd": self.reserved_cost_usd(),
            "available_budget_usd": self.available_budget_usd(),
            "tasks": [task.to_dict() for task in self.tasks.values()],
            "reservations": [res.to_dict() for res in self.reservations.values()],
        }
        try:
            from amca.agent_observability import lifecycle_counts

            payload["lifecycle_counts"] = lifecycle_counts(self.tasks)
        except Exception:
            pass
        return payload


class DuplicateResultReuse:
    """Verified-result cache keyed by canonical execution identity with version awareness.

    Distinguishes historical v1 records from hardened execution-significant v2 records.
    Historical v1 records are never silently treated as v2.
    """

    def __init__(self) -> None:
        # storage: normalized_key -> (version, summary)
        self._verified: dict[str, tuple[str, str]] = {}

    def register(self, identity: str, summary: str, version: str | None = None) -> None:
        if not identity or not summary.strip():
            raise ValueError("reuse registry requires a valid identity and non-empty summary")

        if identity.startswith("v2:"):
            detected_ver = "v2"
            key = identity
        elif identity.startswith("v1:"):
            detected_ver = "v1"
            key = identity
        else:
            detected_ver = version or "v2"
            key = f"{detected_ver}:{identity}"

        self._verified[key] = (detected_ver, summary)
        # For legacy bare digest lookups when version is v1
        if detected_ver == "v1" and not identity.startswith("v1:"):
            self._verified[identity] = ("v1", summary)

    def lookup(self, identity: str, version: str = "v2") -> str | None:
        if not identity:
            return None

        # 1. Exact key match
        if identity in self._verified:
            entry_ver, summary = self._verified[identity]
            if entry_ver == version:
                return summary
            if ":" in identity and identity.startswith(f"{version}:"):
                return summary

        # 2. Prefixed lookup if identity was given without prefix
        prefixed_key = f"{version}:{identity}"
        if prefixed_key in self._verified:
            entry_ver, summary = self._verified[prefixed_key]
            if entry_ver == version:
                return summary

        # 3. Bare legacy key if looking up v1
        if version == "v1" and identity in self._verified:
            entry_ver, summary = self._verified[identity]
            if entry_ver == "v1":
                return summary

        return None

    def get_version(self, identity: str) -> str | None:
        if identity in self._verified:
            return self._verified[identity][0]
        for ver in ("v2", "v1"):
            key = f"{ver}:{identity}"
            if key in self._verified:
                return self._verified[key][0]
        return None

    def clear(self) -> None:
        self._verified.clear()

    def __len__(self) -> int:
        return len(self._verified)


class NanoScheduler:
    """Bounded scheduler executing one organization's logical task graph."""

    def __init__(
        self,
        adapters_by_id: Mapping[str, Any],
        *,
        budget_usd: float,
        max_concurrent: int = 4,
        max_attempts: int = 2,
        fallback_adapters_by_id: Mapping[str, Any] | None = None,
        reuse: DuplicateResultReuse | None = None,
        envelope: ResourceEnvelope | None = None,
        settings: AgentFabricSettings | None = None,
        recovery_store=None,
        scheduling_barrier=None,
        tool_dispatcher=None,
        tool_catalog: Mapping[str, Any] | None = None,
        max_tool_iterations: int = 8,
        tool_repeat_guard: int = 3,
        event_sink: Callable[[str, Mapping[str, Any]], None] | None = None,
        deterministic_executor=None,
    ) -> None:
        if budget_usd < 0:
            raise ValueError("budget_usd must be non-negative")
        self.recovery_store = recovery_store
        self.scheduling_barrier = scheduling_barrier
        self.event_sink = event_sink
        # 17E4 (PS1-20W): governed model->tool continuation loop hooks.
        # ALL OPTIONAL and default-off: without a dispatcher the scheduler
        # behaves exactly as before (single generate per task). The loop
        # executes only through the injected governed dispatcher (existing
        # Tool Fabric / permission path) - never a new execution system.
        self.tool_dispatcher = tool_dispatcher
        self.tool_catalog = dict(tool_catalog or {})
        self.max_tool_iterations = max(1, int(max_tool_iterations))
        self.tool_repeat_guard = max(1, int(tool_repeat_guard))
        # Telemetry for 20W acceptance: iterations actually consumed.
        self.tool_loop_reports: list[dict[str, Any]] = []
        # A3.2B telemetry: one entry per run() with native-batch activity.
        self.batch_reports: list[dict[str, Any]] = []
        # A3.3.1: OPTIMIZATION reuse hits (deliberate cache avoidance) -
        # distinct from A2 recovery reuse, which never touches this counter.
        self.optimization_reuse_hits = 0
        self.adapters_by_id = dict(adapters_by_id)
        # Governed recovery (manager decision, Decision 1).  A task that fails
        # for a retriable reason may be given exactly one alternate-model step,
        # bounded so a provider outage cannot become a retry storm.  The failed
        # model is excluded, and a task that already performed a side-effecting
        # tool action is never replayed from the start.
        self.fallback_adapters_by_id = dict(fallback_adapters_by_id or {})
        self.recovery_records: list[dict[str, Any]] = []
        self._recovery_steps_taken: dict[str, int] = {}
        #: task id -> tool names that actually executed, so recovery can tell
        #: whether replaying the task would repeat a side effect.
        self.executed_tools_by_task: dict[str, list[str]] = {}
        # Run-scoped runtime health (Work Order 09 sections 1.8 and 6.4).
        # Accumulates what actually happened during THIS run and disappears with
        # it.  It can rank or temporarily exclude a candidate; it can never
        # mutate a model's capability facts, and it has no reference to the
        # Model Registry at all.
        from .runtime_health import RunScopedHealth

        self.runtime_health = RunScopedHealth()
        #: The router decision that chose the last recovery replacement, kept so
        #: the choice is auditable rather than implicit.
        self.recovery_selection: Any = None
        self.budget_usd = budget_usd
        self.max_concurrent = max_concurrent
        self.max_attempts = max(1, int(max_attempts))
        self.reuse = reuse if reuse is not None else DuplicateResultReuse()
        self.envelope = envelope if envelope is not None else ResourceEnvelope()
        self.settings = settings if settings is not None else AgentFabricSettings(envelope=self.envelope)
        # M5.6.1 surgical cancellation surface: task id -> in-flight asyncio
        # execution task, populated only while run() is dispatching. Lets an
        # authoritative caller stop individual running tasks through the
        # EXISTING scheduler (no second execution engine, no task registry
        # redesign).
        self._active_executions: dict[str, "asyncio.Task[Any]"] = {}
        # Optional deterministic execution handler: called as
        # handler(task, org) -> str for every deterministic_tool task. When
        # absent the zero-model default summary is kept unchanged.
        self.deterministic_executor = deterministic_executor

    def active_task_ids(self) -> tuple[str, ...]:
        """M5.6.1: ids whose execution is currently in flight."""

        return tuple(self._active_executions)

    async def cancel_active_execution(self, task_id: str) -> bool:
        """M5.6.1: cancel one in-flight task execution.

        Returns True when the task was actively executing and received the
        cancellation. The record's truthful CANCELLED transition is applied
        by the run loop when the cancellation lands; queued work is never
        touched here (callers transition queued records themselves).
        """

        exec_task = self._active_executions.get(task_id)
        if exec_task is None or exec_task.done():
            return False
        exec_task.cancel()
        return True

    # -- 17E4 (PS1-20W) governed model->tool continuation loop ------------------

    @staticmethod
    def _task_tool_names(task: "AgentTaskRecord") -> tuple[str, ...]:
        cfg = getattr(task, "tool_config", None)
        if isinstance(cfg, Mapping):
            names = cfg.get("tools") or cfg.get("allowed") or ()
        elif isinstance(cfg, (list, tuple, set, frozenset)):
            names = cfg
        else:
            names = ()
        return tuple(str(n) for n in names if str(n).strip())

    @staticmethod
    def _adapter_supports_tools(adapter: Any) -> bool:
        try:
            caps = adapter.capabilities() if callable(getattr(adapter, "capabilities", None)) else getattr(adapter, "capabilities", None)
            return bool(getattr(caps, "supports_tools", False))
        except Exception:
            return False

    @staticmethod
    def _adapter_model_name(adapter: Any, request: ModelRequest) -> str:
        for attr in ("model", "model_id", "default_model"):
            val = getattr(adapter, attr, None)
            if val:
                return str(val)
        try:
            meta = getattr(request, "metadata", None) or {}
            val = meta.get("model") if isinstance(meta, Mapping) else None
            if val:
                return str(val)
        except Exception:
            pass
        return "unknown-model"

    def _tool_defs_for(self, names: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
        defs: list[dict[str, Any]] = []
        for name in names:
            entry = self.tool_catalog.get(name)
            if entry is None:
                continue
            if isinstance(entry, Mapping):
                description = str(entry.get("description", ""))[:1024]
                params = entry.get("parameters")
                if not isinstance(params, Mapping):
                    params = {"type": "object", "properties": {}}
            else:
                description = str(getattr(entry, "description", ""))[:1024]
                params = getattr(entry, "parameters", None)
                if not isinstance(params, Mapping):
                    params = {"type": "object", "properties": {}}
            defs.append({
                "type": "function",
                "name": name,
                "description": description,
                "parameters": dict(params),
            })
        return tuple(defs)

    @staticmethod
    def _tool_result_message(result: ToolResult) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": result.call_id,
            "name": result.name,
            "content": str(result.content)[:4000],
            "is_error": bool(result.is_error),
        }

    def _record_tool_provenance(
        self, call: ToolCall, result: ToolResult, task: "AgentTaskRecord", org: OrganizationRun
    ) -> None:
        if self.recovery_store is None:
            return
        database = getattr(self.recovery_store, "database", None)
        if database is None:
            return
        try:
            from amca.agent_provenance import AgentProvenanceStore

            state = result.state
            if state == ToolResultState.SUCCESS:
                outcome = "succeeded"
            elif state == ToolResultState.CANCELLED:
                outcome = "cancelled"
            elif state in (ToolResultState.DENIED, ToolResultState.UNKNOWN_TOOL):
                outcome = "denied"
            else:
                outcome = "failed"
            AgentProvenanceStore(database).record_tool_binding(
                organization_id=org.organization_id,
                project_id=task.project_id,
                task_id=task.id,
                task_attempt=int(task.attempts),
                agent_id=str(task.agent_id),
                executor_kind=str(task.executor_kind),
                tool_id=call.name,
                operation=str(call.name.rsplit(".", 1)[-1])[:64],
                outcome=outcome,
                tool_execution_id=f"17e4-{call.call_id}",
                capability_ref=call.name,
                call_key=f"toolloop:{call.call_id}",
            )
        except Exception:
            pass

    async def _dispatch_tool_call(
        self,
        call: ToolCall,
        task: "AgentTaskRecord",
        org: OrganizationRun,
        allowed: tuple[str, ...],
        sig_counts: dict[str, int],
    ) -> ToolResult:
        digest = hashlib.sha256(
            json.dumps(call.arguments, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()[:16]
        # Record which tools this task actually ran.  Governed recovery needs
        # this to decide whether a failed task can safely be replayed on another
        # model: a task that already wrote a file must not be rerun from the
        # start, because the write would happen twice.
        self.executed_tools_by_task.setdefault(str(task.id), []).append(str(call.name))
        sig = f"{call.name}:{digest}"
        n = sig_counts.get(sig, 0) + 1
        sig_counts[sig] = n
        if n > self.tool_repeat_guard:
            return ToolResult(call_id=call.call_id, name=call.name,
                              state=ToolResultState.DENIED,
                              content="cyclic tool-call guard: repeated identical call refused (17E4)",
                              arguments=dict(call.arguments))
        if call.name not in self.tool_catalog:
            return ToolResult(call_id=call.call_id, name=call.name,
                              state=ToolResultState.UNKNOWN_TOOL,
                              content="tool is not registered in the governed catalog",
                              arguments=dict(call.arguments))
        if call.name not in allowed:
            return ToolResult(call_id=call.call_id, name=call.name,
                              state=ToolResultState.DENIED,
                              content="capability not granted to this task",
                              arguments=dict(call.arguments))
        if task.status != AgentTaskStatus.RUNNING:
            return ToolResult(call_id=call.call_id, name=call.name,
                              state=ToolResultState.CANCELLED,
                              content="cancelled before dispatch",
                              arguments=dict(call.arguments))
        if self.event_sink is not None:
            try:
                self.event_sink("tool_call", {
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": dict(call.arguments),
                })
            except Exception:
                pass
        try:
            out = self.tool_dispatcher(
                call,
                {
                    "organization_id": org.organization_id,
                    "project_id": task.project_id,
                    "task_id": task.id,
                    "task_attempt": int(task.attempts),
                    "agent_id": str(task.agent_id),
                    "executor_kind": str(task.executor_kind),
                },
            )
            if asyncio.iscoroutine(out) or isinstance(out, asyncio.Future):
                out = await out
            if not isinstance(out, ToolResult):
                out = ToolResult(call_id=call.call_id, name=call.name,
                                 state=ToolResultState.EXECUTION_ERROR,
                                 content="dispatcher returned non-canonical result",
                                 arguments=dict(call.arguments))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            out = ToolResult(call_id=call.call_id, name=call.name,
                             state=ToolResultState.EXECUTION_ERROR,
                             content=f"tool execution failed: {type(exc).__name__}: {str(exc)[:200]}",
                             arguments=dict(call.arguments))
        self._record_tool_provenance(call, out, task, org)
        if self.event_sink is not None:
            try:
                self.event_sink("tool_result", {
                    "call_id": out.call_id,
                    "name": out.name,
                    "state": out.state.value if hasattr(out.state, "value") else str(out.state),
                    "content": out.content,
                    "is_error": out.is_error,
                })
            except Exception:
                pass
        return out

    async def _generate_with_tools(
        self, adapter: Any, request: ModelRequest, task: "AgentTaskRecord", org: OrganizationRun
    ) -> Any:
        """Bounded governed continuation: model -> validated ToolCall -> existing
        dispatcher/Tool Fabric -> canonical ToolResult -> reinjection -> model.

        Default-off: without tool_dispatcher/catalog/task tools this is exactly
        the previous single generate(). Never executes raw model text, never
        creates a second permission system; denials/malformed/unknown produce
        reinjectable results with ZERO execution.
        """
        if self.tool_dispatcher is None:
            return await asyncio.wait_for(adapter.generate(request), timeout=300)
        names = self._task_tool_names(task)
        if not names or not self.tool_catalog:
            return await asyncio.wait_for(adapter.generate(request), timeout=300)
        if not effective_tool_support(
            self._adapter_supports_tools(adapter),
            self._adapter_model_name(adapter, request),
            True,
        ):
            return await asyncio.wait_for(adapter.generate(request), timeout=300)
        defs = self._tool_defs_for(names)
        if not defs:
            return await asyncio.wait_for(adapter.generate(request), timeout=300)
        messages = list(request.messages)
        sig_counts: dict[str, int] = {}
        iterations = 0
        while True:
            req = ModelRequest(messages=tuple(messages), tools=defs)
            resp = await asyncio.wait_for(adapter.generate(req), timeout=300)
            calls = tuple(getattr(resp, "tool_calls", ()) or ())
            pending: list[ToolResult] = []
            tool_calls: list[ToolCall] = []
            for c in calls:
                # ModelResponse freezes tool_calls into mapping proxies, so
                # classify structurally: a result-like carries state/content;
                # a call-like carries arguments.
                is_mapping = isinstance(c, Mapping)
                keys = set(c.keys()) if is_mapping else set(getattr(c, "__dataclass_fields__", {}) or ())
                if is_mapping and ("state" in keys or "content" in keys):
                    pending.append(ToolResult(
                        call_id=str(c.get("call_id") or ""),
                        name=str(c.get("name") or "tool"),
                        state=c.get("state") if isinstance(c.get("state"), ToolResultState) else str(c.get("state") or "execution_error"),
                        content=str(c.get("content") or ""),
                    ))
                else:
                    arguments = c.get("arguments") if is_mapping else getattr(c, "arguments", None)
                    tool_calls.append(ToolCall(
                        call_id=str((c.get("call_id") if is_mapping else getattr(c, "call_id", "")) or ""),
                        name=str((c.get("name") if is_mapping else getattr(c, "name", "")) or ""),
                        arguments=dict(arguments or {}),
                        provider_call_id=(c.get("provider_call_id") if is_mapping else getattr(c, "provider_call_id", None)),
                        index=int((c.get("index") if is_mapping else getattr(c, "index", 0)) or 0),
                    ))
            if self.event_sink is not None:
                try:
                    self.event_sink("turn_complete", {
                        "task_id": task.id,
                        "iteration": iterations,
                        "text": getattr(resp, "text", "") or "",
                        "tool_calls_count": len(tool_calls) + len(pending),
                    })
                except Exception:
                    pass
            if not tool_calls and not pending:
                self.tool_loop_reports.append(
                    {"task_id": task.id, "iterations": iterations, "halted": "final"}
                )
                return resp
            if iterations >= self.max_tool_iterations:
                self.tool_loop_reports.append(
                    {"task_id": task.id, "iterations": iterations, "halted": "max_tool_iterations"}
                )
                note = f"\n[17E4 bounded halt: max_tool_iterations={self.max_tool_iterations} reached; remaining tool calls not executed]"
                try:
                    from dataclasses import replace as _replace

                    resp = _replace(resp, text=(getattr(resp, "text", "") or "") + note)
                except Exception:
                    pass
                return resp
            iterations += 1
            provider = str(getattr(resp, "provider", "") or "").lower()
            messages.append({
                "role": "assistant",
                "content": getattr(resp, "text", "") or "",
                "tool_calls": [
                    {
                        "id": c.call_id,
                        "type": "function",
                        "function": {
                            "name": c.name,
                            # Provider-native argument serialization of the
                            # canonical dict: Ollama validates arguments as an
                            # object; OpenAI/chat-compat wire format requires
                            # a JSON string. The canonical protocol stays the
                            # single source; only the wire form differs.
                            "arguments": (
                                dict(c.arguments)
                                if provider == "ollama"
                                else json.dumps(c.arguments, ensure_ascii=False, sort_keys=True, default=str)
                            ),
                        },
                    }
                    for c in tool_calls
                ],
            })
            for pr in pending:
                messages.append(self._tool_result_message(pr))
            for call in tool_calls:
                result = await self._dispatch_tool_call(call, task, org, names, sig_counts)
                messages.append(self._tool_result_message(result))

    def schedule(self, task_graph: Sequence[Mapping[str, Any]], *, organization_id: str, project_id: str, goal: str) -> OrganizationRun:
        known_ids: set[str] = set()
        for entry in task_graph:
            task_id = str(entry["id"])
            if task_id in known_ids:
                raise ValueError(f"duplicate task id {task_id!r} in task graph")
            known_ids.add(task_id)
        org = OrganizationRun(
            organization_id=organization_id,
            project_id=project_id,
            goal=goal,
            budget_usd=self.budget_usd,
            max_concurrent=self.max_concurrent,
            max_attempts=self.max_attempts,
        )
        for entry in task_graph:
            dependencies = tuple(str(item) for item in entry.get("depends_on", ()))
            unknown = sorted(set(dependencies) - known_ids)
            if unknown:
                raise ValueError(
                    f"task {entry['id']!r} references unknown dependencies: "
                    + ", ".join(unknown)
                )
            executor_kind = str(
                entry.get("executor_kind", ExecutorKind.NANO_LOCAL_MODEL)
            )
            if executor_kind not in {
                ExecutorKind.DETERMINISTIC_TOOL,
                ExecutorKind.NANO_LOCAL_MODEL,
                ExecutorKind.VERIFICATION_CELL,
                ExecutorKind.COLLABORATION,
                ExecutorKind.MANAGER,
            }:
                raise ValueError(f"unsupported executor kind {executor_kind!r}")
            verifier_ids = tuple(str(item) for item in entry.get("verifier_ids", ()))
            participant_ids = tuple(str(item) for item in entry.get("participant_ids", ()))
            collab_arch = entry.get("collaboration_architecture")
            sync_mode = entry.get("sync_mode")
            max_rounds = max(1, int(entry.get("max_rounds", 1)))
            max_adaptive_phases = max(1, int(entry.get("max_adaptive_phases", 4)))
            decomposition_plan = entry.get("decomposition_plan")
            collab_payload = entry.get("collaboration_payload")
            requires_verified = bool(entry.get("requires_verified", False))
            max_cost_val = entry.get("max_cost_usd")
            max_cost_usd = float(max_cost_val) if max_cost_val is not None else None
            task = AgentTaskRecord(
                id=str(entry["id"]),
                organization_id=organization_id,
                project_id=project_id,
                agent_id=str(entry.get("agent_id", entry["id"])),
                focus=str(entry.get("focus", entry["id"])),
                depends_on=dependencies,
                executor_kind=executor_kind,
                verifier_ids=verifier_ids,
                participant_ids=participant_ids,
                collaboration_architecture=collab_arch,
                sync_mode=sync_mode,
                max_rounds=max_rounds,
                max_adaptive_phases=max_adaptive_phases,
                decomposition_plan=decomposition_plan,
                collaboration_payload=collab_payload,
                requires_verified=requires_verified,
                max_cost_usd=max_cost_usd,
            )
            # Governor authorization is implicit here because schedule()
            # already enforced the structural limits; the explicit lifecycle
            # transitions keep every state change auditable.
            task.transition(AgentTaskStatus.AUTHORIZED)
            if len(task.depends_on) == 0:
                v2_ident = task.execution_identity("v2", dependency_results={}, adapters=self.adapters_by_id)
                cached = self.reuse.lookup(v2_ident, version="v2")
                reused_key = v2_ident if cached is not None else None
            else:
                cached = None
                reused_key = None
            if cached is not None and task.executor_kind != ExecutorKind.VERIFICATION_CELL:
                task.result_summary = cached
                task.reused_from = reused_key
                self.optimization_reuse_hits += 1
                task.transition(AgentTaskStatus.COMPLETED)
                task.completed_at = _now()
            else:
                task.transition(AgentTaskStatus.QUEUED)
            org.tasks[task.id] = task
        return org

    def _assert_budget_invariant(self, org: OrganizationRun) -> None:
        """Enforce hard budget invariant: incurred + reserved <= budget at all times."""
        incurred = org.total_cost_usd()
        reserved = org.reserved_cost_usd()
        if incurred + reserved > org.budget_usd + 1e-9:
            raise AssertionError(
                f"Hard budget invariant violated: incurred ${incurred:.4f} + "
                f"reserved ${reserved:.4f} = ${incurred + reserved:.4f} > "
                f"budget ${org.budget_usd:.4f}"
            )

    def _calculate_adapter_bound(self, adapter_id: str) -> float:
        adapter = self.adapters_by_id.get(adapter_id)
        if adapter is None:
            raise ValueError(f"unknown or unavailable adapter {adapter_id!r}")

        kind, bound_val = _resolve_adapter_bound_classification(adapter)
        if kind in {CostBoundKind.ZERO_COST, CostBoundKind.CONSERVATIVE_BOUND}:
            return bound_val

        if kind == CostBoundKind.ESTIMATE_ONLY:
            raise ValueError(
                f"adapter {adapter_id!r} provides only an estimate ({bound_val:.4f} USD) rather than an enforceable conservative maximum; rejected in hard-budget mode"
            )

        raise ValueError(
            f"cannot establish conservative cost bound for paid adapter {adapter_id!r} in hard-budget mode"
        )

    def _calculate_task_cost_bound(self, task: AgentTaskRecord) -> float:
        if task.executor_kind == ExecutorKind.DETERMINISTIC_TOOL:
            return 0.0

        if task.executor_kind == ExecutorKind.VERIFICATION_CELL:
            if not task.verifier_ids:
                return 0.0
            if any(v_id not in self.adapters_by_id for v_id in task.verifier_ids):
                return 0.0
            cell_bound = 0.0
            for v_id in task.verifier_ids:
                cell_bound += self._calculate_adapter_bound(v_id)
            return cell_bound

        if task.executor_kind == ExecutorKind.COLLABORATION:
            p_ids = task.participant_ids or (task.agent_id,)
            if not p_ids:
                return 0.0
            for pid in p_ids:
                if pid not in self.adapters_by_id:
                    raise ValueError(f"collaboration task {task.id!r} references unknown participant adapter {pid!r}")

            arch = (task.collaboration_architecture or "joint").strip().lower()
            max_rounds = max(1, getattr(task, "max_rounds", 1) or 1)

            if arch == "solo":
                agent_id = p_ids[0] if p_ids else task.agent_id
                bound_per_call = self._calculate_adapter_bound(agent_id)
                return bound_per_call * max_rounds

            elif arch == "joint":
                single_round_bound = sum(self._calculate_adapter_bound(pid) for pid in p_ids)
                return single_round_bound * max_rounds

            elif arch in {"independent_discuss", "independent_discussion"}:
                calls_per_participant = 1 + max_rounds
                return sum(self._calculate_adapter_bound(pid) * calls_per_participant for pid in p_ids)

            elif arch == "parallel":
                plan = getattr(task, "decomposition_plan", None)
                if plan is None and task.collaboration_payload:
                    plan = task.collaboration_payload.get("decomposition_plan")
                if plan is None:
                    raise ValueError(
                        f"cannot establish conservative whole-execution bound for PARALLEL collaboration task {task.id!r}: missing decomposition_plan"
                    )
                work_units = getattr(plan, "work_units", None)
                if work_units is None and isinstance(plan, dict):
                    work_units = plan.get("work_units", [])
                if not work_units:
                    raise ValueError(
                        f"cannot establish conservative whole-execution bound for PARALLEL collaboration task {task.id!r}: decomposition_plan must contain at least one work unit"
                    )
                total_parallel = 0.0
                integration_owners: set[str] = set()
                for unit in work_units:
                    assignee_ids = getattr(unit, "assignee_ids", None)
                    if assignee_ids is None and isinstance(unit, dict):
                        assignee_ids = unit.get("assignee_ids", [])
                    if not assignee_ids:
                        raise ValueError(f"parallel work unit missing assignee_ids in task {task.id!r}")
                    total_parallel += self._calculate_adapter_bound(str(assignee_ids[0]))

                    integ_owner = getattr(unit, "integration_owner", None)
                    if integ_owner is None and isinstance(unit, dict):
                        integ_owner = unit.get("integration_owner")
                    if integ_owner:
                        integration_owners.add(str(integ_owner))

                # Include unique integration owner bounds
                for owner in sorted(integration_owners):
                    if owner in self.adapters_by_id:
                        total_parallel += self._calculate_adapter_bound(owner)

                return total_parallel

            elif arch in {"adaptive", "adaptive_hybrid"}:
                raise ValueError(
                    f"cannot establish conservative whole-execution bound for ADAPTIVE_HYBRID collaboration task {task.id!r} in hard-budget mode: "
                    "Adaptive Hybrid dynamic phase transitions require explicit bounded child phase plans to guarantee whole-execution limits; fails closed in hard-budget mode."
                )

            else:
                raise ValueError(
                    f"cannot establish conservative whole-execution bound for unsupported collaboration architecture {arch!r} in hard-budget mode"
                )

        if task.executor_kind in {ExecutorKind.NANO_LOCAL_MODEL, ExecutorKind.MANAGER}:
            adapter = self.adapters_by_id.get(task.agent_id)
            if adapter is None:
                return 0.0

            if task.max_cost_usd is not None:
                if getattr(adapter, "enforces_max_cost", False):
                    return float(task.max_cost_usd)
                # Task max_cost_usd without enforceable adapter cap cannot serve as hard bound
                kind, bound_val = _resolve_adapter_bound_classification(adapter)
                if kind in {CostBoundKind.ZERO_COST, CostBoundKind.CONSERVATIVE_BOUND}:
                    return bound_val
                raise ValueError(
                    f"task {task.id!r} max_cost_usd={task.max_cost_usd} is policy metadata without an enforceable executor cap, and adapter {task.agent_id!r} lacks a conservative bound"
                )

            return self._calculate_adapter_bound(task.agent_id)

        return 0.0

    async def run(self, org: OrganizationRun) -> OrganizationRun:
        if not hasattr(self, "_admission_lock") or self._admission_lock is None:
            self._admission_lock = asyncio.Lock()

        def ready_tasks() -> list[AgentTaskRecord]:
            ready: list[AgentTaskRecord] = []
            for task in org.tasks.values():
                if task.status != AgentTaskStatus.QUEUED:
                    continue
                deps_complete = all(
                    org.tasks[dep].status in {AgentTaskStatus.COMPLETED, AgentTaskStatus.MERGED}
                    for dep in task.depends_on
                )
                if not deps_complete:
                    continue
                unverified: list[str] = []
                for dep in task.depends_on:
                    dep_task = org.tasks[dep]
                    if dep_task.executor_kind == ExecutorKind.VERIFICATION_CELL:
                        v_res = dep_task.verification_result or {}
                        if v_res.get("outcome") != "verified":
                            unverified.append(f"{dep} (outcome: {v_res.get('outcome', 'unknown')})")
                if unverified:
                    task.error = f"upstream verification gate failed: {', '.join(unverified)}"
                    task.transition(AgentTaskStatus.CANCELLED)
                    continue
                ready.append(task)
            ready.sort(key=lambda item: item.id)
            return ready

        while True:
            if self.scheduling_barrier is not None:
                # A2.1C startup barrier: paid dispatch must never bypass
                # startup reconciliation (fail closed).
                self.scheduling_barrier.ensure_schedulable()
            statuses = {task.status for task in org.tasks.values()}
            # PS1-13D: VERIFYING is active work (cancellable), not terminal.
            if not (statuses & {AgentTaskStatus.QUEUED, AgentTaskStatus.STARTING, AgentTaskStatus.RUNNING, AgentTaskStatus.VERIFYING}):
                break

            batch: list[tuple[AgentTaskRecord, CostReservation | None]] = []
            async with self._admission_lock:
                for task in ready_tasks():
                    if len(batch) >= org.max_concurrent:
                        break

                    try:
                        bound = self._calculate_task_cost_bound(task)
                    except ValueError as exc:
                        task.error = f"cost bound error: {exc}"
                        task.transition(AgentTaskStatus.CANCELLED)
                        continue

                    available = org.available_budget_usd()
                    if bound > available + 1e-9:
                        # If no tasks are currently executing or reserved, no reservations will be freed
                        if org.reserved_cost_usd() <= 1e-9:
                            task.error = f"budget exhausted: required reservation ${bound:.4f} exceeds available ${available:.4f}"
                            task.transition(AgentTaskStatus.CANCELLED)
                        continue

                    res: CostReservation | None = None
                    if bound > 0.0:
                        linked = None
                        if self.recovery_store is not None:
                            # A2.1D continuation: a recovered attempt reuses
                            # its existing ACTIVE reservation when it still
                            # covers the conservative bound - never a second
                            # reservation for the same logical work.
                            linked = self.recovery_store.find_active_reservation(
                                org.organization_id,
                                task.id,
                                task.attempts + 1,
                            )
                        if linked is not None and float(
                            linked["amount_usd"]
                        ) >= bound - 1e-9:
                            res = CostReservation(
                                reservation_id=str(linked["reservation_id"]),
                                organization_id=org.organization_id,
                                task_id=task.id,
                                attempt=task.attempts + 1,
                                reserved_usd=float(linked["amount_usd"]),
                            )
                        else:
                            res = CostReservation(
                                reservation_id=f"res-{uuid4().hex[:16]}",
                                organization_id=org.organization_id,
                                task_id=task.id,
                                attempt=task.attempts + 1,
                                reserved_usd=bound,
                            )
                        org.reservations[res.reservation_id] = res
                        task.reservation_id = res.reservation_id
                        if (
                            self.recovery_store is not None
                            and linked is None
                        ):
                            # Atomic admission: the paid reservation and its
                            # linked RESERVED call attempt commit together or
                            # not at all. The transaction is short - it never
                            # spans provider latency.
                            with self.recovery_store.transaction() as tx:
                                _ensure_durable_baseline(tx, org, task)
                                tx.create_reservation(
                                    reservation_id=res.reservation_id,
                                    organization_id=org.organization_id,
                                    task_id=task.id,
                                    attempt=res.attempt,
                                    amount_usd=res.reserved_usd,
                                )
                                tx.record_call_state(
                                    organization_id=org.organization_id,
                                    task_id=task.id,
                                    task_attempt_number=res.attempt,
                                    call_key=_primary_call_key(task, res.attempt),
                                    state="RESERVED",
                                    executor_kind=str(task.executor_kind),
                                    adapter_id=task.agent_id,
                                    reservation_id=res.reservation_id,
                                    reserved_usd=res.reserved_usd,
                                )

                    self._assert_budget_invariant(org)
                    batch.append((task, res))

            if batch:
                singles, native_plans = self._plan_native_batches(org, batch)
                coros = [self._execute(org, task, res) for task, res in singles]
                coros += [self._execute_native_batch(org, plan)
                          for plan in native_plans]
                if coros:
                    # M5.6.1 surgical cancellation extension (authorized):
                    # executions run as tracked asyncio tasks so an
                    # authoritative run/parent cancellation can stop
                    # individual running tasks through the existing
                    # scheduler. A cancelled execution terminates only its
                    # own record truthfully instead of aborting the whole
                    # batch; every other gather behavior is unchanged
                    # (non-cancellation exceptions still propagate).
                    member_ids = [task.id for task, _res in singles]
                    member_ids += [
                        str(getattr(plan, "batch_id", f"native-batch-{index}"))
                        for index, plan in enumerate(native_plans)
                    ]
                    started = {
                        member_id: asyncio.ensure_future(coro)
                        for member_id, coro in zip(member_ids, coros)
                    }
                    self._active_executions.update(started)
                    outcomes = await asyncio.gather(
                        *started.values(), return_exceptions=True
                    )
                    for member_id in started:
                        self._active_executions.pop(member_id, None)
                    for member_id, outcome in zip(started.keys(), outcomes):
                        if isinstance(outcome, asyncio.CancelledError):
                            task = org.tasks.get(member_id)
                            # PS1-13D: VERIFYING uses the EXISTING surgical
                            # cancel path (race-safe terminal handling).
                            if task is not None and task.status in {
                                AgentTaskStatus.STARTING,
                                AgentTaskStatus.RUNNING,
                                AgentTaskStatus.WAITING,
                                AgentTaskStatus.VERIFYING,
                            }:
                                task.transition(AgentTaskStatus.CANCELLED)
                        elif isinstance(outcome, BaseException):
                            raise outcome
                continue

            busy = any(
                task.status
                # PS1-13D: VERIFYING counts as active/busy so cancellation
                # of verifying work is never treated as idle/zombie.
                in {AgentTaskStatus.STARTING, AgentTaskStatus.RUNNING, AgentTaskStatus.WAITING, AgentTaskStatus.VERIFYING}
                for task in org.tasks.values()
            )
            if busy or not any(
                task.status == AgentTaskStatus.QUEUED for task in org.tasks.values()
            ):
                break
            # Nothing is executing and nothing can be dispatched: the remaining
            # queued tasks have dependencies that already failed/cancelled.
            for task in org.tasks.values():
                if task.status == AgentTaskStatus.QUEUED:
                    task.error = "upstream dependency did not complete"
                    task.transition(AgentTaskStatus.CANCELLED)
        # Work Order 09 section 5: close the recovery loop in one place rather
        # than at every completion site.  A recovered task that ended completed
        # tells the kernel recovery WORKED; one that did not tells it recovery
        # was tried and the task still failed.
        for task in org.tasks.values():
            if task.status in {AgentTaskStatus.COMPLETED, AgentTaskStatus.RETIRED}:
                self.runtime_health.record_success(str(task.agent_id))
        for task_id, steps in self._recovery_steps_taken.items():
            task = org.tasks.get(task_id)
            if task is None:
                continue
            if task.status in {AgentTaskStatus.COMPLETED, AgentTaskStatus.RETIRED}:
                self._emit_recovery("TASK_RECOVERED", {
                    "task_id": task_id, "agent_id": str(task.agent_id),
                    "recovery_steps": steps,
                })
            else:
                self._emit_recovery("TASK_DEGRADED", {
                    "task_id": task_id, "agent_id": str(task.agent_id),
                    "recovery_steps": steps, "final_status": str(task.status),
                })
        return org

    async def retire(self, org: OrganizationRun) -> OrganizationRun:
        for task in sorted(org.tasks.values(), key=lambda item: item.id):
            if task.status == AgentTaskStatus.COMPLETED:
                task.transition(AgentTaskStatus.MERGED)
                task.transition(AgentTaskStatus.RETIRED)
                if task.executor_kind != ExecutorKind.VERIFICATION_CELL:
                    dep_results = {
                        dep: org.tasks[dep].result_summary
                        for dep in task.depends_on
                        if dep in org.tasks and org.tasks[dep].result_summary
                    }
                    v2_ident = task.execution_identity("v2", dependency_results=dep_results, adapters=self.adapters_by_id)
                    self.reuse.register(v2_ident, task.result_summary or "", version="v2")
        org.retired = True
        return org

    def _journal_call(
        self,
        *,
        org_id: str,
        task_id: str,
        attempt: int,
        call_key: str,
        state: str,
        executor_kind: str,
        adapter_id: str,
        reservation_id: str,
        reserved_usd: float = 0.0,
        actual_cost_usd: float = 0.0,
        response_payload: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        token_source: str | None = None,
        batch_transport_id: str | None = None,
        provider_latency_ms: float | None = None,
    ) -> None:
        """Persist one dispatch checkpoint. No-op without recovery_store."""
        if self.recovery_store is None:
            return
        self.recovery_store.record_call_state(
            organization_id=org_id,
            task_id=task_id,
            task_attempt_number=attempt,
            call_key=call_key,
            state=state,
            executor_kind=executor_kind,
            adapter_id=adapter_id,
            reservation_id=reservation_id,
            reserved_usd=reserved_usd,
            actual_cost_usd=actual_cost_usd,
            response_payload=response_payload,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            token_source=token_source,
            batch_transport_id=batch_transport_id,
            provider_latency_ms=provider_latency_ms,
        )

    def _plan_native_batches(self, org, pairs):
        """Partition admitted (task, reservation) pairs into native-batch
        plans and singles. Only planner-compatible, batch-capable,
        independent nano/model tasks are grouped (A3.2B v1)."""
        from amca.call_batching import BatchItemCandidate, SafeBatchingPlanner

        candidates = []
        for task, res in pairs:
            if str(task.executor_kind) != ExecutorKind.NANO_LOCAL_MODEL:
                continue
            adapter = self.adapters_by_id.get(task.agent_id)
            if adapter is None or not getattr(
                adapter, "supports_batch_generation", False
            ):
                continue
            attempt = task.attempts + 1
            candidates.append(BatchItemCandidate(
                task_id=task.id,
                agent_id=task.agent_id,
                prompt=task.focus[:200],
                model_id=str(getattr(adapter, "model", "") or ""),
                provider=str(getattr(adapter, "provider", "") or ""),
                executor_kind="nano_local_model",
                conservative_cost_usd=res.reserved_usd if res else 0.0,
                call_key=f"task:{task.id}:attempt:{attempt}",
                reservation_id=res.reservation_id if res else None,
            ))
        if len(candidates) < 2:
            return pairs, []
        plans, rejections = SafeBatchingPlanner.plan_batches(
            candidates, self.adapters_by_id,
            available_budget_usd=org.available_budget_usd(),
        )
        multi = [p for p in plans if len(p.items) >= 2]
        covered = {item.task_id for p in multi for item in p.items}
        singles = [(t, r) for t, r in pairs if t.id not in covered]
        self.batch_reports.append({
            "batch_candidate_count": len(candidates),
            "rejected": [reason for _, reason in rejections],
            "batched_item_count": sum(len(p.items) for p in multi),
            "transport_plans": len(multi),
        })
        return singles, multi

    async def _execute_native_batch(self, org, plan) -> None:
        """One native transport carrying N independent logical calls.

        Every item keeps its own task identity, call-attempt journal rows,
        reservation, cost, and recovery disposition. Durable dispatch intent
        for EVERY member commits before generate_batch crosses the boundary.
        """
        adapter = self.adapters_by_id[plan.adapter_id]
        items = list(plan.items)
        entries = []
        for item in items:
            task = org.tasks[item.task_id]
            task.attempts += 1
            task.transition(AgentTaskStatus.STARTING)
            task.started_at = _now()
            task.transition(AgentTaskStatus.RUNNING)
            reservation = org.reservations.get(task.reservation_id)
            entries.append((item, task, reservation))

        incurred_by_task: dict[str, float] = {}
        committed_tasks: set[str] = set()
        if self.recovery_store is not None:
            # Zero-cost members skip paid admission; make sure their durable
            # baseline rows exist before the batch dispatch journals run.
            for _, task, _ in entries:
                _ensure_durable_baseline(self.recovery_store, org, task)
        try:
            for item, task, reservation in entries:
                self._journal_call(
                    org_id=org.organization_id, task_id=task.id,
                    attempt=task.attempts, call_key=item.call_key,
                    state="DISPATCH_INTENT_DURABLE",
                    executor_kind=str(task.executor_kind),
                    adapter_id=adapter.adapter_id,
                    reservation_id=reservation.reservation_id if reservation else "",
                    reserved_usd=item.conservative_cost_usd,
                )
                self._journal_call(
                    org_id=org.organization_id, task_id=task.id,
                    attempt=task.attempts, call_key=item.call_key,
                    state="DISPATCHED",
                    executor_kind=str(task.executor_kind), adapter_id=adapter.adapter_id,
                    reservation_id=reservation.reservation_id if reservation else "",
                )
            requests = [
                ModelRequest(messages=({"role": "user", "content": task.focus},))
                for _, task, _ in entries
            ]
            responses = await asyncio.wait_for(
                adapter.generate_batch(requests), timeout=300
            )
            if responses is None or len(responses) != len(entries):
                raise RuntimeError("native batch returned mismatched outcome count")
            for (item, task, reservation), response in zip(entries, responses):
                if response is None:
                    # Authoritative per-item non-outcome: this member stays
                    # unresolved while its siblings commit independently.
                    task.error = "batch member unresolved: no provider response"
                    task.transition(AgentTaskStatus.FAILED)
                    task.completed_at = _now()
                    continue
                text = getattr(response, "text", "")
                usage = getattr(response, "usage", None)
                cost = float(getattr(usage, "cost_usd", 0.0) or 0.0)
                usage = getattr(response, "usage", None)
                self._journal_call(
                    org_id=org.organization_id, task_id=task.id,
                    attempt=task.attempts, call_key=item.call_key,
                    state="RESPONSE_COMMITTED",
                    executor_kind=str(task.executor_kind), adapter_id=adapter.adapter_id,
                    reservation_id=reservation.reservation_id if reservation else "",
                    actual_cost_usd=cost, response_payload=text[:2000],
                    input_tokens=getattr(usage, "input_tokens", None),
                    output_tokens=getattr(usage, "output_tokens", None),
                    token_source=("provider_reported"
                                  if getattr(usage, "input_tokens", None) is not None
                                  and getattr(usage, "output_tokens", None) is not None
                                  else None),
                    batch_transport_id=plan.batch_id,
                )
                # PS1-13I: durable ACTUAL-executed-model binding (same DB file).
                _record_provenance_model_binding(
                    self.recovery_store,
                    organization_id=org.organization_id,
                    project_id=task.project_id,
                    task=task,
                    attempt=task.attempts,
                    call_key=item.call_key,
                    adapter=adapter,
                    response=response,
                    outcome="succeeded",
                    cost_usd=cost,
                    input_tokens=getattr(usage, "input_tokens", None),
                    output_tokens=getattr(usage, "output_tokens", None),
                    response_text=text,
                )
                committed_tasks.add(task.id)
                incurred_by_task[task.id] = cost
                task.result_summary = text[:500]
                if _is_adapter_local(adapter):
                    task.local_calls += 1
                else:
                    task.api_calls += 1
                task.cost_usd += cost
                task.error = None
                task.transition(AgentTaskStatus.VERIFYING)
                task.transition(AgentTaskStatus.COMPLETED)
                task.completed_at = _now()
        except Exception as exc:
            for item, task, reservation in entries:
                if task.status == AgentTaskStatus.RUNNING:
                    task.error = f"{type(exc).__name__}: {exc}"[:400]
                    task.transition(AgentTaskStatus.FAILED)
                    task.completed_at = _now()
        finally:
            for item, task, reservation in entries:
                incurred = incurred_by_task.get(task.id, 0.0)
                uncommitted = task.id not in committed_tasks
                if reservation is not None and reservation.status == "reserved":
                    reservation.settled_actual_usd = incurred
                    reservation.status = "uncertain" if uncommitted else "settled"
                    reservation.settled_at = _now() if not uncommitted else None
                if self.recovery_store is not None:
                    with self.recovery_store.transaction() as tx:
                        if reservation is not None:
                            tx.transition_reservation(
                                reservation.reservation_id,
                                "uncertain" if uncommitted else "settled",
                                settled_actual_usd=incurred,
                            )
                        tx.settle_call_attempts(
                            organization_id=org.organization_id,
                            task_id=task.id,
                            task_attempt_number=task.attempts,
                            include_uncommitted=not uncommitted,
                        )

    def _select_fallback_model(self, original_model: str, task: "AgentTaskRecord") -> Any:
        """Choose a replacement through the canonical Model Router.

        Returns the adapter to use, or None when nothing is eligible.  Before
        this, recovery used a pre-wired one-to-one map with no eligibility
        check at all — the audit's finding that the requirement engine existed
        but production never asked it.
        """

        # The map is keyed by AGENT id, not by model ref: it says "if this
        # agent's model fails, use this adapter".  So the entry for the agent
        # that just failed is exactly the one we want, and excluding it would
        # remove the only candidate.  What must not be selected is the FAILED
        # MODEL, and that is excluded by identity below.
        adapter = self.fallback_adapters_by_id.get(str(task.agent_id))
        if adapter is None:
            return None
        if str(getattr(adapter, "adapter_id", "")) == str(original_model):
            # A fallback that IS the failed model is not a fallback.
            return None
        configured = {str(getattr(adapter, "adapter_id", task.agent_id)): adapter}

        from .model_router import (
            CostPolicy,
            ModelCandidateFacts,
            ModelSelectionRequest,
            select_model,
        )

        facts: list[ModelCandidateFacts] = []
        by_ref: dict[str, Any] = {}
        for ref, adapter in configured.items():
            caps = (
                adapter.capabilities()
                if callable(getattr(adapter, "capabilities", None))
                else getattr(adapter, "capabilities", None)
            )
            by_ref[ref] = adapter
            facts.append(
                ModelCandidateFacts(
                    model_ref=ref,
                    provider=str(getattr(adapter, "provider", "") or ""),
                    capabilities=frozenset(
                        name for name, on in (
                            ("tools", bool(getattr(caps, "supports_tools", False))),
                            ("vision", bool(getattr(caps, "supports_vision", False))),
                        ) if on
                    ),
                    supports_tools=bool(getattr(caps, "supports_tools", False)),
                    supports_json=bool(getattr(caps, "supports_json", False)),
                    supports_vision=bool(getattr(caps, "supports_vision", False)),
                    is_local=bool(getattr(caps, "is_local", False)),
                )
            )

        decision = select_model(
            facts,
            ModelSelectionRequest(
                task_type="recovery_fallback",
                required_tools=bool(getattr(task, "tool_config", None)),
                cost_policy=CostPolicy.BALANCED,
                runtime_health=self.runtime_health,
                maximum_participants=1,
            ),
        )
        self.recovery_selection = decision
        if not decision.selected_model:
            return None
        return by_ref.get(decision.selected_model)

    def _attempt_governed_recovery(
        self, task: "AgentTaskRecord", org: "OrganizationRun", exc: BaseException
    ) -> None:
        """One bounded alternate-model step, or an honest refusal.

        Manager decision, Decision 1.  The rules enforced here:

        * a denial, a cancellation, a bad credential, an invalid request or a
          policy violation is NOT recovered — the refusal is the point;
        * a task that already performed a side-effecting tool action is not
          replayed from the start;
        * at most one alternate-model step per task, and the failed model is
          excluded, so recovery cannot bounce between providers;
        * every outcome is recorded, including the refusals, because a recovery
          that leaves no trace cannot be audited.
        """
        from .governed_recovery import (
            MAX_ALTERNATE_MODEL_STEPS,
            RecoveryAttempt,
            RecoveryClass,
            classify_failure,
            failure_reason,
        )

        task_id = str(task.id)
        original_model = str(task.agent_id)
        # Health evidence for this run.  A denial or cancellation records
        # UNKNOWN rather than an unhealthy signal, so a control refusal cannot
        # make a working model look broken.
        self.runtime_health.record_failure(original_model, exc)
        recovery_class = classify_failure(exc)
        reason = failure_reason(exc)
        taken = self._recovery_steps_taken.get(task_id, 0)

        # Task F migration: recovery ASKS THE ROUTER rather than taking a
        # pre-wired replacement with no eligibility check.  The candidates are
        # the configured fallbacks, and the router applies the same hard
        # filters, health and policy as every other selection path — so a
        # fallback cannot be chosen for a model that cannot run the task.
        fallback = self._select_fallback_model(original_model, task)

        # Work Order 09 section 5: recovery must not be a hidden mechanism
        # invisible to the Collaboration Kernel.  Every step is published
        # through the scheduler's event sink, which is the same channel the
        # kernel already consumes for tool calls and turn completion.
        self._emit_recovery("MODEL_FAILURE", {
            "task_id": task_id, "agent_id": original_model,
            "failure_class": recovery_class.value, "reason": reason,
            "attempts": int(task.attempts),
        })

        record = RecoveryAttempt(
            task_id=task_id,
            original_model=original_model,
            failure_class=recovery_class.value,
            failure_reason=reason,
            attempt_count=int(task.attempts),
            fallback_requested=False,
            final_task_status=str(task.status),
        )

        if recovery_class is not RecoveryClass.RETRIABLE_ON_ANOTHER_MODEL:
            record.selection_reason = (
                "failure is not eligible for recovery on another model; "
                "the cause is reported rather than retried"
            )
            self._emit_recovery("TASK_FAILED", {
                "task_id": task_id, "agent_id": original_model,
                "action": "fail", "reason": record.selection_reason,
            })
            self.recovery_records.append(record.to_dict())
            return

        if taken >= MAX_ALTERNATE_MODEL_STEPS:
            record.selection_reason = "alternate-model recovery already attempted for this task"
            self._emit_recovery("TASK_FAILED", {
                "task_id": task_id, "agent_id": original_model,
                "action": "fail", "reason": record.selection_reason,
            })
            self.recovery_records.append(record.to_dict())
            return

        if fallback is None:
            record.selection_reason = "no eligible replacement model was configured"
            self._emit_recovery("TASK_FAILED", {
                "task_id": task_id, "agent_id": original_model,
                "action": "fail", "reason": record.selection_reason,
            })
            self.recovery_records.append(record.to_dict())
            return

        # The directive's order is RESUME first, REPLAY only when nothing
        # irreversible has happened, FAIL otherwise.  Replay safety is NOT
        # inferred here from the tool's name — the Tool Fabric owns that fact
        # and an unknown tool fails closed (Work Order 09, sections 2 and 3).
        from .governed_recovery import (
            RecoveryAction,
            RecoveryEnvelope,
            TaskCheckpoint,
            decide_recovery,
        )

        executed = self.executed_tools_by_task.get(task_id, [])
        from .tool_effects import task_replay_safe as _replay_safe

        _safe, _blocking = _replay_safe(tuple(executed))
        checkpoint = TaskCheckpoint(
            task_id=task_id,
            completed_model_steps=int(task.attempts),
            completed_tool_executions=tuple(executed),
            # A tool that is not proven replay-safe has already changed
            # something, so the checkpoint must say so.
            side_effects_performed=bool(_blocking),
            pending_step=str(getattr(task, "focus", "") or "")[:200] if executed else "",
        )
        action, action_reason = decide_recovery(checkpoint, executed)

        record.action = action.value
        if action is RecoveryAction.FAIL:
            record.selection_reason = action_reason
            self._emit_recovery("TASK_FAILED", {
                "task_id": task_id, "agent_id": original_model,
                "action": action.value, "reason": action_reason,
            })
            self.recovery_records.append(record.to_dict())
            return

        if action is RecoveryAction.RESUME:
            # Continue rather than restart, so verified work is preserved and
            # the recovering model is told not to repeat what already happened.
            # The context is the full bounded envelope (Work Order 09 section 4),
            # rebuilt from authoritative records rather than from a transcript.
            envelope = RecoveryEnvelope(
                run_id=str(org.organization_id),
                task_id=task_id,
                goal_contract_summary=str(getattr(org, "goal", "") or "")[:300],
                active_focus=str(getattr(task, "focus", "") or "")[:300],
                task_requirements=tuple(
                    str(name) for name in (getattr(task, "tool_config", None) or ())
                ),
                original_model=original_model,
                failure_class=recovery_class.value,
                failure_reason=reason,
                attempt_count=int(task.attempts),
                verified_completed_work=tuple(executed) if executed else (),
                artifact_versions=checkpoint.artifact_versions,
                pending_step=checkpoint.pending_step,
                side_effects_performed=checkpoint.side_effects_performed,
                action=action.value,
            )
            task.focus = f"{task.focus}\n\n{envelope.render()}"
            record.selection_reason = action_reason
            record.fallback_result = "resuming"
            self._emit_recovery("TASK_RESUMED", {
                "task_id": task_id, "agent_id": original_model,
                "completed_model_steps": checkpoint.completed_model_steps,
                "side_effects_performed": checkpoint.side_effects_performed,
            })

        record.fallback_requested = True
        record.selected_fallback = str(getattr(fallback, "adapter_id", original_model))
        self._emit_recovery("FALLBACK_REQUESTED", {
            "task_id": task_id, "agent_id": original_model,
            "action": record.action, "reason": reason,
        })
        self._emit_recovery("FALLBACK_SELECTED", {
            "task_id": task_id, "original_model": original_model,
            "selected_fallback": record.selected_fallback,
            "action": record.action,
        })
        # Keep the action reason — it says whether this is a resume or a replay,
        # which is the part an auditor needs — and append why the task failed.
        record.selection_reason = f"{record.selection_reason}; cause: {reason}"
        if not record.fallback_result:
            record.fallback_result = "attempting"
        self._recovery_steps_taken[task_id] = taken + 1
        self.adapters_by_id[original_model] = fallback
        task.completed_at = None
        task.error = None
        task.transition(AgentTaskStatus.QUEUED)
        record.final_task_status = str(task.status)
        self.recovery_records.append(record.to_dict())

    def _emit_recovery(self, event: str, payload: Mapping[str, Any]) -> None:
        """Publish a recovery event on the scheduler's event channel.

        Work Order 09 section 5 requires recovery to be visible to the
        Collaboration Kernel rather than a hidden mechanism.  This uses the
        same sink the kernel already consumes for tool calls and turn
        completion, so recovery appears in the same stream as everything else.

        A sink that raises must not take the run down: the record is still kept
        on the scheduler and returned in the response, so losing the event is
        a visibility loss, not a correctness one.
        """

        if self.event_sink is None:
            return
        try:
            self.event_sink(f"recovery.{event}", dict(payload))
        except Exception:  # noqa: BLE001 - a broken sink must not fail the run
            pass

    async def _execute(
        self,
        org: OrganizationRun,
        task: AgentTaskRecord,
        reservation: CostReservation | None = None,
    ) -> None:
        if task.depends_on:
            dep_results = {
                dep: org.tasks[dep].result_summary
                for dep in task.depends_on
                if dep in org.tasks and org.tasks[dep].result_summary
            }
            if len(dep_results) == len(task.depends_on):
                v2_ident = task.execution_identity("v2", dependency_results=dep_results, adapters=self.adapters_by_id)
                cached = self.reuse.lookup(v2_ident, version="v2")
                if cached is not None and task.executor_kind != ExecutorKind.VERIFICATION_CELL:
                    # A3.1 optimization reuse: authoritative prior result,
                    # zero inference / zero transport; Governor and budget
                    # admission have both already run.
                    task.result_summary = cached
                    task.reused_from = v2_ident
                    self.optimization_reuse_hits += 1
                    task.transition(AgentTaskStatus.COMPLETED)
                    task.completed_at = _now()
                    return

        if task.status != AgentTaskStatus.QUEUED:
            # M5.6.1 surgical cancellation guard: the task was cancelled
            # between admission and execution start (e.g. authoritative
            # run/parent cancellation landed on the queued record). Cancelled
            # work is never dispatched; the record's terminal state stands.
            return

        task.attempts += 1
        task.transition(AgentTaskStatus.STARTING)
        task.started_at = _now()
        task.transition(AgentTaskStatus.RUNNING)
        incurred_cost = 0.0
        dispatched_keys: set[str] = set()
        committed_keys: set[str] = set()
        if self.recovery_store is not None:
            # Zero-cost work skips admission journaling entirely; make sure
            # the baseline rows exist before its dispatch journals run.
            _ensure_durable_baseline(self.recovery_store, org, task)
        try:
            if task.executor_kind == ExecutorKind.DETERMINISTIC_TOOL:
                if self.deterministic_executor is not None:
                    # Real deterministic work through the injected handler;
                    # a raised exception follows the existing FAILED path.
                    task.result_summary = str(
                        self.deterministic_executor(task, org)
                    )[:200]
                else:
                    task.result_summary = f"deterministic:{task.focus[:200]}"
                task.transition(AgentTaskStatus.COMPLETED)
                task.completed_at = _now()
                return

            if task.executor_kind == ExecutorKind.VERIFICATION_CELL:
                requested_verifier_ids = tuple(
                    dict.fromkeys(str(v).strip() for v in task.verifier_ids if str(v).strip())
                )
                if len(requested_verifier_ids) < 2:
                    raise ValueError(
                        f"verification cell {task.id!r} requires at least 2 explicit verifier_ids, found {len(requested_verifier_ids)}"
                    )

                missing = [v_id for v_id in requested_verifier_ids if v_id not in self.adapters_by_id]
                if missing:
                    raise ValueError(
                        f"verification cell {task.id!r} requested unknown or unavailable verifier adapter(s): {', '.join(missing)}"
                    )

                eligible_adapters = {
                    v_id: self.adapters_by_id[v_id]
                    for v_id in requested_verifier_ids
                }

                async def _call_verifier(v_id: str, adapter: Any) -> tuple[str, dict[str, Any], float, bool]:
                    _vk = f"verifier:{v_id}"
                    self._journal_call(
                        org_id=org.organization_id, task_id=task.id,
                        attempt=task.attempts, call_key=_vk,
                        state="DISPATCH_INTENT_DURABLE",
                        executor_kind=str(task.executor_kind), adapter_id=v_id,
                        reservation_id=reservation.reservation_id if reservation else "",
                    )
                    dispatched_keys.add(_vk)
                    self._journal_call(
                        org_id=org.organization_id, task_id=task.id,
                        attempt=task.attempts, call_key=_vk,
                        state="DISPATCHED",
                        executor_kind=str(task.executor_kind), adapter_id=v_id,
                        reservation_id=reservation.reservation_id if reservation else "",
                    )
                    req = ModelRequest(messages=({"role": "user", "content": task.focus},))
                    resp = await asyncio.wait_for(adapter.generate(req), timeout=300)
                    self._journal_call(
                        org_id=org.organization_id, task_id=task.id,
                        attempt=task.attempts, call_key=_vk,
                        state="RESPONSE_RECEIVED",
                        executor_kind=str(task.executor_kind), adapter_id=v_id,
                        reservation_id=reservation.reservation_id if reservation else "",
                    )
                    resp_text = getattr(resp, "text", "")
                    usage = getattr(resp, "usage", None)
                    cost_val = float(getattr(usage, "cost_usd", 0.0) or 0.0)
                    is_local = _is_adapter_local(adapter)
                    parsed = parse_verifier_payload(resp_text)
                    self._journal_call(
                        org_id=org.organization_id, task_id=task.id,
                        attempt=task.attempts, call_key=_vk,
                        state="RESPONSE_COMMITTED",
                        executor_kind=str(task.executor_kind), adapter_id=v_id,
                        reservation_id=reservation.reservation_id if reservation else "",
                        actual_cost_usd=cost_val,
                        response_payload=json.dumps(parsed, sort_keys=True)[:2000],
                        input_tokens=getattr(resp.usage, "input_tokens", None),
                        output_tokens=getattr(resp.usage, "output_tokens", None),
                        token_source=("provider_reported"
                                      if getattr(resp.usage, "input_tokens", None) is not None
                                      and getattr(resp.usage, "output_tokens", None) is not None
                                      else None),
                    )
                    committed_keys.add(_vk)
                    # PS1-13I: durable ACTUAL-executed-model binding per verifier
                    # (multi-call: distinct call_key per verifier).
                    _record_provenance_model_binding(
                        self.recovery_store,
                        organization_id=org.organization_id,
                        project_id=task.project_id,
                        task=task,
                        attempt=task.attempts,
                        call_key=_vk,
                        adapter=adapter,
                        response=resp,
                        outcome="succeeded",
                        cost_usd=cost_val,
                        input_tokens=getattr(resp.usage, "input_tokens", None),
                        output_tokens=getattr(resp.usage, "output_tokens", None),
                        response_text=resp_text,
                    )
                    return v_id, parsed, cost_val, is_local

                results = await asyncio.gather(
                    *(_call_verifier(v_id, adapter) for v_id, adapter in eligible_adapters.items())
                )

                verdicts: dict[str, dict[str, Any]] = {}
                total_cell_cost = 0.0
                local_count = 0
                api_count = 0
                for v_id, parsed_v, call_cost, is_local in results:
                    verdicts[v_id] = parsed_v
                    total_cell_cost += call_cost
                    if is_local:
                        local_count += 1
                    else:
                        api_count += 1

                incurred_cost = total_cell_cost

                # Deterministic Non-Majoritarian Reducer
                all_pass = all(v["verdict"] == "pass" for v in verdicts.values())
                any_fail = any(v["verdict"] == "fail" for v in verdicts.values())
                any_uncertain = any(v["verdict"] not in {"pass", "fail"} for v in verdicts.values())

                if any_fail:
                    outcome = "disputed"
                    dissent_present = True
                elif all_pass and len(verdicts) >= 2:
                    outcome = "verified"
                    dissent_present = False
                elif any_uncertain or len(verdicts) < 2:
                    outcome = "inconclusive"
                    dissent_present = False
                else:
                    outcome = "inconclusive"
                    dissent_present = False

                verification_result = {
                    "outcome": outcome,
                    "verdicts": verdicts,
                    "dissent_present": dissent_present,
                    "total_verifiers": len(verdicts),
                    "aggregate_cost_usd": total_cell_cost,
                }

                task.verification_result = verification_result
                task.result_summary = json.dumps(verification_result, sort_keys=True)
                task.local_calls += local_count
                task.api_calls += api_count
                task.cost_usd += total_cell_cost
                task.error = None
                task.transition(AgentTaskStatus.VERIFYING)
                task.transition(AgentTaskStatus.COMPLETED)
                task.completed_at = _now()
                return

            if task.executor_kind == ExecutorKind.COLLABORATION:
                p_ids = task.participant_ids or (task.agent_id,)
                participants = []
                for pid in p_ids:
                    adapter = self.adapters_by_id.get(pid)
                    if adapter is None:
                        raise ValueError(f"no adapter bound for collaboration participant {pid!r}")
                    participants.append(adapter)

                # Initialize StateManager with ProjectState
                state = ProjectState(
                    project_id=org.project_id,
                    goal=task.focus,
                    privacy_mode=PrivacyMode.FULL_FRONTIER,
                    compute_policy=ComputePolicy.MAXIMUM_INTELLIGENCE,
                )
                state_mgr = StateManager(state)
                max_rounds = max(1, getattr(task, "max_rounds", 1) or 1)
                max_phases = max(1, getattr(task, "max_adaptive_phases", 4) or 4)
                kernel_config = KernelConfig(max_rounds=max_rounds, max_adaptive_phases=max_phases)

                arch_str = (task.collaboration_architecture or "joint").lower()
                try:
                    mode_val = CollaborationMode(arch_str)
                except Exception:
                    mode_val = CollaborationMode.JOINT

                kernel = CollaborationKernel(
                    state_mgr,
                    participants,
                    config=kernel_config,
                    recovery_store=self.recovery_store,
                    journal_context={
                        "organization_id": org.organization_id,
                        "task_id": task.id,
                        "attempt": task.attempts,
                        "architecture": arch_str,
                        "phase": 1,
                    },
                )

                sync_str = (task.sync_mode or "bounded_staleness").lower()
                try:
                    sync_val = SyncMode(sync_str)
                except Exception:
                    sync_val = SyncMode.BOUNDED_STALENESS

                plan = getattr(task, "decomposition_plan", None)
                if plan is None and task.collaboration_payload:
                    plan = task.collaboration_payload.get("decomposition_plan")

                result = await kernel.run(mode=mode_val, sync_mode=sync_val, decomposition_plan=plan)

                # Record participant calls and costs
                collab_cost = 0.0
                for call in result.call_records:
                    adapter = self.adapters_by_id.get(call.adapter_id)
                    if adapter is not None and _is_adapter_local(adapter):
                        task.local_calls += 1
                    else:
                        task.api_calls += 1
                    collab_cost += call.cost_usd

                incurred_cost = collab_cost
                task.cost_usd += collab_cost
                # PS1-13I: durable ACTUAL-executed-model bindings for
                # collaboration participants (best-effort, from durable rows).
                try:
                    if self.recovery_store is not None:
                        _rows = self.recovery_store.list_call_attempts(task.id)
                        for _row in _rows:
                            if int(_row.get("task_attempt_number") or 0) != int(task.attempts):
                                continue
                            if _row.get("response_committed_at") is None and str(_row.get("state")) not in {"RESPONSE_COMMITTED", "SETTLED"}:
                                continue
                            _ad = self.adapters_by_id.get(str(_row.get("adapter_id") or ""))
                            if _ad is None:
                                continue
                            _record_provenance_model_binding(
                                self.recovery_store,
                                organization_id=org.organization_id,
                                project_id=task.project_id,
                                task=task,
                                attempt=task.attempts,
                                call_key=str(_row.get("call_key")),
                                adapter=_ad,
                                response=None,
                                outcome="succeeded",
                                cost_usd=float(_row.get("actual_cost_usd") or 0.0),
                                response_text=str(_row.get("response_payload") or "") or None,
                            )
                except Exception:
                    pass

                call_summaries = [call.summary for call in result.call_records if call.summary]
                summary_text = "\n".join(call_summaries) if call_summaries else "collaboration completed"
                task.result_summary = summary_text
                task.collaboration_result = result.to_dict()
                task.error = None
                task.transition(AgentTaskStatus.VERIFYING)
                task.transition(AgentTaskStatus.COMPLETED)
                task.completed_at = _now()
                return

            if task.executor_kind == ExecutorKind.MANAGER:
                if task.manager_depth >= self.envelope.max_manager_depth:
                    raise ValueError(
                        f"manager task {task.id!r} depth {task.manager_depth} exceeds max_manager_depth limit ({self.envelope.max_manager_depth})"
                    )
                adapter = self.adapters_by_id.get(task.agent_id)
                if adapter is None:
                    raise ValueError(f"no adapter bound for manager agent {task.agent_id!r}")

                bounded_context = {
                    "organization_id": org.organization_id,
                    "organization_goal": org.goal,
                    "manager_task_id": task.id,
                    "manager_focus": task.focus,
                    "manager_depth": task.manager_depth,
                    "max_children": 8,
                    "parent_results": {
                        dep_id: org.tasks[dep_id].result_summary
                        for dep_id in task.depends_on
                        if dep_id in org.tasks and org.tasks[dep_id].result_summary
                    },
                    "completed_tasks": [
                        t.id for t in org.tasks.values() if t.status in {AgentTaskStatus.COMPLETED, AgentTaskStatus.MERGED}
                    ],
                }

                prompt_content = json.dumps(bounded_context, ensure_ascii=False)
                request = ModelRequest(messages=({"role": "user", "content": prompt_content},))
                _mck = _MANAGER_PROPOSAL_CALL_KEY
                _mrid = reservation.reservation_id if reservation else ""
                _mrusd = reservation.reserved_usd if reservation else 0.0
                self._journal_call(
                    org_id=org.organization_id, task_id=task.id,
                    attempt=task.attempts, call_key=_mck,
                    state="DISPATCH_INTENT_DURABLE",
                    executor_kind=str(task.executor_kind), adapter_id=task.agent_id,
                    reservation_id=_mrid, reserved_usd=_mrusd,
                )
                dispatched_keys.add(_mck)
                self._journal_call(
                    org_id=org.organization_id, task_id=task.id,
                    attempt=task.attempts, call_key=_mck,
                    state="DISPATCHED",
                    executor_kind=str(task.executor_kind), adapter_id=task.agent_id,
                    reservation_id=_mrid,
                )
                response = await asyncio.wait_for(adapter.generate(request), timeout=300)
                self._journal_call(
                    org_id=org.organization_id, task_id=task.id,
                    attempt=task.attempts, call_key=_mck,
                    state="RESPONSE_RECEIVED",
                    executor_kind=str(task.executor_kind), adapter_id=task.agent_id,
                    reservation_id=_mrid,
                )
                text = getattr(response, "text", "")
                usage = getattr(response, "usage", None)
                cost = float(getattr(usage, "cost_usd", 0.0) or 0.0)
                incurred_cost = cost
                if _is_adapter_local(adapter):
                    task.local_calls += 1
                else:
                    task.api_calls += 1
                task.cost_usd += cost

                existing_ids = set(org.tasks.keys())
                proposal = parse_manager_proposal_payload(
                    text,
                    manager_id=task.id,
                    organization_id=org.organization_id,
                    project_id=org.project_id,
                    manager_depth=task.manager_depth,
                    max_children=8,
                    existing_task_ids=existing_ids,
                )

                if not proposal["valid"]:
                    task.proposed_children = ()
                    raise ValueError(f"manager proposal validation failed: {proposal['error']}")

                # The raw proposal payload is now durable on the call attempt
                # row: a crash after this point recovers from the committed
                # response instead of calling the provider again.
                self._journal_call(
                    org_id=org.organization_id, task_id=task.id,
                    attempt=task.attempts, call_key=_mck,
                    state="RESPONSE_COMMITTED",
                    executor_kind=str(task.executor_kind), adapter_id=task.agent_id,
                    reservation_id=_mrid,
                    actual_cost_usd=cost,
                    response_payload=text[:2000],
                    input_tokens=getattr(usage, "input_tokens", None),
                    output_tokens=getattr(usage, "output_tokens", None),
                    token_source=("provider_reported"
                                  if getattr(usage, "input_tokens", None) is not None
                                  and getattr(usage, "output_tokens", None) is not None
                                  else None),
                )
                committed_keys.add(_mck)
                # PS1-13I: durable ACTUAL-executed-model binding (manager proposal).
                _record_provenance_model_binding(
                    self.recovery_store,
                    organization_id=org.organization_id,
                    project_id=task.project_id,
                    task=task,
                    attempt=task.attempts,
                    call_key=_mck,
                    adapter=adapter,
                    response=response,
                    outcome="succeeded",
                    cost_usd=cost,
                    input_tokens=getattr(usage, "input_tokens", None),
                    output_tokens=getattr(usage, "output_tokens", None),
                    response_text=text,
                )

                # Transition manager task to completed before evaluating dynamic authorization
                task.error = None
                task.transition(AgentTaskStatus.VERIFYING)
                task.transition(AgentTaskStatus.COMPLETED)
                task.completed_at = _now()

                # Governor Evaluation & Dynamic Atomic Insertion under admission lock
                if not hasattr(self, "_admission_lock") or self._admission_lock is None:
                    self._admission_lock = asyncio.Lock()

                async with self._admission_lock:
                    authorized_siblings: set[str] = set()
                    denied_siblings: set[str] = set()
                    updated_children_records = []

                    for child_rec in proposal["children"]:
                        auth_decision = AgentGovernor.authorize_child_proposal(
                            child_rec,
                            parent_task=task,
                            org=org,
                            envelope=self.envelope,
                            settings=self.settings,
                            available_adapters=self.adapters_by_id,
                            authorized_siblings=authorized_siblings,
                            denied_siblings=denied_siblings,
                        )
                        child_rec.authorization_record = auth_decision
                        if auth_decision["authorized"]:
                            authorized_siblings.add(child_rec.id)
                            child_rec.transition(AgentTaskStatus.AUTHORIZED)

                            # Sanctioned duplicate reuse check (Governor has
                            # authorized first; reuse never bypasses it)
                            dep_results = {
                                dep: org.tasks[dep].result_summary
                                for dep in child_rec.depends_on
                                if dep in org.tasks and org.tasks[dep].result_summary
                            }
                            if len(dep_results) == len(child_rec.depends_on):
                                child_v2 = child_rec.execution_identity("v2", dependency_results=dep_results, adapters=self.adapters_by_id)
                                cached = self.reuse.lookup(child_v2, version="v2")
                                child_reused_key = child_v2
                            else:
                                cached = None
                                child_reused_key = None
                            if cached is not None and child_rec.executor_kind != ExecutorKind.VERIFICATION_CELL:
                                child_rec.result_summary = cached
                                child_rec.reused_from = child_reused_key
                                child_rec.transition(AgentTaskStatus.COMPLETED)
                                child_rec.completed_at = _now()
                            else:
                                child_rec.transition(AgentTaskStatus.QUEUED)

                            # Insert into live task graph
                            org.tasks[child_rec.id] = child_rec
                        else:
                            denied_siblings.add(child_rec.id)

                        updated_children_records.append(child_rec.to_dict())

                    task.proposed_children = tuple(updated_children_records)
                    task.result_summary = json.dumps({
                        "proposal_version": proposal["version"],
                        "proposed_child_count": len(proposal["children"]),
                        "authorized_child_count": len(authorized_siblings),
                        "reason": proposal["reason"],
                    })

                return

            adapter = self.adapters_by_id.get(task.agent_id)
            if adapter is None:
                raise ValueError(f"no adapter bound for agent {task.agent_id!r}")
            # Agent-to-agent handoff: the downstream agent receives a structured
            # reference to what its dependencies produced.  Without this the
            # graph ran in order but each agent still started from nothing, so
            # "Agent A -> Agent B" was a sequence of independent calls.
            focus = task.focus
            envelope = build_handoff_envelope(task, org)
            if envelope:
                focus = f"{focus}\n\n{HANDOFF_HEADER}:\n\n{envelope}"
            request = ModelRequest(messages=({"role": "user", "content": focus},))
            _ck = f"task:{task.id}:attempt:{task.attempts}"
            _rid = reservation.reservation_id if reservation else ""
            _rusd = reservation.reserved_usd if reservation else 0.0
            self._journal_call(
                org_id=org.organization_id, task_id=task.id,
                attempt=task.attempts, call_key=_ck,
                state="DISPATCH_INTENT_DURABLE",
                executor_kind=str(task.executor_kind), adapter_id=task.agent_id,
                reservation_id=_rid, reserved_usd=_rusd,
            )
            dispatched_keys.add(_ck)
            self._journal_call(
                org_id=org.organization_id, task_id=task.id,
                attempt=task.attempts, call_key=_ck,
                state="DISPATCHED",
                executor_kind=str(task.executor_kind), adapter_id=task.agent_id,
                reservation_id=_rid,
            )
            response = await self._generate_with_tools(adapter, request, task, org)
            self._journal_call(
                org_id=org.organization_id, task_id=task.id,
                attempt=task.attempts, call_key=_ck,
                state="RESPONSE_RECEIVED",
                executor_kind=str(task.executor_kind), adapter_id=task.agent_id,
                reservation_id=_rid,
            )
            text = getattr(response, "text", "")
            usage = getattr(response, "usage", None)
            cost = float(getattr(usage, "cost_usd", 0.0) or 0.0)
            incurred_cost = cost
            task.result_summary = text[:500]
            if _is_adapter_local(adapter):
                task.local_calls += 1
            else:
                task.api_calls += 1
            task.cost_usd += cost
            self._journal_call(
                org_id=org.organization_id, task_id=task.id,
                attempt=task.attempts, call_key=_ck,
                state="RESPONSE_COMMITTED",
                executor_kind=str(task.executor_kind), adapter_id=task.agent_id,
                reservation_id=_rid,
                actual_cost_usd=cost,
                response_payload=text[:2000],
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                token_source=("provider_reported"
                              if getattr(usage, "input_tokens", None) is not None
                              and getattr(usage, "output_tokens", None) is not None
                              else None),
            )
            committed_keys.add(_ck)
            # PS1-13I: durable ACTUAL-executed-model binding (primary call).
            _record_provenance_model_binding(
                self.recovery_store,
                organization_id=org.organization_id,
                project_id=task.project_id,
                task=task,
                attempt=task.attempts,
                call_key=_ck,
                adapter=adapter,
                response=response,
                outcome="succeeded",
                cost_usd=cost,
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                response_text=text,
            )
            task.error = None
            task.transition(AgentTaskStatus.VERIFYING)
            task.transition(AgentTaskStatus.COMPLETED)
            task.completed_at = _now()
            return
        except Exception as exc:
            cost_val = float(getattr(exc, "cost_usd", 0.0) or 0.0)
            if cost_val > 0:
                incurred_cost = max(incurred_cost, cost_val)
                task.cost_usd += cost_val
            task.error = f"{type(exc).__name__}: {exc}"[:400]
            if task.status in {
                AgentTaskStatus.STARTING,
                AgentTaskStatus.RUNNING,
                AgentTaskStatus.WAITING,
                AgentTaskStatus.VERIFYING,
            }:
                task.transition(AgentTaskStatus.FAILED)
            task.completed_at = _now()
            can_retry = task.attempts < org.max_attempts
            if can_retry:
                # FAILED -> QUEUED retry edge; attempt 2 will acquire fresh reservation
                task.completed_at = None
                task.transition(AgentTaskStatus.QUEUED)
            else:
                # Per-model attempts are exhausted.  Governed recovery: only a
                # retriable failure may move to another model, only one such
                # step is permitted per task, and a task that already changed
                # something is never replayed from the start.
                self._attempt_governed_recovery(task, org, exc)
        finally:
            if reservation is not None and reservation.status == "reserved":
                if not hasattr(self, "_admission_lock") or self._admission_lock is None:
                    self._admission_lock = asyncio.Lock()
                async with self._admission_lock:
                    reservation.settled_actual_usd = incurred_cost
                    reservation.status = "settled"
                    reservation.settled_at = _now()
                    if incurred_cost > reservation.reserved_usd + 1e-9:
                        reservation.integrity_violation = True
                        task.error = (
                            f"budget bound integrity violation: actual incurred cost ${incurred_cost:.4f} "
                            f"exceeded conservative reservation ${reservation.reserved_usd:.4f}"
                        )
                    self._assert_budget_invariant(org)
            if self.recovery_store is not None:
                uncommitted_external = bool(dispatched_keys - committed_keys)
                try:
                    # Atomic settlement of THIS attempt. An ambiguous external
                    # failure (dispatch intent without a committed response)
                    # keeps its exposure: the reservation goes UNCERTAIN and
                    # uncommitted rows stay put for A2.1C reconciliation.
                    with self.recovery_store.transaction() as tx:
                        if reservation is not None:
                            if uncommitted_external:
                                tx.transition_reservation(
                                    reservation.reservation_id, "uncertain"
                                )
                            else:
                                tx.transition_reservation(
                                    reservation.reservation_id,
                                    "settled",
                                    settled_actual_usd=incurred_cost,
                                    integrity_violation=reservation.integrity_violation,
                                )
                        tx.settle_call_attempts(
                            organization_id=org.organization_id,
                            task_id=task.id,
                            task_attempt_number=task.attempts,
                            include_uncommitted=not uncommitted_external,
                        )
                except Exception:
                    # Post-terminal bookkeeping only: the live run's financial
                    # truth stays in-memory; leftover rows are reconciled from
                    # evidence at startup (A2.1C), never half-settled here.
                    pass


def live_organization_from_call_records(
    call_records: Sequence[Mapping[str, Any]],
    *,
    organization_id: str,
    project_id: str,
    goal: str,
    adapters_by_id: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive a retired organization record from MEASURED model calls.

    This is the honest bridge between planning and execution for desktop chat:
    every task corresponds to one actual kernel call with its real focus, cost
    and outcome.  Nothing is fabricated - failed calls become FAILED tasks,
    and counts always match the underlying records.
    """

    total_cost = 0.0
    for record in call_records:
        try:
            total_cost += _finite_non_negative(record.get("cost_usd", 0.0), "cost_usd")
        except (TypeError, ValueError):
            continue
    org = OrganizationRun(
        organization_id=organization_id,
        project_id=project_id,
        goal=goal,
        budget_usd=total_cost,
        max_concurrent=max(1, len(call_records) or 1),
    )
    for index, record in enumerate(call_records, start=1):
        status = str(record.get("status", ""))
        adapter_id = str(record.get("adapter_id", f"unknown-{index}"))
        succeeded = status in {"complete", "completed"}
        # The call record carries the adapter's own classification from the
        # kernel (cloud adapters must never be labeled as local nano work).
        adapter = (adapters_by_id or {}).get(adapter_id)
        is_local = (_is_adapter_local(adapter) if adapter is not None
                    else record.get("adapter_is_local", record.get("is_local")))
        executor_kind = (
            ExecutorKind.NANO_LOCAL_MODEL if is_local is True else
            ExecutorKind.NANO_CLOUD_MODEL if is_local is False else "model_unknown"
        )
        # Timestamp honesty: use the call's own captured execution timing
        # when present; a reconstruction-time stamp only fills in timing
        # that was never captured and is then flagged in the record.
        stamps: dict[str, Any] = {}
        for target, source_key in (
            ("created_at", "created_at"),
            ("started_at", "started_at"),
            ("completed_at", "completed_at"),
        ):
            source_value = record.get(source_key)
            stamps[target] = (
                str(source_value) if source_value else None
            )
        # Only record creation can use reconstruction time. Missing model
        # execution timestamps stay unknown rather than masquerading as now.
        if stamps["created_at"] is None:
            stamps["created_at"] = stamps["started_at"] or _now()
        task = AgentTaskRecord(
            id=f"call-{index:02d}-{adapter_id}",
            organization_id=organization_id,
            project_id=project_id,
            agent_id=adapter_id,
            focus=str(record.get("focus", ""))[:200] or goal[:200],
            depends_on=(),
            executor_kind=executor_kind,
            attempts=1,
            result_summary=(str(record.get("summary"))[:300] if record.get("summary") else None),
            error=(str(record["error"])[:400] if record.get("error") else None),
            cost_usd=_finite_non_negative(record.get("cost_usd", 0.0), "cost_usd"),
            created_at=stamps["created_at"],
            started_at=stamps["started_at"],
            completed_at=stamps["completed_at"],
        )
        # This is a RECONSTRUCTION from measured call records, not a live
        # execution, so final states are assigned directly: retired for
        # completed calls, failed otherwise. Counts always match reality.
        task.status = AgentTaskStatus.RETIRED if succeeded else AgentTaskStatus.FAILED
        if succeeded:
            task.result_summary = task.result_summary or ""
        org.tasks[task.id] = task
    document = org.to_dict()
    document["source"] = "reconstructed_from_calls"
    for task_record in document["tasks"]:
        task_record["source"] = "reconstructed_from_calls"
    return document


__all__ = [
    "AgentTaskRecord",
    "AgentTaskStatus",
    "DuplicateResultReuse",
    "ExecutorKind",
    "NanoScheduler",
    "OrganizationRun",
    "parse_verifier_payload",
]
