"""Offline benchmark loading, execution, and criterion-evaluator plumbing.

The runner deliberately separates orchestration from quality evaluation. It
uses the real collaboration kernel with local scripted participants, while a
deterministic evaluator checks only fixture predicates that can be implemented
without network access, model judging, or unbounded code execution.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from .domain import (
    CollaborationMode,
    ComputePolicy,
    GoalContract,
    GoalCriterion,
    PrivacyMode,
    ProjectState,
    SyncMode,
)
from .kernel import CollaborationKernel, KernelConfig
from .models import ScriptedAdapter
from .state import StateManager
from .storage import SQLiteStore
from .tools.python_sandbox import (
    PythonSandboxReport,
    SandboxPolicyError,
    run_python_function_cases,
)


RUNNER_SCHEMA_VERSION = "1.0.0"
EVALUATOR_VERSION = "deterministic-fixture-v2"
MAXIMUM_SUITE_BYTES = 8 * 1024 * 1024
MAXIMUM_RESPONSE_ARTIFACT_BYTES = 32 * 1024 * 1024
_MISSING = object()


class BenchmarkFormatError(ValueError):
    """Raised when a fixture or response-artifact file is malformed."""


class ExternalModelEvaluationUnsupported(RuntimeError):
    """Raised when model-as-judge evaluation is requested in this milestone."""


class CriterionStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_RUN = "not_run"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class BenchmarkTask:
    task_id: str
    version: int
    domain: str
    title: str
    prompt: str
    response_contract: Mapping[str, Any]
    criteria: tuple[Mapping[str, Any], ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class BenchmarkSuite:
    suite_id: str
    schema_version: str
    title: str
    evaluation_policy: Mapping[str, Any]
    tasks: tuple[BenchmarkTask, ...]

    def task(self, task_id: str) -> BenchmarkTask | None:
        return next((task for task in self.tasks if task.task_id == task_id), None)


@dataclass(frozen=True, slots=True)
class CriterionOutcome:
    criterion_id: str
    kind: str
    required: bool
    weight: float
    status: CriterionStatus
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "kind": self.kind,
            "required": self.required,
            "weight": self.weight,
            "status": self.status.value,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkEvaluation:
    task_id: str
    status: str
    score: float
    maximum_score: float
    required_criterion_failed: bool
    response_contract_valid: bool | None
    missing_response_fields: tuple[str, ...]
    candidate_sha256: str | None
    outcomes: tuple[CriterionOutcome, ...]
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "score": self.score,
            "maximum_score": self.maximum_score,
            "required_criterion_failed": self.required_criterion_failed,
            "response_contract_valid": self.response_contract_valid,
            "missing_response_fields": list(self.missing_response_fields),
            "candidate_sha256": self.candidate_sha256,
            "criteria": [outcome.to_dict() for outcome in self.outcomes],
            "limitations": list(self.limitations),
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BenchmarkFormatError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def load_benchmark_suite(source: str | Path) -> BenchmarkSuite:
    """Load and validate a machine-readable AMCA benchmark suite."""

    path = Path(source)
    if path.stat().st_size > MAXIMUM_SUITE_BYTES:
        raise BenchmarkFormatError(
            f"benchmark file exceeds the {MAXIMUM_SUITE_BYTES}-byte limit"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkFormatError(
            f"benchmark file is not valid JSON: {exc.msg}"
        ) from exc
    root = _mapping(document, label="benchmark document")
    raw_tasks = root.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise BenchmarkFormatError("benchmark document requires a non-empty tasks array")

    tasks: list[BenchmarkTask] = []
    identifiers: set[str] = set()
    for index, raw_task in enumerate(raw_tasks):
        task = _mapping(raw_task, label=f"task {index}")
        task_id = str(task.get("task_id", "")).strip()
        if not task_id:
            raise BenchmarkFormatError(f"task {index} requires task_id")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", task_id):
            raise BenchmarkFormatError(
                f"task id {task_id!r} is not a safe bounded identifier"
            )
        if task_id in identifiers:
            raise BenchmarkFormatError(f"duplicate benchmark task id {task_id!r}")
        identifiers.add(task_id)
        prompt = str(task.get("prompt", "")).strip()
        if not prompt:
            raise BenchmarkFormatError(f"task {task_id!r} requires a prompt")
        response_contract = _mapping(
            task.get("response_contract"),
            label=f"task {task_id!r} response_contract",
        )
        raw_criteria = task.get("criteria")
        if not isinstance(raw_criteria, list) or not raw_criteria:
            raise BenchmarkFormatError(f"task {task_id!r} requires criteria")
        criteria: list[Mapping[str, Any]] = []
        criterion_ids: set[str] = set()
        total_weight = 0.0
        for criterion_index, raw_criterion in enumerate(raw_criteria):
            criterion = _mapping(
                raw_criterion,
                label=f"task {task_id!r} criterion {criterion_index}",
            )
            criterion_id = str(criterion.get("criterion_id", "")).strip()
            kind = str(criterion.get("kind", "")).strip()
            if not criterion_id or not kind:
                raise BenchmarkFormatError(
                    f"task {task_id!r} criteria require criterion_id and kind"
                )
            if criterion_id in criterion_ids:
                raise BenchmarkFormatError(
                    f"task {task_id!r} has duplicate criterion {criterion_id!r}"
                )
            criterion_ids.add(criterion_id)
            weight = criterion.get("weight")
            if (
                not isinstance(weight, (int, float))
                or isinstance(weight, bool)
                or not math.isfinite(float(weight))
                or float(weight) < 0
            ):
                raise BenchmarkFormatError(
                    f"task {task_id!r} criterion {criterion_id!r} has invalid weight"
                )
            total_weight += float(weight)
            criteria.append(dict(criterion))
        if not math.isclose(total_weight, 100.0, abs_tol=1e-9):
            raise BenchmarkFormatError(
                f"task {task_id!r} criterion weights total {total_weight}, not 100"
            )
        tasks.append(
            BenchmarkTask(
                task_id=task_id,
                version=int(task.get("version", 1)),
                domain=str(task.get("domain", "")),
                title=str(task.get("title", task_id)),
                prompt=prompt,
                response_contract=dict(response_contract),
                criteria=tuple(criteria),
                raw=dict(task),
            )
        )

    declared_count = root.get("task_count")
    if declared_count is not None and int(declared_count) != len(tasks):
        raise BenchmarkFormatError(
            f"declared task_count {declared_count!r} does not match {len(tasks)} tasks"
        )
    return BenchmarkSuite(
        suite_id=str(root.get("suite_id", path.stem)),
        schema_version=str(root.get("schema_version", "unknown")),
        title=str(root.get("title", path.stem)),
        evaluation_policy=dict(
            _mapping(root.get("evaluation_policy", {}), label="evaluation_policy")
        ),
        tasks=tuple(tasks),
    )


def load_response_artifacts(
    source: str | Path,
    *,
    valid_task_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Load offline candidate artifacts keyed by benchmark task id."""

    path = Path(source)
    if path.stat().st_size > MAXIMUM_RESPONSE_ARTIFACT_BYTES:
        raise BenchmarkFormatError(
            "response-artifact file exceeds the "
            f"{MAXIMUM_RESPONSE_ARTIFACT_BYTES}-byte limit"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkFormatError(
            f"response-artifact file is not valid JSON: {exc.msg}"
        ) from exc
    root = _mapping(document, label="response-artifact document")
    raw_responses = root.get("responses", root)
    responses = dict(_mapping(raw_responses, label="responses"))
    if valid_task_ids is not None:
        unknown = sorted(set(responses).difference(valid_task_ids))
        if unknown:
            raise BenchmarkFormatError(
                "response artifacts contain unknown task ids: " + ", ".join(unknown)
            )
    return responses


def _resolve_path(document: Any, path: object) -> Any:
    if not isinstance(path, str) or not path.startswith("$."):
        return _MISSING
    current = document
    for part in path[2:].split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _exact_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isfinite(float(actual)) and float(actual) == float(expected)
    return actual == expected


def _numeric_equal(actual: Any, expected: Any, criterion: Mapping[str, Any]) -> bool:
    if (
        not isinstance(actual, (int, float))
        or isinstance(actual, bool)
        or not isinstance(expected, (int, float))
        or isinstance(expected, bool)
    ):
        return False
    actual_value = float(actual)
    expected_value = float(expected)
    if not math.isfinite(actual_value) or not math.isfinite(expected_value):
        return False
    absolute = float(criterion.get("absolute_tolerance", 0.0))
    relative = float(criterion.get("relative_tolerance", 0.0))
    if absolute < 0 or relative < 0 or not all(
        math.isfinite(value) for value in (absolute, relative)
    ):
        raise BenchmarkFormatError("numeric tolerances must be finite and non-negative")
    return math.isclose(
        actual_value,
        expected_value,
        abs_tol=absolute,
        rel_tol=relative,
    )


def _normalized_text(value: Any) -> str:
    if isinstance(value, Mapping):
        value = " ".join(
            f"{key} {_normalized_text(item)}" for key, item in value.items()
        )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        value = " ".join(_normalized_text(item) for item in value)
    text = str(value).casefold().replace("â€“", "-").replace("â€”", "-")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _declared_phrase_result(
    criterion: Mapping[str, Any], candidate: Any
) -> tuple[CriterionStatus, str]:
    paths = criterion.get("paths")
    if paths is None:
        paths = [criterion.get("path")]
    if not isinstance(paths, list):
        paths = [paths]
    selected = [
        _resolve_path(candidate, path)
        for path in paths
        if isinstance(path, str)
    ]
    if not selected or any(value is _MISSING for value in selected):
        return CriterionStatus.FAIL, "A declared rubric evidence path was missing."
    haystack = _normalized_text(selected)
    requirements: list[str] = []
    for field_name in ("must_establish", "must_include", "must_also_include"):
        values = criterion.get(field_name, [])
        if isinstance(values, list):
            requirements.extend(str(value) for value in values)
    named = criterion.get("must_include_named_items", [])
    if isinstance(named, list):
        requirements.extend(str(value) for value in named)
    missing = [
        requirement
        for requirement in requirements
        if _normalized_text(requirement) not in haystack
    ]
    if missing:
        return (
            CriterionStatus.FAIL,
            "Deterministic rubric evidence was missing: " + "; ".join(missing),
        )
    return (
        CriterionStatus.PASS,
        f"All {len(requirements)} declared rubric statements were present at the bounded evidence paths.",
    )


def _numeric_leaf_sum(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        result = float(value)
        if not math.isfinite(result):
            raise BenchmarkFormatError("structured numeric values must be finite")
        return result
    if isinstance(value, Mapping):
        return sum(_numeric_leaf_sum(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return sum(_numeric_leaf_sum(item) for item in value)
    return 0.0


def _comparison(actual: float, operator: object, expected: object) -> bool:
    if not isinstance(expected, (int, float)) or isinstance(expected, bool):
        raise BenchmarkFormatError("structured constraint requires a numeric value")
    expected_value = float(expected)
    if not math.isfinite(actual) or not math.isfinite(expected_value):
        return False
    return {
        "<=": actual <= expected_value,
        "<": actual < expected_value,
        ">=": actual >= expected_value,
        ">": actual > expected_value,
        "==": actual == expected_value,
    }.get(str(operator), False)


def _find_numeric_by_description(candidate: Any, description: object) -> float | None:
    tokens = set(_normalized_text(description).split())
    stop = {"a", "an", "the", "maximum", "minimum", "planned", "total"}
    tokens.difference_update(stop)
    matches: list[float] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_tokens = set(_normalized_text(key).split())
                if (
                    tokens
                    and tokens.issubset(key_tokens)
                    and isinstance(item, (int, float))
                    and not isinstance(item, bool)
                ):
                    matches.append(float(item))
                visit(item)
        elif isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for item in value:
                visit(item)

    visit(candidate)
    return matches[0] if len(matches) == 1 and math.isfinite(matches[0]) else None


def _structured_constraints_result(
    criterion: Mapping[str, Any], candidate: Any
) -> tuple[CriterionStatus, str]:
    actual = _resolve_path(candidate, criterion.get("path"))
    expected = criterion.get("must_equal")
    if actual is _MISSING or not isinstance(actual, Mapping) or not isinstance(expected, Mapping):
        return CriterionStatus.FAIL, "The declared structured object was missing."
    mismatched = [
        str(key)
        for key, expected_value in expected.items()
        if key not in actual or not _exact_equal(actual[key], expected_value)
    ]
    failures: list[str] = []
    if mismatched:
        failures.append("mismatched fields: " + ", ".join(mismatched))
    constraints = criterion.get("global_numeric_constraints", [])
    if isinstance(constraints, list):
        for item in constraints:
            if not isinstance(item, Mapping):
                raise BenchmarkFormatError("global numeric constraint must be an object")
            value = _find_numeric_by_description(candidate, item.get("description"))
            if value is None or not _comparison(value, item.get("operator"), item.get("value")):
                failures.append(str(item.get("description", "numeric constraint")))
    if failures:
        return CriterionStatus.FAIL, "Structured constraints failed: " + "; ".join(failures)
    return CriterionStatus.PASS, "All declared structured equality and numeric constraints passed."


def _structured_sum_result(
    criterion: Mapping[str, Any], candidate: Any
) -> tuple[CriterionStatus, str]:
    constraints = criterion.get("constraints")
    if not isinstance(constraints, list):
        raise BenchmarkFormatError("structured_sum_constraints requires constraints")
    evidence: list[str] = []
    for item in constraints:
        if not isinstance(item, Mapping):
            raise BenchmarkFormatError("sum constraint must be an object")
        value = _resolve_path(candidate, item.get("path"))
        if value is _MISSING:
            return CriterionStatus.FAIL, f"Missing sum path {item.get('path')!r}."
        if item.get("sum_cost_line_items") and isinstance(value, Mapping):
            explicit = next(
                (
                    value[key]
                    for key in ("total", "total_usd", "total_cost", "grand_total")
                    if key in value
                    and isinstance(value[key], (int, float))
                    and not isinstance(value[key], bool)
                ),
                None,
            )
            actual = float(explicit) if explicit is not None else _numeric_leaf_sum(value)
        else:
            actual = _numeric_leaf_sum(value)
        if not _comparison(actual, item.get("operator"), item.get("value")):
            return (
                CriterionStatus.FAIL,
                f"Sum {actual:g} at {item.get('path')!r} violated the declared limit.",
            )
        evidence.append(f"{item.get('path')}={actual:g}")
    return CriterionStatus.PASS, "Structured sums passed: " + ", ".join(evidence)


def _cross_product_result(
    criterion: Mapping[str, Any], candidate: Any
) -> tuple[CriterionStatus, str]:
    matrix = _resolve_path(candidate, criterion.get("path"))
    factors = criterion.get("factors")
    if not isinstance(matrix, list) or not isinstance(factors, Mapping):
        return CriterionStatus.FAIL, "Experimental matrix or factor declaration was missing."
    factor_names = list(factors)
    expected: set[tuple[str, ...]] = {()}
    for name in factor_names:
        values = factors[name]
        if not isinstance(values, list):
            raise BenchmarkFormatError("cross-product factor values must be arrays")
        expected = {
            prefix + (_canonical_json(value),)
            for prefix in expected
            for value in values
        }
    actual: set[tuple[str, ...]] = set()
    for row in matrix:
        if isinstance(row, Mapping) and all(name in row for name in factor_names):
            actual.add(tuple(_canonical_json(row[name]) for name in factor_names))
    missing = expected.difference(actual)
    phrase_status, phrase_evidence = _declared_phrase_result(criterion, candidate)
    if missing or phrase_status is CriterionStatus.FAIL:
        details = []
        if missing:
            details.append(f"{len(missing)} factor combination(s) missing")
        if phrase_status is CriterionStatus.FAIL:
            details.append(phrase_evidence)
        return CriterionStatus.FAIL, "; ".join(details)
    return (
        CriterionStatus.PASS,
        f"All {len(expected)} factor combinations and required matrix controls were present.",
    )


def _threshold_result(
    criterion: Mapping[str, Any], candidate: Any
) -> tuple[CriterionStatus, str]:
    actual = _resolve_path(candidate, criterion.get("path"))
    required = criterion.get("required_thresholds")
    if not isinstance(actual, Mapping) or not isinstance(required, Mapping):
        return CriterionStatus.FAIL, "Threshold object was missing."
    failed: list[str] = []
    for key, expected in required.items():
        value = actual.get(key, _MISSING)
        if (
            value is _MISSING
            or not isinstance(value, (int, float))
            or isinstance(value, bool)
        ):
            failed.append(str(key))
            continue
        if str(key).endswith("_max"):
            passed = float(value) <= float(expected)
        elif str(key).endswith("_min"):
            passed = float(value) >= float(expected)
        else:
            passed = _exact_equal(value, expected)
        if not passed:
            failed.append(str(key))
    if criterion.get("must_require_all") and actual.get("require_all") is not True:
        failed.append("require_all")
    if failed:
        return CriterionStatus.FAIL, "Missing or weaker thresholds: " + ", ".join(failed)
    return CriterionStatus.PASS, "Every required threshold is present and at least as strict."


def _candidate_source(candidate: Any) -> str | None:
    if isinstance(candidate, str):
        return candidate
    if isinstance(candidate, Mapping) and isinstance(candidate.get("source"), str):
        return str(candidate["source"])
    return None


def _code_rubric_result(
    criterion: Mapping[str, Any],
    candidate: Any,
    task: BenchmarkTask,
    report: PythonSandboxReport | None,
) -> tuple[CriterionStatus, str]:
    if report is None or report.status not in {"passed", "failed"}:
        return CriterionStatus.FAIL, "No successful bounded static/runtime analysis was available."
    source = _candidate_source(candidate)
    if source is None:
        return CriterionStatus.FAIL, "Candidate source was missing."
    evidence_map = candidate.get("rubric_evidence", {}) if isinstance(candidate, Mapping) else {}
    evidence = (
        evidence_map.get(str(criterion.get("criterion_id")), "")
        if isinstance(evidence_map, Mapping)
        else ""
    )
    requirements: list[str] = []
    for key in ("must_establish", "must_include"):
        values = criterion.get(key, [])
        if isinstance(values, list):
            requirements.extend(str(value) for value in values)
    evidence_text = _normalized_text(evidence)
    missing_claims = [item for item in requirements if _normalized_text(item) not in evidence_text]
    features = set(report.source_features)
    structural_failures: list[str] = []
    if any("deterministic tie" in item.casefold() for item in requirements):
        if not ({"call:sorted", "call:min", "call:heappush", "call:heappop"} & features):
            structural_failures.append("no deterministic selection primitive was detected")
    if any("input validation" in item.casefold() for item in requirements):
        try:
            tree = ast.parse(source)
            if not any(isinstance(node, ast.Raise) for node in ast.walk(tree)):
                structural_failures.append("no explicit validation raise was detected")
        except SyntaxError:
            structural_failures.append("source could not be parsed")
    if any("does not enumerate" in item.casefold() or "no enumeration" in item.casefold() for item in requirements):
        if {"call:permutations", "call:product"} & features:
            structural_failures.append("path/order enumeration primitive was detected")
    if task.task_id == "coding-dag-001" and str(criterion.get("criterion_id")) == "complexity":
        if not ({"call:heappush", "call:heappop"} <= features):
            structural_failures.append("heap-backed Kahn frontier was not detected")
    if task.task_id == "coding-coupon-path-002" and str(criterion.get("criterion_id")) == "algorithm":
        normalized_source = _normalized_text(source)
        if not ({"call:heappush", "call:heappop"} <= features):
            structural_failures.append("priority queue operations were not detected")
        if "coupon" not in normalized_source or "used" not in normalized_source:
            structural_failures.append("explicit coupon-used state was not detected")
    if missing_claims or structural_failures:
        parts = []
        if missing_claims:
            parts.append("missing rubric evidence: " + "; ".join(missing_claims))
        parts.extend(structural_failures)
        return CriterionStatus.FAIL, "; ".join(parts)
    return (
        CriterionStatus.PASS,
        f"Declared code evidence was present and {len(features)} bounded AST features supported the check.",
    )


def _criterion_result(
    criterion: Mapping[str, Any],
    candidate: Any,
    task: BenchmarkTask,
    *,
    python_report: PythonSandboxReport | None = None,
) -> tuple[CriterionStatus, str]:
    kind = str(criterion["kind"])
    if kind == "json_path_exact":
        actual = _resolve_path(candidate, criterion.get("path"))
        passed = actual is not _MISSING and _exact_equal(actual, criterion.get("expected"))
        return (
            CriterionStatus.PASS if passed else CriterionStatus.FAIL,
            "Exact value matched at the declared JSON path."
            if passed
            else "Declared JSON path was missing or did not match the expected value.",
        )
    if kind == "json_path_numeric":
        actual = _resolve_path(candidate, criterion.get("path"))
        passed = actual is not _MISSING and _numeric_equal(
            actual, criterion.get("expected"), criterion
        )
        return (
            CriterionStatus.PASS if passed else CriterionStatus.FAIL,
            "Numeric value matched within the declared tolerance."
            if passed
            else "Numeric value was missing or outside the declared tolerance.",
        )
    if kind == "residue_vector":
        actual = _resolve_path(candidate, criterion.get("value_path"))
        expected = criterion.get("expected")
        passed = isinstance(actual, int) and not isinstance(actual, bool)
        if passed and isinstance(expected, list):
            for item in expected:
                if not isinstance(item, Mapping):
                    passed = False
                    break
                modulus = item.get("modulus")
                residue = item.get("residue")
                if (
                    not isinstance(modulus, int)
                    or isinstance(modulus, bool)
                    or modulus <= 0
                    or actual % modulus != residue
                ):
                    passed = False
                    break
        else:
            passed = False
        return (
            CriterionStatus.PASS if passed else CriterionStatus.FAIL,
            "The submitted integer satisfies every declared residue check."
            if passed
            else "The submitted value was missing or failed a declared residue check.",
        )
    if kind == "transition_map_equivalent":
        actual = _resolve_path(candidate, criterion.get("path"))
        expected = _resolve_path(task.raw, criterion.get("expected_reference_path"))
        passed = actual is not _MISSING and expected is not _MISSING and actual == expected
        return (
            CriterionStatus.PASS if passed else CriterionStatus.FAIL,
            "Transition map exactly matched the fixture reference."
            if passed
            else "Transition map was missing or differed from the fixture reference.",
        )
    if kind in {"json_paths_exact", "json_paths_exact_or_numeric"}:
        expected_values = criterion.get("expected")
        if not isinstance(expected_values, Mapping):
            raise BenchmarkFormatError(f"{kind} requires an expected object")
        passed = True
        for path, expected in expected_values.items():
            actual = _resolve_path(candidate, path)
            if kind == "json_paths_exact_or_numeric" and isinstance(
                expected, (int, float)
            ) and not isinstance(expected, bool):
                matches = actual is not _MISSING and _numeric_equal(
                    actual, expected, criterion
                )
            else:
                matches = actual is not _MISSING and _exact_equal(actual, expected)
            passed = passed and matches
        return (
            CriterionStatus.PASS if passed else CriterionStatus.FAIL,
            "All declared JSON paths matched."
            if passed
            else "One or more declared JSON paths were missing or mismatched.",
        )
    if kind == "rubric_predicate":
        return _declared_phrase_result(criterion, candidate)
    if kind == "structured_constraints":
        return _structured_constraints_result(criterion, candidate)
    if kind == "structured_sum_constraints":
        return _structured_sum_result(criterion, candidate)
    if kind == "cross_product_coverage":
        return _cross_product_result(criterion, candidate)
    if kind == "threshold_set":
        return _threshold_result(criterion, candidate)
    if kind == "python_test_group":
        if python_report is None:
            return CriterionStatus.FAIL, "Bounded Python execution did not produce a report."
        names = criterion.get("case_names")
        if not isinstance(names, list) or not names:
            raise BenchmarkFormatError("python_test_group requires case_names")
        selected = [python_report.case(str(name)) for name in names]
        missing = [str(name) for name, result in zip(names, selected) if result is None]
        failed = [result.name for result in selected if result is not None and not result.passed]
        if python_report.status in {"timeout", "error"} or missing or failed:
            details = []
            if python_report.error:
                details.append(python_report.error)
            if missing:
                details.append("missing cases: " + ", ".join(missing))
            if failed:
                details.append("failed cases: " + ", ".join(failed))
            return CriterionStatus.FAIL, "; ".join(details) or "Python test group failed."
        return (
            CriterionStatus.PASS,
            "Bounded sandbox passed cases: " + ", ".join(str(name) for name in names),
        )
    if kind == "code_rubric":
        return _code_rubric_result(criterion, candidate, task, python_report)
    return (
        CriterionStatus.UNSUPPORTED,
        (
            f"Evaluator kind {kind!r} is not safely automated in this milestone. "
            "No pass is inferred from model attestations or participant count."
        ),
    )


def _contract_result(
    task: BenchmarkTask, candidate: Any
) -> tuple[bool, tuple[str, ...]]:
    contract_kind = task.response_contract.get("type", task.response_contract.get("kind"))
    if contract_kind == "object":
        required = task.response_contract.get("required", [])
        if not isinstance(required, list):
            raise BenchmarkFormatError(
                f"task {task.task_id!r} response_contract.required must be an array"
            )
        if not isinstance(candidate, Mapping):
            return False, tuple(str(item) for item in required)
        missing = tuple(str(item) for item in required if item not in candidate)
        return not missing, missing
    if contract_kind == "python_function":
        if isinstance(candidate, str):
            return bool(candidate.strip()), (() if candidate.strip() else ("source",))
        if isinstance(candidate, Mapping):
            source = candidate.get("source")
            return isinstance(source, str) and bool(source.strip()), (
                () if isinstance(source, str) and source.strip() else ("source",)
            )
        return False, ("source",)
    raise BenchmarkFormatError(
        f"task {task.task_id!r} uses unsupported response contract {contract_kind!r}"
    )


def _python_evaluation_report(
    task: BenchmarkTask, candidate: Any
) -> PythonSandboxReport | None:
    contract_kind = task.response_contract.get("type", task.response_contract.get("kind"))
    if contract_kind != "python_function":
        return None
    source = _candidate_source(candidate)
    entrypoint = task.response_contract.get("entrypoint")
    evaluator = task.raw.get("evaluator")
    if source is None or not isinstance(entrypoint, str) or not isinstance(evaluator, Mapping):
        return PythonSandboxReport(
            status="error",
            cases=(),
            elapsed_seconds=0.0,
            memory_limit_enforced=False,
            source_features=(),
            error="Python source, entrypoint, or evaluator cases were missing.",
        )
    cases = evaluator.get("cases")
    if not isinstance(cases, list) or not all(isinstance(case, Mapping) for case in cases):
        raise BenchmarkFormatError(f"task {task.task_id!r} has invalid Python cases")
    try:
        return run_python_function_cases(
            source,
            entrypoint=entrypoint,
            cases=cases,
            timeout_seconds=float(evaluator.get("timeout_seconds", 5.0)),
        )
    except SandboxPolicyError as error:
        return PythonSandboxReport(
            status="error",
            cases=(),
            elapsed_seconds=0.0,
            memory_limit_enforced=False,
            source_features=(),
            error=f"Sandbox policy rejected candidate: {error}",
        )


class DeterministicFixtureEvaluator:
    """Evaluate only predicates that are deterministic and bounded locally."""

    name = "amca-deterministic-fixture-evaluator"
    version = EVALUATOR_VERSION
    supported_kinds = frozenset(
        {
            "code_rubric",
            "cross_product_coverage",
            "json_path_exact",
            "json_path_numeric",
            "json_paths_exact",
            "json_paths_exact_or_numeric",
            "python_test_group",
            "residue_vector",
            "rubric_predicate",
            "structured_constraints",
            "structured_sum_constraints",
            "threshold_set",
            "transition_map_equivalent",
        }
    )

    def not_run(self, task: BenchmarkTask) -> BenchmarkEvaluation:
        outcomes = tuple(
            CriterionOutcome(
                criterion_id=str(criterion["criterion_id"]),
                kind=str(criterion["kind"]),
                required=bool(criterion.get("required", True)),
                weight=float(criterion["weight"]),
                status=CriterionStatus.NOT_RUN,
                evidence="No offline response artifact was supplied for this task.",
            )
            for criterion in task.criteria
        )
        return BenchmarkEvaluation(
            task_id=task.task_id,
            status="not_evaluated",
            score=0.0,
            maximum_score=100.0,
            required_criterion_failed=False,
            response_contract_valid=None,
            missing_response_fields=(),
            candidate_sha256=None,
            outcomes=outcomes,
            limitations=(
                "The scripted kernel run validates orchestration only; it does not generate a benchmark answer.",
            ),
        )

    def evaluate(self, task: BenchmarkTask, candidate: Any) -> BenchmarkEvaluation:
        contract_valid, missing_fields = _contract_result(task, candidate)
        python_report = _python_evaluation_report(task, candidate)
        outcomes: list[CriterionOutcome] = []
        for criterion in task.criteria:
            status, evidence = _criterion_result(
                criterion,
                candidate,
                task,
                python_report=python_report,
            )
            outcomes.append(
                CriterionOutcome(
                    criterion_id=str(criterion["criterion_id"]),
                    kind=str(criterion["kind"]),
                    required=bool(criterion.get("required", True)),
                    weight=float(criterion["weight"]),
                    status=status,
                    evidence=evidence,
                )
            )

        required_failed = not contract_valid or any(
            outcome.required and outcome.status is CriterionStatus.FAIL
            for outcome in outcomes
        )
        required_unsupported = any(
            outcome.required and outcome.status is CriterionStatus.UNSUPPORTED
            for outcome in outcomes
        )
        score = sum(
            outcome.weight
            for outcome in outcomes
            if outcome.status is CriterionStatus.PASS
        )
        if required_failed:
            cap = float(
                task.raw.get("required_criterion_failure_score_cap", 59)
            )
            score = min(score, cap)
            status = "failed"
        elif required_unsupported:
            status = "partial"
        else:
            status = "passed"
        limitations = tuple(
            sorted(
                {
                    (
                        f"Criterion kind {outcome.kind!r} requires a bounded code "
                        "sandbox or an explicit human/rubric evaluator."
                    )
                    for outcome in outcomes
                    if outcome.status is CriterionStatus.UNSUPPORTED
                }
            )
        )
        return BenchmarkEvaluation(
            task_id=task.task_id,
            status=status,
            score=float(score),
            maximum_score=100.0,
            required_criterion_failed=required_failed,
            response_contract_valid=contract_valid,
            missing_response_fields=missing_fields,
            candidate_sha256=_sha256_json(candidate),
            outcomes=tuple(outcomes),
            limitations=limitations,
        )


class KernelCriterionEvaluator:
    """Bridge deterministic benchmark outcomes into the kernel callback API."""

    def __init__(self, evaluation: BenchmarkEvaluation) -> None:
        self.evaluation = evaluation
        self._outcomes = {
            outcome.criterion_id: outcome for outcome in evaluation.outcomes
        }

    def __call__(
        self,
        _state: ProjectState,
        criterion: GoalCriterion,
        attestations: Mapping[str, str],
    ) -> str | None:
        outcome = self._outcomes.get(criterion.id)
        if (
            outcome is None
            or outcome.status is not CriterionStatus.PASS
            or not attestations
        ):
            return None
        return (
            f"{EVALUATOR_VERSION}: {outcome.evidence} The pass came from the "
            "offline response artifact; model attestations only triggered evaluation."
        )


def _project_state(task: BenchmarkTask, project_id: str) -> ProjectState:
    criteria = [
        GoalCriterion(
            id=str(criterion["criterion_id"]),
            description=(
                f"Benchmark criterion {criterion['criterion_id']} "
                f"({criterion['kind']}, weight {criterion['weight']})."
            ),
            required=bool(criterion.get("required", True)),
        )
        for criterion in task.criteria
    ]
    return ProjectState(
        project_id=project_id,
        goal=task.prompt,
        goal_contract=GoalContract(
            goal=task.prompt,
            criteria=criteria,
            max_iterations=1,
        ),
        current_focus=task.prompt,
        active_subproblem=task.title,
        privacy_mode=PrivacyMode.OFFLINE,
        compute_policy=ComputePolicy.RESEARCH,
    )


def _scripted_probe(task: BenchmarkTask) -> str:
    return _canonical_json(
        {
            "summary": (
                f"Ran the offline orchestration probe for {task.task_id}; no "
                "external model answer or judge was invoked."
            ),
            "events": [
                {
                    "type": "evidence",
                    "content": (
                        f"Loaded fixture {task.task_id} version {task.version} and "
                        "its machine-readable criterion contract."
                    ),
                    "severity": "info",
                    "confidence": 1.0,
                    "evidence_refs": [f"fixture:{task.task_id}:v{task.version}"],
                }
            ],
            "criterion_evidence": [
                {
                    "criterion_id": str(criterion["criterion_id"]),
                    "evidence": (
                        "Evaluation requested from the configured offline fixture "
                        "evaluator; this is not a correctness attestation."
                    ),
                }
                for criterion in task.criteria
            ],
            "focus_resolved": True,
        }
    )


def _adapters(task: BenchmarkTask, condition: CollaborationMode) -> tuple[ScriptedAdapter, ...]:
    count = 1 if condition is CollaborationMode.SOLO else 2
    response = _scripted_probe(task)
    return tuple(
        ScriptedAdapter(
            [response],
            adapter_id=f"benchmark-scripted-{index + 1}",
            model="offline-scripted-probe",
            provider="offline-script",
            is_local=True,
        )
        for index in range(count)
    )


def _conditions(values: Sequence[str | CollaborationMode]) -> tuple[CollaborationMode, ...]:
    normalized: list[CollaborationMode] = []
    for value in values:
        condition = value if isinstance(value, CollaborationMode) else CollaborationMode(value)
        if condition not in {CollaborationMode.SOLO, CollaborationMode.JOINT}:
            raise ValueError("benchmark runner currently supports only solo and joint")
        if condition not in normalized:
            normalized.append(condition)
    if not normalized:
        raise ValueError("at least one benchmark condition is required")
    return tuple(normalized)


async def run_benchmark_suite(
    suite: BenchmarkSuite,
    store: SQLiteStore,
    *,
    conditions: Sequence[str | CollaborationMode] = (
        CollaborationMode.SOLO,
        CollaborationMode.JOINT,
    ),
    task_ids: Sequence[str] | None = None,
    responses: Mapping[str, Any] | None = None,
    evaluator: DeterministicFixtureEvaluator | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Execute selected fixtures through the kernel without network access."""

    condition_values = _conditions(conditions)
    selected_ids = tuple(task_ids or (task.task_id for task in suite.tasks))
    if not selected_ids:
        raise ValueError("at least one benchmark task is required")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("benchmark task ids must be unique")
    selected: list[BenchmarkTask] = []
    for task_id in selected_ids:
        task = suite.task(task_id)
        if task is None:
            raise ValueError(f"unknown benchmark task id {task_id!r}")
        selected.append(task)
    response_values = dict(responses or {})
    unknown_responses = sorted(set(response_values).difference(task.task_id for task in suite.tasks))
    if unknown_responses:
        raise ValueError(
            "responses contain unknown task ids: " + ", ".join(unknown_responses)
        )

    fixture_evaluator = evaluator or DeterministicFixtureEvaluator()
    resolved_run_id = run_id or uuid4().hex[:12]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", resolved_run_id):
        raise ValueError(
            "run_id must be 1-64 ASCII letters, digits, dots, underscores, or hyphens"
        )
    results: list[dict[str, Any]] = []
    for task in selected:
        for condition in condition_values:
            response_scope = "none"
            candidate = _MISSING
            if task.task_id in response_values:
                response_value = response_values[task.task_id]
                condition_map = (
                    response_value.get("__amca_conditions__")
                    if isinstance(response_value, Mapping)
                    else None
                )
                if isinstance(condition_map, Mapping):
                    candidate = condition_map.get(condition.value, _MISSING)
                    response_scope = "condition_specific"
                else:
                    candidate = response_value
                    response_scope = "shared_across_conditions"
            evaluation = (
                fixture_evaluator.evaluate(task, candidate)
                if candidate is not _MISSING
                else fixture_evaluator.not_run(task)
            )
            project_id = f"benchmark-{resolved_run_id}-{condition.value}-{task.task_id}"
            if store.load_state(project_id) is not None:
                raise ValueError(
                    f"benchmark project {project_id!r} already exists; choose a new run id"
                )
            state = _project_state(task, project_id)
            store.save_state(state)
            kernel_result = await CollaborationKernel(
                StateManager(state, store=store),
                _adapters(task, condition),
                criterion_evaluator=KernelCriterionEvaluator(evaluation),
                config=KernelConfig(max_rounds=1, max_staleness_versions=8),
            ).run(mode=condition, sync_mode=SyncMode.HARD)
            results.append(
                {
                    "task_id": task.task_id,
                    "task_version": task.version,
                    "domain": task.domain,
                    "title": task.title,
                    "condition": condition.value,
                    "response_scope": response_scope,
                    "project_id": project_id,
                    "execution": {
                        "kind": "offline_scripted_orchestration_probe",
                        "network_allowed": False,
                        "external_model_called": False,
                        "external_model_judge_called": False,
                        "stop_reason": kernel_result.stop_reason.value,
                        "rounds_completed": kernel_result.rounds_completed,
                        "synchronization_passed": kernel_result.synchronization_passed,
                        "kernel_goal_satisfied": kernel_result.state.goal_satisfied,
                        "verified_criterion_count": sum(
                            criterion.satisfied
                            for criterion in (
                                kernel_result.state.goal_contract.criteria
                                if kernel_result.state.goal_contract is not None
                                else ()
                            )
                        ),
                        "decision_record_count": len(
                            kernel_result.state.decision_records
                        ),
                        "verified_evidence_record_count": sum(
                            record.verified
                            for record in kernel_result.state.evidence_records.values()
                        ),
                        "final_state_version": kernel_result.state.version,
                        "call_count": len(kernel_result.call_records),
                        "input_tokens": sum(
                            call.input_tokens for call in kernel_result.call_records
                        ),
                        "output_tokens": sum(
                            call.output_tokens for call in kernel_result.call_records
                        ),
                        "api_cost_usd": sum(
                            call.cost_usd for call in kernel_result.call_records
                        ),
                    },
                    "evaluation": evaluation.to_dict(),
                }
            )

    status_counts: dict[str, int] = {}
    for result in results:
        status = str(result["evaluation"]["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    manifest: dict[str, Any] = {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "kind": "amca_benchmark_run_manifest",
        "suite_id": suite.suite_id,
        "suite_schema_version": suite.schema_version,
        "run_id": resolved_run_id,
        "fixture_count": len(selected),
        "condition_count": len(condition_values),
        "run_count": len(results),
        "conditions": [condition.value for condition in condition_values],
        "evaluator": {
            "name": fixture_evaluator.name,
            "version": fixture_evaluator.version,
            "supported_kinds": sorted(fixture_evaluator.supported_kinds),
            "external_model_evaluation_supported": False,
            "code_execution_supported": True,
            "code_execution_boundary": "AST-constrained isolated interpreter with deadline and memory/process limits",
            "rubric_judging_supported": True,
            "rubric_boundary": "deterministic declared-phrase and structural checks; no semantic model judge",
        },
        "claims": [
            "Scripted runs validate kernel orchestration and persistence only.",
            "Only explicitly supported deterministic predicates can pass.",
            "Participant count and model attestations are never correctness signals.",
            "Eight fixtures are engineering validation, not evidence of broad superiority.",
        ],
        "summary": {
            "evaluation_status_counts": dict(sorted(status_counts.items())),
            "kernel_goals_satisfied": sum(
                bool(result["execution"]["kernel_goal_satisfied"])
                for result in results
            ),
            "external_model_calls": 0,
            "external_judge_calls": 0,
        },
        "results": results,
        "manifest_digest_scope": "All manifest fields except manifest_sha256.",
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return manifest


def require_evaluator(name: str) -> DeterministicFixtureEvaluator:
    """Resolve a CLI evaluator name while refusing unsupported model judges."""

    if name == "deterministic":
        return DeterministicFixtureEvaluator()
    if name == "external-model":
        raise ExternalModelEvaluationUnsupported(
            "External-model evaluation is not implemented or validated in this "
            "offline-first milestone. Supply response artifacts for deterministic "
            "checks, and use explicit human review for unsupported rubric criteria."
        )
    raise ValueError(f"unknown benchmark evaluator {name!r}")


__all__ = [
    "BenchmarkEvaluation",
    "BenchmarkFormatError",
    "BenchmarkSuite",
    "BenchmarkTask",
    "CriterionOutcome",
    "CriterionStatus",
    "DeterministicFixtureEvaluator",
    "ExternalModelEvaluationUnsupported",
    "KernelCriterionEvaluator",
    "load_benchmark_suite",
    "load_response_artifacts",
    "require_evaluator",
    "run_benchmark_suite",
]
