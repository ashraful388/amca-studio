"""Safe Call Batching Planner and Compatibility Engine for AMCA (AG-A3.2A).

Canonical Principle:
    Batching is permitted only when combining transport/execution does not alter
    the semantic result, authorization, budget, verification isolation,
    provenance, or recovery truth of any individual call.
    Cost savings never override correctness.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence
from uuid import uuid4


class BatchOutcomeStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    REJECTED = "rejected"
    UNKNOWN_EXTERNAL_OUTCOME = "unknown_external_outcome"


class BatchRejectionReason(str, Enum):
    DIFFERENT_PROVIDER = "different_provider"
    DIFFERENT_MODEL = "different_model"
    DIFFERENT_MODEL_PARAMETERS = "different_model_parameters"
    DEPENDENCY_BETWEEN_TASKS = "dependency_between_tasks"
    UNRESOLVED_DEPENDENCIES = "unresolved_dependencies"
    JOINT_COLLABORATION_EXCLUDED = "joint_collaboration_excluded"
    ADAPTIVE_HYBRID_EXCLUDED = "adaptive_hybrid_excluded"
    VERIFICATION_CELL_EXCLUDED = "verification_cell_excluded"
    SIDE_EFFECTING_TOOLS_EXCLUDED = "side_effecting_tools_excluded"
    MANAGER_PROPOSAL_EXCLUDED = "manager_proposal_excluded"
    ADAPTER_LACKS_BATCH_CAPABILITY = "adapter_lacks_batch_capability"
    AUTHORIZATION_ENVELOPE_MISMATCH = "authorization_envelope_mismatch"
    BUDGET_INCOMPATIBLE = "budget_incompatible"
    EXECUTOR_KIND_INCOMPATIBLE = "executor_kind_incompatible"
    OUTPUT_SCHEMA_MISMATCH = "output_schema_mismatch"


def _strip_credentials(data: Any) -> Any:
    """Recursively strip any credential, token, key, or password values."""
    if isinstance(data, dict):
        cleaned = {}
        for k, v in sorted(data.items(), key=lambda item: str(item[0])):
            k_lower = str(k).lower()
            if any(s in k_lower for s in ("secret", "token", "key", "password", "auth", "credential", "bearer")):
                continue
            cleaned[k] = _strip_credentials(v)
        return cleaned
    if isinstance(data, (list, tuple)):
        return [_strip_credentials(x) for x in data]
    return data


@dataclass(slots=True)
class BatchItemCandidate:
    """A single logical task or inference request considered for call batching."""

    task_id: str
    agent_id: str
    prompt: str
    model_id: str
    provider: str
    executor_kind: str = "nano_local_model"
    model_parameters: dict[str, Any] = field(default_factory=dict)
    tool_config: dict[str, Any] | list[str] | None = None
    tool_side_effects: bool = False
    output_schema: dict[str, Any] | str | None = None
    collaboration_mode: str | None = None
    depends_on: tuple[str, ...] = ()
    envelope_id: str | None = None
    max_cost_usd: float | None = None
    conservative_cost_usd: float = 0.0
    call_key: str | None = None
    reservation_id: str | None = None

    def __post_init__(self) -> None:
        self.depends_on = tuple(self.depends_on)
        if not self.call_key:
            self.call_key = f"call-{self.task_id}-{uuid4().hex[:12]}"


def compute_batch_compatibility_key(candidate: BatchItemCandidate) -> str:
    """Compute deterministic compatibility digest for batch grouping.

    Two candidates have the exact same compatibility key if and only if they can
    be transported in the same homogeneous batch without altering semantics.
    Credentials and volatile execution metadata are strictly excluded.
    """
    clean_params = _strip_credentials(candidate.model_parameters)
    clean_tool_cfg = _strip_credentials(candidate.tool_config)
    clean_schema = _strip_credentials(candidate.output_schema)

    spec = {
        "version": "v1",
        "provider": candidate.provider.strip().lower(),
        "model_id": candidate.model_id.strip().lower(),
        "model_parameters": clean_params,
        "executor_kind": candidate.executor_kind.strip().lower(),
        "tool_config": clean_tool_cfg,
        "output_schema": clean_schema,
        "envelope_id": candidate.envelope_id,
    }

    payload = json.dumps(
        spec,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"batch_key:{hashlib.sha256(payload).hexdigest()}"


@dataclass(slots=True)
class BatchItemResult:
    """Independently attributable result for a single batched item."""

    task_id: str
    call_key: str
    status: BatchOutcomeStatus
    output_text: str | None = None
    settled_cost_usd: float = 0.0
    error: str | None = None
    reservation_id: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "call_key": self.call_key,
            "status": self.status.value,
            "output_text": self.output_text,
            "settled_cost_usd": self.settled_cost_usd,
            "error": self.error,
            "reservation_id": self.reservation_id,
            "provenance": self.provenance,
        }


@dataclass(slots=True)
class BatchPlan:
    """A scheduled homogeneous batch combining transport for multiple independent items."""

    batch_id: str
    compatibility_key: str
    provider: str
    model_id: str
    adapter_id: str
    items: list[BatchItemCandidate]
    total_reserved_usd: float = 0.0

    @property
    def logical_task_count(self) -> int:
        return len(self.items)

    @property
    def model_inference_count(self) -> int:
        return len(self.items)

    @property
    def transport_request_count(self) -> int:
        return 1 if self.items else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "compatibility_key": self.compatibility_key,
            "provider": self.provider,
            "model_id": self.model_id,
            "adapter_id": self.adapter_id,
            "logical_task_count": self.logical_task_count,
            "model_inference_count": self.model_inference_count,
            "transport_request_count": self.transport_request_count,
            "total_reserved_usd": self.total_reserved_usd,
            "items": [
                {
                    "task_id": item.task_id,
                    "call_key": item.call_key,
                    "prompt": item.prompt,
                    "conservative_cost_usd": item.conservative_cost_usd,
                    "reservation_id": item.reservation_id,
                }
                for item in self.items
            ],
        }


@dataclass(slots=True)
class BatchExecutionReport:
    """Durable telemetry and execution outcome across a dispatched batch."""

    batch_id: str
    logical_task_count: int
    model_inference_count: int
    transport_request_count: int
    total_settled_cost_usd: float
    results: dict[str, BatchItemResult] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "logical_task_count": self.logical_task_count,
            "model_inference_count": self.model_inference_count,
            "transport_request_count": self.transport_request_count,
            "total_settled_cost_usd": self.total_settled_cost_usd,
            "results": {k: v.to_dict() for k, v in self.results.items()},
        }


class SafeBatchingPlanner:
    """Eligibility engine and planner for safe call batching (AG-A3.2A)."""

    @staticmethod
    def evaluate_candidate_eligibility(
        candidate: BatchItemCandidate,
        adapter: Any,
    ) -> tuple[bool, str | None]:
        """Check if an individual candidate satisfies all baseline batching constraints."""
        # 1. Adapter must explicitly declare native batch capability
        has_batch_support = bool(
            getattr(adapter, "supports_batch_generation", False)
            or getattr(adapter, "supports_batch", False)
        )
        if not has_batch_support:
            return False, BatchRejectionReason.ADAPTER_LACKS_BATCH_CAPABILITY.value

        # 2. Verification Cell calls are strictly excluded in v1 (sealed independence)
        if candidate.executor_kind.lower() in {"verification_cell", "verificationcell"}:
            return False, BatchRejectionReason.VERIFICATION_CELL_EXCLUDED.value

        # 3. Manager proposal calls are excluded
        if candidate.executor_kind.lower() in {"manager"}:
            return False, BatchRejectionReason.MANAGER_PROPOSAL_EXCLUDED.value

        # 4. Joint collaboration turns are excluded
        if candidate.collaboration_mode and candidate.collaboration_mode.lower() in {"joint", "independent_discuss"}:
            return False, BatchRejectionReason.JOINT_COLLABORATION_EXCLUDED.value

        # 5. Adaptive hybrid dependent phases are excluded
        if candidate.collaboration_mode and candidate.collaboration_mode.lower() in {"adaptive_hybrid"}:
            return False, BatchRejectionReason.ADAPTIVE_HYBRID_EXCLUDED.value

        # 6. Side-effecting tools are strictly excluded
        if candidate.tool_side_effects:
            return False, BatchRejectionReason.SIDE_EFFECTING_TOOLS_EXCLUDED.value

        return True, None

    @classmethod
    def can_batch_pair(
        cls,
        candidate_a: BatchItemCandidate,
        candidate_b: BatchItemCandidate,
        adapter: Any,
    ) -> tuple[bool, str | None]:
        """Check if two candidates can be combined into the same batch."""
        # Check individual eligibility
        ok_a, reason_a = cls.evaluate_candidate_eligibility(candidate_a, adapter)
        if not ok_a:
            return False, reason_a
        ok_b, reason_b = cls.evaluate_candidate_eligibility(candidate_b, adapter)
        if not ok_b:
            return False, reason_b

        # Check topological dependency independence
        if candidate_a.task_id in candidate_b.depends_on or candidate_b.task_id in candidate_a.depends_on:
            return False, BatchRejectionReason.DEPENDENCY_BETWEEN_TASKS.value

        # Check provider match
        if candidate_a.provider.strip().lower() != candidate_b.provider.strip().lower():
            return False, BatchRejectionReason.DIFFERENT_PROVIDER.value

        # Check model match
        if candidate_a.model_id.strip().lower() != candidate_b.model_id.strip().lower():
            return False, BatchRejectionReason.DIFFERENT_MODEL.value

        # Check model parameters
        if _strip_credentials(candidate_a.model_parameters) != _strip_credentials(candidate_b.model_parameters):
            return False, BatchRejectionReason.DIFFERENT_MODEL_PARAMETERS.value

        # Check tool configuration
        if _strip_credentials(candidate_a.tool_config) != _strip_credentials(candidate_b.tool_config):
            return False, BatchRejectionReason.EXECUTOR_KIND_INCOMPATIBLE.value

        # Check output schema
        if _strip_credentials(candidate_a.output_schema) != _strip_credentials(candidate_b.output_schema):
            return False, BatchRejectionReason.OUTPUT_SCHEMA_MISMATCH.value

        # Check authorization envelope match
        if candidate_a.envelope_id != candidate_b.envelope_id:
            return False, BatchRejectionReason.AUTHORIZATION_ENVELOPE_MISMATCH.value

        return True, None

    @classmethod
    def plan_batches(
        cls,
        candidates: Sequence[BatchItemCandidate],
        adapters_by_id: Mapping[str, Any],
        available_budget_usd: float = float("inf"),
        max_batch_size: int = 16,
    ) -> tuple[list[BatchPlan], list[tuple[BatchItemCandidate, str]]]:
        """Group compatible candidates into batch plans while enforcing budget & dependencies."""
        rejected_candidates: list[tuple[BatchItemCandidate, str]] = []
        eligible_candidates: list[BatchItemCandidate] = []

        # 1. Dependency cycle & internal dependency check across candidates
        candidate_ids = {c.task_id for c in candidates}
        for c in candidates:
            # If candidate depends on another candidate in the same candidate set -> dependency between tasks
            internal_deps = [d for d in c.depends_on if d in candidate_ids]
            if internal_deps:
                rejected_candidates.append((c, BatchRejectionReason.DEPENDENCY_BETWEEN_TASKS.value))
                continue

            adapter = adapters_by_id.get(c.agent_id)
            if adapter is None:
                rejected_candidates.append((c, BatchRejectionReason.ADAPTER_LACKS_BATCH_CAPABILITY.value))
                continue

            ok, reason = cls.evaluate_candidate_eligibility(c, adapter)
            if not ok:
                rejected_candidates.append((c, reason or "ineligible"))
                continue

            eligible_candidates.append(c)

        # 2. Group by BatchCompatibilityKey
        groups: dict[str, list[BatchItemCandidate]] = {}
        for c in eligible_candidates:
            key = compute_batch_compatibility_key(c)
            groups.setdefault(key, []).append(c)

        # 3. Form BatchPlans respecting max_batch_size and budget
        plans: list[BatchPlan] = []
        remaining_budget = available_budget_usd

        for key, group in groups.items():
            for i in range(0, len(group), max_batch_size):
                chunk = group[i : i + max_batch_size]
                chunk_cost = sum(item.conservative_cost_usd for item in chunk)
                if chunk_cost > remaining_budget + 1e-9:
                    # Exceeds budget, reject chunk
                    for item in chunk:
                        rejected_candidates.append((item, BatchRejectionReason.BUDGET_INCOMPATIBLE.value))
                    continue

                first = chunk[0]
                plan = BatchPlan(
                    batch_id=f"batch-{uuid4().hex[:12]}",
                    compatibility_key=key,
                    provider=first.provider,
                    model_id=first.model_id,
                    adapter_id=first.agent_id,
                    items=list(chunk),
                    total_reserved_usd=chunk_cost,
                )
                plans.append(plan)
                remaining_budget -= chunk_cost

        return plans, rejected_candidates

    @staticmethod
    def process_batch_response(
        batch: BatchPlan,
        per_item_outputs: Mapping[str, Mapping[str, Any]],
    ) -> BatchExecutionReport:
        """Process provider batch response preserving per-item attribution and partial outcomes."""
        results: dict[str, BatchItemResult] = {}
        total_settled_cost = 0.0

        for item in batch.items:
            # Provider output keyed by task_id or call_key
            item_data = per_item_outputs.get(item.task_id) or per_item_outputs.get(item.call_key or "")
            if item_data is None:
                # Missing from provider response -> UNKNOWN_EXTERNAL_OUTCOME
                res = BatchItemResult(
                    task_id=item.task_id,
                    call_key=item.call_key or item.task_id,
                    status=BatchOutcomeStatus.UNKNOWN_EXTERNAL_OUTCOME,
                    error="item omitted from provider batch response",
                    reservation_id=item.reservation_id,
                )
            else:
                raw_status = str(item_data.get("status", "success")).lower()
                cost = float(item_data.get("cost_usd", item.conservative_cost_usd) or 0.0)
                total_settled_cost += cost

                if raw_status in {"success", "completed", "ok"}:
                    status = BatchOutcomeStatus.SUCCESS
                    error_msg = None
                elif raw_status in {"rejected", "denied"}:
                    status = BatchOutcomeStatus.REJECTED
                    error_msg = str(item_data.get("error", "item rejected by provider"))
                else:
                    status = BatchOutcomeStatus.FAILURE
                    error_msg = str(item_data.get("error", "item execution failed"))

                res = BatchItemResult(
                    task_id=item.task_id,
                    call_key=item.call_key or item.task_id,
                    status=status,
                    output_text=item_data.get("text"),
                    settled_cost_usd=cost,
                    error=error_msg,
                    reservation_id=item.reservation_id,
                    provenance={
                        "batch_id": batch.batch_id,
                        "provider": batch.provider,
                        "model_id": batch.model_id,
                        "provider_item_id": item_data.get("provider_item_id"),
                    },
                )
            results[item.task_id] = res

        return BatchExecutionReport(
            batch_id=batch.batch_id,
            logical_task_count=batch.logical_task_count,
            model_inference_count=batch.model_inference_count,
            transport_request_count=batch.transport_request_count,
            total_settled_cost_usd=total_settled_cost,
            results=results,
        )

    @staticmethod
    def handle_batch_crash_or_timeout(
        batch: BatchPlan,
        error_message: str,
    ) -> BatchExecutionReport:
        """Handle unauthoritative transport failure or system crash across a batch."""
        results: dict[str, BatchItemResult] = {}
        for item in batch.items:
            res = BatchItemResult(
                task_id=item.task_id,
                call_key=item.call_key or item.task_id,
                status=BatchOutcomeStatus.UNKNOWN_EXTERNAL_OUTCOME,
                error=f"batch crash/timeout: {error_message}",
                reservation_id=item.reservation_id,
                provenance={"batch_id": batch.batch_id, "crash_error": error_message},
            )
            results[item.task_id] = res

        return BatchExecutionReport(
            batch_id=batch.batch_id,
            logical_task_count=batch.logical_task_count,
            model_inference_count=batch.model_inference_count,
            transport_request_count=batch.transport_request_count,
            total_settled_cost_usd=0.0,
            results=results,
        )
