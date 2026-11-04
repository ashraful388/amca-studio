"""Deterministic Phase 1 routers and budget/privacy admission control."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Any, Iterable

from amca.domain import (
    CollaborationMode,
    ComplexityLevel,
    ComputePolicy,
    PrivacyMode,
    ProjectState,
    Severity,
    SyncMode,
)
from amca.models.base import ModelAdapter
from amca.modes import AdaptiveFeatures, AdaptiveTransitionRecord, canonical_digest
from amca.privacy import PRIVATE_HYBRID_TEMPLATE_VERSION

if TYPE_CHECKING:
    from amca.local_runtime import LocalRuntimeSnapshot


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    complexity: ComplexityLevel
    collaboration_mode: CollaborationMode
    sync_mode: SyncMode
    selected_adapter_ids: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CallAdmission:
    allowed: bool
    reason: str
    estimated_cost_usd: float


class ComplexityRouter:
    """Transparent lexical baseline, intended to be calibrated by benchmarks."""

    _critical_terms = frozenset(
        {"theorem", "proof", "prove", "irreversible", "safety-critical", "novel scientific"}
    )
    _high_terms = frozenset(
        {
            "derive",
            "prove",
            "scientific",
            "architecture",
            "optimize",
            "hypothesis",
            "contradiction",
            "complex",
            "research",
        }
    )

    def assess(self, goal: str, *, novelty_required: bool = False) -> ComplexityLevel:
        text = goal.casefold()
        if any(term in text for term in self._critical_terms):
            return ComplexityLevel.CRITICAL
        score = sum(term in text for term in self._high_terms)
        if novelty_required or score >= 2 or len(goal.split()) >= 80:
            return ComplexityLevel.HIGH
        if score == 1 or len(goal.split()) >= 25:
            return ComplexityLevel.MEDIUM
        return ComplexityLevel.LOW


class CollaborationRouter:
    def select(
        self,
        state: ProjectState,
        *,
        novelty_required: bool = False,
        weakly_coupled: bool = False,
        stagnating: bool = False,
    ) -> tuple[CollaborationMode, str]:
        if state.has_critical_objection:
            return CollaborationMode.JOINT, "A critical objection requires same-focus review."
        if stagnating or novelty_required:
            return (
                CollaborationMode.INDEPENDENT_DISCUSS,
                "Independent exploration reduces anchoring before discussion.",
            )
        if state.complexity in {ComplexityLevel.HIGH, ComplexityLevel.CRITICAL}:
            return CollaborationMode.JOINT, "A strongly coupled high-complexity focus needs joint deliberation."
        if weakly_coupled and state.complexity is ComplexityLevel.MEDIUM:
            return CollaborationMode.PARALLEL, "The medium-complexity work is weakly coupled."
        return CollaborationMode.SOLO, "A single model is sufficient for this focus."


class AdaptiveRouter:
    """Deterministic, auditable supervisor for the four executable base modes.

    The router is deliberately static.  It does not learn from outputs and its
    complete decision input is represented by ``feature_digest`` on the returned
    transition record.  A user lock wins unless a critical objection or a
    conflicting parallel result requires Joint review.
    """

    _base_modes = (
        CollaborationMode.SOLO,
        CollaborationMode.JOINT,
        CollaborationMode.PARALLEL,
        CollaborationMode.INDEPENDENT_DISCUSS,
    )

    def decide(
        self,
        state: ProjectState,
        features: AdaptiveFeatures,
        *,
        plan_available: bool = False,
        requested_sync_mode: SyncMode = SyncMode.BOUNDED_STALENESS,
        user_lock: CollaborationMode | None = None,
        manual_instruction: CollaborationMode | None = None,
        phase_index: int = 0,
        timestamp: str = "",
    ) -> AdaptiveTransitionRecord:
        user_lock = self._base_mode(user_lock, "user_lock")
        manual_instruction = self._base_mode(
            manual_instruction, "manual_instruction"
        )
        triggers: list[str] = []
        if state.has_critical_objection:
            triggers.append("critical_objection")
        if features.parallel_conflict:
            triggers.append("parallel_conflict")
        if features.high_disagreement or any(
            disagreement.severity in {Severity.HIGH, Severity.CRITICAL}
            for disagreement in state.unresolved_disagreements
        ):
            triggers.append("high_disagreement")
        if features.ambiguous_mixed_signals:
            triggers.append("ambiguous_mixed_signals")
        if features.routine_residual:
            triggers.append("routine_residual")
        if features.novelty_required:
            triggers.append("high_novelty")
        if features.stagnating:
            triggers.append("stagnation")
        if features.weakly_coupled:
            triggers.append("weak_coupling")
        if features.agreement_established:
            triggers.append("agreement_established")
        if features.no_active_disagreement:
            triggers.append("no_active_disagreement")
        if features.cost_pressure:
            triggers.append("cost_pressure")
        if features.time_pressure:
            triggers.append("time_pressure")
        triggers.append(f"complexity:{state.complexity.value}")

        safety_override = state.has_critical_objection or features.parallel_conflict
        if safety_override:
            selected = CollaborationMode.JOINT
            selected_sync = SyncMode.HARD
            rule = "safety.conflict_to_joint"
        elif user_lock is not None:
            selected = user_lock
            selected_sync = requested_sync_mode
            rule = "user.mode_lock"
        elif manual_instruction is not None:
            selected = manual_instruction
            selected_sync = requested_sync_mode
            rule = "user.manual_transition"
        elif "high_disagreement" in triggers:
            selected = CollaborationMode.JOINT
            selected_sync = SyncMode.QUORUM
            rule = "static.high_disagreement_to_joint"
        elif features.ambiguous_mixed_signals:
            selected = CollaborationMode.JOINT
            selected_sync = requested_sync_mode
            rule = "adaptive.ambiguous_alignment_to_joint"
        elif features.routine_residual:
            selected = CollaborationMode.SOLO
            selected_sync = SyncMode.SOFT
            rule = "static.routine_residual_to_solo"
        elif features.cost_pressure or features.time_pressure:
            selected = CollaborationMode.SOLO
            selected_sync = SyncMode.SOFT
            rule = "static.resource_pressure_to_solo"
        elif features.novelty_required or features.stagnating:
            selected = CollaborationMode.INDEPENDENT_DISCUSS
            selected_sync = requested_sync_mode
            rule = "static.novelty_or_stagnation_to_independent"
        elif features.weakly_coupled and plan_available:
            selected = CollaborationMode.PARALLEL
            selected_sync = requested_sync_mode
            rule = "static.weak_coupling_with_plan_to_parallel"
        elif state.complexity in {ComplexityLevel.HIGH, ComplexityLevel.CRITICAL}:
            selected = CollaborationMode.JOINT
            selected_sync = requested_sync_mode
            rule = "static.high_complexity_to_joint"
        elif features.weakly_coupled:
            selected = CollaborationMode.SOLO
            selected_sync = SyncMode.SOFT
            rule = "static.weak_coupling_without_plan_to_solo"
        else:
            selected = CollaborationMode.SOLO
            selected_sync = SyncMode.SOFT
            rule = "static.low_or_medium_routine_to_solo"

        decision_input = {
            "phase_index": phase_index,
            "state_version": state.version,
            "from_mode": state.current_collaboration_mode.value,
            "complexity": state.complexity.value,
            "has_critical_objection": state.has_critical_objection,
            "features": features.to_dict(),
            "plan_available": plan_available,
            "requested_sync_mode": requested_sync_mode.value,
            "user_lock": user_lock.value if user_lock else None,
            "manual_instruction": (
                manual_instruction.value if manual_instruction else None
            ),
        }
        return AdaptiveTransitionRecord(
            phase_index=phase_index,
            from_mode=state.current_collaboration_mode,
            selected_mode=selected,
            selected_sync_mode=selected_sync,
            observed_triggers=tuple(triggers),
            alternative_modes=tuple(
                mode for mode in self._base_modes if mode is not selected
            ),
            policy_rule=rule,
            state_version=state.version,
            state_version_before=state.version,
            feature_digest=canonical_digest(decision_input),
            estimated_cost_usd=features.estimated_cost_usd,
            estimated_latency_seconds=features.estimated_latency_seconds,
            user_lock=user_lock,
            manual_instruction=manual_instruction,
            safety_override=safety_override,
            timestamp=timestamp,
        )

    @classmethod
    def _base_mode(
        cls, mode: CollaborationMode | None, field_name: str
    ) -> CollaborationMode | None:
        if mode is None:
            return None
        mode = CollaborationMode(mode)
        if mode not in cls._base_modes:
            raise ValueError(f"{field_name} must identify one executable base mode")
        return mode


class SynchronizationRouter:
    def select(self, state: ProjectState, *, final_decision: bool = False) -> tuple[SyncMode, str]:
        if final_decision or state.complexity is ComplexityLevel.CRITICAL:
            return SyncMode.HARD, "Final or critical decisions require every required participant."
        if any(
            disagreement.severity in {Severity.HIGH, Severity.CRITICAL}
            for disagreement in state.unresolved_disagreements
        ):
            return SyncMode.QUORUM, "A significant unresolved objection requires quorum."
        if state.current_collaboration_mode is CollaborationMode.JOINT:
            return SyncMode.BOUNDED_STALENESS, "Joint mode limits speed-dominant canonical advancement."
        return SyncMode.SOFT, "Ordinary provisional work can proceed without a barrier."


class CostLatencyController:
    """Admit calls without silently crossing privacy or user-budget boundaries."""

    _frontier_limits = {
        ComputePolicy.ECONOMY: 1,
        ComputePolicy.BALANCED: 2,
        ComputePolicy.RESEARCH: 4,
        ComputePolicy.MAXIMUM_INTELLIGENCE: 8,
        ComputePolicy.CUSTOM: 8,
    }

    def __init__(self) -> None:
        self._run_api_call_baseline = 0

    def start_run(
        self, state: ProjectState, *, desktop_cloud_call_limit: int | None = None
    ) -> None:
        """Snapshot desktop telemetry once per run, never per round or phase."""
        telemetry = bool(getattr(state.goal_contract, "budget_is_telemetry", False))
        if desktop_cloud_call_limit is not None and (
            type(desktop_cloud_call_limit) is not int or desktop_cloud_call_limit < 0
        ):
            raise ValueError("desktop_cloud_call_limit must be a non-negative integer")
        self._run_api_call_baseline = state.cost_state.api_model_calls if telemetry else 0
        self._desktop_cloud_call_limit = desktop_cloud_call_limit if telemetry else None

    def frontier_call_limit(self, policy: ComputePolicy) -> int:
        return self._frontier_limits[policy]

    def remaining_frontier_calls(self, state: ProjectState) -> int:
        # Only desktop telemetry is run-scoped; the persisted ledger is untouched.
        baseline = (
            getattr(self, "_run_api_call_baseline", 0)
            if getattr(state.goal_contract, "budget_is_telemetry", False)
            else 0
        )
        used = max(0, state.cost_state.api_model_calls - baseline)
        planned_limit = (
            getattr(self, "_desktop_cloud_call_limit", None)
            if getattr(state.goal_contract, "budget_is_telemetry", False)
            else None
        )
        limit = (
            self.frontier_call_limit(state.compute_policy)
            if planned_limit is None
            else planned_limit
        )
        return max(0, limit - used)

    def admit(
        self,
        state: ProjectState,
        adapter: ModelAdapter,
        *,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
    ) -> CallAdmission:
        if state.privacy_mode is PrivacyMode.OFFLINE and not adapter.capabilities().is_local:
            return CallAdmission(False, "Offline privacy mode forbids cloud inference.", 0.0)
        if (
            state.privacy_mode is PrivacyMode.PRIVATE_HYBRID
            and not adapter.capabilities().is_local
            and adapter.provider_metadata().get("privacy_boundary")
            != PRIVATE_HYBRID_TEMPLATE_VERSION
        ):
            return CallAdmission(
                False,
                "Private Hybrid requires an exact-scope approved excerpt boundary.",
                0.0,
            )
        estimated = adapter.estimate_cost(estimated_input_tokens, estimated_output_tokens)
        contract = state.goal_contract
        # 28B: desktop goal-contract budgets are telemetry; a paid call is
        # governed by provider/privacy authorization, never a budget refusal.
        contract_limit = (
            None
            if contract is None or bool(getattr(contract, "budget_is_telemetry", False))
            else contract.max_api_cost_usd
        )
        if contract_limit is not None and state.cost_state.api_cost_usd + estimated > contract_limit:
            return CallAdmission(False, "The call would exceed the goal contract API budget.", estimated)
        if not adapter.capabilities().is_local:
            if self.remaining_frontier_calls(state) <= 0:
                return CallAdmission(False, "The compute policy frontier-call limit was reached.", estimated)
        return CallAdmission(True, "Call fits privacy and budget policy.", estimated)


class ModelRouter:
    """Select viable adapters while keeping decisions explainable."""

    def select(
        self,
        state: ProjectState,
        adapters: Iterable[ModelAdapter],
        *,
        maximum: int | None = None,
        local_runtime: "LocalRuntimeSnapshot | None" = None,
    ) -> tuple[ModelAdapter, ...]:
        viable = [
            adapter
            for adapter in adapters
            if not (
                state.privacy_mode is PrivacyMode.OFFLINE
                and not adapter.capabilities().is_local
            )
            and not (
                state.privacy_mode is PrivacyMode.PRIVATE_HYBRID
                and not adapter.capabilities().is_local
                and adapter.provider_metadata().get("privacy_boundary")
                != PRIVATE_HYBRID_TEMPLATE_VERSION
            )
        ]

        # A refreshed provider inventory is authoritative for that provider.
        # With no inventory evidence we keep the configured adapter viable so
        # first-run discovery remains possible.
        if local_runtime is not None and local_runtime.inventory_providers:
            inventoried_providers = set(local_runtime.inventory_providers)
            viable = [
                adapter
                for adapter in viable
                if not (
                    adapter.capabilities().is_local
                    and adapter.provider in inventoried_providers
                    and local_runtime.model(adapter.provider, adapter.model) is None
                )
            ]

        # COMPATIBILITY SHIM (Task F, manager decision 2026-09-19).
        #
        # This method used to rank adapters itself: locality by compute policy,
        # then local runtime measurements through `routing_rank`, with its own
        # tie-breaks.  That was one of the three contradictory selectors the
        # audit found.
        #
        # The ranking logic is GONE.  The real MEASUREMENTS are not discarded —
        # the manager was explicit that measured throughput, latency and
        # hardware fit are legitimate routing facts — they are promoted into
        # canonical PerformanceEvidence and handed to the one Model Router.  The
        # adapter order this returns is the router's decision, not this
        # function's.
        from .model_router import (
            CostPolicy,
            ModelCandidateFacts,
            ModelSelectionRequest,
            evidence_from_local_runtime,
            hardware_fit_from_local_runtime,
            select_model,
        )

        prefer_local = (
            state.compute_policy is ComputePolicy.ECONOMY
            or state.privacy_mode in {PrivacyMode.OFFLINE, PrivacyMode.PRIVATE_HYBRID}
        )

        facts: list[ModelCandidateFacts] = []
        by_ref: dict[str, ModelAdapter] = {}
        for adapter in viable:
            capabilities = adapter.capabilities()
            provider = str(adapter.provider)
            model = str(adapter.model)
            ref = str(adapter.adapter_id)
            by_ref[ref] = adapter
            facts.append(
                ModelCandidateFacts(
                    model_ref=ref,
                    provider=provider,
                    capabilities=frozenset(
                        name for name, on in (
                            ("coding", True),
                            ("tools", bool(getattr(capabilities, "supports_tools", False))),
                            ("vision", bool(getattr(capabilities, "supports_vision", False))),
                        ) if on
                    ),
                    supports_tools=bool(getattr(capabilities, "supports_tools", False)),
                    supports_json=bool(getattr(capabilities, "supports_json", False)),
                    supports_vision=bool(getattr(capabilities, "supports_vision", False)),
                    is_local=bool(getattr(capabilities, "is_local", False)),
                    # Real measurements, promoted rather than discarded.
                    performance=evidence_from_local_runtime(
                        local_runtime, provider, model
                    ) if getattr(capabilities, "is_local", False) else (),
                    hardware_fit=hardware_fit_from_local_runtime(
                        local_runtime, provider, model
                    ) if getattr(capabilities, "is_local", False) else None,
                )
            )

        if maximum is None:
            maximum = 1 if state.current_collaboration_mode is CollaborationMode.SOLO else len(viable)
        if not facts:
            return ()

        request = ModelSelectionRequest(
            task_type="cli_route",
            cost_policy=(
                CostPolicy.ECONOMY if state.compute_policy is ComputePolicy.ECONOMY
                else CostPolicy.BALANCED
            ),
            prefer_local=prefer_local,
            minimum_participants=1,
            maximum_participants=max(1, min(maximum, len(facts))),
        )
        decision = select_model(facts, request)
        ordered = [by_ref[ref] for ref in decision.selected_models if ref in by_ref]
        if not ordered:
            # The router excluded every candidate on a hard constraint.  Return
            # nothing rather than silently falling back to the unranked list,
            # which would reintroduce this function as a selector.
            return ()
        return tuple(ordered[:maximum])


def route_project(
    state: ProjectState,
    adapters: Iterable[ModelAdapter],
    *,
    novelty_required: bool = False,
    local_runtime: "LocalRuntimeSnapshot | None" = None,
) -> RoutingDecision:
    reasons: list[str] = []
    state.complexity = ComplexityRouter().assess(state.goal, novelty_required=novelty_required)
    mode, reason = CollaborationRouter().select(state, novelty_required=novelty_required)
    state.current_collaboration_mode = mode
    reasons.append(reason)
    sync, reason = SynchronizationRouter().select(state)
    state.current_sync_mode = sync
    reasons.append(reason)
    selected = ModelRouter().select(state, adapters, local_runtime=local_runtime)
    if local_runtime is not None:
        installed = sum(model.present for model in local_runtime.models)
        measured = sum(measurement.success for measurement in local_runtime.measurements)
        tier = (
            local_runtime.hardware_profile.recommended_local_tier
            if local_runtime.hardware_profile is not None
            else "unprofiled"
        )
        reasons.append(
            f"Local runtime evidence: {installed} installed model(s), "
            f"{measured} successful measurement(s), hardware tier {tier}."
        )
    reasons.append(f"Selected {len(selected)} policy-compatible adapter(s).")
    return RoutingDecision(
        complexity=state.complexity,
        collaboration_mode=mode,
        sync_mode=sync,
        selected_adapter_ids=tuple(adapter.adapter_id for adapter in selected),
        reasons=tuple(reasons),
    )


@dataclass(frozen=True, slots=True)
class ModelCapabilityProfile:
    model_id: str
    display_name: str
    context_window: int
    supports_tools: bool
    supports_vision: bool
    is_local: bool
    cost_per_1k_input_usd: float = 0.0
    cost_per_1k_output_usd: float = 0.0
    speed_tier: str = "standard"

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name,
            "context_window": self.context_window,
            "supports_tools": self.supports_tools,
            "supports_vision": self.supports_vision,
            "is_local": self.is_local,
            "cost_per_1k_input_usd": self.cost_per_1k_input_usd,
            "cost_per_1k_output_usd": self.cost_per_1k_output_usd,
            "speed_tier": self.speed_tier,
        }


@dataclass(frozen=True, slots=True)
class OrchestrationDecisionRecord:
    decision_id: str
    project_id: str
    task_summary: str
    selected_model_id: str
    rationale: str
    estimated_cost_usd: float
    actual_cost_usd: float
    is_local_zero_cost: bool
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "project_id": self.project_id,
            "task_summary": self.task_summary,
            "selected_model_id": self.selected_model_id,
            "rationale": self.rationale,
            "estimated_cost_usd": self.estimated_cost_usd,
            "actual_cost_usd": self.actual_cost_usd,
            "is_local_zero_cost": self.is_local_zero_cost,
            "created_at": self.created_at,
        }


def evaluate_model_suitability(
    profile: ModelCapabilityProfile,
    task_summary: str,
    *,
    requires_vision: bool = False,
    requires_tools: bool = False,
) -> tuple[float, list[str]]:
    """Evaluates suitability of a model capability profile for a task. Returns (score, reasons)."""
    score = 5.0
    reasons: list[str] = []

    if requires_vision and not profile.supports_vision:
        return 0.0, ["Model does not support required vision capabilities"]
    if requires_tools and not profile.supports_tools:
        return 0.0, ["Model does not support required tool capabilities"]

    if profile.is_local:
        score += 3.0
        reasons.append("Local zero-marginal-cost execution prioritized")
    else:
        reasons.append("Cloud model; consumes external quota/credits")

    if profile.supports_tools and ("tool" in task_summary.casefold() or "code" in task_summary.casefold()):
        score += 1.5
        reasons.append("Tool capabilities match code/tooling task signals")

    if profile.speed_tier == "fast":
        score += 0.5
        reasons.append("High inference throughput")

    return round(score, 2), reasons


class OrchestrationDecisionStore:
    """Project-isolated SQLite store for auditable model orchestration decisions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS orchestration_decisions (
                    decision_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    selected_model_id TEXT NOT NULL,
                    task_summary TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_orch_proj ON orchestration_decisions(project_id);"
            )

    def record_decision(self, record: OrchestrationDecisionRecord) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO orchestration_decisions VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.decision_id,
                    record.project_id,
                    record.selected_model_id,
                    record.task_summary,
                    json.dumps(record.to_dict()),
                    record.created_at or datetime.now(UTC).isoformat(timespec="microseconds"),
                ),
            )

    def list_decisions(self, project_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM orchestration_decisions WHERE project_id=? ORDER BY created_at ASC",
                (project_id,),
            ).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]

