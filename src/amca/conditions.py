"""Deterministic condition evaluation for conditional automations (B1.2B).

A condition is a small versioned JSON object persisted on the automation
definition (``condition_json``). Evaluation is pure and deterministic: no
shell, no external processes, no file-content reads, and zero model/provider
calls. Unknown or malformed conditions always fail closed.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


SUPPORTED_CONDITION_VERSIONS = {1}
SUPPORTED_CONDITION_KINDS = frozenset({"always", "file_exists"})
MAX_CONDITION_JSON_CHARS = 4_000


@dataclass(frozen=True)
class ConditionResult:
    """Outcome of one deterministic condition evaluation."""

    kind: str
    ok: bool                      # False means the evaluation itself failed
    value: bool | None = None     # true/false only when ok
    error: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


def normalize_condition_path(raw: str) -> str:
    """Validate a contained project-relative path; return normalized form."""

    text = raw.strip()
    if not text:
        raise ValueError("condition path must be a non-empty project-relative path")
    if text.startswith("\\\\"):
        raise ValueError("condition path must not be a UNC path")
    if text.startswith(("/", "\\")):
        raise ValueError("condition path must be project-relative, not absolute")
    if re.match(r"^[A-Za-z]:", text):
        raise ValueError("condition path must not include a drive letter")
    segments = [
        segment
        for segment in text.replace("\\", "/").split("/")
        if segment not in ("", ".")
    ]
    if not segments:
        raise ValueError("condition path must name a file inside the project")
    if any(segment == ".." for segment in segments):
        raise ValueError("condition path must not contain '..' traversal")
    if any(":" in segment for segment in segments):
        raise ValueError("condition path must not contain drive-like segments")
    return "/".join(segments)


def validate_condition(condition: Any) -> dict[str, Any]:
    """Validate a persisted condition object for saving; raises ValueError."""

    if not isinstance(condition, Mapping):
        raise ValueError("condition must be a JSON object")
    try:
        encoded = json.dumps(dict(condition), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise ValueError(f"condition is not JSON-serializable: {error}") from error
    if len(encoded) > MAX_CONDITION_JSON_CHARS:
        raise ValueError(
            f"condition payload exceeds {MAX_CONDITION_JSON_CHARS} characters"
        )
    version = condition.get("version")
    if version not in SUPPORTED_CONDITION_VERSIONS:
        raise ValueError(f"unsupported condition version: {version!r}")
    kind = condition.get("kind")
    if not isinstance(kind, str) or kind not in SUPPORTED_CONDITION_KINDS:
        raise ValueError(f"unknown condition kind: {kind!r}")
    normalized: dict[str, Any] = {"version": version, "kind": kind}
    if kind == "file_exists":
        path = condition.get("path")
        if not isinstance(path, str):
            raise ValueError("file_exists condition requires a string path")
        normalized["path"] = normalize_condition_path(path)
    return normalized


class ConditionEvaluator:
    """Answers one deterministic question about the authorized workspace.

    ``file_exists`` checks existence of a single project-relative path while
    enforcing containment: the resolved path (following symlinks/junctions
    where the OS supports them) must remain inside the resolved workspace
    root. Only existence is answered — contents are never read.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root)

    def evaluate(self, condition: Any) -> ConditionResult:
        kind_hint = (
            str(condition.get("kind"))
            if isinstance(condition, Mapping) and isinstance(condition.get("kind"), str)
            else "unknown"
        )
        try:
            normalized = validate_condition(condition)
        except ValueError as error:
            return ConditionResult(
                kind=kind_hint,
                ok=False,
                error=f"invalid condition: {error}",
                evidence={"kind": kind_hint, "result": "error"},
            )
        kind = normalized["kind"]
        if kind == "always":
            return ConditionResult(
                kind="always",
                ok=True,
                value=True,
                evidence={"kind": "always", "exists": True},
            )
        # file_exists
        evidence: dict[str, Any] = {"kind": "file_exists", "path": normalized["path"]}
        try:
            root = os.path.normcase(os.path.realpath(self.workspace_root))
            candidate = os.path.normcase(
                os.path.realpath(
                    os.path.join(self.workspace_root, *normalized["path"].split("/"))
                )
            )
        except OSError as error:
            return ConditionResult(
                kind="file_exists",
                ok=False,
                error=f"path resolution failed: {error}",
                evidence=evidence,
            )
        if candidate != root and not candidate.startswith(root + os.sep):
            return ConditionResult(
                kind="file_exists",
                ok=False,
                error="path escapes the authorized project workspace",
                evidence=evidence,
            )
        exists = os.path.exists(candidate)
        return ConditionResult(
            kind="file_exists",
            ok=True,
            value=exists,
            evidence={**evidence, "exists": bool(exists)},
        )
