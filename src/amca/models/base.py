"""Provider-neutral model contracts used by the AMCA collaboration kernel.

The objects in this module deliberately depend only on the Python standard
library.  Provider SDKs are implementation details of concrete adapters and
must never be imported as a side effect of importing :mod:`amca.models`.
"""

from __future__ import annotations

import asyncio
import json
import math
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


class ModelError(RuntimeError):
    """Base class for failures raised by model adapters."""


class ModelConfigurationError(ModelError):
    """The adapter is missing a credential or required configuration."""


class ModelProtocolError(ModelError):
    """A provider returned data that does not match its documented protocol."""


class ModelUnavailableError(ModelError):
    """The requested model/provider is temporarily unavailable."""


class ModelAuthenticationError(ModelError):
    """The provider rejected the request because of credential or scope issues."""


class ModelRateLimitError(ModelError):
    """The provider returned 429 or equivalent throttling signal."""


class ModelInvalidRequestError(ModelError):
    """The provider rejected the request as malformed (HTTP 400/422 or SDK BadRequest)."""


class ModelTimeoutError(ModelUnavailableError):
    """The provider transport reached the bounded request timeout.

    Inherits from ModelUnavailableError so the pre-M1.1.1 contract
    (except ModelUnavailableError catches a transport timeout) is
    preserved.
    """


class ModelProviderUnavailableError(ModelUnavailableError):
    """The provider itself is unreachable (network/DNS/5xx/transport).

    Inherits from ModelUnavailableError so existing
    except ModelUnavailableError callers continue to work. When a 404
    cannot be safely interpreted as a specific model absence (i.e.
    without reading an unsafe raw body), this broader bounded category
    is used and the ambiguity is documented at the call site.
    """


class ModelCancelledError(ModelError):
    """The request was cancelled by the caller (asyncio.CancelledError or AMCA cancel())."""


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Provider-neutral declaration of a model's supported operations."""

    supports_streaming: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    supports_audio: bool = False
    supports_json: bool = False
    supports_system_messages: bool = True
    is_local: bool = False
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_modalities: tuple[str, ...] = ("text",)
    output_modalities: tuple[str, ...] = ("text",)

    @property
    def streaming(self) -> bool:
        """Short alias retained for policy/routing code."""

        return self.supports_streaming

    @property
    def tools(self) -> bool:
        return self.supports_tools

    @property
    def local(self) -> bool:
        return self.is_local


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Normalized token and API-price accounting for one model call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        if not math.isfinite(self.cost_usd) or self.cost_usd < 0:
            raise ValueError("cost_usd must be a finite, non-negative value")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: object) -> TokenUsage:
        if not isinstance(other, TokenUsage):
            return NotImplemented
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


def _freeze_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


def _freeze_mappings(
    values: Sequence[Mapping[str, Any]] | None,
) -> tuple[Mapping[str, Any], ...]:
    return tuple(_freeze_mapping(value) for value in (values or ()))


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """A provider-neutral generation request.

    ``messages`` follows the common ``role``/``content`` shape.  A plain string
    is accepted as a convenience and becomes one user message. ``extra`` is an
    explicit escape hatch for documented provider options; adapters protect
    their required fields from being overwritten by it.
    """

    messages: Sequence[Mapping[str, Any]] | str
    model: str | None = None
    system: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    tools: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    tool_choice: Any = None
    stop: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    extra: Mapping[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        raw_messages: Sequence[Mapping[str, Any]]
        if isinstance(self.messages, str):
            raw_messages = ({"role": "user", "content": self.messages},)
        else:
            raw_messages = self.messages
        if not raw_messages:
            raise ValueError("messages must contain at least one message")

        normalized: list[Mapping[str, Any]] = []
        for message in raw_messages:
            if not isinstance(message, Mapping):
                raise TypeError("each message must be a mapping")
            role = message.get("role")
            if not isinstance(role, str) or not role.strip():
                raise ValueError("each message requires a non-empty role")
            if "content" not in message:
                raise ValueError("each message requires content")
            normalized.append(_freeze_mapping(message))

        if self.temperature is not None and not math.isfinite(self.temperature):
            raise ValueError("temperature must be finite")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.timeout_seconds is not None and (
            not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if self.request_id is not None and not self.request_id.strip():
            raise ValueError("request_id cannot be blank")

        object.__setattr__(self, "messages", tuple(normalized))
        object.__setattr__(self, "tools", _freeze_mappings(self.tools))
        object.__setattr__(self, "stop", tuple(self.stop))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))
        object.__setattr__(self, "extra", _freeze_mapping(self.extra))

    @classmethod
    def from_prompt(cls, prompt: str, **kwargs: Any) -> ModelRequest:
        return cls(messages=prompt, **kwargs)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Normalized result of a completed model call."""

    text: str
    model: str
    provider: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str | None = None
    tool_calls: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)
    raw: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_calls", _freeze_mappings(self.tool_calls))
        object.__setattr__(
            self, "provider_metadata", _freeze_mapping(self.provider_metadata)
        )

    @property
    def input_tokens(self) -> int:
        return self.usage.input_tokens

    @property
    def output_tokens(self) -> int:
        return self.usage.output_tokens

    @property
    def total_tokens(self) -> int:
        return self.usage.total_tokens

    @property
    def cost_usd(self) -> float:
        return self.usage.cost_usd


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """One normalized streaming delta, or the terminal stream record."""

    delta: str = ""
    done: bool = False
    model: str | None = None
    provider: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str | None = None
    tool_calls: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    response: ModelResponse | None = None
    raw: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_calls", _freeze_mappings(self.tool_calls))
        if self.done and self.response is not None:
            if self.model is None:
                object.__setattr__(self, "model", self.response.model)
            if self.provider is None:
                object.__setattr__(self, "provider", self.response.provider)
            if self.usage == TokenUsage():
                object.__setattr__(self, "usage", self.response.usage)
            if self.finish_reason is None:
                object.__setattr__(self, "finish_reason", self.response.finish_reason)

    @property
    def text(self) -> str:
        return self.delta

    @property
    def input_tokens(self) -> int:
        return self.usage.input_tokens

    @property
    def output_tokens(self) -> int:
        return self.usage.output_tokens

    @property
    def cost_usd(self) -> float:
        return self.usage.cost_usd


_SENSITIVE_METADATA_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
)


class ModelAdapter(ABC):
    """Abstract asynchronous interface implemented by every model provider."""

    def __init__(
        self,
        *,
        adapter_id: str,
        provider: str,
        model: str,
        capabilities: ModelCapabilities | None = None,
        input_cost_per_million: float = 0.0,
        output_cost_per_million: float = 0.0,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if not adapter_id.strip():
            raise ValueError("adapter_id cannot be blank")
        if not provider.strip():
            raise ValueError("provider cannot be blank")
        if not model.strip():
            raise ValueError("model cannot be blank")
        if not all(
            math.isfinite(price) and price >= 0
            for price in (input_cost_per_million, output_cost_per_million)
        ):
            raise ValueError("model prices must be finite and non-negative")

        self._adapter_id = adapter_id
        self._provider = provider
        self._model = model
        self._capabilities = capabilities or ModelCapabilities()
        self._input_cost_per_million = float(input_cost_per_million)
        self._output_cost_per_million = float(output_cost_per_million)
        self._metadata = {
            key: value
            for key, value in dict(metadata or {}).items()
            if not any(fragment in key.lower() for fragment in _SENSITIVE_METADATA_FRAGMENTS)
        }
        self._active_calls: dict[asyncio.Task[Any], str | None] = {}

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(adapter_id={self.adapter_id!r}, "
            f"provider={self.provider!r}, model={self.model!r})"
        )

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate a complete response."""

    @abstractmethod
    def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        """Yield text deltas and exactly one terminal ``done`` chunk."""

    async def cancel(self, request_id: str | None = None) -> bool:
        """Cancel in-flight calls, optionally restricting by request ID.

        Provider transports differ in their cancellation support.  Cancelling
        the owning asyncio tasks provides a safe common denominator.
        """

        current = asyncio.current_task()
        targets = [
            task
            for task, call_request_id in tuple(self._active_calls.items())
            if task is not current
            and (request_id is None or request_id == call_request_id)
            and not task.done()
        ]
        for task in targets:
            task.cancel()
        return bool(targets)

    def estimate_tokens(self, value: ModelRequest | str | Sequence[Any]) -> int:
        """Return a dependency-free conservative token estimate.

        Exact tokenizer packages are intentionally optional.  Four UTF-8 bytes
        per token plus small per-message overhead is adequate for routing and
        preflight budgeting; provider-reported usage replaces it after a call.
        """

        if isinstance(value, ModelRequest):
            serialized = self._request_text(value)
            overhead = 4 * len(value.messages)
        elif isinstance(value, str):
            serialized = value
            overhead = 0
        else:
            serialized = json.dumps(value, ensure_ascii=False, default=str)
            overhead = 0
        byte_count = len(serialized.encode("utf-8"))
        return max(1, math.ceil(byte_count / 4) + overhead)

    def estimate_cost(self, input_tokens: int, output_tokens: int = 0) -> float:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        return (
            input_tokens * self._input_cost_per_million
            + output_tokens * self._output_cost_per_million
        ) / 1_000_000

    def context_limit(self) -> int | None:
        return self._capabilities.context_window

    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

    def provider_metadata(self) -> Mapping[str, Any]:
        """Return non-secret adapter metadata suitable for telemetry."""

        return MappingProxyType(
            {
                **self._metadata,
                "adapter_id": self.adapter_id,
                "provider": self.provider,
                "model": self.model,
                "input_cost_per_million": self._input_cost_per_million,
                "output_cost_per_million": self._output_cost_per_million,
            }
        )

    def _resolved_model(self, request: ModelRequest) -> str:
        return request.model or self.model

    def _transport_timeout(self, request: ModelRequest) -> float:
        """Return the bounded timeout the transport must use for one request.

        Precedence (deterministic, no new global setting):

            runtime/request timeout
                    ↓
            existing adapter ceiling/policy (self._timeout where defined)
                    ↓
            effective bounded timeout
                    ↓
            provider transport

        A stricter existing adapter ceiling is never bypassed by a caller.
        Subclasses without ``self._timeout`` (e.g. the offline scripted
        adapter) receive the raw request timeout or zero as applicable.
        """

        adapter_ceiling = getattr(self, "_timeout", None)
        requested = getattr(request, "timeout_seconds", None)
        if adapter_ceiling is None:
            return float(requested) if requested is not None else 0.0
        if requested is None:
            return float(adapter_ceiling)
        return float(min(adapter_ceiling, requested))

    def _usage(
        self, input_tokens: int | None, output_tokens: int | None
    ) -> TokenUsage:
        normalized_input = max(0, int(input_tokens or 0))
        normalized_output = max(0, int(output_tokens or 0))
        return TokenUsage(
            input_tokens=normalized_input,
            output_tokens=normalized_output,
            cost_usd=self.estimate_cost(normalized_input, normalized_output),
        )

    @staticmethod
    def _request_text(request: ModelRequest) -> str:
        pieces: list[str] = []
        if request.system:
            pieces.append(request.system)
        for message in request.messages:
            content = message.get("content", "")
            if isinstance(content, str):
                pieces.append(content)
            else:
                pieces.append(json.dumps(content, ensure_ascii=False, default=str))
        return "\n".join(pieces)

    @asynccontextmanager
    async def _call_scope(self, request: ModelRequest) -> AsyncIterator[None]:
        task = asyncio.current_task()
        if task is not None:
            self._active_calls[task] = request.request_id
        try:
            yield
        finally:
            if task is not None:
                self._active_calls.pop(task, None)


def get_field(value: Any, name: str, default: Any = None) -> Any:
    """Read a field from either an SDK object or a JSON-style mapping."""

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def as_mapping(value: Any) -> Mapping[str, Any]:
    """Convert an SDK value to a shallow, serialization-friendly mapping."""

    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    result: dict[str, Any] = {}
    for name in ("id", "type", "name", "arguments", "call_id", "input", "index"):
        field_value = get_field(value, name)
        if field_value is not None:
            result[name] = field_value
    function = get_field(value, "function")
    if function is not None:
        result["function"] = as_mapping(function)
    return result


async def await_if_needed(value: Any) -> Any:
    """Await SDK coroutine results while keeping simple synchronous fakes usable."""

    if hasattr(value, "__await__"):
        return await value
    return value


from .tool_protocol import (
    ToolDefinition,
    ToolCall,
    ToolResult,
    ToolResultState,
    effective_tool_support,
)
