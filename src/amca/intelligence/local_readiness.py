"""M5.8 truthful local-model readiness service.

Additive module introduced by the M5.8 Live Local Intelligence Readiness
E2E milestone (zcode/amca-v0.5.0-m5-8-live-local-intelligence-readiness-e2e-01).

The single authoritative source for "is the existing local model runtime
ready?" — reused, never duplicated:

REUSED verbatim: ``amca.local_runtime.discover_ollama_models`` (the
existing bounded local inventory reader, injectable transport for tests),
the existing ``OllamaAdapter``/``ModelAdapter`` stack via the injected
adapter factory, and ``LocalModelPlannerRuntime.complete_structured`` for
the optional one-shot structured probe.

Rules (work order ZC-AMCA-V0.5.0-M5.8):
- LOCAL ONLY: no cloud/paid endpoint is ever contacted; the probe targets
  the loopback Ollama HTTP API exclusively;
- BOUNDED: one short-timeout inventory call per probe; generations only
  when an explicit structured probe is requested (never automatic, never
  on panel render);
- TRUTHFUL: only ready / not_ready / unknown states with bounded reason
  codes (local_runtime_unreachable, no_eligible_local_model,
  structured_output_unverified, ready) — no provider error dumps;
- CACHED: results are cached until the next explicit refresh.
"""

import dataclasses
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Tuple

from ..local_runtime import discover_ollama_models

READY = "ready"
NOT_READY = "not_ready"
UNKNOWN = "unknown"

REASON_READY = "ready"
REASON_UNREACHABLE = "local_runtime_unreachable"
REASON_NO_MODEL = "no_eligible_local_model"
REASON_STRUCTURED_UNVERIFIED = "structured_output_unverified"

#: Capabilities whose readiness this service derives (Nano stays ready from
#: M5.6 and deterministic stays ready structurally; they are echoed here so
#: consumers always receive a complete map).
DERIVED_CAPABILITIES = ("adaptive_planner", "hybrid_execution", "model_router")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


@dataclasses.dataclass(frozen=True)
class LocalReadinessReport:
    """One truthful, bounded readiness observation."""

    runtime_reachable: bool
    models_detected: Tuple[str, ...]
    selected_model: Optional[str]
    readiness: Mapping[str, str]
    reasons: Mapping[str, str]
    probed_at: str
    structured_probe_run: bool = False
    live: bool = False  # True ONLY when a real local Ollama answered

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "runtime_reachable": self.runtime_reachable,
            "models_detected": list(self.models_detected),
            "selected_model": self.selected_model,
            "readiness": dict(self.readiness),
            "reasons": dict(self.reasons),
            "probed_at": self.probed_at,
            "structured_probe_run": self.structured_probe_run,
            "live": self.live,
        }


def _unreachable_report(base_url: str, probed_at: str) -> LocalReadinessReport:
    reasons = {
        "adaptive_planner": REASON_UNREACHABLE,
        "model_router": REASON_UNREACHABLE,
        "hybrid_execution": REASON_UNREACHABLE,
        "nano_agents": REASON_READY,
        "deterministic": REASON_READY,
    }
    return LocalReadinessReport(
        runtime_reachable=False,
        models_detected=(),
        selected_model=None,
        readiness={
            "adaptive_planner": NOT_READY,
            "model_router": NOT_READY,
            "hybrid_execution": NOT_READY,
            "nano_agents": READY,
            "deterministic": READY,
        },
        reasons=reasons,
        probed_at=probed_at,
    )


class LocalReadinessService:
    """Bounded, local-only, cached readiness source for the M5.7 controller."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        inventory_timeout: float = 2.0,
        preferred_model: str = "qwen3:1.7b",
        inventory_fn: Callable[..., Tuple] = discover_ollama_models,
        meta_factory: Optional[Callable[[str], Any]] = None,
        structured_probe_timeout: float = 90.0,
        structured_probe_tokens: int = 512,
    ) -> None:
        if inventory_timeout <= 0:
            raise ValueError("inventory_timeout must be positive")
        if structured_probe_timeout <= 0 or structured_probe_tokens <= 0:
            raise ValueError(
                "structured_probe_timeout and structured_probe_tokens must "
                "be positive"
            )
        self._base_url = base_url
        self._timeout = inventory_timeout
        self._preferred_model = preferred_model
        self._inventory_fn = inventory_fn
        # M5.9 live-validation fix (regression-tested): reasoning models
        # (e.g. qwen3) spend visible-token budget on <think> before the
        # answer, so the bounded structured probe needs a small but real
        # generation budget and its own short bounded timeout.
        # CLOSURE-05B Part C: on consumer hardware that CPU-offloads part of
        # the model, one warm structured probe takes ~60s wall time; a 20s
        # bound made readiness permanently 'structured_output_unverified'
        # even though the runtime answers correctly. 90s keeps the probe
        # bounded and local-only while being physically achievable.
        self._probe_timeout = structured_probe_timeout
        self._probe_tokens = structured_probe_tokens
        if meta_factory is None:
            from .contracts import ContractMetadata, Sensitivity

            def meta_factory(record_id: str) -> Any:
                return ContractMetadata(
                    record_id=record_id,
                    correlation_id="local-readiness",
                    producer="amca.intelligence.local_readiness",
                    created_at=_utc_now(),
                    sensitivity=Sensitivity.INTERNAL,
                )

        self._meta_factory = meta_factory
        self._last: Optional[LocalReadinessReport] = None

    # -- probing -----------------------------------------------------------------

    def probe(
        self,
        *,
        structured_probe: bool = False,
        adapter_factory: Optional[Callable[[str], Any]] = None,
    ) -> LocalReadinessReport:
        """Run one bounded local probe and cache the truthful result.

        ``structured_probe`` opts into the single tiny structured-response
        check through the EXISTING adapter path (required before Adaptive
        may report ready); it never runs automatically.
        """

        probed_at = _utc_now()
        try:
            models = tuple(self._inventory_fn(self._base_url, timeout=self._timeout))
        except Exception:
            # Bounded local failure: normalized to a reason code, never a
            # provider error dump.
            report = _unreachable_report(self._base_url, probed_at)
            self._last = report
            return report
        live = True
        names = tuple(record.model for record in models)
        selected = None
        for preferred in (self._preferred_model,):
            if preferred in names:
                selected = preferred
                break
        if selected is None and names:
            selected = names[0]

        router_ready = bool(names)
        adaptive_state = UNKNOWN
        adaptive_reason = REASON_STRUCTURED_UNVERIFIED
        if structured_probe:
            adaptive_state, adaptive_reason = self._structured_check(
                selected, adapter_factory
            )
        readiness = {
            "adaptive_planner": adaptive_state,
            "model_router": READY if router_ready else NOT_READY,
            "hybrid_execution": (
                READY if adaptive_state == READY else adaptive_state
            ),
            "nano_agents": READY,
            "deterministic": READY,
        }
        reasons = {
            "adaptive_planner": adaptive_reason,
            "model_router": REASON_READY if router_ready else REASON_NO_MODEL,
            "hybrid_execution": (
                REASON_READY
                if adaptive_state == READY
                else ("hybrid_dependency_not_ready" if adaptive_state != UNKNOWN else adaptive_reason)
            ),
            "nano_agents": REASON_READY,
            "deterministic": REASON_READY,
        }
        report = LocalReadinessReport(
            runtime_reachable=True,
            models_detected=names,
            selected_model=selected,
            readiness=readiness,
            reasons=reasons,
            probed_at=probed_at,
            structured_probe_run=structured_probe,
            live=live,
        )
        self._last = report
        return report

    def _structured_check(
        self, selected_model: Optional[str],
        adapter_factory: Optional[Callable[[str], Any]],
    ) -> Tuple[str, str]:
        """One tiny structured-response probe through the existing path."""

        if adapter_factory is None or selected_model is None:
            return (UNKNOWN, REASON_STRUCTURED_UNVERIFIED)
        try:
            from ..models.ollama import OllamaAdapter

            adapter = adapter_factory(selected_model)
            if adapter is None:
                return (UNKNOWN, REASON_STRUCTURED_UNVERIFIED)
            from .local_runtime import LocalModelPlannerRuntime
            from .contracts import ModelCallRequest

            runtime = LocalModelPlannerRuntime(
                adapter=adapter,
                timeout_seconds=self._probe_timeout,
                meta_factory=self._meta_factory,
            )
            request = ModelCallRequest(
                meta=self._meta_factory("readiness-probe"),
                request_id="readiness-probe-1",
                provider_ref="local/ollama",
                system_instruction=None,
                messages=('Reply with exactly this JSON object: {"ok": true}',),
                temperature=0.0,
                max_output_tokens=self._probe_tokens,
                timeout_seconds=self._probe_timeout,
                idempotency_key="readiness-probe",
                privacy_mode="offline",
                approval_ref=None,
            )
            parsed = runtime.complete_structured(request)
            if isinstance(parsed, dict) and parsed:
                return (READY, REASON_READY)
            return (NOT_READY, REASON_STRUCTURED_UNVERIFIED)
        except Exception:
            return (NOT_READY, REASON_STRUCTURED_UNVERIFIED)

    # -- cached access -----------------------------------------------------------

    def refresh(self, **kwargs) -> LocalReadinessReport:
        return self.probe(**kwargs)

    def latest(self) -> Optional[LocalReadinessReport]:
        return self._last

    def readiness_map(self) -> Mapping[str, str]:
        report = self._last
        if report is None:
            return {}
        return dict(report.readiness)

    def reason_map(self) -> Mapping[str, str]:
        report = self._last
        if report is None:
            return {}
        return dict(report.reasons)
