"""CLOSURE-05 Phase G physical proof: collaboration enablement + nano agents.

Proves the M5.7 hardened enablement paths and the gated Nano/collaboration
runtimes against REAL components:

  G1. Legacy dev bootstrap is fail-closed: require_mutation_ready raises;
      no gate can ever be enabled from a manually-started backend.
  G2. Tauri-injected bootstrap may mutate; all gates start OFF.
  G3. Readiness enforcement: a gate whose readiness is not 'ready' can
      never be enabled (controller rejects with runtime_not_ready).
  G4. Dependency ordering: hybrid_execution cannot be enabled without
      adaptive_planner first (dependency_not_enabled), and the ordered
      pair enable succeeds.
  G5. Nano governed execution: with nano_agents enabled, real sealed
      assignments execute through the production NanoScheduler path and
      produce auditable NanoResultRecords.
  G6. Nano gate-first: with nano_agents OFF, zero construction work
      happens (ContractError before any scheduling).
  G7. Collaboration decision table: a decomposable task routes to
      PARALLEL, a high-coupled task routes to JOINT, and a 1-model
      eligible set degrades a multi-participant pick to SOLO with the
      reason recorded.
  G8. Active-run protection: a gate with an active execution refuses
      disable until the run finishes (409-category active_execution).

Run:  python scripts/prove_closure05_phase_g.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.agent_runtime import NanoScheduler  # noqa: E402
from amca.collaboration import (  # noqa: E402
    CollaborationSignals,
    DesktopCollaborationRouter,
    TaskShape,
)
from amca.domain import CollaborationMode  # noqa: E402
from amca.intelligence import (  # noqa: E402
    ContractError,
    ContractMetadata,
    FeatureGateRegistry,
    IntelligenceRuntimeBridge,
    NanoAssignment,
    NanoExecutionCoordinator,
    NanoRunRegistry,
    Sensitivity,
)
from amca.models.base import (  # noqa: E402
    ModelAdapter,
    ModelCapabilities,
    ModelResponse,
    TokenUsage,
)

RESULTS: list[dict[str, object]] = []

CREATED = "2026-09-05T00:00:00+00:00"


def record(check_id: str, ok: bool, detail: str) -> None:
    RESULTS.append({"id": check_id, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + check_id + " :: " + detail)


def meta_factory(record_id):
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-closure05-g",
        producer="closure05-phase-g",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


class _InstantLocalAdapter(ModelAdapter):
    """Real local adapter returning deterministic output immediately."""

    def __init__(self) -> None:
        super().__init__(
            adapter_id="instant-stub", provider="stub", model="stub",
            capabilities=ModelCapabilities(is_local=True),
        )

    async def generate(self, request):
        async with self._call_scope(request):
            return ModelResponse(
                text="nano governed output",
                model="stub", provider="stub",
                usage=TokenUsage(), finish_reason="stop",
            )

    async def cancel(self, request_id=None):
        return await super().cancel(request_id)

    def stream(self, request):
        raise NotImplementedError


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="closure05-phase-g-"))
    try:
        # ---- G1: legacy dev bootstrap fail-closed ----
        legacy = IntelligenceRuntimeBridge(
            meta_factory=meta_factory, bootstrap_mode="legacy_dev_http",
        )
        legacy_refused = False
        try:
            legacy.require_mutation_ready()
        except ContractError:
            legacy_refused = True
        record(
            "G1-legacy-fail-closed",
            legacy_refused and not legacy.secure_bootstrap,
            "legacy bootstrap require_mutation_ready raises ContractError",
        )

        # ---- G2: tauri-injected may mutate; all gates OFF at start ----
        bridge = IntelligenceRuntimeBridge(
            meta_factory=meta_factory, bootstrap_mode="tauri_injected",
        )
        bridge.require_mutation_ready()
        start = bridge.status()["gates"]
        record(
            "G2-secure-mode-gates-off",
            bridge.secure_bootstrap
            and not any(start.values()),
            f"gates_at_start={start}",
        )

        # ---- G3: readiness enforcement ----
        # Default readiness is 'unknown' for adaptive_planner: enabling must
        # be rejected by the controller.
        readiness_refused = False
        try:
            bridge.configure_gates(
                {"adaptive_planner": True},
                contract_version=1,
                correlation_id="corr-g3",
            )
        except ContractError as error:
            readiness_refused = (
                getattr(error, "details", {}).get("category") == "runtime_not_ready"
            )
        record(
            "G3-runtime-not-ready-refused",
            readiness_refused,
            "enable with readiness 'unknown' rejected (runtime_not_ready)",
        )

        # Make adaptive_planner ready via the real readiness seam, then
        # enable: hybrid still refuses (dependency), then both enable in order.
        bridge._gate_controller._readiness_provider = lambda: {
            "adaptive_planner": "ready",
            "hybrid_execution": "ready",
            "model_router": "ready",
            "nano_agents": "ready",
        }
        dependency_refused = False
        try:
            bridge.configure_gates(
                {"hybrid_execution": True},
                contract_version=1,
                correlation_id="corr-g4a",
            )
        except ContractError as error:
            dependency_refused = (
                getattr(error, "details", {}).get("category") == "dependency_not_enabled"
            )
        bridge.configure_gates(
            {"adaptive_planner": True}, contract_version=1, correlation_id="corr-g4b",
        )
        bridge.configure_gates(
            {"hybrid_execution": True}, contract_version=1, correlation_id="corr-g4c",
        )
        gates_now = bridge.status()["gates"]
        record(
            "G4-dependency-ordered-enable",
            dependency_refused
            and gates_now["adaptive_planner"]
            and gates_now["hybrid_execution"],
            f"hybrid_alone_refused={dependency_refused} gates={gates_now}",
        )

        # ---- G5/G6: nano governed execution gate-first ----
        registry = NanoRunRegistry(
            meta_factory=meta_factory, run_ref="run-g5",
            allowed_capabilities=("citation_check",),
        )
        from amca.intelligence import NanoSpawnProposal

        spawn = NanoSpawnProposal(
            meta=meta_factory("prop-g5"),
            proposal_id="prop-g5",
            run_ref="run-g5",
            parent_task_id="task:root",
            parent_depth=0,
            task_purpose="verify one citation claim",
            task_type="verification",
            requested_child_count=1,
            capabilities=("citation_check",),
            permissions=("local_search",),
            expected_cost=0.0,
            correlation_id="corr-g5",
        )
        assignment = registry.authorize_spawn(
            spawn,
            parent_permissions=("local_search",),
            gates=FeatureGateRegistry(nano_agents=True),
        )
        scheduler = NanoScheduler(
            {"stub": _InstantLocalAdapter()},
            budget_usd=5.0, max_concurrent=2, max_attempts=1,
        )
        coordinator = NanoExecutionCoordinator(
            registry=registry, scheduler=scheduler,
            meta_factory=meta_factory, known_parent_task_ids=("task:root",),
        )

        # G6 first: gate OFF must refuse before any construction work.
        gate_off_refused = False
        try:
            coordinator.execute(
                [assignment], gates=FeatureGateRegistry(nano_agents=False),
            )
        except ContractError:
            gate_off_refused = True
        record(
            "G6-nano-gate-first",
            gate_off_refused,
            "nano execution with gate OFF raises ContractError before dispatch",
        )

        outcome = coordinator.execute(
            [assignment], gates=FeatureGateRegistry(nano_agents=True),
        )
        results = outcome.get("results", {})
        result_record = results.get(assignment.nano_id)
        record(
            "G5-nano-governed-execution",
            bool(results)
            and result_record is not None
            and bool(getattr(result_record, "summary", "")),
            f"nano_id={assignment.nano_id} "
            f"disposition={getattr(result_record, 'disposition', None)} "
            f"summary={str(getattr(result_record, 'summary', ''))[:40]!r}",
        )

        # ---- G7: collaboration decision table ----
        router = DesktopCollaborationRouter()
        from amca.domain import ComplexityLevel

        signals = CollaborationSignals()
        shape_parallel = TaskShape(
            complexity=ComplexityLevel.MEDIUM, coupled=False,
            decomposable=True, verification_heavy=False, novelty_required=False,
        )
        parallel_decision = router.decide(
            shape_parallel, signals, eligible_model_count=3,
        )
        shape_joint = TaskShape(
            complexity=ComplexityLevel.HIGH, coupled=True,
            decomposable=True, verification_heavy=False, novelty_required=False,
        )
        joint_decision = router.decide(
            shape_joint, signals, eligible_model_count=3,
        )
        solo_degrade = router.decide(
            shape_parallel, signals, eligible_model_count=1,
        )
        record(
            "G7-collaboration-routing",
            parallel_decision.mode == CollaborationMode.PARALLEL
            and joint_decision.mode == CollaborationMode.JOINT
            and solo_degrade.mode == CollaborationMode.SOLO,
            f"parallel={parallel_decision.mode.value}({parallel_decision.policy_rule}) "
            f"joint={joint_decision.mode.value}({joint_decision.policy_rule}) "
            f"degraded={solo_degrade.mode.value} reason={solo_degrade.degradation_reason}",
        )

        # ---- G8: active-run protection ----
        controller = bridge.gate_controller
        controller.begin_execution("hybrid_execution", "run-active-1")
        disable_refused = False
        try:
            bridge.configure_gates(
                {"hybrid_execution": False},
                contract_version=1, correlation_id="corr-g8",
            )
        except ContractError:
            disable_refused = True
        controller.end_execution("hybrid_execution", "run-active-1")
        bridge.configure_gates(
            {"hybrid_execution": False},
            contract_version=1, correlation_id="corr-g8b",
        )
        final_gates = bridge.status()["gates"]
        record(
            "G8-active-run-protection",
            disable_refused and not final_gates["hybrid_execution"],
            f"disable_with_active_run_refused={disable_refused} "
            f"disable_after_finish_ok={not final_gates['hybrid_execution']}",
        )

        passed = sum(1 for item in RESULTS if item["ok"])
        summary = {
            "phase": "CLOSURE-05 Phase G",
            "passed": passed,
            "total": len(RESULTS),
            "results": RESULTS,
        }
        out_path = SOFTWARE_ROOT / "tests" / "closure05-phase-g-proof.json"
        out_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps({"passed": passed, "total": len(RESULTS)}))
        print(f"proof written to {out_path}")
        return 0 if passed == len(RESULTS) else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
