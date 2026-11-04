"""PS1-13H Agent Observability Core (backend core only).

Safe, truthful, project-isolated observability over the EXISTING agent
lifecycle. No UI, no provider/model logic, no run-tree API, no health
agent, no polling/gauges.

Covers:
- Safe AgentTask serialization (additive tool_config recursive sanitize,
  output_schema as-is if safe, resolved_model allowlist only, input_summary
  without raw input_payload).
- Nano timing from existing lifecycle info (missing=null, monotonicity).
- Truthful active count from authoritative lifecycle; crash-uncertain work
  never shown active.
- Truthful active_organization (null valid if none; explicit
  unknown/unavailable, never fabricated).
- Full lifecycle observability over the 12 real states; no new states.
- Cross-project isolation for new telemetry.

Secret handling uses FAKE-pattern-safe generic redaction: key-based via
``amca.security.is_sensitive_key`` (covers secret/token/password/api key/
credential/authorization/bearer/cookie/private key incl. nested) plus
value-pattern redaction for Bearer assignments so nested FAKE secrets in
tool/model/input/error paths can never leak into observability output.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from amca.security import is_sensitive_key

REDACTED = "[REDACTED]"

# -- Authoritative lifecycle (mirrors AgentTaskStatus; no new states) --------

ALL_TASK_STATUSES: tuple[str, ...] = (
    "proposed",
    "authorized",
    "queued",
    "starting",
    "running",
    "waiting",
    "verifying",
    "completed",
    "merged",
    "retired",
    "failed",
    "cancelled",
)

ACTIVE_TASK_STATUSES: frozenset[str] = frozenset({
    "queued",
    "starting",
    "running",
    "waiting",
    "verifying",
})

_ALL_SET = frozenset(ALL_TASK_STATUSES)

# -- Generic value-pattern redaction (FAKE-safe, no hardcoded test values) ---

_BEARER_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9\-_\.]+)", re.IGNORECASE)
_ASSIGN_RE = re.compile(
    r"((?:api[_-]?key|password|passwd|passphrase|secret|token|credential|"
    r"authorization|cookie|bearer|private[_-]?key)\s*[:=]\s*)([^\s,;}\"']+)",
    re.IGNORECASE,
)
_HEADER_KEY_RE = re.compile(r"header", re.IGNORECASE)


def _redact_string_patterns(text: str) -> str:
    redacted = _BEARER_RE.sub(lambda m: m.group(1) + REDACTED, text)
    redacted = _ASSIGN_RE.sub(lambda m: m.group(1) + REDACTED, redacted)
    return redacted


def sanitize_value(value: Any) -> Any:
    """Recursively sanitize one observability value.

    - Mapping keys matching credential material are replaced with REDACTED
      (entire subtree redacted; never recursed for exfiltration).
    - Sequences are sanitized element-wise (sets become sorted lists).
    - Strings get Bearer/assignment pattern redaction.
    """
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, nested in value.items():
            if is_sensitive_key(key) or _HEADER_KEY_RE.search(str(key)):
                out[str(key)] = REDACTED
            else:
                out[str(key)] = sanitize_value(nested)
        return out
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((sanitize_value(item) for item in value), key=lambda v: str(v))
    if isinstance(value, str):
        return _redact_string_patterns(value)
    return value


def sanitize_tool_config(config: Any) -> Any:
    """Additive safe tool_config: recursive sanitize, None preserved."""
    if config is None:
        return None
    return sanitize_value(config)


def _contains_sensitive_key(value: Any, *, _seen: set[int] | None = None) -> bool:
    seen = _seen if _seen is not None else set()
    if isinstance(value, Mapping):
        if id(value) in seen:
            return False
        seen.add(id(value))
        for key, nested in value.items():
            if is_sensitive_key(key) or _HEADER_KEY_RE.search(str(key)):
                return True
            if isinstance(nested, (Mapping, list, tuple, set, frozenset)):
                if _contains_sensitive_key(nested, _seen=seen):
                    return True
        return False
    if isinstance(value, (list, tuple, set, frozenset)):
        if id(value) in seen:
            return False
        seen.add(id(value))
        for item in value:
            if isinstance(item, (Mapping, list, tuple, set, frozenset)):
                if _contains_sensitive_key(item, _seen=seen):
                    return True
        return False
    return False


def _contains_secret_pattern(value: Any, *, _seen: set[int] | None = None) -> bool:
    seen = _seen if _seen is not None else set()
    if isinstance(value, str):
        return _redact_string_patterns(value) != value
    if isinstance(value, Mapping):
        if id(value) in seen:
            return False
        seen.add(id(value))
        return any(
            _contains_secret_pattern(v, _seen=seen) for v in value.values()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        if id(value) in seen:
            return False
        seen.add(id(value))
        return any(_contains_secret_pattern(v, _seen=seen) for v in value)
    return False


def sanitize_output_schema(schema: Any) -> Any:
    """output_schema as-is if safe, else sanitized copy. None preserved."""
    if schema is None:
        return None
    if isinstance(schema, (Mapping, list, tuple, set, frozenset)):
        if not _contains_sensitive_key(schema) and not _contains_secret_pattern(schema):
            return copy.deepcopy(schema)
        return sanitize_value(schema)
    if isinstance(schema, str):
        return _redact_string_patterns(schema)
    return copy.deepcopy(schema)


# -- Resolved model allowlist -------------------------------------------------

_RESOLVED_MODEL_ALLOWLIST: frozenset[str] = frozenset({
    "model_id",
    "model",
    "provider",
    "provider_id",
    "is_local",
    "is_cloud",
    "local",
    "cloud",
    "temperature",
    "top_p",
    "max_tokens",
    "max_rounds",
    "max_adaptive_phases",
    "executor_kind",
    "agent_id",
    "adapter_id",
})


def sanitize_resolved_model(config: Any) -> dict[str, Any] | None:
    """Allowlist-only resolved model identity. Never credentials/headers."""
    if config is None:
        return None
    if not isinstance(config, Mapping):
        return None
    out: dict[str, Any] = {}
    for key, val in config.items():
        name = str(key)
        if name not in _RESOLVED_MODEL_ALLOWLIST:
            continue
        if is_sensitive_key(name) or _HEADER_KEY_RE.search(name):
            continue
        if isinstance(val, str):
            out[name] = _redact_string_patterns(val)
        elif isinstance(val, (bool, int, float)) or val is None:
            out[name] = val
        else:
            # Safe params only: stringify conservatively, redacted.
            out[name] = _redact_string_patterns(str(val))
    return out


# -- Input summary (never raw payload) ----------------------------------------

def summarize_input(payload: Any) -> dict[str, Any] | None:
    """Digest/size/sanitized top-level keys/redacted preview; NO raw payload."""
    if payload is None:
        return None
    try:
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, default=str,
        ).encode("utf-8")
    except Exception:
        canonical = str(payload).encode("utf-8", errors="replace")
    digest = hashlib.sha256(canonical).hexdigest()
    size_bytes = len(canonical)
    if isinstance(payload, Mapping):
        top_keys = sorted(str(k) for k in payload.keys())
    else:
        top_keys = []
    sanitized = sanitize_value(payload)
    try:
        preview_full = json.dumps(
            sanitized, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, default=str,
        )
    except Exception:
        preview_full = str(sanitized)
    preview = preview_full[:200] + ("...[truncated]" if len(preview_full) > 200 else "")
    return {
        "digest": digest,
        "size_bytes": size_bytes,
        "top_level_keys": top_keys,
        "preview": preview,
    }


def sanitize_error_text(error: Any) -> Any:
    if error is None:
        return None
    if isinstance(error, str):
        return _redact_string_patterns(error)
    return sanitize_value(error)


# -- Nano timing ---------------------------------------------------------------

def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        # fromisoformat handles +00:00; normalize trailing Z.
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        return None


def build_timing(
    created_at: Any, started_at: Any, completed_at: Any
) -> dict[str, Any]:
    """created/started/completed-or-retired + elapsed; missing=null; monotonic."""
    created_dt = _parse_ts(created_at)
    started_dt = _parse_ts(started_at)
    completed_dt = _parse_ts(completed_at)

    elapsed: float | None = None
    if created_dt is not None and completed_dt is not None:
        try:
            elapsed = (completed_dt - created_dt).total_seconds()
            if elapsed != elapsed or elapsed == float("inf") or elapsed == float("-inf"):
                elapsed = None
        except Exception:
            elapsed = None

    present = [
        dt for dt in (created_dt, started_dt, completed_dt) if dt is not None
    ]
    raw_present_count = sum(
        1 for raw in (created_at, started_at, completed_at)
        if isinstance(raw, str) and raw.strip()
    )
    if len(present) < 2:
        monotonic: bool | None = None
    else:
        ordered = [created_dt, started_dt, completed_dt]
        seq = [dt for dt in ordered if dt is not None]
        monotonic = all(a <= b for a, b in zip(seq, seq[1:]))
        # If any raw timestamp was present but unparseable, truthfully
        # report non-monotonic rather than unknown.
        if raw_present_count != len(present):
            monotonic = False

    return {
        "created_at": created_at if isinstance(created_at, str) else None,
        "started_at": started_at if isinstance(started_at, str) else None,
        "completed_at": completed_at if isinstance(completed_at, str) else None,
        "elapsed_seconds": elapsed,
        "monotonic": monotonic,
    }


# -- Active / lifecycle helpers -------------------------------------------------

def _task_status(task: Any) -> str | None:
    if isinstance(task, Mapping):
        val = task.get("status")
    else:
        val = getattr(task, "status", None)
    return str(val) if val is not None else None


def _task_is_uncertain(task: Any) -> bool:
    if isinstance(task, Mapping):
        if task.get("external_outcome_uncertain"):
            return True
        if task.get("recovery_disposition") == "BLOCKED_UNCERTAIN_EXTERNAL_OUTCOME":
            return True
        return False
    if bool(getattr(task, "external_outcome_uncertain", False)):
        return True
    if getattr(task, "recovery_disposition", None) == "BLOCKED_UNCERTAIN_EXTERNAL_OUTCOME":
        return True
    return False


def is_active_status(status: Any) -> bool:
    return str(status) in ACTIVE_TASK_STATUSES


def is_task_active(task: Any) -> bool:
    """Authoritative active check; crash-uncertain work never active."""
    status = _task_status(task)
    if status not in ACTIVE_TASK_STATUSES:
        return False
    if _task_is_uncertain(task):
        return False
    return True


def count_active(tasks: Sequence[Any] | Mapping[str, Any]) -> int:
    values = tasks.values() if isinstance(tasks, Mapping) else tasks
    return sum(1 for t in values if is_task_active(t))


def lifecycle_counts(tasks: Sequence[Any] | Mapping[str, Any]) -> dict[str, int]:
    values = list(tasks.values()) if isinstance(tasks, Mapping) else list(tasks)
    counts: dict[str, int] = {state: 0 for state in ALL_TASK_STATUSES}
    for task in values:
        status = _task_status(task)
        if status in counts:
            counts[status] += 1
    return counts


# -- Safe task / organization description ---------------------------------------

def _task_project(task: Any) -> str | None:
    if isinstance(task, Mapping):
        val = task.get("project_id")
    else:
        val = getattr(task, "project_id", None)
    return str(val) if val is not None else None


def describe_task(task: Any) -> dict[str, Any]:
    """Safe additive serialization of one AgentTaskRecord (or dict row).

    Preserves all existing to_dict keys when given a record dict; adds only
    safe additive fields and never exposes raw input_payload.
    """
    if isinstance(task, Mapping):
        base = dict(task)
        tool_cfg = task.get("tool_config")
        out_schema = task.get("output_schema")
        resolved = task.get("resolved_model_config", task.get("resolved_model"))
        inp = task.get("input_payload")
        created = task.get("created_at")
        started = task.get("started_at")
        completed = task.get("completed_at")
        error = task.get("error")
        verification = task.get("verification_result")
        summary = task.get("result_summary")
    else:
        base = task.to_dict() if hasattr(task, "to_dict") else {}
        tool_cfg = getattr(task, "tool_config", None)
        out_schema = getattr(task, "output_schema", None)
        resolved = getattr(task, "resolved_model_config", None)
        inp = getattr(task, "input_payload", None)
        created = getattr(task, "created_at", None)
        started = getattr(task, "started_at", None)
        completed = getattr(task, "completed_at", None)
        error = getattr(task, "error", None)
        verification = getattr(task, "verification_result", None)
        summary = getattr(task, "result_summary", None)

    # Never expose raw input payload, even if base already contained it.
    base.pop("input_payload", None)

    # Sanitize free-text/error paths for secret patterns (truthful otherwise).
    if isinstance(error, str):
        base["error"] = sanitize_error_text(error)
    elif error is not None:
        base["error"] = sanitize_value(error)
    if isinstance(summary, str):
        base["result_summary"] = _redact_string_patterns(summary)
    if isinstance(verification, (Mapping, list)):
        base["verification_result"] = sanitize_value(verification)
    elif isinstance(verification, str):
        base["verification_result"] = _redact_string_patterns(verification)

    base["tool_config"] = sanitize_tool_config(tool_cfg)
    base["output_schema"] = sanitize_output_schema(out_schema)
    base["resolved_model"] = sanitize_resolved_model(resolved)
    base["input_summary"] = summarize_input(inp)
    base["timing"] = build_timing(created, started, completed)
    return base


def describe_organization(org: Any, *, requesting_project_id: str) -> dict[str, Any]:
    """Project-isolated safe organization snapshot. No cross-project leak.

    Raises PermissionError when the organization belongs to another project;
    the error carries no task content.
    """
    if isinstance(org, Mapping):
        org_project = str(org.get("project_id"))
        org_id = str(org.get("organization_id"))
        tasks = org.get("tasks", [])
        get = org.get
    else:
        org_project = str(getattr(org, "project_id", ""))
        org_id = str(getattr(org, "organization_id", ""))
        tasks = getattr(org, "tasks", {})
        get = None

    if org_project != str(requesting_project_id):
        raise PermissionError("cross-project telemetry denied")

    if isinstance(tasks, Mapping):
        task_list = list(tasks.values())
    else:
        task_list = list(tasks or [])

    # Enforce per-task project scope as well (defense in depth).
    scoped = [t for t in task_list if (_task_project(t) or org_project) == str(requesting_project_id)]

    safe_tasks = [describe_task(t) for t in scoped]
    counts = lifecycle_counts(scoped)
    active = sum(1 for t in scoped if is_task_active(t))
    return {
        "organization_id": org_id,
        "project_id": org_project,
        "active_count": active,
        "lifecycle_counts": counts,
        "tasks": safe_tasks,
        "task_count": len(safe_tasks),
    }


def resolve_active_organization(
    organizations: Sequence[Any] | None,
    *,
    requesting_project_id: str | None = None,
) -> dict[str, Any]:
    """Truthful active_organization: null valid if none; explicit status.

    Never fabricates an organization. Statuses: none | available |
    unknown | unavailable.
    """
    if organizations is None:
        return {"active_organization": None, "active_organization_status": "unavailable"}
    scoped = list(organizations)
    if requesting_project_id is not None:
        scoped = [
            o for o in scoped
            if str(o.get("project_id") if isinstance(o, Mapping) else getattr(o, "project_id", "")) == str(requesting_project_id)
        ]
    if not scoped:
        return {"active_organization": None, "active_organization_status": "none"}
    # Prefer the first organization with truthful active work; otherwise first.
    for org in scoped:
        tasks = org.get("tasks", []) if isinstance(org, Mapping) else getattr(org, "tasks", {})
        values = list(tasks.values()) if isinstance(tasks, Mapping) else list(tasks or [])
        if any(is_task_active(t) for t in values):
            chosen = org
            break
    else:
        chosen = scoped[0]
    if isinstance(chosen, Mapping):
        summary = {
            "organization_id": str(chosen.get("organization_id")),
            "project_id": str(chosen.get("project_id")),
        }
    else:
        summary = {
            "organization_id": str(getattr(chosen, "organization_id", "unknown")),
            "project_id": str(getattr(chosen, "project_id", "unknown")),
        }
    if not summary["organization_id"] or summary["organization_id"] == "unknown":
        return {"active_organization": None, "active_organization_status": "unknown"}
    return {"active_organization": summary, "active_organization_status": "available"}


__all__ = [
    "REDACTED",
    "ALL_TASK_STATUSES",
    "ACTIVE_TASK_STATUSES",
    "sanitize_value",
    "sanitize_tool_config",
    "sanitize_output_schema",
    "sanitize_resolved_model",
    "summarize_input",
    "sanitize_error_text",
    "build_timing",
    "is_active_status",
    "is_task_active",
    "count_active",
    "lifecycle_counts",
    "describe_task",
    "describe_organization",
    "resolve_active_organization",
]
