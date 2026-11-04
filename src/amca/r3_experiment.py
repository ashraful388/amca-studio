"""Validation and deterministic costing for the preregistered R3 experiment."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from amca.credentials import CredentialNotFound, desktop_credential_store
from amca.domain import (
    CollaborationMode,
    ComplexityLevel,
    ComputePolicy,
    EventType,
    GoalContract,
    GoalCriterion,
    PrivacyMode,
    ProjectState,
    SyncMode,
)
from amca.kernel import CollaborationKernel, KernelConfig, KernelResult
from amca.model_profiles import ModelProfileStore
from amca.models import AnthropicAdapter, KimiAdapter, ModelAdapter, OpenAIAdapter
from amca.security import assert_no_sensitive_keys
from amca.semantic import is_model_authorable_event_type
from amca.state import StateManager
from amca.storage import SQLiteStore


SCHEMA_VERSION = "1.0.0"
REQUIRED_PROVIDERS = ("openai", "anthropic", "kimi")
REQUIRED_CONDITIONS = ("solo", "joint")
EVIDENCE_SCHEMA_INSTRUCTION = """For criterion_evidence, evidence must be a minified JSON string using the matching neutral schema:
- exact_expectation: {"exact_fraction":"<fraction>","decimal":<number>}
- transition_automaton: {"empty":{"H":"<state>","T":"<state>"},"H":{"H":"<state>","T":"<state>"},"HT":{"H":"<state>","T":"<state>"}}
- first_step_equations: {"E_empty":{"constant":"<fraction>","E_empty":"<fraction>","E_H":"<fraction>"},"E_H":{"constant":"<fraction>","E_H":"<fraction>","E_HT":"<fraction>"},"E_HT":{"constant":"<fraction>","E_empty":"<fraction>"}}
- deterministic_verification: {"values":{"E_empty":"<fraction>","E_H":"<fraction>","E_HT":"<fraction>"},"residuals":{"empty":"<fraction>","H":"<fraction>","HT":"<fraction>"}}
Do not copy placeholders. Derive every value. Round 1 must remain provisional with focus_resolved false. Only after prior semantic evidence is visible may a later round set focus_resolved true and attach evidence for all verified criteria."""
PROVISIONAL_ROUND_INSTRUCTION = """

# Mandatory round-one response bound
This is the provisional round. Return only the required semantic JSON object.
Use a summary of at most 60 characters, exactly one hypothesis event whose
content is at most 100 characters, an empty criterion_evidence array, and
focus_resolved false. Do not solve the task yet and do not reproduce any schema.
"""
PROVISIONAL_ROUND_INSTRUCTION_V2 = """

# Mandatory falsifiable round-one response
This is the provisional round. Return only the required semantic JSON object.
Use a summary of at most 80 characters and exactly one hypothesis event whose
content is at most 160 characters. The hypothesis must state a provisional
exact expectation and one key transition or equation so teammates can check a
specific claim. Use an empty criterion_evidence array and focus_resolved false.
Do not reproduce any schema or include hidden reasoning.
"""
REVIEW_ROUND_INSTRUCTION_V1 = (
    "\n\n# Mandatory review-round evidence format\n"
    + EVIDENCE_SCHEMA_INSTRUCTION
)
REVIEW_ROUND_INSTRUCTION_V2 = REVIEW_ROUND_INSTRUCTION_V1 + """

# Mandatory compact review bound
Return only the semantic JSON object with no surrounding prose or fence. Keep
summary at most 80 characters. Use no more than two events, each at most 120
characters. Include exactly four criterion_evidence entries—one for each
criterion—and keep every evidence string to only the fields in its declared
minified schema. Do not repeat instructions, schemas, derivations, or analysis.
"""
REVIEW_ROUND_INSTRUCTION_V3 = (
    "\n\n# Mandatory evidence-only review\n"
    "Return only one minified JSON object, with no prose or Markdown fence. "
    "Use this exact outer structure and criterion order:\n"
    r'{"summary":"<at most 80 characters>","events":[],'
    r'"criterion_evidence":['
    r'{"criterion_id":"exact_expectation","evidence":"{\"exact_fraction\":'
    r'\"<fraction>\",\"decimal\":<number>}"},'
    r'{"criterion_id":"transition_automaton","evidence":"{\"empty\":'
    r'{\"H\":\"<state>\",\"T\":\"<state>\"},\"H\":{\"H\":'
    r'\"<state>\",\"T\":\"<state>\"},\"HT\":{\"H\":\"<state>\",'
    r'\"T\":\"<state>\"}}"},'
    r'{"criterion_id":"first_step_equations","evidence":"{\"E_empty\":'
    r'{\"constant\":\"<fraction>\",\"E_empty\":\"<fraction>\",\"E_H\":'
    r'\"<fraction>\"},\"E_H\":{\"constant\":\"<fraction>\",\"E_H\":'
    r'\"<fraction>\",\"E_HT\":\"<fraction>\"},\"E_HT\":{\"constant\":'
    r'\"<fraction>\",\"E_empty\":\"<fraction>\"}}"},'
    r'{"criterion_id":"deterministic_verification","evidence":"{\"values\":'
    r'{\"E_empty\":\"<fraction>\",\"E_H\":\"<fraction>\",\"E_HT\":'
    r'\"<fraction>\"},\"residuals\":{\"empty\":\"<fraction>\",\"H\":'
    r'\"<fraction>\",\"HT\":\"<fraction>\"}}"}],'
    r'"focus_resolved":true}'
    "\nReplace every placeholder with a derived value. Do not request tools, add "
    "events or keys, repeat the derivation, or copy any placeholder.\n"
)
REVIEW_ROUND_INSTRUCTION_V4 = (
    "\n\n# Mandatory bounded-correction review\n"
    "Return only one minified JSON object, with no prose or Markdown fence. "
    "Use this exact criterion order. The events array may be empty or contain "
    "exactly one correction or objection event. Use a correction only to fix "
    "your own provisional claim; use an objection only to identify a visible "
    "conflicting claim and the decisive check. Event content must be at most "
    "160 characters and must use severity info, low, or medium, confidence "
    "from 0 to 1, and an empty evidence_refs array. Do not request tools or "
    "add other event types.\n"
    r'{"summary":"<at most 80 characters>","events":[],'
    r'"criterion_evidence":['
    r'{"criterion_id":"exact_expectation","evidence":"{\"exact_fraction\":'
    r'\"<fraction>\",\"decimal\":<number>}"},'
    r'{"criterion_id":"transition_automaton","evidence":"{\"empty\":'
    r'{\"H\":\"<state>\",\"T\":\"<state>\"},\"H\":{\"H\":'
    r'\"<state>\",\"T\":\"<state>\"},\"HT\":{\"H\":\"<state>\",'
    r'\"T\":\"<state>\"}}"},'
    r'{"criterion_id":"first_step_equations","evidence":"{\"E_empty\":'
    r'{\"constant\":\"<fraction>\",\"E_empty\":\"<fraction>\",\"E_H\":'
    r'\"<fraction>\"},\"E_H\":{\"constant\":\"<fraction>\",\"E_H\":'
    r'\"<fraction>\",\"E_HT\":\"<fraction>\"},\"E_HT\":{\"constant\":'
    r'\"<fraction>\",\"E_empty\":\"<fraction>\"}}"},'
    r'{"criterion_id":"deterministic_verification","evidence":"{\"values\":'
    r'{\"E_empty\":\"<fraction>\",\"E_H\":\"<fraction>\",\"E_HT\":'
    r'\"<fraction>\"},\"residuals\":{\"empty\":\"<fraction>\",\"H\":'
    r'\"<fraction>\",\"HT\":\"<fraction>\"}}"}],'
    r'"focus_resolved":true}'
    "\nReplace every placeholder with a derived value. If an event is needed, "
    "replace events:[] with the single bounded event object.\n"
)
REVIEW_ROUND_INSTRUCTION_V5 = REVIEW_ROUND_INSTRUCTION_V4.replace(
    "160 characters", "200 characters"
) + (
    "The final focus_resolved value must remain true; an unresolved review "
    "fails the protocol even if it includes criterion evidence.\n"
)


class R3ManifestError(ValueError):
    """Raised when the R3 preregistration is incomplete or internally unsafe."""


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise R3ManifestError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _positive_int(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise R3ManifestError(f"{label} must be a positive integer")
    return value


def _nonnegative_float(value: object, *, label: str) -> float:
    if isinstance(value, bool):
        raise R3ManifestError(f"{label} must be a finite nonnegative number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise R3ManifestError(
            f"{label} must be a finite nonnegative number"
        ) from exc
    if not math.isfinite(number) or number < 0:
        raise R3ManifestError(f"{label} must be a finite nonnegative number")
    return number


def conservative_cost(document: Mapping[str, Any]) -> float:
    """Return the preregistered maximum token cost without making a call."""

    root = _mapping(document, label="manifest")
    bounds = _mapping(root.get("execution_bounds"), label="execution_bounds")
    input_limit = _positive_int(
        bounds.get("conservative_input_tokens_per_call"),
        label="conservative_input_tokens_per_call",
    )
    output_limit = _positive_int(
        bounds.get("maximum_output_tokens_per_call"),
        label="maximum_output_tokens_per_call",
    )
    participants = {
        str(item.get("id")): _mapping(item, label="participant")
        for item in root.get("participants", [])
        if isinstance(item, Mapping)
    }
    total = 0.0
    for condition_value in root.get("conditions", []):
        condition = _mapping(condition_value, label="condition")
        rounds = _positive_int(condition.get("max_rounds"), label="max_rounds")
        for participant_id in condition.get("participants", []):
            participant = participants.get(str(participant_id))
            if participant is None:
                raise R3ManifestError(f"unknown participant {participant_id!r}")
            input_price = _nonnegative_float(
                participant.get("input_usd_per_million"),
                label="input_usd_per_million",
            )
            output_price = _nonnegative_float(
                participant.get("output_usd_per_million"),
                label="output_usd_per_million",
            )
            total += rounds * (
                input_limit * input_price + output_limit * output_price
            ) / 1_000_000
    return total


def validate_manifest(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the R3 design, provider set, call bounds, and cost arithmetic."""

    root = _mapping(document, label="manifest")
    assert_no_sensitive_keys(root, path="r3_manifest")
    if root.get("schema_version") != SCHEMA_VERSION:
        raise R3ManifestError("unsupported R3 manifest schema")
    if root.get("status") != "preregistered_not_executed":
        raise R3ManifestError("preflight accepts only an unexecuted preregistration")
    prompt_protocol = root.get("round_prompt_protocol")
    if prompt_protocol is not None and prompt_protocol not in {
        "staged_compact_v1",
        "staged_compact_v2",
        "staged_compact_v3",
        "staged_compact_v4",
        "staged_compact_v5",
    }:
        raise R3ManifestError("unsupported staged round-prompt protocol")
    provider_request_protocol = root.get("provider_request_protocol")
    if provider_request_protocol not in {None, "kimi_json_object_v1"}:
        raise R3ManifestError("unsupported provider request protocol")
    if (
        provider_request_protocol == "kimi_json_object_v1"
        and prompt_protocol
        not in {"staged_compact_v3", "staged_compact_v4", "staged_compact_v5"}
    ):
        raise R3ManifestError(
            "Kimi JSON request mode requires a compatible staged protocol"
        )
    review_contract_protocol = root.get("review_contract_protocol")
    if review_contract_protocol not in {
        None,
        "complete_attestations_empty_events_v1",
        "complete_attestations_bounded_correction_v1",
        "complete_attestations_bounded_correction_v2",
    }:
        raise R3ManifestError("unsupported review contract protocol")
    if (
        review_contract_protocol == "complete_attestations_empty_events_v1"
        and prompt_protocol != "staged_compact_v3"
    ):
        raise R3ManifestError(
            "the exact evidence-only contract requires staged compact v3"
        )
    if (
        review_contract_protocol
        == "complete_attestations_bounded_correction_v1"
        and prompt_protocol != "staged_compact_v4"
    ):
        raise R3ManifestError(
            "the bounded-correction contract requires staged compact v4"
        )
    if (
        review_contract_protocol
        == "complete_attestations_bounded_correction_v2"
        and prompt_protocol != "staged_compact_v5"
    ):
        raise R3ManifestError(
            "the resolved bounded-correction v2 contract requires staged compact v5"
        )

    participant_values = root.get("participants")
    if not isinstance(participant_values, list):
        raise R3ManifestError("participants must be an array")
    participants = [_mapping(item, label="participant") for item in participant_values]
    provider_ids = tuple(str(item.get("id")) for item in participants)
    providers = tuple(str(item.get("provider")) for item in participants)
    if provider_ids != REQUIRED_PROVIDERS or providers != REQUIRED_PROVIDERS:
        raise R3ManifestError("R3 requires exact OpenAI, Anthropic, and Kimi participants")

    condition_values = root.get("conditions")
    if not isinstance(condition_values, list):
        raise R3ManifestError("conditions must be an array")
    conditions = [_mapping(item, label="condition") for item in condition_values]
    if tuple(str(item.get("id")) for item in conditions) != REQUIRED_CONDITIONS:
        raise R3ManifestError("R3 requires Solo then Joint conditions")
    solo, joint = conditions
    if solo.get("mode") != "solo" or solo.get("participants") != ["kimi"]:
        raise R3ManifestError("Solo must use the preregistered Kimi comparator")
    if joint.get("mode") != "joint" or joint.get("participants") != list(
        REQUIRED_PROVIDERS
    ):
        raise R3ManifestError("Joint must use GPT, Claude, and Kimi")
    if joint.get("sync_mode") != "bounded_staleness":
        raise R3ManifestError("Joint must exercise bounded staleness")

    expected_calls = 0
    condition_budget_total = 0.0
    for condition in conditions:
        rounds = _positive_int(condition.get("max_rounds"), label="max_rounds")
        participants_in_condition = condition.get("participants")
        if not isinstance(participants_in_condition, list) or not participants_in_condition:
            raise R3ManifestError("each condition requires participants")
        calculated_calls = rounds * len(participants_in_condition)
        declared_calls = _positive_int(condition.get("max_calls"), label="max_calls")
        if declared_calls != calculated_calls:
            raise R3ManifestError("condition max_calls does not match rounds × participants")
        expected_calls += calculated_calls
        condition_budget_total += _nonnegative_float(
            condition.get("api_budget_usd"), label="api_budget_usd"
        )

    bounds = _mapping(root.get("execution_bounds"), label="execution_bounds")
    if bounds.get("automatic_retries") != 0 or bounds.get("rebase_calls") != 0:
        raise R3ManifestError("R3 preflight forbids retries and rebase calls")
    if bounds.get("maximum_total_calls") != expected_calls:
        raise R3ManifestError("maximum_total_calls does not match the condition plan")
    ceiling = _nonnegative_float(
        bounds.get("maximum_total_api_cost_usd"),
        label="maximum_total_api_cost_usd",
    )
    if not math.isclose(condition_budget_total, ceiling, abs_tol=1e-12):
        raise R3ManifestError("condition budgets do not sum to the total API ceiling")
    calculated_cost = conservative_cost(root)
    declared_cost = _nonnegative_float(
        bounds.get("conservative_preflight_cost_usd"),
        label="conservative_preflight_cost_usd",
    )
    if not math.isclose(calculated_cost, declared_cost, abs_tol=1e-12):
        raise R3ManifestError("declared conservative cost does not match provider prices")
    if calculated_cost > ceiling:
        raise R3ManifestError("conservative cost exceeds the API ceiling")
    return root


def load_manifest(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise R3ManifestError(f"R3 manifest is invalid JSON: {exc.msg}") from exc
    return validate_manifest(_mapping(value, label="manifest"))


def _fraction(value: object) -> Fraction | None:
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None
    return result if abs(result.denominator) <= 1_000_000 else None


def _evidence_documents(attestations: Mapping[str, str]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for evidence in attestations.values():
        try:
            value = json.loads(evidence)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, Mapping):
            documents.append({str(key): item for key, item in value.items()})
    return documents


def _exact_evidence(value: Mapping[str, Any]) -> bool:
    exact = _fraction(value.get("exact_fraction"))
    decimal = value.get("decimal")
    return bool(
        exact == Fraction(33, 4)
        and isinstance(decimal, (int, float))
        and not isinstance(decimal, bool)
        and math.isclose(float(decimal), 8.25, abs_tol=1e-12)
    )


def _transition_evidence(value: Mapping[str, Any]) -> bool:
    expected = {
        "empty": {"H": "H", "T": "empty"},
        "H": {"H": "H", "T": "HT"},
        "HT": {"H": "done", "T": "empty"},
    }
    return value == expected


def _equation_evidence(value: Mapping[str, Any]) -> bool:
    expected = {
        "E_empty": {"constant": "1", "E_empty": "1/3", "E_H": "2/3"},
        "E_H": {"constant": "1", "E_H": "2/3", "E_HT": "1/3"},
        "E_HT": {"constant": "1", "E_empty": "1/3"},
    }
    if set(value) != set(expected):
        return False
    for state, coefficients in expected.items():
        actual = value.get(state)
        if not isinstance(actual, Mapping) or set(actual) != set(coefficients):
            return False
        if any(
            _fraction(actual.get(name)) != _fraction(coefficient)
            for name, coefficient in coefficients.items()
        ):
            return False
    return True


def _verification_evidence(value: Mapping[str, Any]) -> bool:
    values = value.get("values")
    residuals = value.get("residuals")
    if not isinstance(values, Mapping) or not isinstance(residuals, Mapping):
        return False
    expected_values = {
        "E_empty": Fraction(33, 4),
        "E_H": Fraction(27, 4),
        "E_HT": Fraction(15, 4),
    }
    if any(
        _fraction(values.get(name)) != expected
        for name, expected in expected_values.items()
    ):
        return False
    if set(residuals) != {"empty", "H", "HT"}:
        return False
    return all(_fraction(residuals.get(name)) == 0 for name in residuals)


def deterministic_criterion_evaluator(
    state: ProjectState,
    criterion: GoalCriterion,
    attestations: Mapping[str, str],
) -> str | None:
    """Verify model attestations without exposing the reference in prompts."""

    if len(state.synchronization_checkpoints) < 2:
        return None
    validators = {
        "exact_expectation": _exact_evidence,
        "transition_automaton": _transition_evidence,
        "first_step_equations": _equation_evidence,
        "deterministic_verification": _verification_evidence,
    }
    validator = validators.get(criterion.id)
    if validator is None:
        return None
    if any(validator(document) for document in _evidence_documents(attestations)):
        return f"Deterministic R3 evaluator verified {criterion.id}."
    return None


class _BoundedKimiAdapter(KimiAdapter):
    """Keep K2.6 inside the structured-output budget by disabling thinking."""

    def __init__(
        self, *args: Any, json_mode: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._json_mode = bool(json_mode)

    def _payload(self, request, *, stream: bool):
        payload = super()._payload(request, stream=stream)
        payload["thinking"] = {"type": "disabled"}
        payload["temperature"] = 0.6
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload


def _matching_profiles(
    document: Mapping[str, Any], directory: Path
) -> dict[str, ModelAdapter]:
    profiles = ModelProfileStore(directory / "amca.sqlite3").list()
    saved = desktop_credential_store(directory)
    adapters: dict[str, ModelAdapter] = {}
    for raw in document["participants"]:
        participant = _mapping(raw, label="participant")
        matching = [
            profile
            for profile in profiles
            if profile.provider_kind == participant["provider"]
            and profile.model == participant["model"]
            and profile.endpoint == str(participant["endpoint"]).rstrip("/")
            and profile.pricing_basis == "token"
            and math.isclose(
                profile.input_cost_per_million,
                float(participant["input_usd_per_million"]),
                abs_tol=1e-12,
            )
            and math.isclose(
                profile.output_cost_per_million,
                float(participant["output_usd_per_million"]),
                abs_tol=1e-12,
            )
        ]
        configured = []
        for profile in matching:
            try:
                saved.get(profile.profile_id)
            except CredentialNotFound:
                continue
            configured.append(profile)
        if len(configured) != 1:
            raise R3ManifestError(
                f"participant {participant['id']!r} requires exactly one "
                "matching configured profile"
            )
        profile = configured[0]
        key = saved.get(profile.profile_id)
        shared = {
            "model": profile.model,
            "adapter_id": f"r3:{participant['id']}",
            "api_key": key,
            "base_url": profile.endpoint,
            "timeout": 120.0,
            "input_cost_per_million": profile.input_cost_per_million,
            "output_cost_per_million": profile.output_cost_per_million,
        }
        if participant["provider"] == "openai":
            adapter = OpenAIAdapter(**shared)
        elif participant["provider"] == "anthropic":
            adapter = AnthropicAdapter(**shared, max_retries=0)
        elif participant["provider"] == "kimi":
            adapter = _BoundedKimiAdapter(
                **shared,
                json_mode=(
                    document.get("provider_request_protocol")
                    == "kimi_json_object_v1"
                ),
            )
        else:  # pragma: no cover - rejected by manifest validation.
            raise R3ManifestError("unsupported R3 participant")
        adapters[str(participant["id"])] = adapter
    return adapters


def _project_state(
    document: Mapping[str, Any], condition: Mapping[str, Any], project_id: str
) -> ProjectState:
    task = _mapping(document["task"], label="task")
    criteria = [
        GoalCriterion(
            id=str(item["id"]),
            description=str(item["description"]),
        )
        for item in document["criteria"]
    ]
    goal = str(task["active_focus"])
    return ProjectState(
        project_id=project_id,
        goal=goal,
        goal_contract=GoalContract(
            goal=goal,
            criteria=criteria,
            max_iterations=int(condition["max_rounds"]),
            max_runtime_seconds=float(
                document["execution_bounds"][
                    "maximum_runtime_seconds_per_condition"
                ]
            ),
            max_api_cost_usd=float(condition["api_budget_usd"]),
        ),
        current_focus=goal,
        active_subproblem=goal,
        complexity=ComplexityLevel.HIGH,
        privacy_mode=PrivacyMode.FULL_FRONTIER,
        compute_policy=ComputePolicy.MAXIMUM_INTELLIGENCE,
        user_constraints=(
            [str(task["round_protocol"])]
            if str(document.get("round_prompt_protocol", "")).startswith(
                "staged_compact_"
            )
            else [str(task["round_protocol"]), EVIDENCE_SCHEMA_INSTRUCTION]
        ),
    )


def _staged_round_prompt_v1(round_number: int, _state: ProjectState) -> str:
    return (
        PROVISIONAL_ROUND_INSTRUCTION
        if round_number == 1
        else REVIEW_ROUND_INSTRUCTION_V1
    )


def _staged_round_prompt_v2(round_number: int, _state: ProjectState) -> str:
    return (
        PROVISIONAL_ROUND_INSTRUCTION
        if round_number == 1
        else REVIEW_ROUND_INSTRUCTION_V2
    )


def _staged_round_prompt_v3(round_number: int, _state: ProjectState) -> str:
    return (
        PROVISIONAL_ROUND_INSTRUCTION
        if round_number == 1
        else REVIEW_ROUND_INSTRUCTION_V3
    )


def _staged_round_prompt_v4(round_number: int, _state: ProjectState) -> str:
    return (
        PROVISIONAL_ROUND_INSTRUCTION_V2
        if round_number == 1
        else REVIEW_ROUND_INSTRUCTION_V4
    )


def _staged_round_prompt_v5(round_number: int, _state: ProjectState) -> str:
    return (
        PROVISIONAL_ROUND_INSTRUCTION_V2
        if round_number == 1
        else REVIEW_ROUND_INSTRUCTION_V5
    )


def _round_prompt_callback(document: Mapping[str, Any]):
    return {
        "staged_compact_v1": _staged_round_prompt_v1,
        "staged_compact_v2": _staged_round_prompt_v2,
        "staged_compact_v3": _staged_round_prompt_v3,
        "staged_compact_v4": _staged_round_prompt_v4,
        "staged_compact_v5": _staged_round_prompt_v5,
    }.get(document.get("round_prompt_protocol"))


def _review_evidence_audit(
    condition: Mapping[str, Any],
    result: KernelResult,
    *,
    events: list[Any],
    provider_execution_complete: bool,
    round_prompt_protocol: object,
    review_contract_protocol: object = None,
) -> dict[str, Any]:
    """Fail closed when a staged review violates its versioned contract."""

    if not str(round_prompt_protocol or "").startswith("staged_compact_"):
        return {
            "required": False,
            "evaluable": True,
            "compliant": True,
            "missing": {},
            "unexpected_events": {},
            "event_contract_violations": {},
        }
    if not provider_execution_complete:
        return {
            "required": True,
            "evaluable": False,
            "compliant": None,
            "missing": {},
            "unexpected_events": {},
            "event_contract_violations": {},
        }
    contract = result.state.goal_contract
    criterion_ids = [
        criterion.id for criterion in (contract.criteria if contract else [])
    ]
    missing: dict[str, list[str]] = {}
    for participant in condition["participants"]:
        source = f"r3:{participant}"
        absent = [
            criterion_id
            for criterion_id in criterion_ids
            if source not in result.criterion_attestations.get(criterion_id, {})
        ]
        if absent:
            missing[source] = absent
    unexpected_events: dict[str, dict[str, int]] = {}
    event_contract_violations: dict[str, dict[str, int]] = {}
    if (
        round_prompt_protocol == "staged_compact_v3"
        or review_contract_protocol
        == "complete_attestations_empty_events_v1"
    ):
        participant_sources = {
            f"r3:{participant}" for participant in condition["participants"]
        }
        checkpoint_count = 0
        for event in events:
            if event.event_type is EventType.SYNC_CHECKPOINT_CLOSED:
                checkpoint_count += 1
                continue
            if checkpoint_count == 1 and event.source in participant_sources:
                if not is_model_authorable_event_type(event.event_type):
                    continue
                by_type = unexpected_events.setdefault(event.source, {})
                event_type = event.event_type.value
                by_type[event_type] = by_type.get(event_type, 0) + 1
    elif review_contract_protocol in {
        "complete_attestations_bounded_correction_v1",
        "complete_attestations_bounded_correction_v2",
    }:
        event_content_limit = (
            200
            if review_contract_protocol
            == "complete_attestations_bounded_correction_v2"
            else 160
        )
        participant_sources = {
            f"r3:{participant}" for participant in condition["participants"]
        }
        if (
            review_contract_protocol
            == "complete_attestations_bounded_correction_v2"
        ):
            for source in participant_sources:
                unresolved = sum(
                    call.round_number == 2
                    and call.adapter_id == source
                    and not call.focus_resolved
                    for call in result.call_records
                )
                if unresolved:
                    event_contract_violations.setdefault(source, {})[
                        "focus_not_resolved"
                    ] = unresolved
        checkpoint_count = 0
        review_events: dict[str, list[Any]] = {}
        for event in events:
            if event.event_type is EventType.SYNC_CHECKPOINT_CLOSED:
                checkpoint_count += 1
                continue
            if checkpoint_count == 1 and event.source in participant_sources:
                if not is_model_authorable_event_type(event.event_type):
                    continue
                review_events.setdefault(event.source, []).append(event)
        allowed_types = {EventType.CORRECTION, EventType.OBJECTION}
        for source, authored in review_events.items():
            violations = event_contract_violations.setdefault(source, {})
            if len(authored) > 1:
                violations["event_count_over_1"] = len(authored)
            for event in authored:
                if event.event_type not in allowed_types:
                    by_type = unexpected_events.setdefault(source, {})
                    event_type = event.event_type.value
                    by_type[event_type] = by_type.get(event_type, 0) + 1
                if len(str(event.payload.get("content", ""))) > event_content_limit:
                    key = f"content_over_{event_content_limit}_chars"
                    violations[key] = (
                        violations.get(key, 0) + 1
                    )
                if event.severity.value not in {"info", "low", "medium"}:
                    violations["severity_not_allowed"] = (
                        violations.get("severity_not_allowed", 0) + 1
                    )
                if event.payload.get("confidence") is None:
                    violations["confidence_missing"] = (
                        violations.get("confidence_missing", 0) + 1
                    )
                if event.payload.get("evidence_refs"):
                    violations["evidence_refs_not_empty"] = (
                        violations.get("evidence_refs_not_empty", 0) + 1
                    )
            if not violations:
                event_contract_violations.pop(source)
    return {
        "required": True,
        "evaluable": True,
        "compliant": (
            not missing
            and not unexpected_events
            and not event_contract_violations
        ),
        "missing": missing,
        "unexpected_events": unexpected_events,
        "event_contract_violations": event_contract_violations,
    }


def _condition_report(
    condition: Mapping[str, Any],
    result: KernelResult,
    *,
    wall_seconds: float,
    events: list[Any],
    round_prompt_protocol: object = None,
    review_contract_protocol: object = None,
) -> dict[str, Any]:
    calls_by_round: dict[int, list[float]] = {}
    for call in result.call_records:
        calls_by_round.setdefault(call.round_number, []).append(call.elapsed_seconds)
    concurrent_floor = sum(max(values) for values in calls_by_round.values() if values)
    event_counts = {
        event_type.value: sum(event.event_type is event_type for event in events)
        for event_type in (
            EventType.OBJECTION,
            EventType.CORRECTION,
            EventType.EVIDENCE,
            EventType.TOOL_REQUEST,
            EventType.TOOL_RESULT,
        )
    }
    contract = result.state.goal_contract
    criteria = {
        criterion.id: criterion.status.value
        for criterion in (contract.criteria if contract else [])
    }
    complete_calls = sum(call.status == "complete" for call in result.call_records)
    expected_calls = int(condition["max_calls"])
    provider_execution_complete = complete_calls == expected_calls
    protocol_audit = _review_evidence_audit(
        condition,
        result,
        events=events,
        provider_execution_complete=provider_execution_complete,
        round_prompt_protocol=round_prompt_protocol,
        review_contract_protocol=review_contract_protocol,
    )
    return {
        "condition": condition["id"],
        "mode": condition["mode"],
        "participants": list(condition["participants"]),
        "execution_complete": provider_execution_complete
        and protocol_audit["compliant"] is True,
        "provider_execution_complete": provider_execution_complete,
        "review_protocol_required": protocol_audit["required"],
        "review_protocol_evaluable": protocol_audit["evaluable"],
        "review_protocol_compliant": protocol_audit["compliant"],
        "missing_review_attestations": protocol_audit["missing"],
        "unexpected_review_events": protocol_audit["unexpected_events"],
        "review_event_contract_violations": protocol_audit.get(
            "event_contract_violations", {}
        ),
        "expected_calls": expected_calls,
        "actual_calls": len(result.call_records),
        "complete_calls": complete_calls,
        "rounds_completed": result.rounds_completed,
        "synchronization_passed": result.synchronization_passed,
        "stop_reason": result.stop_reason.value,
        "correct": bool(contract and contract.satisfied),
        "criterion_completion": sum(value == "pass" for value in criteria.values()),
        "criterion_total": len(criteria),
        "criteria": criteria,
        "unresolved_objections": len(result.state.unresolved_disagreements),
        "event_counts": event_counts,
        "cognitive_revisions": result.state.cognitive_revision,
        "verified_evidence": sum(
            record.verified for record in result.state.evidence_records.values()
        ),
        "latency_seconds": round(wall_seconds, 6),
        "orchestration_overhead_seconds": round(
            max(0.0, wall_seconds - concurrent_floor), 6
        ),
        "input_tokens": sum(call.input_tokens for call in result.call_records),
        "output_tokens": sum(call.output_tokens for call in result.call_records),
        "api_cost_usd": sum(call.cost_usd for call in result.call_records),
        "maximum_cognitive_lag": max(
            (call.cognitive_lag for call in result.call_records), default=0
        ),
        "usage_uncertain": any(call.usage_uncertain for call in result.call_records),
        "calls": [
            {
                "adapter_id": call.adapter_id,
                "round": call.round_number,
                "status": call.status,
                "input_tokens": call.input_tokens,
                "output_tokens": call.output_tokens,
                "cost_usd": call.cost_usd,
                "elapsed_seconds": call.elapsed_seconds,
                "staleness": call.staleness.value,
                "cognitive_lag": call.cognitive_lag,
                "rebase_calls": call.rebase_calls,
                "error_class": call.error.split(":", 1)[0] if call.error else None,
                "summary": call.summary,
                "focus_resolved": call.focus_resolved,
            }
            for call in result.call_records
        ],
    }


async def run_experiment(
    *,
    manifest: str | Path,
    database: str | Path,
    output: str | Path,
    profile_directory: str | Path | None = None,
    adapters: Mapping[str, ModelAdapter] | None = None,
) -> dict[str, Any]:
    """Execute the frozen Solo then Joint comparison with no retries or rebases."""

    document = load_manifest(manifest)
    if adapters is None:
        directory = (
            Path(profile_directory)
            if profile_directory is not None
            else Path(os.environ["APPDATA"]) / "ai.amca.desktop"
        )
        selected = _matching_profiles(document, directory.resolve())
    else:
        selected = dict(adapters)
    if set(selected) != set(REQUIRED_PROVIDERS):
        raise R3ManifestError(
            "execution requires exact OpenAI, Anthropic, and Kimi adapters"
        )

    run_id = uuid4().hex[:12]
    condition_reports: list[dict[str, Any]] = []
    database_path = Path(database)
    for raw_condition in document["conditions"]:
        condition = _mapping(raw_condition, label="condition")
        project_id = f"r3-{condition['id']}-{run_id}"
        state = _project_state(document, condition, project_id)
        condition_adapters = tuple(
            selected[name] for name in condition["participants"]
        )
        started = time.perf_counter()
        with SQLiteStore(database_path) as store:
            manager = StateManager(state, store=store)
            manager.save()
            kernel = CollaborationKernel(
                manager,
                condition_adapters,
                criterion_evaluator=deterministic_criterion_evaluator,
                round_prompt_addendum=_round_prompt_callback(document),
                config=KernelConfig(
                    max_rounds=int(condition["max_rounds"]),
                    max_staleness_versions=int(
                        condition.get("max_cognitive_lag", 32)
                    ),
                    model_timeout_seconds=120.0,
                    rebase_stale_responses=False,
                    bounded_staleness_base_mode=SyncMode(
                        condition.get("bounded_staleness_base", "quorum")
                    ),
                    max_output_tokens_per_call=int(
                        document["execution_bounds"][
                            "maximum_output_tokens_per_call"
                        ]
                    ),
                ),
            )
            result = await kernel.run(
                mode=CollaborationMode(condition["mode"]),
                sync_mode=SyncMode(condition["sync_mode"]),
            )
            events = store.load_events(project_id, limit=2_000)
        report = _condition_report(
            condition,
            result,
            wall_seconds=time.perf_counter() - started,
            events=events,
            round_prompt_protocol=document.get("round_prompt_protocol"),
            review_contract_protocol=document.get("review_contract_protocol"),
        )
        condition_reports.append(report)
        if not report["execution_complete"]:
            break

    total_cost = sum(float(item["api_cost_usd"]) for item in condition_reports)
    ceiling = float(document["execution_bounds"]["maximum_total_api_cost_usd"])
    if total_cost > ceiling + 1e-12:
        raise RuntimeError(
            "R3 provider-reported cost exceeded the hard aggregate ceiling"
        )
    solo = condition_reports[0] if condition_reports else None
    joint = condition_reports[1] if len(condition_reports) > 1 else None
    comparison_evaluable = bool(
        solo is not None
        and joint is not None
        and solo["execution_complete"]
        and joint["execution_complete"]
    )
    comparison = None
    if comparison_evaluable:
        assert solo is not None and joint is not None
        improvement = int(joint["criterion_completion"]) - int(
            solo["criterion_completion"]
        )
        comparison = {
            "criterion_completion_delta_joint_minus_solo": improvement,
            "correctness_changed": bool(joint["correct"]) != bool(solo["correct"]),
            "cost_delta_usd": float(joint["api_cost_usd"])
            - float(solo["api_cost_usd"]),
            "latency_delta_seconds": float(joint["latency_seconds"])
            - float(solo["latency_seconds"]),
            "material_correction_or_improvement": bool(
                improvement > 0
                and (
                    joint["event_counts"]["correction"] > 0
                    or joint["event_counts"]["objection"] > 0
                )
            ),
            "general_superiority_claimed": False,
        }
    provider_failure = any(
        not item["provider_execution_complete"] for item in condition_reports
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": document["experiment_id"],
        "review_contract_protocol": document.get("review_contract_protocol"),
        "comparison_evaluable": comparison_evaluable,
        "run_id": run_id,
        "status": (
            "completed"
            if len(condition_reports) == 2
            and all(item["execution_complete"] for item in condition_reports)
            else (
                "partial_provider_failure"
                if provider_failure
                else "partial_protocol_failure"
            )
        ),
        "maximum_total_calls": document["execution_bounds"][
            "maximum_total_calls"
        ],
        "actual_calls": sum(int(item["actual_calls"]) for item in condition_reports),
        "automatic_retries": 0,
        "rebase_calls": sum(
            int(call["rebase_calls"])
            for item in condition_reports
            for call in item["calls"]
        ),
        "authorized_api_cost_usd": ceiling,
        "accounted_api_cost_usd": total_cost,
        "conditions": condition_reports,
        "comparison": comparison,
        "credential_persisted": False,
    }
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile-directory", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        document = load_manifest(args.manifest)
        if not args.execute:
            print(
                json.dumps(
                    {
                        "status": "preflight_passed",
                        "maximum_total_calls": document["execution_bounds"][
                            "maximum_total_calls"
                        ],
                        "maximum_total_api_cost_usd": document[
                            "execution_bounds"
                        ]["maximum_total_api_cost_usd"],
                        "conservative_preflight_cost_usd": conservative_cost(
                            document
                        ),
                        "network_started": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.database is None or args.output is None:
            parser.error("--execute requires --database and --output")
        report = asyncio.run(
            run_experiment(
                manifest=args.manifest,
                database=args.database,
                output=args.output,
                profile_directory=args.profile_directory,
            )
        )
    except BaseException as exc:
        print(f"R3 experiment failed safely: {type(exc).__name__}")
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "completed" else 2


__all__ = [
    "R3ManifestError",
    "SCHEMA_VERSION",
    "conservative_cost",
    "deterministic_criterion_evaluator",
    "load_manifest",
    "run_experiment",
    "validate_manifest",
]


if __name__ == "__main__":
    raise SystemExit(main())
