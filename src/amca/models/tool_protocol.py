"""Canonical Tool Protocol Foundation (17E1, 17E2, 17E3).

Provides provider-neutral representations for:
1. ToolDefinition
2. ToolCall (with deterministic synthetic IDs and fail-closed argument validation)
3. ToolResult & ToolResultState
4. Provider-specific adapter normalization and reinjection (OpenAI, Anthropic, Gemini, Ollama)
5. Effective tool capability truth table & router intersection
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import threading
from datetime import UTC, datetime
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import json
import re
from typing import Any, Callable, Mapping


# Maximum allowed parameter nesting depth and argument size
MAX_ARGUMENT_DEPTH: int = 6
MAX_ARGUMENT_BYTES: int = 16384
MAX_TOOL_NAME_LENGTH: int = 64
MAX_TOOL_DESCRIPTION_LENGTH: int = 1024
MAX_TOOL_PARAMETERS_BYTES: int = 8192
MAX_RESULT_CONTENT_BYTES: int = 65536

_TOOL_NAME_REGEX = re.compile(r"^[a-z0-9._-]{1,64}$")


class ToolResultState(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    EXECUTION_ERROR = "execution_error"
    MALFORMED_REQUEST = "malformed_request"
    UNKNOWN_TOOL = "unknown_tool"


@dataclass(frozen=True, slots=True)
class ToolDefinition(Mapping[str, Any]):
    """Canonical, provider-neutral definition of an available governed tool."""

    name: str
    description: str
    parameters: dict[str, Any]
    strict: bool = True
    category: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _TOOL_NAME_REGEX.match(self.name):
            raise ValueError(
                f"invalid tool name {self.name!r}: must match ^[a-z0-9._-]{{1,{MAX_TOOL_NAME_LENGTH}}}$"
            )
        if not isinstance(self.description, str) or len(self.description) > MAX_TOOL_DESCRIPTION_LENGTH:
            raise ValueError(
                f"tool description must be <= {MAX_TOOL_DESCRIPTION_LENGTH} characters"
            )
        if not isinstance(self.parameters, dict):
            raise ValueError("tool parameters must be a dictionary JSON schema")
        params_bytes = len(json.dumps(self.parameters, ensure_ascii=False).encode("utf-8"))
        if params_bytes > MAX_TOOL_PARAMETERS_BYTES:
            raise ValueError(
                f"tool parameters exceed {MAX_TOOL_PARAMETERS_BYTES} bytes: {params_bytes}"
            )

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __iter__(self):
        return iter(("name", "description", "parameters", "strict", "category"))

    def __len__(self) -> int:
        return 5


@dataclass(frozen=True, slots=True)
class ToolCall(Mapping[str, Any]):
    """Canonical model tool call."""

    call_id: str
    name: str
    arguments: dict[str, Any]
    provider_call_id: str | None = None
    index: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.call_id, str) or not self.call_id:
            raise ValueError("ToolCall requires a non-empty call_id")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("ToolCall requires a non-empty name")
        if not isinstance(self.arguments, dict):
            raise ValueError("ToolCall arguments must be a dictionary")

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __iter__(self):
        return iter(("call_id", "name", "arguments", "provider_call_id", "index"))

    def __len__(self) -> int:
        return 5


@dataclass(frozen=True, slots=True)
class ToolResult(Mapping[str, Any]):
    """Canonical execution result returned to a model."""

    call_id: str
    name: str
    state: ToolResultState
    content: str
    is_error: bool = False
    exit_status: int | None = None
    details: dict[str, Any] = field(default_factory=dict)
    arguments: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 20W bounded normalization: accept a valid state string by coercing
        # it to the canonical enum so provider reinjection (.value access)
        # cannot fail merely because a caller passed the string equivalent.
        if not isinstance(self.state, ToolResultState):
            try:
                object.__setattr__(self, "state", ToolResultState(str(self.state)))
            except ValueError:
                raise ValueError(
                    f"invalid tool result state: {self.state!r}"
                ) from None
        # derive is_error if state is not SUCCESS
        if self.state != ToolResultState.SUCCESS:
            object.__setattr__(self, "is_error", True)

    def __getitem__(self, key: str) -> Any:
        try:
            val = getattr(self, key)
            if key == "state" and isinstance(val, ToolResultState):
                return val.value
            return val
        except AttributeError:
            raise KeyError(key)

    def __iter__(self):
        return iter(("call_id", "name", "state", "content", "is_error", "exit_status", "details", "arguments"))

    def __len__(self) -> int:
        return 8



def ensure_tool_definition(t: Any) -> ToolDefinition:
    """Ensure an object or mapping is converted to a canonical ToolDefinition."""
    if isinstance(t, ToolDefinition):
        return t
    if isinstance(t, Mapping):
        if t.get("type") == "function" and isinstance(t.get("function"), Mapping):
            fn = t["function"]
            return ToolDefinition(
                name=str(fn.get("name", "")),
                description=str(fn.get("description", "")),
                parameters=dict(fn.get("parameters", {})),
                strict=bool(fn.get("strict", True)),
            )
        if "input_schema" in t and isinstance(t["input_schema"], Mapping):
            return ToolDefinition(
                name=str(t.get("name", "")),
                description=str(t.get("description", "")),
                parameters=dict(t["input_schema"]),
                strict=bool(t.get("strict", True)),
            )
        if "name" in t and "parameters" in t:
            return ToolDefinition(
                name=str(t["name"]),
                description=str(t.get("description", "")),
                parameters=dict(t.get("parameters", {})),
                strict=bool(t.get("strict", True)),
            )
    raise ValueError(f"Unable to normalize tool definition from {t}")



def build_synthetic_call_id(
    organization_id: str,
    task_id: str,
    round_index: int,
    call_index: int,
    tool_name: str,
) -> str:
    """Generate deterministic synthetic call ID according to 17D rule B."""
    seed = f"{organization_id}|{task_id}|{round_index}|{call_index}|{tool_name}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"amca-call-{digest}"


def calculate_nesting_depth(obj: Any) -> int:
    """Recursively calculate the maximum nesting depth of a dictionary or list."""
    if isinstance(obj, dict):
        if not obj:
            return 1
        return 1 + max(calculate_nesting_depth(v) for v in obj.values())
    elif isinstance(obj, list):
        if not obj:
            return 1
        return 1 + max(calculate_nesting_depth(item) for item in obj)
    return 0


def validate_and_normalize_arguments(
    raw_args: Any,
    call_id: str = "unassigned",
    tool_name: str = "unknown",
) -> tuple[dict[str, Any] | None, ToolResult | None]:
    """Validate argument dictionary against size, scalar, depth, and JSON invariants.

    Fails closed to a ToolResult with MALFORMED_REQUEST if invalid.
    """
    if raw_args is None:
        return None, ToolResult(
            call_id=call_id,
            name=tool_name,
            state=ToolResultState.MALFORMED_REQUEST,
            content=json.dumps({"ok": False, "state": "malformed_request", "error": "arguments cannot be null"}),
            is_error=True,
        )

    # If it is a string, decode JSON once
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except Exception as err:
            return None, ToolResult(
                call_id=call_id,
                name=tool_name,
                state=ToolResultState.MALFORMED_REQUEST,
                content=json.dumps({"ok": False, "state": "malformed_request", "error": f"malformed JSON arguments: {err}"}),
                is_error=True,
            )
    elif isinstance(raw_args, dict):
        parsed = raw_args
    else:
        return None, ToolResult(
            call_id=call_id,
            name=tool_name,
            state=ToolResultState.MALFORMED_REQUEST,
            content=json.dumps({"ok": False, "state": "malformed_request", "error": f"arguments must be an object, got {type(raw_args).__name__}"}),
            is_error=True,
        )

    # Must be a dict (reject scalar and list roots)
    if not isinstance(parsed, dict):
        return None, ToolResult(
            call_id=call_id,
            name=tool_name,
            state=ToolResultState.MALFORMED_REQUEST,
            content=json.dumps({"ok": False, "state": "malformed_request", "error": f"arguments root must be a JSON object, got {type(parsed).__name__}"}),
            is_error=True,
        )

    # Check depth
    depth = calculate_nesting_depth(parsed)
    if depth > MAX_ARGUMENT_DEPTH:
        return None, ToolResult(
            call_id=call_id,
            name=tool_name,
            state=ToolResultState.MALFORMED_REQUEST,
            content=json.dumps({"ok": False, "state": "malformed_request", "error": f"argument nesting depth {depth} exceeds limit of {MAX_ARGUMENT_DEPTH}"}),
            is_error=True,
        )

    # Check serialized byte size
    serialized = json.dumps(parsed, ensure_ascii=False).encode("utf-8")
    if len(serialized) > MAX_ARGUMENT_BYTES:
        return None, ToolResult(
            call_id=call_id,
            name=tool_name,
            state=ToolResultState.MALFORMED_REQUEST,
            content=json.dumps({"ok": False, "state": "malformed_request", "error": f"argument payload size {len(serialized)} bytes exceeds limit of {MAX_ARGUMENT_BYTES}"}),
            is_error=True,
        )

    return parsed, None


# ==============================================================================
# 17E2: Adapter Definition Normalization & Call Parsers & Reinjection Builders
# ==============================================================================

# OpenAI-compatible chat endpoints enforce ``^[A-Za-z0-9_-]{1,64}$`` on the
# function name (OpenAI API reference; enforced by BAI and NVIDIA alike), while
# AMCA's canonical governed names are dotted (``filesystem.write``). The names
# are therefore aliased on the wire only: governance, approval, dispatch and
# audit keep the canonical dotted name, and the alias is reversed when the
# provider's tool call comes back.
_WIRE_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")
#: OpenAI-compatible function-name bound, independent of the canonical limit.
MAX_WIRE_TOOL_NAME_LENGTH: int = 64


def wire_tool_name(name: str) -> str:
    """Return the transport-safe function name for a canonical tool name.

    Fails closed on an empty name and bounds the result to the endpoint's
    64-character limit.  A name that would be truncated keeps a short digest of
    the canonical name, so two long names that share a prefix stay distinct and
    the caller's collision check can still refuse an ambiguous pair.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("wire tool name must be a non-empty string")
    wire = _WIRE_UNSAFE_NAME_CHARS.sub("_", name)
    if len(wire) > MAX_WIRE_TOOL_NAME_LENGTH:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        keep = MAX_WIRE_TOOL_NAME_LENGTH - len(digest) - 1
        wire = f"{wire[:keep]}_{digest}"
    return wire


def wire_tool_name_map(tools: Any) -> dict[str, str]:
    """Map canonical tool name -> wire name for one request's catalog.

    Fails closed on a collision: two catalog entries that would be
    indistinguishable on the wire are refused instead of silently merged, so a
    provider could never return a call that dispatches to the wrong tool.
    """
    mapping: dict[str, str] = {}
    seen: dict[str, str] = {}
    for tool in tools or ():
        canonical = ensure_tool_definition(tool).name
        wire = wire_tool_name(canonical)
        owner = seen.get(wire)
        if owner is not None and owner != canonical:
            raise ValueError(
                "tool wire-name collision: "
                f"{owner!r} and {canonical!r} both serialize as {wire!r}"
            )
        seen[wire] = canonical
        mapping[canonical] = wire
    return mapping


def canonical_tool_name(name: str, mapping: Mapping[str, str]) -> str:
    """Reverse a wire name using the catalog actually sent in this request.

    The true invariant is narrow: the result is always a canonical name that
    was present in the catalog transmitted for this request, so the mapping can
    never widen the set of tools a provider may reach.  It is *not* evidence
    that the provider legitimately emitted that tool — a provider that invents
    a wire alias of an offered tool still resolves to that offered tool, which
    is the same authority the model was given.  Names matching no alias are
    returned unchanged, and an empty mapping (no catalog transmitted) maps
    nothing at all.
    """
    for canonical, wire in (mapping or {}).items():
        if wire == name:
            return canonical
    return name


def format_openai_tool_definition(defn: ToolDefinition) -> dict[str, Any]:
    """Format canonical ToolDefinition for OpenAI Responses API."""
    return {
        "type": "function",
        "name": defn.name,
        "description": defn.description,
        "parameters": defn.parameters,
        "strict": defn.strict,
    }


def format_openai_compatible_tool_definition(defn: ToolDefinition) -> dict[str, Any]:
    """Format canonical ToolDefinition for OpenAI-compatible chat completion."""
    return {
        "type": "function",
        "function": {
            "name": defn.name,
            "description": defn.description,
            "parameters": defn.parameters,
        },
    }


def format_anthropic_tool_definition(defn: ToolDefinition) -> dict[str, Any]:
    """Format canonical ToolDefinition for Anthropic Messages API."""
    return {
        "name": defn.name,
        "description": defn.description,
        "input_schema": defn.parameters,
    }


def format_gemini_tool_definition(defn: ToolDefinition) -> dict[str, Any]:
    """Format canonical ToolDefinition for Gemini generateContent functionDeclarations."""
    params = dict(defn.parameters)
    params.pop("additionalProperties", None)
    return {
        "name": defn.name,
        "description": defn.description,
        "parameters": params,
    }


def format_ollama_tool_definition(defn: ToolDefinition) -> dict[str, Any]:
    """Format canonical ToolDefinition for Ollama chat API."""
    return format_openai_compatible_tool_definition(defn)


def _as_mapping(obj: Any) -> Mapping[str, Any]:
    if isinstance(obj, Mapping):
        return obj
    if hasattr(obj, "__dict__"):
        return getattr(obj, "__dict__")
    return {}


def parse_openai_tool_call(
    raw: Any,
    index: int = 0,
    organization_id: str = "amca",
    task_id: str = "task",
    round_index: int = 0,
) -> ToolCall | ToolResult:
    """Parse an OpenAI or OpenAI-compatible tool call item."""
    raw_map = _as_mapping(raw)
    provider_call_id = raw_map.get("id") or raw_map.get("call_id")
    func = _as_mapping(raw_map.get("function")) if raw_map.get("function") is not None else {}
    name = str(func.get("name") or raw_map.get("name") or "")

    if provider_call_id:
        call_id = str(provider_call_id)
    else:
        call_id = build_synthetic_call_id(organization_id, task_id, round_index, index, name)

    raw_args = func.get("arguments") if "arguments" in func else raw_map.get("arguments")
    args, err_result = validate_and_normalize_arguments(raw_args, call_id=call_id, tool_name=name)
    if err_result is not None:
        return err_result

    return ToolCall(
        call_id=call_id,
        name=name,
        arguments=args or {},
        provider_call_id=str(provider_call_id) if provider_call_id else None,
        index=index,
    )


def parse_anthropic_tool_call(
    raw: Any,
    index: int = 0,
    organization_id: str = "amca",
    task_id: str = "task",
    round_index: int = 0,
) -> ToolCall | ToolResult:
    """Parse an Anthropic tool_use content block."""
    raw_map = _as_mapping(raw)
    provider_call_id = raw_map.get("id")
    name = str(raw_map.get("name") or "")
    if provider_call_id:
        call_id = str(provider_call_id)
    else:
        call_id = build_synthetic_call_id(organization_id, task_id, round_index, index, name)

    raw_input = raw_map.get("input")
    args, err_result = validate_and_normalize_arguments(raw_input, call_id=call_id, tool_name=name)
    if err_result is not None:
        return err_result

    return ToolCall(
        call_id=call_id,
        name=name,
        arguments=args or {},
        provider_call_id=str(provider_call_id) if provider_call_id else None,
        index=index,
    )


def parse_gemini_tool_call(
    raw: Any,
    index: int = 0,
    organization_id: str = "amca",
    task_id: str = "task",
    round_index: int = 0,
) -> ToolCall | ToolResult:
    """Parse a Gemini functionCall part."""
    raw_map = _as_mapping(raw)
    func_call = _as_mapping(raw_map.get("functionCall") or raw_map.get("function_call") or raw_map)
    name = str(func_call.get("name") or "")
    call_id = build_synthetic_call_id(organization_id, task_id, round_index, index, name)

    raw_args = func_call.get("args")
    args, err_result = validate_and_normalize_arguments(raw_args, call_id=call_id, tool_name=name)
    if err_result is not None:
        return err_result

    return ToolCall(
        call_id=call_id,
        name=name,
        arguments=args or {},
        provider_call_id=None,
        index=index,
    )


def parse_ollama_tool_call(
    raw: Any,
    index: int = 0,
    organization_id: str = "amca",
    task_id: str = "task",
    round_index: int = 0,
) -> ToolCall | ToolResult:
    """Parse an Ollama tool call item."""
    raw_map = _as_mapping(raw)
    func = _as_mapping(raw_map.get("function") or raw_map)
    name = str(func.get("name") or "")
    call_id = build_synthetic_call_id(organization_id, task_id, round_index, index, name)

    raw_args = func.get("arguments")
    args, err_result = validate_and_normalize_arguments(raw_args, call_id=call_id, tool_name=name)
    if err_result is not None:
        return err_result

    return ToolCall(
        call_id=call_id,
        name=name,
        arguments=args or {},
        provider_call_id=None,
        index=index,
    )


# Reinjection Builders
def build_openai_tool_result_message(result: ToolResult) -> dict[str, Any]:
    """Format ToolResult as OpenAI Responses function_call_output item."""
    return {
        "type": "function_call_output",
        "call_id": result.call_id,
        "output": result.content,
    }


def build_openai_compatible_tool_result_message(result: ToolResult) -> dict[str, Any]:
    """Format ToolResult as OpenAI-compatible chat role: tool message."""
    return {
        "role": "tool",
        "tool_call_id": result.call_id,
        "content": result.content,
    }


def build_anthropic_tool_result_block(result: ToolResult) -> dict[str, Any]:
    """Format ToolResult as Anthropic tool_result content block."""
    return {
        "type": "tool_result",
        "tool_use_id": result.call_id,
        "content": result.content,
        "is_error": result.is_error,
    }


def build_gemini_tool_result_part(result: ToolResult) -> dict[str, Any]:
    """Format ToolResult as Gemini functionResponse part inside role: user."""
    try:
        response_obj = json.loads(result.content)
    except Exception:
        response_obj = {
            "ok": not result.is_error,
            "state": result.state.value,
            "content": result.content,
        }
    return {
        "functionResponse": {
            "name": result.name,
            "response": response_obj,
        }
    }


def build_ollama_tool_result_message(result: ToolResult) -> dict[str, Any]:
    """Format ToolResult as Ollama role: tool message."""
    return {
        "role": "tool",
        "content": result.content,
        "tool_name": result.name,
    }


# ==============================================================================
# 17E3: Effective Tool Capability Truth
# ==============================================================================

KNOWN_MODEL_TOOL_SUPPORT: Mapping[str, bool] = {
    "qwen3:1.7b": True,
    "dolphin-llama3:8b": False,
    # Standard cloud models
    "gpt-4o": True,
    "gpt-4o-mini": True,
    "gpt-4-turbo": True,
    "gpt-4": True,
    "gpt-3.5-turbo": True,
    "claude-3-5-sonnet": True,
    "claude-3-opus": True,
    "claude-3-haiku": True,
    "claude-3-7-sonnet": True,
    "claude-2": False,
    "gemini-1.5-pro": True,
    "gemini-1.5-flash": True,
    "gemini-2.0-flash": True,
    "gemini-2.5-flash": True,
    "gemini-test": True,
}


def known_model_tool_support(model_name: str) -> bool | None:
    """Static-table verdict for ``model_name``; ``None`` when the model is unknown.

    This is the historical ``KNOWN_MODEL_TOOL_SUPPORT`` lookup, exposed so
    callers can distinguish "known to support tools", "known NOT to support
    tools" and "not in the table" (which must keep failing closed).
    """
    model_key = model_name.strip().lower()
    if model_key.startswith("models/"):
        model_key = model_key[7:]
    # Check exact match
    if model_key in KNOWN_MODEL_TOOL_SUPPORT:
        return KNOWN_MODEL_TOOL_SUPPORT[model_key]
    # Check if basename or prefix matches
    for k, v in KNOWN_MODEL_TOOL_SUPPORT.items():
        if model_key.endswith(k) or (
            not k.startswith("qwen")
            and not k.startswith("dolphin")
            and model_key.startswith(k)
        ):
            return v
    return None


#: AMCA-MR-02: the canonical registry is the authority for tool capability.
#: The service installs a resolver at startup; when none is installed the
#: deprecated fallback below is the only source available.
#:
#: Resolvers are held as a set, not a single slot.  A process can hold more than
#: one service context (tests, an in-process embedder), and a single slot let a
#: second context silently disarm the first one's recorded denials.  A lookup
#: consults every installed resolver and fails closed: any denial, conflict or
#: unavailable registry blocks; a grant counts only when nothing objects.
_REGISTRY_TOOL_RESOLVERS: "list[Callable[[str], Any]]" = []

#: Bounded record of every time the deprecated fallback decided a result, so its
#: remaining use is observable and it can be removed once the count is zero.
_LEGACY_FALLBACK_EVENTS: "deque[dict[str, Any]]" = deque(maxlen=200)
_LEGACY_FALLBACK_COUNT = 0
_LEGACY_LOCK = threading.Lock()
LOGGER = logging.getLogger("amca.tool_protocol")

#: The marking attached to every fallback decision.  This is the "must be
#: marked" requirement: each use is labelled deprecated with a removal target so
#: the path can be deleted later without guesswork.
LEGACY_FALLBACK_MARKER: dict[str, Any] = {
    "deprecated": True,
    "removal_target": "MR-03",
    "source": "legacy_name_prefix_table",
    "reason": "the registry has no record for this model",
}


def install_registry_tool_resolver(resolver: "Callable[[str], Any] | None") -> None:
    """Install the registry lookup used as the authority for tool capability.

    Installing ``None`` clears every resolver (used by tests and teardown).
    """
    with _LEGACY_LOCK:
        if resolver is None:
            _REGISTRY_TOOL_RESOLVERS.clear()
        elif not any(item == resolver for item in _REGISTRY_TOOL_RESOLVERS):
            # Equality, not identity: a bound method is a fresh object on every
            # attribute access, so identity would never match the installed one.
            _REGISTRY_TOOL_RESOLVERS.append(resolver)


def release_registry_tool_resolver(resolver: "Callable[[str], Any] | None") -> None:
    """Release this resolver's authority, leaving any other context's intact."""
    with _LEGACY_LOCK:
        if resolver is None:
            return
        _REGISTRY_TOOL_RESOLVERS[:] = [
            item for item in _REGISTRY_TOOL_RESOLVERS if item != resolver
        ]


def _consult_registry_resolvers(model_name: str) -> dict[str, Any] | None:
    """Ask every installed resolver; fail closed when they disagree.

    Returns ``None`` only when no resolver has a record for this model, which is
    the single case where the caller may use the deprecated fallback.
    """
    with _LEGACY_LOCK:
        resolvers = list(_REGISTRY_TOOL_RESOLVERS)
    if not resolvers:
        return None
    answers: list[dict[str, Any]] = []
    for resolver in resolvers:
        try:
            answer = resolver(model_name)
        except Exception:  # noqa: BLE001 - an unusable lookup means "blocked"
            return {"value": None, "source": "registry_unavailable"}
        if answer is None:
            continue
        if not isinstance(answer, Mapping):
            return {"value": None, "source": "registry_unavailable"}
        answers.append(dict(answer))
    if not answers:
        return None
    if len(answers) > 1:
        # Compare without hashing: a resolver may return an unhashable value,
        # and a comparison must never raise out of a model call.
        def _same(left: Any, right: Any) -> bool:
            return left is right or left == right

        first = answers[0].get("value")
        disagrees = any(
            not _same(answer.get("value"), first) for answer in answers[1:]
        )
        off_source = any(
            str(answer.get("source")) != "registry" for answer in answers
        )
        if disagrees or off_source:
            return {"value": None, "source": "registry_conflict"}
    return answers[0]


def registry_tool_resolution(model_name: str) -> dict[str, Any] | None:
    """The registry's raw answer for a model, or None when it has no record.

    Exposed so a caller that must explain a decision can ask the same authority
    the decision used, instead of re-deriving it from a deprecated table.
    """
    return _consult_registry_resolvers(model_name)


def legacy_fallback_events() -> tuple[dict[str, Any], ...]:
    """The most recent deprecated-fallback decisions, newest last."""
    return tuple(_LEGACY_FALLBACK_EVENTS)


def legacy_fallback_count() -> int:
    """How many times the deprecated fallback has decided a result."""
    return _LEGACY_FALLBACK_COUNT


def reset_legacy_fallback_events() -> None:
    """Clear the fallback record (test isolation)."""
    global _LEGACY_FALLBACK_COUNT
    with _LEGACY_LOCK:
        _LEGACY_FALLBACK_EVENTS.clear()
        _LEGACY_WARNED.clear()
        _LEGACY_FALLBACK_COUNT = 0


#: One warning per distinct model, so a hot path cannot flood the log while the
#: fact that the deprecated path is still taken stays visible.
_LEGACY_WARNED: "set[str]" = set()


def _mark_legacy_fallback(
    model_name: str, verdict: bool | None, *, reason: str | None = None
) -> None:
    """Record one deprecated-fallback decision, in memory and in the log.

    The in-memory deque is bounded, so it cannot prove on its own that the path
    is unused; the log line is the durable half of the evidence.
    """
    global _LEGACY_FALLBACK_COUNT
    model = str(model_name)[:200]
    with _LEGACY_LOCK:
        _LEGACY_FALLBACK_COUNT += 1
        event = {
            **LEGACY_FALLBACK_MARKER,
            "reason": reason or LEGACY_FALLBACK_MARKER["reason"],
            "model": model,
            "verdict": verdict,
            "observed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        }
        _LEGACY_FALLBACK_EVENTS.append(event)
        first_time = model not in _LEGACY_WARNED
        if first_time:
            _LEGACY_WARNED.add(model)
    if first_time:
        LOGGER.warning(
            "deprecated tool-capability fallback used (removal_target=%s): %s",
            LEGACY_FALLBACK_MARKER["removal_target"],
            event,
        )


def effective_tool_support(
    adapter_supports_tools: bool,
    model_name: str,
    route_supports_tools: bool = True,
    declared_model_capability: bool | None = None,
    registry_capability: bool | None = None,
) -> bool:
    """Compute effective tool capability truth.

    Truth is the intersection:
      adapter_supports_tools ∩ model_capability ∩ route_supports_tools

    ``model_capability`` is resolved in this order (AMCA-MR-02).  **The registry
    is the authority**: nothing can grant a capability the registry denies.

    1. If the registry has a record for this model:
       * ``tools=false`` blocks;
       * a fact that is unknown blocks;
       * records that disagree block;
       * ``tools=true`` enables — unless specific negative evidence vetoes it,
         namely a runtime probe that observed this exact model fail, or a
         curated entry saying the model has no tool support.  A veto is recorded
         as a deprecated-fallback event.
    2. If the registry has no record at all, the ordered migration path applies:
       verified runtime evidence for this exact model decides; failing that, the
       deprecated name-prefix table decides and the decision is marked and
       logged so the path can be removed once it is no longer taken.

    A registry lookup that fails counts as unknown and blocks.  An unknown
    capability always fails closed to False.
    """
    # Coerce first: a non-string model name (None from an unset field, bytes
    # from a decode slip) must fail closed, not raise out of a model call.
    model_name = "" if model_name is None else str(model_name)
    if registry_capability is not None:
        resolution: dict[str, Any] | None = {
            "value": registry_capability,
            "source": "registry",
        }
    else:
        resolution = _consult_registry_resolvers(model_name)

    if resolution is not None and not isinstance(resolution, Mapping):
        # The seam is fed by a caller-supplied resolver; anything that is not a
        # resolution mapping is treated as a failed lookup, which blocks.
        _mark_legacy_fallback(model_name, False, reason="registry lookup failed")
        return False

    if resolution is not None:
        if resolution.get("source") in ("registry_conflict", "registry_unavailable"):
            # Two records disagree about this model: block rather than letting
            # one of them win.
            return False
        stated = resolution.get("value")
        if stated is not True and stated is not False:
            # Unknown, or a value that is not a boolean -> block.
            return False
        if stated is False:
            # The registry denies it.  Nothing may override a denial.
            return False
        model_cap = True
        # The registry grants.  Specific negative evidence may still veto:
        # a runtime probe that observed this exact model fail with tools, or a
        # curated entry saying this model has no tool support.  Nothing can
        # grant against the registry.
        if declared_model_capability is False:
            return False
        veto = known_model_tool_support(model_name)
        if veto is False:
            _mark_legacy_fallback(model_name, False, reason="curated model veto")
            return False
    else:
        # No record at all for this model: the ordered migration path.
        if declared_model_capability is not None:
            model_cap = bool(declared_model_capability)
        else:
            static_capability = known_model_tool_support(model_name)
            _mark_legacy_fallback(model_name, static_capability)
            if static_capability is None:
                # Unknown capability -> Fail closed!
                return False
            model_cap = static_capability

    return bool(adapter_supports_tools and model_cap and route_supports_tools)


__all__ = [
    "LEGACY_FALLBACK_MARKER",
    "effective_tool_support",
    "install_registry_tool_resolver",
    "release_registry_tool_resolver",
    "registry_tool_resolution",
    "known_model_tool_support",
    "legacy_fallback_count",
    "legacy_fallback_events",
    "reset_legacy_fallback_events",
    "MAX_ARGUMENT_DEPTH",
    "MAX_ARGUMENT_BYTES",
    "MAX_TOOL_NAME_LENGTH",
    "MAX_WIRE_TOOL_NAME_LENGTH",
    "MAX_TOOL_DESCRIPTION_LENGTH",
    "MAX_TOOL_PARAMETERS_BYTES",
    "MAX_RESULT_CONTENT_BYTES",
    "ToolResultState",
    "ToolDefinition",
    "ToolCall",
    "ToolResult",
    "ensure_tool_definition",
    "build_synthetic_call_id",
    "calculate_nesting_depth",
    "validate_and_normalize_arguments",
    "format_openai_tool_definition",
    "format_openai_compatible_tool_definition",
    "wire_tool_name",
    "wire_tool_name_map",
    "canonical_tool_name",
    "format_anthropic_tool_definition",
    "format_gemini_tool_definition",
    "format_ollama_tool_definition",
    "parse_openai_tool_call",
    "parse_anthropic_tool_call",
    "parse_gemini_tool_call",
    "parse_ollama_tool_call",
    "build_openai_tool_result_message",
    "build_openai_compatible_tool_result_message",
    "build_anthropic_tool_result_block",
    "build_gemini_tool_result_part",
    "build_ollama_tool_result_message",
    "KNOWN_MODEL_TOOL_SUPPORT",
    "known_model_tool_support",
    "effective_tool_support",
]

