"""M1.2 model-backed planner adapter (provider-neutral, stub-driven).

Additive module introduced by the M1.2 Model-Backed Planner Adapter milestone
(zcode/amca-v0.5.0-m1.2-model-backed-planner-adapter-01). Implements the M1
``AdaptivePlanner`` interface on top of AMCA's provider-neutral model-call
envelopes (``ModelCallRequest``/``ModelCallResponse``), so a future local
model runtime can propose rankings without any provider-specific SDK and
without any paid call in this milestone (tests inject fake/stub runtimes).

Authority rules preserved from M0/M1/M1.1:
- the model may only rank candidates that exist in the locally supplied
  candidate set; unknown candidates are rejected and can never be invented
  into existence;
- locally enforced M1.1 hard constraints stay authoritative: model scores are
  re-evaluated through :func:`proposal_engine.propose`, so an inadmissible
  recommendation remains rejected;
- every failure normalizes into the intelligence error taxonomy as a
  recoverable signal (``AdaptivePlannerError``/``AdaptivePlannerUnavailable``)
  so the existing M1 deterministic fallback path takes over; deterministic
  planner failures are never touched here;
- evidence records are bounded and provider-neutral (logical identifiers,
  duration, token usage, cost if supplied, outcome category) and never carry
  API keys, authorization headers, secret prompts, or raw provider traces.

This module is import-isolated: nothing in the pre-existing application
imports it, the adaptive_planner gate remains FALSE, and the adapter is
reachable only from direct instantiation (tests today).
"""

import dataclasses
import json
import math
import time
from typing import Any, Callable, ClassVar, Mapping, Optional, Protocol, Tuple

from .contracts import (
    Contract,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    ExecutionStrategy,
    ModelCallRequest,
    ModelCallResponse,
    PlannerFailure,
    PlannerFailureCategory,
    PlannerInput,
    PlannerResult,
    PlannerResultStatus,
    contract_post_init,
)
from .gates import FeatureGateRegistry
from .planner_selection import AdaptivePlannerError, AdaptivePlannerUnavailable
from .proposal_engine import (
    AdaptiveProposalStatus,
    CandidateRiskClass,
    ProposalCandidate,
    ProposalConstraints,
    propose,
)

#: Usage/keys the model response may carry. Anything else is malformed.
ALLOWED_MODEL_USAGE_KEYS = frozenset(
    {"input_tokens", "output_tokens", "duration_ms", "estimated_cost"}
)

#: v0.5.1: the native structured-output constraint for the adaptive planner
#: prompt/response. Passed as the Ollama ``format`` field through the
#: documented ``ModelRequest.extra`` escape hatch so a supported local model
#: is TOKEN-CONSTRAINED to the approved planner contract (candidate_id,
#: model_id, confidence) plus the bounded score/reason fields. Parsing is
#: unchanged and stays fail-closed: whatever the model returns is still
#: validated by _parse_model_output before any of it becomes authoritative.
PLANNER_RESPONSE_FORMAT: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "response_version": {"type": "integer", "enum": [1]},
        "policy_id": {"type": "string"},
        "policy_version": {"type": "string"},
        "recommendations": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "model_id": {"type": "string"},
                    "confidence": {"type": "number",
                                   "minimum": 0, "maximum": 1},
                    "quality_score": {"type": "number"},
                    "latency_score": {"type": "number"},
                    "cost_score": {"type": "number"},
                    "risk_score": {"type": "number"},
                    "reason_code": {"type": "string"},
                },
                "required": ["candidate_id", "model_id", "confidence"],
            },
        },
    },
    "required": [
        "response_version", "policy_id", "policy_version", "recommendations",
    ],
}

#: The only outcome categories recorded in model-planner evidence.
MODEL_PLANNER_OUTCOMES = frozenset(
    {
        "proposed",
        "blocked",
        "cancelled",
        "provider_error",
        "timeout",
        "malformed_model_result",
    }
)


class ModelCallCancelled(Exception):
    """The model runtime reported cancellation for this call."""


class PlannerModelRuntime(Protocol):
    """Structural seam over AMCA's provider-neutral model call surface."""

    def complete(self, request: ModelCallRequest) -> ModelCallResponse:
        ...


@dataclasses.dataclass(frozen=True)
class ModelRecommendation(Contract):
    """One model-proposed ranking entry for a locally known candidate."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.model_recommendation"

    meta: ContractMetadata
    candidate_id: str
    model_id: str
    confidence: float

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if not 0.0 <= self.confidence <= 1.0:
            raise ContractError(
                ContractErrorCode.INVALID,
                "confidence must be within [0, 1]",
            )


@dataclasses.dataclass(frozen=True)
class ModelPlannerResponse(Contract):
    """Adapter-sealed envelope around one strictly structured model output.

    The model supplies the raw payload fields; the adapter validates them and
    seals them into this versioned record (the model never writes contract
    metadata itself).
    """

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.model_planner_response"

    meta: ContractMetadata
    request_ref: str
    policy_id: str
    policy_version: str
    recommendations: Tuple[ModelRecommendation, ...]
    usage: Mapping[str, Any]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        unexpected = set(self.usage) - ALLOWED_MODEL_USAGE_KEYS
        if unexpected:
            raise ContractError(
                ContractErrorCode.INVALID,
                f"model usage keys outside the whitelist: {sorted(unexpected)}",
            )
        seen = set()
        for recommendation in self.recommendations:
            if recommendation.candidate_id in seen:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    f"duplicate recommendation for candidate "
                    f"{recommendation.candidate_id!r}",
                )
            seen.add(recommendation.candidate_id)


@dataclasses.dataclass(frozen=True)
class ModelPlannerEvidence(Contract):
    """Bounded provider-neutral cost/usage evidence for one planner call."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.model_planner_evidence"

    meta: ContractMetadata
    request_ref: str
    outcome: str
    model_id: Optional[str]
    provider_ref: Optional[str]
    duration_ms: float
    input_tokens: int
    output_tokens: int
    estimated_cost: Optional[float]
    policy_id: str
    policy_version: str
    recommendation_count: int

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.outcome not in MODEL_PLANNER_OUTCOMES:
            raise ContractError(
                ContractErrorCode.INVALID,
                f"unknown model planner outcome {self.outcome!r}",
            )
        if self.duration_ms < 0 or self.input_tokens < 0 or self.output_tokens < 0:
            raise ContractError(
                ContractErrorCode.INVALID,
                "duration and token counts must be non-negative",
            )


def _normalize_runtime_failure(error: Exception) -> Exception:
    """Map a runtime failure onto the intelligence failure taxonomy."""

    if isinstance(error, ModelCallCancelled):
        return AdaptivePlannerError(f"model_call_cancelled: {error}")
    if isinstance(error, AdaptivePlannerError):
        return error
    if isinstance(error, TimeoutError):
        return AdaptivePlannerUnavailable(f"timeout: {error}")
    return AdaptivePlannerUnavailable(f"provider_error: {error}")


def _finite(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _parse_model_output(
    raw_output: Optional[str], meta_factory, request_ref: str
) -> ModelPlannerResponse:
    """Strictly validate one model output payload into a sealed envelope."""

    if not isinstance(raw_output, str) or not raw_output.strip():
        raise AdaptivePlannerError("malformed_model_result: empty output")
    try:
        payload = json.loads(raw_output)
    except ValueError as error:
        raise AdaptivePlannerError(
            f"malformed_model_result: output is not valid JSON ({error})"
        ) from None
    if not isinstance(payload, dict):
        raise AdaptivePlannerError(
            "malformed_model_result: output must be a JSON object"
        )
    version = payload.get("response_version")
    if version != 1:
        raise AdaptivePlannerError(
            f"unknown_response_version: {version!r}"
        )
    raw_recommendations = payload.get("recommendations")
    if not isinstance(raw_recommendations, list):
        raise AdaptivePlannerError(
            "malformed_model_result: recommendations must be an array"
        )
    recommendations = []
    for index, item in enumerate(raw_recommendations):
        if not isinstance(item, dict):
            raise AdaptivePlannerError(
                f"malformed_model_result: recommendation {index} must be an object"
            )
        candidate_id = item.get("candidate_id")
        model_id = item.get("model_id")
        confidence = item.get("confidence")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise AdaptivePlannerError(
                f"malformed_model_result: recommendation {index} candidate_id "
                "must be a non-empty string"
            )
        if not isinstance(model_id, str) or not model_id:
            raise AdaptivePlannerError(
                f"malformed_model_result: recommendation {index} model_id "
                "must be a non-empty string"
            )
        if not _finite(confidence):
            raise AdaptivePlannerError(
                f"malformed_model_result: recommendation {index} confidence "
                "must be a finite number"
            )
        try:
            recommendations.append(
                ModelRecommendation(
                    meta=meta_factory(f"rec-{index}"),
                    candidate_id=candidate_id,
                    model_id=model_id,
                    confidence=float(confidence),
                )
            )
        except ContractError as error:
            raise AdaptivePlannerError(
                f"malformed_model_result: {error.message}"
            ) from None
    usage = payload.get("usage", {})
    if not isinstance(usage, dict):
        raise AdaptivePlannerError(
            "malformed_model_result: usage must be an object"
        )
    for key, value in usage.items():
        if key not in ALLOWED_MODEL_USAGE_KEYS:
            raise AdaptivePlannerError(
                f"malformed_model_result: unknown usage key {key!r}"
            )
        if not _finite(value) or (isinstance(value, float) and value < 0):
            raise AdaptivePlannerError(
                f"malformed_model_result: usage {key!r} must be a "
                "non-negative finite number"
            )
    try:
        return ModelPlannerResponse(
            meta=meta_factory("model-response"),
            request_ref=request_ref,
            policy_id=str(payload.get("policy_id", "unspecified")),
            policy_version=str(payload.get("policy_version", "unspecified")),
            recommendations=tuple(recommendations),
            usage=dict(usage),
        )
    except ContractError as error:
        raise AdaptivePlannerError(
            f"malformed_model_result: {error.message}"
        ) from None


class ModelBackedAdaptivePlanner:
    """Provider-neutral adaptive planner driven by a stubbed model runtime.

    Implements the M1 adaptive interface. Locally enforced constraints stay
    authoritative: model confidence scores are re-evaluated through the M1.1
    proposal engine, so the model can never invent candidates or authorize an
    inadmissible strategy. The instance is passive data-flow only; it never
    executes anything itself.
    """

    def __init__(
        self,
        *,
        runtime: PlannerModelRuntime,
        meta_factory: Callable[[str], ContractMetadata],
        policy_id: str,
        policy_version: str,
    ) -> None:
        self._runtime = runtime
        self._meta_factory = meta_factory
        self.policy_id = policy_id
        self.policy_version = policy_version
        self.last_evidence: Optional[ModelPlannerEvidence] = None

    def propose(
        self,
        planner_input: PlannerInput,
        candidates,
        policy_context,
    ) -> PlannerResult:
        """Propose a ranking for locally known candidates via the runtime."""

        request_ref = planner_input.request_id
        request = ModelCallRequest(
            meta=self._meta_factory("model-request"),
            request_id=request_ref,
            provider_ref="amca.intelligence.stub",
            messages=(self._render_prompt(planner_input, candidates),),
            system_instruction=None,
            temperature=0.0,
            max_output_tokens=1024,
            timeout_seconds=30.0,
            idempotency_key=f"m1.2:{planner_input.meta.correlation_id}",
            privacy_mode="offline",
            approval_ref=None,
        )
        started = time.monotonic()
        try:
            response = self._runtime.complete(request)
        except Exception as error:  # noqa: BLE001 - normalized below
            normalized = _normalize_runtime_failure(error)
            outcome = "cancelled" if "model_call_cancelled" in str(
                normalized
            ) else (
                "timeout" if "timeout" in str(normalized) else "provider_error"
            )
            self._record_evidence(
                request_ref=request_ref,
                outcome=outcome,
                response=None,
                duration_ms=(time.monotonic() - started) * 1000.0,
                parsed=None,
                policy_id=self.policy_id,
                policy_version=self.policy_version,
                recommendation_count=0,
            )
            raise normalized from error

        duration_ms = (time.monotonic() - started) * 1000.0
        try:
            parsed = _parse_model_output(
                response.text_output, self._meta_factory, request_ref
            )
        except AdaptivePlannerError:
            self._record_evidence(
                request_ref=request_ref,
                outcome="malformed_model_result",
                response=response,
                duration_ms=duration_ms,
                parsed=None,
                policy_id=self.policy_id,
                policy_version=self.policy_version,
                recommendation_count=0,
            )
            raise

        proposal, authoritative_strategies = self._evaluate_locally(
            planner_input, candidates, policy_context, parsed
        )
        if proposal.status is AdaptiveProposalStatus.PROPOSED:
            outcome = "proposed"
        else:
            outcome = "blocked"
        self._record_evidence(
            request_ref=request_ref,
            outcome=outcome,
            response=response,
            duration_ms=duration_ms,
            parsed=parsed,
            policy_id=self.policy_id,
            policy_version=self.policy_version,
            recommendation_count=len(parsed.recommendations),
        )
        return self._to_planner_result(
            proposal, planner_input, authoritative_strategies
        )

    # -- internals ---------------------------------------------------------

    def _render_prompt(self, planner_input: PlannerInput, candidates) -> str:
        """Build the deterministic offline prompt payload (never persisted).

        v0.5.1: the prompt now carries the required structured response
        contract so a structured-output-constrained model can fill the
        approved fields exactly (the transport additionally constrains
        generation to this schema; the fail-closed parser is unchanged).
        """

        # The authoritative execution_strategy (if a candidate entry carries
        # one) is LOCAL authority: it never enters the prompt and the model
        # can neither see nor alter it.
        def model_facing(entry):
            return {key: value for key, value in entry.items()
                    if key != "execution_strategy"}

        return json.dumps(
            {
                "objective": planner_input.objective
                if hasattr(planner_input, "objective")
                else "",
                "candidates": [model_facing(dict(item))
                               for item in candidates.candidates],
                "response_contract": {
                    "instruction": (
                        "Respond with ONLY the JSON object described by "
                        "response_format; no other text."
                    ),
                    "response_format": PLANNER_RESPONSE_FORMAT,
                    "policy_id": self.policy_id,
                    "policy_version": self.policy_version,
                },
            },
            sort_keys=True,
        )

    def _local_constraints(self, policy_context) -> ProposalConstraints:
        envelope: Mapping[str, Any] = (
            policy_context.evaluated_envelope if policy_context is not None else {}
        )
        risk_value = envelope.get("max_allowed_risk", "low")
        return ProposalConstraints(
            meta=self._meta_factory("local-constraints"),
            max_total_cost=float(envelope.get("max_total_cost", 0.0)),
            max_work_units=int(envelope.get("max_work_units", 1)),
            allowed_permissions=tuple(envelope.get("allowed_permissions", ())),
            max_allowed_risk=CandidateRiskClass(
                risk_value if isinstance(risk_value, str) else "low"
            ),
            scoring_dimensions=("confidence",),
        )

    def _evaluate_locally(self, planner_input, candidates, policy_context, parsed):
        """Re-enforce local authority over the model's recommendations."""

        local_by_id: dict[str, Mapping[str, Any]] = {}
        for entry in candidates.candidates:
            identifier = entry.get("candidate_id")
            if isinstance(identifier, str) and identifier:
                local_by_id[identifier] = entry
        for recommendation in parsed.recommendations:
            if recommendation.candidate_id not in local_by_id:
                raise AdaptivePlannerError(
                    "unknown_candidate: model recommended "
                    f"{recommendation.candidate_id!r} which is not in the "
                    "local candidate set"
                )

        def to_proposal_candidate(recommendation):
            entry = local_by_id[recommendation.candidate_id]
            risk_value = entry.get("risk_class", "low")
            return ProposalCandidate(
                meta=self._meta_factory(
                    f"pc-{recommendation.candidate_id}"
                ),
                candidate_id=recommendation.candidate_id,
                label=str(entry.get("label", recommendation.candidate_id)),
                collaboration_mode=str(
                    entry.get("collaboration_mode", "adaptive_candidate")
                ),
                synchronization_mode=str(
                    entry.get("synchronization_mode", "sequential")
                ),
                estimated_cost=float(entry.get("estimated_cost", 0.0)),
                work_unit_count=int(entry.get("work_unit_count", 1)),
                required_permissions=tuple(
                    entry.get("required_permissions", ())
                ),
                risk_class=CandidateRiskClass(
                    risk_value if isinstance(risk_value, str) else "low"
                ),
            )

        proposal_candidates = tuple(
            to_proposal_candidate(recommendation)
            for recommendation in parsed.recommendations
        )
        confidence_by_id = {
            recommendation.candidate_id: recommendation.confidence
            for recommendation in parsed.recommendations
        }

        def evaluator(candidate):
            # confidence is higher-is-better; the engine ranks lower-first,
            # so invert deterministically.
            return {"confidence": 1.0 - confidence_by_id[candidate.candidate_id]}

        authoritative_strategies: dict[str, ExecutionStrategy] = {}
        for identifier, entry in local_by_id.items():
            candidate_strategy = entry.get("execution_strategy")
            if isinstance(candidate_strategy, ExecutionStrategy):
                authoritative_strategies[identifier] = candidate_strategy

        proposal = propose(
            meta=self._meta_factory("adaptive-proposal"),
            request_ref=planner_input.request_id,
            candidates=proposal_candidates,
            constraints=self._local_constraints(policy_context),
            evaluator=evaluator,
        )
        return proposal, authoritative_strategies

    def _to_planner_result(
        self,
        proposal,
        planner_input: PlannerInput,
        authoritative_strategies: dict,
    ) -> PlannerResult:
        if proposal.status is AdaptiveProposalStatus.PROPOSED:
            selected = proposal.selected
            # v0.5.1 strategy binding: when the selected candidate carries an
            # authoritative local ExecutionStrategy, propagate it VERBATIM so
            # M2 validates the genuine strategy (work units, mode, sync,
            # rounds, cost, stop condition). The model never authors any of
            # it - it only selects the candidate_id. Candidates without an
            # attached strategy keep the historical proposal-only envelope.
            bound = authoritative_strategies.get(selected.candidate_id)
            if bound is not None:
                strategy = bound
            else:
                strategy = ExecutionStrategy(
                    meta=self._meta_factory("strategy"),
                    planner_kind="model_backed_adaptive",
                    planner_version=self.policy_version,
                    collaboration_mode=selected.collaboration_mode,
                    synchronization_mode=selected.synchronization_mode,
                    work_units=(),
                    max_rounds=1,
                    max_total_cost=selected.estimated_cost,
                    stop_condition="m1.2 proposal-only (no execution authority)",
                    fallback_strategy_ref=None,
                )
            return PlannerResult(
                meta=self._meta_factory("result"),
                request_ref=planner_input.request_id,
                status=PlannerResultStatus.PROPOSED,
                strategy=strategy,
                alternatives=(),
                failure=None,
            )
        return PlannerResult(
            meta=self._meta_factory("result"),
            request_ref=planner_input.request_id,
            status=PlannerResultStatus.BLOCKED,
            strategy=None,
            alternatives=(),
            failure=PlannerFailure(
                meta=self._meta_factory("failure"),
                category=PlannerFailureCategory.NO_FEASIBLE_STRATEGY,
                message="no locally admissible candidate remained",
                retryable=False,
                deterministic_fallback=True,
            ),
        )

    def _record_evidence(
        self,
        *,
        request_ref: str,
        outcome: str,
        response: Optional[ModelCallResponse],
        duration_ms: float,
        parsed: Optional[ModelPlannerResponse],
        policy_id: str,
        policy_version: str,
        recommendation_count: int,
    ) -> None:
        estimated_cost = None
        if parsed is not None:
            raw_cost = parsed.usage.get("estimated_cost")
            if raw_cost is not None and _finite(raw_cost):
                estimated_cost = float(raw_cost)
        self.last_evidence = ModelPlannerEvidence(
            meta=self._meta_factory("evidence"),
            request_ref=request_ref,
            outcome=outcome,
            model_id=response.resolved_model_id if response is not None else None,
            provider_ref=response.provider_ref if response is not None else None,
            duration_ms=duration_ms,
            input_tokens=response.input_tokens if response is not None else 0,
            output_tokens=response.output_tokens if response is not None else 0,
            estimated_cost=estimated_cost,
            policy_id=policy_id,
            policy_version=policy_version,
            recommendation_count=recommendation_count,
        )

    # -- feature-gate context --------------------------------------------

    @staticmethod
    def gates_remain_off(registry: FeatureGateRegistry) -> bool:
        """Read-only helper asserting the shipped default gate state."""

        return registry.to_dict() == {
            "adaptive_planner": False,
            "hybrid_execution": False,
            "nano_agents": False,
            "model_router": False,
        }
