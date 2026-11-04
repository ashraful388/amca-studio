"""A2.1D composite agent recovery: safe continuation after restart.

Turns the durable truth established by A2.1A-C into explicit continuation
actions per required external call:

    RESPONSE_COMMITTED / SETTLED -> REUSE the persisted result (0 provider calls)
    PLANNED / RESERVED (no intent) -> RESUME the same logical attempt
    dispatched-without-commit      -> BLOCK (UNKNOWN_EXTERNAL_OUTCOME)
    RECOVERY_INTEGRITY_ERROR       -> BLOCK

Continuation never duplicates logical work: reused tasks are completed from
their durable payloads inside the SAME organization/task/call identity, and
resumed attempts link back onto their existing ACTIVE reservation instead of
acquiring a second one. Uncertain required calls block semantic continuation
of everything that depends on them; they never block independent financial
settlement.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from amca.agent_recovery import AgentRecoveryStore
from amca.agent_runtime import (
    AgentTaskRecord,
    AgentTaskStatus,
    NanoScheduler,
)

ACTION_REUSE = "reuse"
ACTION_RESUME = "resume"
ACTION_BLOCK_UNCERTAIN = "block_uncertain"
ACTION_BLOCK_INTEGRITY = "block_integrity"

ORG_STATUS_RESUMED = "RESUMED"
ORG_STATUS_RECOVERED_COMPLETE = "RECOVERED_COMPLETE"
ORG_STATUS_BLOCKED_UNCERTAIN = "BLOCKED_UNCERTAIN"
ORG_STATUS_BLOCKED_INTEGRITY = "BLOCKED_INTEGRITY"

_UNKNOWN_STATES = {"DISPATCH_INTENT_DURABLE", "DISPATCHED", "RESPONSE_RECEIVED"}
_MANAGER_PROPOSAL_KEY = "manager:proposal"


def _latest_attempt_rows(rows: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    attempts = [int(row["task_attempt_number"]) for row in rows]
    latest = max(attempts)
    return latest, [r for r in rows if int(r["task_attempt_number"]) == latest]


def build_continuation_plan(
    store: AgentRecoveryStore, organization_id: str
) -> dict[str, Any]:
    """Classify every durable task of ONE organization into actions.

    Pure durable-state reading: zero provider calls.
    """

    organization = store.get_organization(organization_id)
    if organization is None:
        raise ValueError(f"unknown organization: {organization_id!r}")

    plan: dict[str, Any] = {
        "organization_id": organization_id,
        "status": None,
        "tasks": {},
        "reused_calls": [],
        "resumed_tasks": [],
        "blocked_tasks": [],
        "integrity_blocked": [],
    }
    for task_row in store.list_tasks_by_organization(organization_id):
        task_id = task_row["id"]
        rows = store.list_call_attempts(task_id)
        entry: dict[str, Any] = {
            "executor_kind": task_row["executor_kind"],
            "action": ACTION_RESUME,
            "calls": [],
            "uncertain_keys": [],
            "reused_payloads": {},
        }
        if rows:
            attempt, attempt_rows = _latest_attempt_rows(rows)
            entry["attempt"] = attempt
            for row in attempt_rows:
                state = row["state"]
                key = row["call_key"]
                if state in _UNKNOWN_STATES:
                    entry["calls"].append((key, ACTION_BLOCK_UNCERTAIN))
                    entry["uncertain_keys"].append(key)
                elif state in ("RESPONSE_COMMITTED", "SETTLED"):
                    action = ACTION_REUSE
                    entry["calls"].append((key, action))
                    entry["reused_payloads"][key] = {
                        "payload": row["response_payload"],
                        "actual_cost_usd": float(row["actual_cost_usd"] or 0.0),
                        "adapter_id": row["adapter_id"],
                    }
                else:
                    entry["calls"].append((key, ACTION_RESUME))
            if entry["uncertain_keys"]:
                entry["action"] = ACTION_BLOCK_UNCERTAIN
            elif entry["reused_payloads"] and not any(
                a == ACTION_RESUME for _, a in entry["calls"]
            ):
                entry["action"] = ACTION_REUSE
                if str(entry["executor_kind"]) == "collaboration":
                    # A collaboration whose last committed turns have NOT all
                    # resolved their focus is still architecturally open: the
                    # kernel must resume (reusing every committed turn from
                    # durable payloads) rather than be closed as finished.
                    conversation_open = False
                    for info in entry["reused_payloads"].values():
                        try:
                            doc = json.loads(str(info["payload"]))
                        except (TypeError, ValueError):
                            continue
                        if isinstance(doc, dict) and (
                            doc.get("focus_resolved") is False
                        ):
                            conversation_open = True
                    if conversation_open:
                        entry["action"] = ACTION_RESUME
        if task_row.get("recovery_disposition") == "RECOVERY_INTEGRITY_ERROR":
            entry["action"] = ACTION_BLOCK_INTEGRITY

        plan["tasks"][task_id] = entry
        if entry["action"] == ACTION_RESUME:
            plan["resumed_tasks"].append(task_id)
        elif entry["action"] == ACTION_REUSE:
            plan["reused_calls"].extend(sorted(entry["reused_payloads"]))
        elif entry["action"] == ACTION_BLOCK_INTEGRITY:
            plan["integrity_blocked"].append(task_id)
            plan["blocked_tasks"].append(task_id)
        else:
            plan["blocked_tasks"].append(task_id)

    if plan["integrity_blocked"]:
        plan["status"] = ORG_STATUS_BLOCKED_INTEGRITY
    elif any(
        plan["tasks"][t]["action"] == ACTION_BLOCK_UNCERTAIN
        for t in plan["blocked_tasks"]
    ):
        plan["status"] = ORG_STATUS_BLOCKED_UNCERTAIN
    elif not plan["resumed_tasks"] and plan["tasks"]:
        plan["status"] = ORG_STATUS_RECOVERED_COMPLETE
    else:
        plan["status"] = ORG_STATUS_RESUMED
    return plan


def _complete_from_payload(
    task: AgentTaskRecord, entry: dict[str, Any]
) -> None:
    """Finish a task locally from its durable committed results."""

    total_cost = sum(
        float(info["actual_cost_usd"]) for info in entry["reused_payloads"].values()
    )
    payloads = [
        str(info["payload"])
        for _, info in sorted(entry["reused_payloads"].items())
    ]
    task.cost_usd += total_cost
    task.result_summary = "\n".join(payloads)[:500]
    task.error = None
    if task.status == AgentTaskStatus.PROPOSED:
        task.transition(AgentTaskStatus.QUEUED)
    task.transition(AgentTaskStatus.STARTING)
    task.transition(AgentTaskStatus.RUNNING)
    task.transition(AgentTaskStatus.COMPLETED)
    task.completed_at = datetime.now(UTC).isoformat()


def continue_organization(
    store: AgentRecoveryStore,
    scheduler: NanoScheduler,
    organization_id: str,
    *,
    run: bool = True,
) -> dict[str, Any]:
    """Continue ONE recovered organization through the real runtime.

    Reuses every durably committed result without touching adapters,
    resumes only provably never-dispatched attempts, and blocks anything
    with an uncertain external outcome or integrity error.
    """

    import asyncio

    plan = build_continuation_plan(store, organization_id)
    task_rows = store.list_tasks_by_organization(organization_id)
    graph: list[dict[str, Any]] = []
    for row in task_rows:
        depends_raw = row.get("depends_on_json")
        depends = depends_raw if isinstance(depends_raw, list) else []
        graph.append({
            "id": row["id"],
            "agent_id": row["agent_id"],
            "executor_kind": row["executor_kind"],
            "focus": row["focus"],
            "verifier_ids": tuple(row.get("verifier_ids_json") or ()),
            "participant_ids": tuple(row.get("participant_ids_json") or ()),
            "collaboration_architecture": row.get("collaboration_architecture"),
            "sync_mode": row.get("sync_mode"),
            "max_rounds": max(1, int(row.get("max_rounds") or 1)),
            "max_adaptive_phases": max(1, int(row.get("max_adaptive_phases") or 4)),
            "depends_on": tuple(depends),
        })

    org = scheduler.schedule(graph, organization_id=organization_id,
                             project_id=str(organization_id),
                             goal="continued after restart")

    reused: list[str] = []
    blocked: list[str] = []
    for task_id, entry in plan["tasks"].items():
        task = org.tasks.get(task_id)
        if task is None:
            continue
        if entry["action"] == ACTION_REUSE:
            _complete_from_payload(task, entry)
            reused.append(task_id)
        elif entry["action"] in (ACTION_BLOCK_UNCERTAIN, ACTION_BLOCK_INTEGRITY):
            reason = (
                "blocked: uncertain external outcome "
                f"({', '.join(entry['uncertain_keys'])})"
                if entry["action"] == ACTION_BLOCK_UNCERTAIN
                else "blocked: recovery integrity error"
            )
            task.error = reason[:400]
            if task.status == AgentTaskStatus.PROPOSED:
                task.transition(AgentTaskStatus.QUEUED)
            if task.status != AgentTaskStatus.CANCELLED:
                task.transition(AgentTaskStatus.CANCELLED)
            blocked.append(task_id)

    if run:
        # Resumed tasks execute through the normal runtime; reused tasks are
        # already COMPLETED and blocked tasks CANCELLED, so the admission
        # loop never touches an adapter for them.
        org = asyncio.run(scheduler.run(org))

    return {
        "plan": plan,
        "organization": org,
        "reused": reused,
        "blocked": blocked,
        "resumed": [t for t in plan["resumed_tasks"] if t in org.tasks],
    }
