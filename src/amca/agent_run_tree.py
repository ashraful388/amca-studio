"""PS1-13J Agent Run-Tree API (bounded read-only backend API only).

Truthful reconstruction of one organization/run execution tree from
EXISTING persistence only (13H observability + 13I durable provenance
bindings + A2 recovery stores)::

    organization -> manager -> nano/dynamic children -> tasks
        -> actual model/tool bindings -> results/artifacts
        -> verification / terminal state

Scope guarantees:
- Read-only: never mutates runtime, never creates a shadow runtime model.
- Project-isolated: foreign and nonexistent organizations are externally
  indistinguishable (one generic ``UnknownRunOrganization`` -> HTTP 404).
- Secret-safe: no raw ``input_payload`` / ``response_payload`` /
  credentials; 13H sanitizers/redactors on every free-text field; result
  payloads exposed only as digest/size/preview already stored by 13I.
- Bounded: deterministic ordering (created_at, then id), MAX_NODES/depth
  caps with deterministic truncation metadata for continuation.
- Lifecycle fidelity: persisted status strings pass through verbatim
  (proposed/authorized/queued/starting/running/waiting/verifying/
  completed/merged/retired/failed/cancelled); no fictional states added.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .agent_observability import (
    ACTIVE_TASK_STATUSES,
    _redact_string_patterns,
    build_timing,
    is_task_active,
    lifecycle_counts,
    sanitize_error_text,
)

RUN_TREE_SCHEMA = "amca-v111-agent-run-tree/1"
RUN_SUMMARY_SCHEMA = "amca-v111-agent-run-summary/1"
MAX_NODES_PER_RESPONSE = 250
MAX_DEPTH = 8


class UnknownRunOrganization(ValueError):
    """Generic error: foreign and nonexistent orgs are indistinguishable."""


def _safe_text(value: Any) -> str | None:
    if value is None:
        return None
    return _redact_string_patterns(str(value))


def _parse_refs(value: Any) -> list[str]:
    """Evidence refs may be a JSON string (persisted) or an already-parsed list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return [value]
        if isinstance(parsed, (list, tuple)):
            return [str(v) for v in parsed]
        return [str(parsed)]
    return [str(value)]


def _task_node(
    task: Mapping[str, Any],
    *,
    model_bindings: list[dict[str, Any]],
    tool_bindings: list[dict[str, Any]],
    call_attempts: list[dict[str, Any]],
    verdicts: list[dict[str, Any]],
    depth: int,
) -> dict[str, Any]:
    """One bounded, secret-free task node from persisted rows only."""
    return {
        "task_id": str(task["id"]),
        "agent_id": _safe_text(task.get("agent_id")),
        "executor_kind": _safe_text(task.get("executor_kind")),
        "status": str(task.get("status")),
        "manager_depth": int(task.get("manager_depth") or 0),
        "proposed_by_manager_id": _safe_text(task.get("proposed_by_manager_id")),
        "depends_on": [str(x) for x in (task.get("depends_on_json") or [])],
        "focus": _safe_text(task.get("focus")),
        "attempts": int(task.get("attempts") or 0),
        "recovery": {
            "disposition": _safe_text(task.get("recovery_disposition")),
            "external_outcome_uncertain": bool(task.get("external_outcome_uncertain")),
        },
        "result": {
            "summary": _safe_text(task.get("result_summary")),
            "error": sanitize_error_text(task.get("error")),
            "cost_usd": float(task.get("cost_usd") or 0.0),
            "api_calls": int(task.get("api_calls") or 0),
            "local_calls": int(task.get("local_calls") or 0),
            "reused_from": _safe_text(task.get("reused_from")),
        },
        "timing": build_timing(
            task.get("created_at"), task.get("started_at"), task.get("completed_at")
        ),
        "model_bindings": [
            {
                "binding_id": b.get("binding_id"),
                "task_attempt": b.get("task_attempt"),
                "call_key": b.get("call_key"),
                "call_attempt_id": b.get("call_attempt_id"),
                "provider_id": b.get("provider_id"),
                "model_id": b.get("model_id"),
                "locality": b.get("locality"),
                "adapter_id": b.get("adapter_id"),
                "outcome": b.get("outcome"),
                "response_digest": b.get("response_digest"),
                "response_size_bytes": b.get("response_size_bytes"),
                "response_preview": b.get("response_preview"),
                "executed_at": b.get("executed_at"),
            }
            for b in model_bindings
        ],
        "tool_bindings": [
            {
                "binding_id": t.get("binding_id"),
                "task_attempt": t.get("task_attempt"),
                "tool_id": t.get("tool_id"),
                "operation": t.get("operation"),
                "capability_ref": t.get("capability_ref"),
                "authz_ref": t.get("authz_ref"),
                "tool_execution_id": t.get("tool_execution_id"),
                "outcome": t.get("outcome"),
                "result_ref": t.get("result_ref"),
                "result_digest": t.get("result_digest"),
                "result_size_bytes": t.get("result_size_bytes"),
                "result_preview": t.get("result_preview"),
                "created_at": t.get("created_at"),
            }
            for t in tool_bindings
        ],
        "call_attempts": [
            {
                "call_attempt_id": c.get("call_attempt_id"),
                "task_attempt_number": c.get("task_attempt_number"),
                "call_key": c.get("call_key"),
                "executor_kind": c.get("executor_kind"),
                "adapter_id": c.get("adapter_id"),
                "state": c.get("state"),
                "actual_cost_usd": c.get("actual_cost_usd"),
                "response_payload_present": bool(c.get("response_payload")),
                "settled_at": c.get("settled_at"),
            }
            for c in call_attempts
        ],
        "verifications": [
            {
                "verifier_id": v.get("verifier_id"),
                "verdict": v.get("verdict"),
                "summary": _safe_text(v.get("summary")),
                "evidence_refs": _parse_refs(v.get("evidence_refs_json")),
                "created_at": v.get("created_at"),
            }
            for v in verdicts
        ],
        "artifact_refs": sorted(
            {str(t["result_ref"]) for t in tool_bindings if t.get("result_ref")}
        ),
        "depth": depth,
        "children": [],
    }


def build_run_tree(
    recovery_store: Any,
    provenance_store: Any,
    organization_id: str,
    *,
    requesting_project_id: str,
    max_nodes: int = MAX_NODES_PER_RESPONSE,
    max_depth: int = MAX_DEPTH,
) -> dict[str, Any]:
    """Reconstruct one bounded run tree from persisted state only.

    Raises ``UnknownRunOrganization`` for both foreign- and nonexistent
    organizations so callers cannot probe existence.
    """
    requesting_project_id = str(requesting_project_id)
    organization = recovery_store.get_organization(str(organization_id))
    if organization is None or str(organization.get("project_id")) != requesting_project_id:
        raise UnknownRunOrganization("unknown organization")

    tasks = list(recovery_store.list_tasks_by_organization(str(organization_id)))
    # Task-level project guard (defense in depth; org already scoped).
    tasks = [t for t in tasks if str(t.get("project_id")) == requesting_project_id]

    model_rows = provenance_store.list_model_bindings_for_organization(
        str(organization_id), requesting_project_id=requesting_project_id
    )
    tool_rows = provenance_store.list_tool_bindings_for_organization(
        str(organization_id), requesting_project_id=requesting_project_id
    )
    model_by_task: dict[str, list[dict[str, Any]]] = {}
    for row in model_rows:
        model_by_task.setdefault(str(row["task_id"]), []).append(row)
    tool_by_task: dict[str, list[dict[str, Any]]] = {}
    for row in tool_rows:
        tool_by_task.setdefault(str(row["task_id"]), []).append(row)

    # Deterministic ordering: created_at then task id.
    ordered = sorted(tasks, key=lambda t: (str(t.get("created_at")), str(t["id"])))

    # Manager agent_id -> earliest task for that agent (parent mapping).
    agent_parent: dict[str, str] = {}
    for task in ordered:
        agent_parent.setdefault(str(task["agent_id"]), str(task["id"]))

    nodes: dict[str, dict[str, Any]] = {}
    # PS1-13K: batch-load per-task call attempts + verdicts with two org-wide
    # reads (was an N+1 of 2 queries per task). Grouping preserved exactly.
    attempts_by_task: dict[str, list[dict[str, Any]]] = {}
    for row in recovery_store.list_call_attempts_by_organization(str(organization_id)):
        attempts_by_task.setdefault(str(row["task_id"]), []).append(row)
    verdicts_by_task_map: dict[str, list[dict[str, Any]]] = {}
    for row in recovery_store.verdicts_by_organization(str(organization_id)):
        verdicts_by_task_map.setdefault(str(row["task_id"]), []).append(row)
    for task in ordered:
        task_id = str(task["id"])
        nodes[task_id] = _task_node(
            task,
            model_bindings=model_by_task.get(task_id, []),
            tool_bindings=tool_by_task.get(task_id, []),
            call_attempts=list(attempts_by_task.get(task_id, [])),
            verdicts=list(verdicts_by_task_map.get(task_id, [])),
            depth=0,
        )

    roots: list[dict[str, Any]] = []
    for task in ordered:
        node = nodes[str(task["id"])]
        parent_agent = node["proposed_by_manager_id"]
        parent_id = agent_parent.get(parent_agent) if parent_agent else None
        if parent_id and parent_id != node["task_id"] and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)

    # Cycle safety + bounded depth assignment via iterative DFS over
    # deterministic child order. Only nodes actually placed in the tree are
    # kept; any node beyond the node budget or depth cap is counted as
    # omitted and pruned from its parent/roots so the response never shows
    # phantom children.
    included = 0
    truncated = False
    omitted = 0
    seen: set[str] = set()

    # stack entries: (node, depth)
    stack: list[tuple[dict[str, Any], int]] = []
    for root in reversed(roots):
        stack.append((root, 0))

    while stack:
        node, depth = stack.pop()
        task_id = node["task_id"]
        if task_id in seen:
            continue
        if included >= max_nodes or depth > max_depth:
            # This node and its (not-yet-walked) subtree are omitted.
            if depth > max_depth or included >= max_nodes:
                truncated = True
            omitted += _count_subtree(node, seen)
            continue
        seen.add(task_id)
        node["depth"] = depth
        included += 1
        # Push children in reverse so left-most (earliest) is processed first.
        for child in reversed(node["children"]):
            stack.append((child, depth + 1))

    # Prune: keep only visited nodes, and drop children never visited.
    def prune(node: dict[str, Any]) -> dict[str, Any]:
        node["children"] = [prune(c) for c in node["children"] if c["task_id"] in seen]
        return node

    final_roots = [prune(r) for r in roots if r["task_id"] in seen]

    scoped_tasks = [t for t in ordered if str(t["id"]) in seen]
    all_statuses = [str(t.get("status")) for t in scoped_tasks]

    return {
        "schema": RUN_TREE_SCHEMA,
        "organization": {
            "organization_id": str(organization["organization_id"]),
            "project_id": str(organization["project_id"]),
            "goal": _safe_text(organization.get("goal")),
            "status": _safe_text(organization.get("status")),
            "budget_usd": float(organization.get("budget_usd") or 0.0),
            "max_concurrent": int(organization.get("max_concurrent") or 0),
            "retired": bool(organization.get("retired")),
            "created_at": organization.get("created_at"),
            "updated_at": organization.get("updated_at"),
        },
        "lifecycle_counts": lifecycle_counts(scoped_tasks),
        "included_nodes": included,
        "total_tasks": len(ordered),
        "status_coverage": sorted(set(all_statuses)),
        "bounds": {
            "max_nodes_per_response": int(max_nodes),
            "max_depth": int(max_depth),
        },
        "truncated": truncated,
        "omitted_nodes": omitted,
        "continuation": {
            "supported": bool(truncated),
            "strategy": "deterministic-order-offset",
            "note": "order is (created_at, task_id); re-request after state change",
        },
        "roots": final_roots,
    }


def _count_subtree(node: dict[str, Any], seen: set[str]) -> int:
    if node["task_id"] in seen:
        return 0
    count = 1
    for child in node.get("children", []):
        count += _count_subtree(child, seen)
    return count


TERMINAL_FAILURE_STATUSES = frozenset({"failed"})
TERMINAL_CANCEL_STATUSES = frozenset({"cancelled"})
SETTLED_SUCCESS_STATUSES = frozenset({"completed", "merged", "retired"})


def _derive_run_state(tasks: list[Mapping[str, Any]], *, retired: bool) -> str:
    """Truthful run_state from persisted lifecycle; never invents a label.

    active   -> any task is authoritative-active (13H: active status, not uncertain)
    failed   -> no active work and at least one failed task
    cancelled-> no active/failed, at least one cancelled task
    completed-> every task in a success terminal state (and there is at least one)
    mixed    -> terminal work present but no clean single outcome and not active
    empty    -> no tasks
    """
    if not tasks:
        return "retired" if retired else "empty"
    statuses = [str(t.get("status")) for t in tasks]
    if any(is_task_active(t) for t in tasks):
        return "active"
    if any(s == "failed" for s in statuses):
        return "failed"
    if all(s in SETTLED_SUCCESS_STATUSES for s in statuses):
        return "completed"
    if any(s == "cancelled" for s in statuses):
        return "cancelled"
    return "mixed"


def build_run_summary(
    recovery_store: Any,
    provenance_store: Any,
    organization_id: str,
    *,
    requesting_project_id: str,
) -> dict[str, Any]:
    """PS1-13K compact, truthful, bounded run summary.

    Derives counts from EXISTING 13H/13I persistence with a fixed number of
    bounded org-wide reads (no per-task N+1). Represents the FULL authorized
    run even when the corresponding tree is truncated. Security parity with
    13J: foreign and nonexistent organizations raise the same
    ``UnknownRunOrganization``; no payloads/secrets exposed.
    """
    requesting_project_id = str(requesting_project_id)
    organization = recovery_store.get_organization(str(organization_id))
    if organization is None or str(organization.get("project_id")) != requesting_project_id:
        raise UnknownRunOrganization("unknown organization")

    tasks = [
        t for t in recovery_store.list_tasks_by_organization(str(organization_id))
        if str(t.get("project_id")) == requesting_project_id
    ]
    model_rows = provenance_store.list_model_bindings_for_organization(
        str(organization_id), requesting_project_id=requesting_project_id
    )
    tool_rows = provenance_store.list_tool_bindings_for_organization(
        str(organization_id), requesting_project_id=requesting_project_id
    )

    agent_count = len({str(t.get("agent_id")) for t in tasks if t.get("agent_id")})
    active_agent_count = sum(1 for t in tasks if is_task_active(t))
    # Distinct ACTUAL executed models (provider+model), not configured candidates.
    model_count = len(
        {
            (str(m.get("provider_id")), str(m.get("model_id")))
            for m in model_rows
            if m.get("model_id")
        }
    )
    # Distinct ACTUAL governed tools executed (outcome-bearing attempts).
    tool_count = len({str(t.get("tool_id")) for t in tool_rows if t.get("tool_id")})
    artifact_count = len(
        {str(t.get("result_ref")) for t in tool_rows if t.get("result_ref")}
    )
    failed_task_count = sum(1 for t in tasks if str(t.get("status")) == "failed")

    created = [t.get("created_at") for t in tasks if t.get("created_at")]
    completed = [t.get("completed_at") for t in tasks if t.get("completed_at")]
    started_at = min(created) if created else None
    # Completed only when no active work remains and everything is terminal.
    run_state = _derive_run_state(tasks, retired=bool(organization.get("retired")))
    completed_at = max(completed) if completed and run_state in {
        "completed", "failed", "cancelled", "mixed"
    } else None
    timing = build_timing(started_at, started_at, completed_at)

    return {
        "schema": RUN_SUMMARY_SCHEMA,
        "organization_id": str(organization["organization_id"]),
        "project_id": str(organization["project_id"]),
        "run_state": run_state,
        "agent_count": agent_count,
        "active_agent_count": active_agent_count,
        "model_count": model_count,
        "tool_count": tool_count,
        "artifact_count": artifact_count,
        "task_count": len(tasks),
        "failed_task_count": failed_task_count,
        "lifecycle_counts": lifecycle_counts(tasks),
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_seconds": timing.get("elapsed_seconds"),
        "timing": timing,
        "counts_cover_full_run": True,
        "uses_existing_persistence": True,
    }


__all__ = [
    "MAX_DEPTH",
    "MAX_NODES_PER_RESPONSE",
    "RUN_TREE_SCHEMA",
    "RUN_SUMMARY_SCHEMA",
    "UnknownRunOrganization",
    "build_run_summary",
    "build_run_tree",
]
