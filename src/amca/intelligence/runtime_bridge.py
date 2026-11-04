"""M5.0 Intelligence Runtime Bridge — the single production integration seam.

Additive module introduced by the M5.0 Intelligence Runtime Integration Seam
milestone (zcode/amca-v0.5.0-m5-0-intelligence-runtime-integration-seam-01).

This facade is the ONE place through which the existing AMCA
orchestration/runtime can access the v0.5.0 intelligence capabilities
(M0 contracts, M1 planner selection, M1.1 proposal engine, M1.2 model-backed
adapter, M2 hybrid coordinator, M3 nano governance, M4 model router). It
coordinates access; it does NOT replace the existing orchestrator and holds
NO execution authority — the existing research runtime remains the sole
execution authority (no shadow runtime).

Gate-first dispatch: every capability checks its authoritative feature gate
before any invocation. With the shipped defaults (all four gates FALSE) the
bridge performs no intelligence work at all — production behavior stays
observationally identical to the v0.4.0 deterministic path: the
deterministic planner remains selected, the Hybrid coordinator is never
instantiated, no Nano registry alters normal execution, and the existing
v0.4.x model-selection path is used exactly as before (no double-routing,
no telemetry-only runs). Cancellation and deterministic-recovery semantics
of the existing runtime are untouched: the bridge keeps no execution state.

The bridge is attached to the authoritative runtime at exactly one narrow
seam (``ServiceContext.intelligence_bridge``); no other production file
imports it.
"""

import dataclasses  # noqa: F401  (kept for envelope introspection helpers)
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Tuple

from .contracts import (
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    PlannerInput,
    Sensitivity,
)
from .gates import DEFAULT_GATES, FeatureGateRegistry
from .hybrid_coordinator import HybridCoordinator
from .nano_foundation import NanoRunRegistry, require_nano_gate
from .planner_selection import (
    AdaptivePlanner,
    DeterministicPlanner,
    PlannerInvocation,
    PlannerSelection,
    PlannerSelectionMode,
    invoke_selected_planner,
    select_planner,
)
from .router_foundation import route, require_router_gate

BRIDGE_SEAM_ID = "amca.intelligence.runtime_bridge"
BRIDGE_VERSION = 1

#: The capability/mode names a validation request may use. "deterministic"
#: is always available; every other name is gate-controlled.
INTELLIGENCE_CAPABILITIES = (
    "deterministic",
    "adaptive_planner",
    "hybrid_execution",
    "nano_agents",
    "model_router",
)

# M5.1.1 capability-state contract: implemented (the foundation exists in
# this codebase), enabled (its authoritative feature gate is ON), and
# runtime_ready (derived ONLY from safe local configuration/state; no
# active probing is ever performed, so readiness that cannot be known
# locally is the explicit contract state "unknown" - never guessed true).
CAPABILITY_IMPLEMENTED = {
    "deterministic": True,
    "adaptive_planner": True,
    "hybrid_execution": True,
    "nano_agents": True,
    "model_router": True,
}
RUNTIME_READY_UNKNOWN = "unknown"
RUNTIME_READY_READY = "ready"
CAPABILITY_CONTRACT_VERSION = 2
# M5.6: the governed local Nano execution pipeline genuinely exists (sealed
# M3 assignments execute through the existing production scheduler with a
# deterministic, dependency-free executor), so Nano readiness is derived
# from safe local state and reported truthfully as "ready" — while the
# nano_agents gate itself stays OFF (enabled = false). Adaptive/Hybrid/
# Router remain "unknown": no active probing is performed for them.
NANO_RUNTIME_READY = RUNTIME_READY_READY

_BRIDGE_OPS = frozenset(
    {
        "resolve_planner",
        "invoke_planner",
        "resolve_local_planner_runtime",
        "validate_request",
        "hybrid_status",
        "request_hybrid_session",
        "nano_context",
        "nano_spawn",
        "nano_execution_context",
        "configure_gates",
        "resolve_model_route",
        "status",
    }
)


def _default_meta_factory(record_id: str) -> ContractMetadata:
    return ContractMetadata(
        record_id=record_id,
        correlation_id=f"bridge:{record_id}",
        producer=BRIDGE_SEAM_ID,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        sensitivity=Sensitivity.INTERNAL,
    )


class IntelligenceRuntimeBridge:
    """Gate-first access coordinator for the v0.5.0 intelligence capabilities.

    The bridge exposes only bounded, side-effect-free dispatch operations;
    it never executes research runs, never touches the canonical project
    stores, and never holds credentials. Existing runtime cancellation,
    interrupted-run reconciliation, resume, and deterministic recovery are
    untouched because the bridge keeps no execution state.
    """

    SEAM_ID = BRIDGE_SEAM_ID
    VERSION = BRIDGE_VERSION

    def __init__(
        self,
        *,
        gates: Optional[FeatureGateRegistry] = None,
        meta_factory: Optional[Callable[[str], ContractMetadata]] = None,
        bootstrap_mode: str = "legacy_dev_http",
    ) -> None:
        if bootstrap_mode not in ("tauri_injected", "legacy_dev_http"):
            raise ContractError(
                ContractErrorCode.INVALID,
                f"unknown bootstrap mode {bootstrap_mode!r}",
            )
        self._gates = gates if gates is not None else DEFAULT_GATES
        self._meta_factory = meta_factory or _default_meta_factory
        # M5.4.1 security-mode contract: machine-enforced distinction between
        # the hardened Tauri-injected bootstrap and the legacy dev HTTP
        # bootstrap. mutation_security_ready is true ONLY in the hardened
        # mode, so future state-changing intelligence endpoints can be made
        # impossible in the insecure bootstrap mode by construction.
        self.bootstrap_mode = bootstrap_mode
        self.secure_bootstrap = bootstrap_mode == "tauri_injected"
        self.mutation_security_ready = self.secure_bootstrap
        # M5.7: the ONE controlled runtime source for gate state. The
        # controller validates and commits atomically; each commit swaps the
        # bridge's immutable registry reference (the atomic update every
        # gate-first dispatch path reads).
        from .gate_controller import ControlledGateController

        self._gate_controller = ControlledGateController(
            gates=self._gates,
            bootstrap_mode=bootstrap_mode,
            meta_factory=self._meta_factory,
            readiness_provider=self._capability_readiness,
            on_commit=lambda gates: setattr(self, "_gates", gates),
        )
        # M5.8: optional truthful local readiness source (cached-only; the
        # owner refreshes it with bounded local calls).
        self._local_readiness = None

    def _capability_readiness(self) -> Mapping[str, str]:
        """Truthful per-capability runtime readiness (no active probing)."""

        return {
            name: entry["runtime_ready"]
            for name, entry in self.status()["capabilities"].items()
        }

    @property
    def gate_controller(self):
        return self._gate_controller

    def configure_gates(
        self,
        changes,
        *,
        contract_version: int,
        correlation_id: str,
        allow_active_disable: bool = False,
    ):
        """M5.7 controlled mutation entry (see ControlledGateController)."""

        return self._gate_controller.apply_changes(
            changes,
            contract_version=contract_version,
            correlation_id=correlation_id,
            allow_active_disable=allow_active_disable,
        )

    def disable_all_experimental(self, *, correlation_id: str, emergency: bool = False):
        """M5.7 fail-safe rollback: every experimental gate back to OFF."""

        return self._gate_controller.disable_all_experimental(
            correlation_id=correlation_id, emergency=emergency,
        )

    def gate_snapshot(self) -> Mapping[str, Any]:
        return self._gate_controller.snapshot()

    def gate_audit_records(self) -> Tuple[Mapping[str, Any], ...]:
        return self._gate_controller.audit_records()

    def attach_local_readiness(self, service) -> None:
        """M5.8: attach the truthful local readiness source.

        The service is CACHED-ONLY from the bridge's perspective: status()
        never probes; the owner refreshes the service explicitly (bounded
        local inventory call at startup, explicit refresh later).
        """

        self._local_readiness = service

    def refresh_local_readiness(self, **kwargs):
        """Explicit bounded local readiness refresh (never automatic)."""

        if self._local_readiness is None:
            raise ContractError(
                ContractErrorCode.INVALID,
                "no local readiness source is attached",
            )
        return self._local_readiness.refresh(**kwargs)

    def local_readiness_report(self) -> Optional[Mapping[str, Any]]:
        if self._local_readiness is None:
            return None
        report = self._local_readiness.latest()
        return report.to_dict() if report is not None else None

    def begin_intelligence_execution(self, capability: str, run_id: str) -> None:
        """Register an active intelligence execution (active-run safety)."""

        self._gate_controller.begin_execution(capability, run_id)

    def end_intelligence_execution(self, capability: str, run_id: str) -> None:
        self._gate_controller.end_execution(capability, run_id)

    def security_context(self) -> Mapping[str, Any]:
        """Minimal internal security context (M5.4.1 contract)."""

        return {
            "bootstrap_mode": self.bootstrap_mode,
            "secure_bootstrap": self.secure_bootstrap,
            "mutation_security_ready": self.mutation_security_ready,
        }

    def require_mutation_ready(self) -> Mapping[str, Any]:
        """Reusable guard for FUTURE state-changing intelligence endpoints.

        Fails closed unless the session was established through the
        hardened Tauri-injected bootstrap. No current endpoint calls this;
        it exists so mutation authority is structurally impossible in the
        insecure bootstrap mode.
        """

        if not self.secure_bootstrap or not self.mutation_security_ready:
            raise ContractError(
                ContractErrorCode.BLOCKED,
                "mutation is prohibited: session was not established "
                f"through the hardened bootstrap (mode "
                f"{self.bootstrap_mode!r})",
            )
        return self.security_context()

    # -- gate-first dispatch -------------------------------------------------

    def resolve_planner(
        self,
        request_ref: str,
        *,
        mode: PlannerSelectionMode = PlannerSelectionMode.AUTO,
    ) -> PlannerSelection:
        """Resolve the planner for one request (selection only).

        With adaptive_planner OFF the deterministic planner is selected and
        no model-backed planner is ever referenced.
        """

        return select_planner(
            gates=self._gates,
            meta=self._meta_factory("planner-selection"),
            request_ref=request_ref,
            requested_mode=mode,
        )

    def invoke_planner(
        self,
        selection: PlannerSelection,
        planner_input: PlannerInput,
        *,
        deterministic: DeterministicPlanner,
        adaptive: Optional[AdaptivePlanner] = None,
    ) -> PlannerInvocation:
        """Invoke the selected planner; adaptive failures fall back to the
        deterministic planner exactly as in M1. Deterministic planner errors
        propagate unchanged."""

        return invoke_selected_planner(
            selection=selection,
            planner_input=planner_input,
            deterministic=deterministic,
            adaptive=adaptive,
            meta=self._meta_factory("planner-invocation"),
        )

    def resolve_local_planner_runtime(
        self,
        *,
        adapter,
        timeout_seconds: float = 60.0,
        cancel_poll_seconds: float = 0.01,
        response_format: Optional[Mapping[str, Any]] = None,
    ):
        """M5.5.2 bridge-level runtime seam: bounded factory resolving the
        concrete ``LocalModelPlannerRuntime`` over an ALREADY-AUTHORIZED
        ``ModelAdapter``.

        Gate-subordinate: refuses while the adaptive_planner gate is OFF, so
        normal deterministic execution can never resolve (let alone call)
        the local runtime through the bridge — the seam exists only for a
        future authorized adaptive path and for explicit test exercise.
        Resolution is construction only: no provider discovery, no Ollama
        instantiation, no readiness probing, and no model call of any kind.
        The caller owns the adapter lifecycle; the returned runtime reaches
        the adapter only when a gated adaptive path invokes it.

        v0.6.0 M0: ``response_format`` optionally binds the provider-native
        structured-output contract (e.g. ``PLANNER_RESPONSE_FORMAT``) so a
        gated chat-path planner gets token-constrained valid JSON instead of
        reasoning-budget truncation.  Pass-through only; no gate semantics
        change.
        """

        if not self._gates.is_enabled("adaptive_planner"):
            raise ContractError(
                ContractErrorCode.BLOCKED,
                "local planner runtime resolution requested while the "
                "adaptive_planner gate is disabled; refusing instead of "
                "activating Adaptive",
            )
        from .local_runtime import LocalModelPlannerRuntime

        return LocalModelPlannerRuntime(
            adapter=adapter,
            timeout_seconds=timeout_seconds,
            cancel_poll_seconds=cancel_poll_seconds,
            meta_factory=self._meta_factory,
            response_format=response_format,
        )

    # -- hybrid ---------------------------------------------------------------

    def hybrid_status(self) -> Mapping[str, Any]:
        """Bounded eligibility snapshot; no coordinator is ever constructed
        here, so a disabled gate means the coordinator does not exist."""

        enabled = self._gates.is_enabled("hybrid_execution")
        return {
            "enabled": enabled,
            "coordinator_available": False,
            "reason": (
                "hybrid_execution gate enabled; provide a coordinator "
                "factory to request a session"
                if enabled
                else "hybrid_execution gate disabled; deterministic "
                "execution remains authoritative"
            ),
        }

    def request_hybrid_session(
        self,
        *,
        coordinator_factory: Callable[[], HybridCoordinator],
        planner_input,
        cancel_check: Optional[Callable[[], bool]] = None,
        candidates=None,
        policy_context=None,
    ):
        """Run one hybrid session behind the hybrid_execution gate.

        The coordinator is built ONLY through the injected factory and only
        when the gate is enabled; with the gate OFF this fails closed and
        the factory is never invoked (no shadow runtime).

        v0.6.0 M0: ``candidates`` / ``policy_context`` optionally supply the
        deterministic-eligibility candidate set and policy envelope the M1.1
        proposal engine validates adaptive recommendations against — the same
        objects the M5.6 trials bound verbatim.  Pass-through only; the gate
        check and fail-closed behavior are unchanged.
        """

        if not self._gates.is_enabled("hybrid_execution"):
            raise ContractError(
                ContractErrorCode.INVALID,
                "hybrid session requested while the hybrid_execution gate is "
                "disabled; refusing instead of activating Hybrid",
            )
        coordinator = coordinator_factory()
        return coordinator.run(
            request_ref=planner_input.request_id,
            planner_input=planner_input,
            gates=self._gates,
            cancel_check=cancel_check,
            candidates=candidates,
            policy_context=policy_context,
        )

    # -- nano -----------------------------------------------------------------

    def nano_context(self) -> Mapping[str, Any]:
        """Bounded Nano governance snapshot; with nano_agents OFF no registry
        exists and nothing about normal execution changes."""

        enabled = self._gates.is_enabled("nano_agents")
        return {
            "enabled": enabled,
            "registry": None,
            "reason": (
                "nano_agents gate enabled; provide a registry factory to "
                "create governed spawn context"
                if enabled
                else "nano_agents gate disabled; the existing Agent Fabric "
                "remains authoritative"
            ),
        }

    def nano_spawn(
        self,
        proposal,
        *,
        parent_permissions,
        registry_factory: Callable[[], NanoRunRegistry],
    ):
        """Spawn behind the nano_agents gate; refuses while disabled."""

        require_nano_gate(self._gates)
        registry = registry_factory()
        return registry.authorize_spawn(
            proposal,
            parent_permissions=parent_permissions,
            gates=self._gates,
        )

    def nano_execution_context(
        self,
        *,
        registry_factory: Callable[[], NanoRunRegistry],
        scheduler_factory: Callable[[], Any],
    ):
        """M5.6: obtain the governed Nano execution context behind the
        nano_agents gate.

        The coordinator binds the M3 ``NanoRunRegistry`` ledger to the
        EXISTING production ``NanoScheduler`` — no second execution engine
        exists. Both factories are injected by the caller; with the gate
        OFF this refuses and neither factory is ever invoked, so normal
        production execution constructs and executes zero Nano tasks.
        """

        require_nano_gate(self._gates)
        from .nano_execution import NanoExecutionCoordinator

        return NanoExecutionCoordinator(
            registry=registry_factory(),
            scheduler=scheduler_factory(),
            meta_factory=self._meta_factory,
        )

    # -- model routing ----------------------------------------------------------

    def resolve_model_route(
        self,
        *,
        request_ref: str = "bridge-route",
        candidates=None,
        policy=None,
        adapters=None,
        state=None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Mapping[str, Any]:
        """Resolve the model route behind the model_router gate.

        With model_router OFF the existing v0.4.x model-selection path is
        used exactly as before: nothing is routed here, no router function
        is invoked, and the marker records that fact so the runtime cannot
        double-route.
        """

        if not self._gates.is_enabled("model_router"):
            return {
                "routed": False,
                "path": "existing_v0_4_model_selection",
                "router_invoked": False,
                "reason": "model_router gate disabled; existing model "
                "selection remains authoritative",
            }
        require_router_gate(self._gates)
        if cancel_check is not None and cancel_check():
            raise ContractError(ContractErrorCode.CANCELLED, "routing cancelled")
        if adapters is not None and state is not None:
            from .router_foundation import route_adapters

            chosen = route_adapters(
                meta_factory=self._meta_factory,
                request_ref=request_ref,
                state=state,
                adapters=adapters,
                gates=self._gates,
            )
            return {
                "routed": True,
                "path": "m4_router",
                "router_invoked": True,
                "candidates": chosen,
            }
        if candidates is None or policy is None:
            raise ContractError(
                ContractErrorCode.INVALID,
                "model routing requires candidates and a policy",
            )
        outcome = route(
            meta=self._meta_factory("model-route"),
            request_ref=request_ref,
            candidates=tuple(candidates),
            policy=policy,
            gates=self._gates,
            cancel_check=cancel_check,
        )
        return {
            "routed": outcome.status.value == "routed",
            "path": "m4_router",
            "router_invoked": True,
            "outcome": outcome,
        }

    # -- status -----------------------------------------------------------------

    def validate_request(
        self, requested, *, contract_version: int = CAPABILITY_CONTRACT_VERSION
    ) -> Mapping[str, Any]:
        """Strict, NON-EXECUTING validation of requested capabilities/modes.

        Rejects malformed requests, unknown capabilities, and conflicting
        requests; reports truthful disabled state for gate-controlled
        capabilities (never a silent downgrade). Runs no task. Contract
        version 1 responses keep the M5.1 shape for existing consumers;
        version 2 (current) adds implemented/runtime_ready/reason.
        """
        if contract_version not in (1, CAPABILITY_CONTRACT_VERSION):
            raise ContractError(
                ContractErrorCode.INVALID,
                f"unsupported contract version {contract_version!r}",
            )

        if not isinstance(requested, (list, tuple)):
            raise ContractError(
                ContractErrorCode.INVALID,
                "requested must be an array of capability names",
            )
        seen: list[str] = []
        for item in requested:
            if isinstance(item, bool) or not isinstance(item, str):
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "requested capabilities must be strings",
                )
            if item not in INTELLIGENCE_CAPABILITIES:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    f"unknown capability {item!r}",
                )
            if item in seen:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    f"conflicting duplicate request for {item!r}",
                )
            seen.append(item)
        if "deterministic" in seen and "adaptive_planner" in seen:
            raise ContractError(
                ContractErrorCode.INVALID,
                "conflicting request: deterministic and adaptive are "
                "mutually exclusive planner modes",
            )
        decisions: dict[str, Any] = {}
        readiness_override = self._local_readiness.readiness_map() if (
            self._local_readiness is not None
        ) else {}
        for name in seen:
            if name == "deterministic":
                enabled = True
                decision = "allowed"
                reason = "deterministic planner is the default"
                runtime_ready = RUNTIME_READY_READY
            else:
                enabled = self._gates.is_enabled(name)
                decision = "disabled_feature"
                reason = "feature gate disabled"
                default_ready = (
                    NANO_RUNTIME_READY if name == "nano_agents"
                    else RUNTIME_READY_UNKNOWN
                )
                runtime_ready = readiness_override.get(name, default_ready)
            decisions[name] = {
                "requested": True,
                "implemented": CAPABILITY_IMPLEMENTED[name],
                "enabled": enabled,
                "runtime_ready": runtime_ready,
                "decision": decision,
                "reason": reason,
            }
        if contract_version == 1:
            legacy = {
                name: {
                    "requested": True,
                    "enabled": entry["enabled"],
                    "available": entry["enabled"],
                    "decision": (
                        "allowed"
                        if entry["enabled"]
                        else "unavailable_or_disabled"
                    ),
                }
                for name, entry in decisions.items()
            }
            return {
                "contract_version": 1,
                "requested": seen,
                "decisions": legacy,
                "execution_authority": "existing_runtime",
            }
        return {
            "contract_version": CAPABILITY_CONTRACT_VERSION,
            "requested": seen,
            "decisions": decisions,
            "execution_authority": "existing_runtime",
        }

    def status(self) -> Mapping[str, Any]:
        """A safe, bounded snapshot of the bridge and gate states.

        Capabilities use the M5.1.1 capability-state contract: implemented
        (foundation exists), enabled (gate state), and runtime_ready derived
        only from safe local state - "unknown" here because no active
        probing is ever performed.
        """

        gates = self._gates.to_dict()
        readiness_override = self._local_readiness.readiness_map() if (
            self._local_readiness is not None
        ) else {}
        reason_map = self._local_readiness.reason_map() if (
            self._local_readiness is not None
        ) else {}
        return {
            "seam_id": BRIDGE_SEAM_ID,
            "version": BRIDGE_VERSION,
            "gates": gates,
            "capability_contract_version": CAPABILITY_CONTRACT_VERSION,
            "capabilities": {
                "deterministic": {
                    "implemented": True,
                    "enabled": True,
                    "runtime_ready": RUNTIME_READY_READY,
                },
                "adaptive_planner": {
                    "implemented": True,
                    "enabled": gates["adaptive_planner"],
                    "runtime_ready": readiness_override.get(
                        "adaptive_planner", RUNTIME_READY_UNKNOWN
                    ),
                    **({"readiness_reason": reason_map["adaptive_planner"]}
                       if "adaptive_planner" in reason_map else {}),
                },
                "hybrid_execution": {
                    "implemented": True,
                    "enabled": gates["hybrid_execution"],
                    "runtime_ready": readiness_override.get(
                        "hybrid_execution", RUNTIME_READY_UNKNOWN
                    ),
                    **({"readiness_reason": reason_map["hybrid_execution"]}
                       if "hybrid_execution" in reason_map else {}),
                },
                "nano_agents": {
                    "implemented": True,
                    "enabled": gates["nano_agents"],
                    "runtime_ready": NANO_RUNTIME_READY,
                },
                "model_router": {
                    "implemented": True,
                    "enabled": gates["model_router"],
                    "runtime_ready": readiness_override.get(
                        "model_router", RUNTIME_READY_UNKNOWN
                    ),
                    **({"readiness_reason": reason_map["model_router"]}
                       if "model_router" in reason_map else {}),
                },
            },
            "execution_authority": "existing_runtime",
        }


# the whitelist of bridge operations, importable for surface assertions
BRIDGE_OPERATIONS = _BRIDGE_OPS


def require_mutation_ready(bridge: "IntelligenceRuntimeBridge") -> Mapping[str, Any]:
    """Module-level reusable guard for FUTURE state-changing intelligence
    endpoints: fails closed unless the bridge reports the hardened Tauri
    bootstrap. See :meth:`IntelligenceRuntimeBridge.require_mutation_ready`.
    """

    return bridge.require_mutation_ready()
