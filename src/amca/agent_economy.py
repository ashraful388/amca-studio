"""A3.3 Agent Economy telemetry: canonical economy/efficiency projections.

Derives EconomyRecords from the AUTHORITATIVE runtime tables in amca.db -
no separate metrics database, no mutable duplicated truth, so restart and
reconciliation are idempotent by construction (recompute yields identical
numbers).

Truth rules enforced here:
- logical tasks / model inferences / transport requests are DISTINCT counts
  (4 batched calls = 4 inferences = 1 transport);
- a reuse hit adds zero inference and zero transport but keeps its logical
  task;
- reserved cost is never copied into actual cost; unknown stays null;
- ZERO_COST is authoritative zero only for executions classified as such;
- tokens are reported with their provenance (provider_reported /
  locally_counted / estimated) or left unknown;
- uncertain exposure is a first-class number, never folded into settled
  spend;
- ROI / quality-per-dollar is never produced here (V1 rule).
"""

from __future__ import annotations

from typing import Any

from amca.agent_recovery import AgentRecoveryStore

_VERIFICATION_KIND = "verification_cell"
_BATCH_KINDS = {"nano_local_model"}


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def build_organization_economy(
    store: AgentRecoveryStore,
    organization_id: str,
    *,
    optimization_reuse_hits: int | None = None,
    recovery_reuse_count: int | None = None,
) -> dict[str, Any]:
    """*reuse counters are caller-supplied live telemetry (scheduler /
    continuation), kept OUT of the durable projection so restarts cannot
    double-count them."""
    """Aggregate one organization's economy from authoritative tables."""

    organization = store.get_organization(organization_id)
    if organization is None:
        raise ValueError(f"unknown organization: {organization_id!r}")

    task_rows = store.list_tasks_by_organization(organization_id)
    architecture_counts: dict[str, int] = {}
    verification = {
        "logical_count": 0,
        "inference_count": 0,
        "actual_cost_usd": 0.0,
        "outcomes": {},
    }
    inference_items = 0
    transport_requests = 0
    batch_transports: set[str] = set()
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    token_sources: set[str] = set()
    actual_cost_known = False
    actual_cost_usd = 0.0
    provider_latency_ms: float | None = None

    for task in task_rows:
        executor_kind = str(task.get("executor_kind") or "")
        architecture_counts[executor_kind] = (
            architecture_counts.get(executor_kind, 0) + 1
        )
        rows = store.list_call_attempts(task["id"])
        for row in rows:
            if row["state"] == "PLANNED":
                continue
            inference_items += 1
            if row["dispatch_intent_at"] is not None:
                batch_id = row["batch_transport_id"]
                if batch_id:
                    batch_transports.add(batch_id)
                else:
                    transport_requests += 1
            if row["state"] in ("RESPONSE_COMMITTED", "SETTLED"):
                actual_cost_known = True
                actual_cost_usd += float(row["actual_cost_usd"] or 0.0)
                latency = row["provider_latency_ms"]
                if latency is not None:
                    provider_latency_ms = (
                        latency if provider_latency_ms is None
                        else max(provider_latency_ms, latency)
                    )
                in_tok, out_tok = row["input_tokens"], row["output_tokens"]
                source = row["token_source"]
                if source:
                    token_sources.add(source)
                if in_tok is None or out_tok is None:
                    input_tokens = output_tokens = None
                elif input_tokens is not None and output_tokens is not None:
                    input_tokens += int(in_tok)
                    output_tokens += int(out_tok)

        if executor_kind == _VERIFICATION_KIND:
            verification["logical_count"] += 1
            vrows = store.list_call_attempts(task["id"])
            verification["inference_count"] += len(vrows)
            for row in vrows:
                if row["state"] in ("RESPONSE_COMMITTED", "SETTLED"):
                    verification["actual_cost_usd"] += float(
                        row["actual_cost_usd"] or 0.0
                    )

    reservations = store.list_reservations(organization_id)
    reserved_cost = sum(float(r["amount_usd"]) for r in reservations)
    settled_cost = sum(
        float(r["settled_actual_usd"] or 0.0)
        for r in reservations
        if r["status"] == "settled"
    )
    position = store.organization_budget_position(organization_id)

    transport_requests += len(batch_transports)

    # Named architecture aggregation: semantic identity from durable task
    # metadata (collaboration_architecture / manager provenance), never from
    # executor_kind alone. Plain nano/model work reports as SOLO-style local
    # inference only when no architecture applies.
    architectures: dict[str, dict[str, Any]] = {}
    for task in task_rows:
        executor_kind = str(task.get("executor_kind") or "")
        arch_value = task.get("collaboration_architecture")
        if task.get("proposed_by_manager_id"):
            bucket = "gate_c_child"
        elif executor_kind == "manager":
            bucket = "gate_c"
        elif executor_kind == "verification_cell":
            bucket = "verification_cell"
        elif arch_value:
            bucket = str(arch_value).lower()
        else:
            bucket = "nano_local"
        entry = architectures.setdefault(bucket, {
            "logical_tasks": 0,
            "model_inference_count": 0,
            "transport_request_count": 0,
            "actual_cost_usd": 0.0,
        })
        entry["logical_tasks"] += 1
        t_rows = store.list_call_attempts(task["id"])
        entry["model_inference_count"] += len([
            r for r in t_rows if r["state"] != "PLANNED"
        ])
        for row in t_rows:
            if row["dispatch_intent_at"] is not None:
                if not row["batch_transport_id"]:
                    entry["transport_request_count"] += 1
            if row["state"] in ("RESPONSE_COMMITTED", "SETTLED"):
                entry["actual_cost_usd"] += float(row["actual_cost_usd"] or 0.0)
    for entry in architectures.values():
        entry["actual_cost_usd"] = round(entry["actual_cost_usd"], 6)

    return {
        "optimization_reuse_hit_count": optimization_reuse_hits,
        "recovery_reuse_count": recovery_reuse_count,
        "architectures": architectures,
        "organization_id": organization_id,
        "budget_usd": float(organization["budget_usd"]),
        "logical_task_count": len(task_rows),
        "architecture_counts": architecture_counts,
        "model_inference_count": inference_items,
        "transport_request_count": transport_requests,
        "batch": {
            "batched_item_count": 0,
            "batch_transport_count": len(batch_transports),
        },
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "total": (
                None if input_tokens is None or output_tokens is None
                else input_tokens + output_tokens
            ),
            "sources": sorted(token_sources),
        },
        "cost": {
            # Reserved exposure is NOT actual spend.
            "reserved_cost_usd": round(reserved_cost, 6),
            "actual_cost_usd": (
                round(actual_cost_usd, 6) if actual_cost_known else None
            ),
            "settled_cost_usd": round(settled_cost, 6),
            "uncertain_exposure_usd": round(position["uncertain"], 6),
            "active_reservation_usd": round(position["active"], 6),
        },
        "latency_ms": {
            "provider": provider_latency_ms,
            "wall_clock": None,
        },
        "verification": {
            **verification,
            "actual_cost_usd": round(verification["actual_cost_usd"], 6),
        },
        "ratios": {
            # Safe descriptive ratios; divide-by-zero yields None, and ROI is
            # deliberately absent (quality is not authoritative in A3.3).
            "transport_reduction_ratio": (
                None if inference_items <= 0
                else round(1 - transport_requests / inference_items, 6)
            ),
            "cost_per_completed_logical_task": None,
        },
        "roi": None,
    }


def list_organization_economies(store: AgentRecoveryStore) -> list[dict[str, Any]]:
    return [
        build_organization_economy(store, organization_id)
        for organization_id in store.list_organization_ids()
    ]
