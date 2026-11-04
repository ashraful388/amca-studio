"""Anthropic Messages API adapter with a lazy optional SDK dependency."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from amca import USER_AGENT
from amca.credentials import ProviderCredentialResolver

from .base import (
    ModelAdapter,
    ModelAuthenticationError,
    ModelCapabilities,
    ModelCancelledError,
    ModelConfigurationError,
    ModelError,
    ModelInvalidRequestError,
    ModelProtocolError,
    ModelProviderUnavailableError,
    ModelRateLimitError,
    ModelRequest,
    ModelResponse,
    ModelTimeoutError,
    ModelUnavailableError,
    StreamChunk,
    as_mapping,
    await_if_needed,
    get_field,
)
from .tool_protocol import (
    effective_tool_support,
    ensure_tool_definition,
    format_anthropic_tool_definition,
    parse_anthropic_tool_call,
    build_anthropic_tool_result_block,
    ToolResult,
    ToolResultState,
)


class AnthropicAdapter(ModelAdapter):

    """Use the official asynchronous Anthropic SDK's Messages API."""

    def __init__(
        self,
        *,
        model: str,
        adapter_id: str | None = None,
        api_key: str | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        base_url: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 0,
        default_max_output_tokens: int = 4096,
        capabilities: ModelCapabilities | None = None,
        input_cost_per_million: float = 0.0,
        output_cost_per_million: float = 0.0,
        client: Any = None,
        client_factory: Callable[[], Any] | None = None,
        credential_resolver: ProviderCredentialResolver | None = None,
        credential_provider: str | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if default_max_output_tokens <= 0:
            raise ValueError("default_max_output_tokens must be positive")
        super().__init__(
            adapter_id=adapter_id or f"anthropic:{model}",
            provider="anthropic",
            model=model,
            capabilities=capabilities
            or ModelCapabilities(
                supports_streaming=True,
                supports_tools=True,
                supports_vision=True,
                supports_json=True,
                context_window=None,
                input_modalities=("text", "image"),
            ),
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
            metadata={"base_url": base_url} if base_url else None,
        )
        if client is not None and client_factory is not None:
            raise ValueError("provide client or client_factory, not both")
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._base_url = base_url
        self._timeout = timeout
        self._max_retries = max_retries
        self._default_max_output_tokens = default_max_output_tokens
        self._client = client
        self._client_factory = client_factory
        self._credential_resolver = credential_resolver
        self._credential_provider = credential_provider or self.provider

    def _get_client(self, timeout: float | None = None) -> Any:
        client = self._client
        if client is not None:
            if timeout is None or timeout == self._timeout:
                return client
            with_options = getattr(client, "with_options", None)
            if not callable(with_options):
                return client
            return with_options(timeout=timeout)
        if self._client_factory is not None:
            client = self._client_factory()
            self._client = client
            if timeout is not None and timeout != self._timeout:
                with_options = getattr(client, "with_options", None)
                if callable(with_options):
                    return with_options(timeout=timeout)
            return client

        api_key = (
            self._credential_resolver.resolve(
                provider=self._credential_provider,
                environment_variable=self._api_key_env,
                explicit=self._api_key,
            )
            if self._credential_resolver is not None
            else self._api_key or os.environ.get(self._api_key_env)
        )
        if not api_key:
            raise ModelConfigurationError(
                f"Anthropic credential is unavailable; set {self._api_key_env} "
                "or pass api_key"
            )
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise ModelConfigurationError(
                "AnthropicAdapter requires the optional 'anthropic' package"
            ) from exc
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": self._timeout,
            "max_retries": self._max_retries,
            # See amca.USER_AGENT: AMCA identifies itself to every provider.
            "default_headers": {"User-Agent": USER_AGENT},
        }
        if self._base_url:
            kwargs["base_url"] = self._base_url
        self._client = AsyncAnthropic(**kwargs)
        return self._client

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = dict(request.extra)
        reserved = {
            "model",
            "messages",
            "system",
            "temperature",
            "max_tokens",
            "tools",
            "tool_choice",
            "stop_sequences",
        }
        if reserved.intersection(payload):
            raise ValueError("request.extra cannot override normalized request fields")
        messages: list[dict[str, Any]] = []
        system_parts: list[Any] = [request.system] if request.system else []
        for message in request.messages:
            normalized = dict(message)
            role = normalized.get("role")
            if role == "system":
                system_parts.append(normalized.get("content", ""))
            elif role == "tool":
                call_id = str(normalized.get("tool_call_id") or normalized.get("call_id") or "")
                res = ToolResult(
                    call_id=call_id,
                    name=str(normalized.get("name") or normalized.get("tool_name") or "tool"),
                    state=ToolResultState.SUCCESS if not normalized.get("is_error") else ToolResultState.EXECUTION_ERROR,
                    content=str(normalized.get("content", "")),
                )
                block = build_anthropic_tool_result_block(res)
                messages.append({"role": "user", "content": [block]})
            else:
                messages.append(normalized)
        if not messages:
            raise ValueError("Anthropic requests require at least one non-system message")

        payload.update(
            {
                "model": self._resolved_model(request),
                "messages": messages,
                "max_tokens": request.max_output_tokens
                or self._default_max_output_tokens,
            }
        )
        if system_parts:
            if all(isinstance(part, str) for part in system_parts):
                payload["system"] = "\n\n".join(system_parts)
            elif len(system_parts) == 1:
                payload["system"] = system_parts[0]
            else:
                blocks: list[Any] = []
                for part in system_parts:
                    if isinstance(part, str):
                        blocks.append({"type": "text", "text": part})
                    elif isinstance(part, list):
                        blocks.extend(part)
                    else:
                        blocks.append(part)
                payload["system"] = blocks
        if request.temperature is not None and self._accepts_temperature(
            self._resolved_model(request)
        ):
            payload["temperature"] = request.temperature
        if request.tools:
            if effective_tool_support(self.capabilities().supports_tools, self._resolved_model(request)):
                payload["tools"] = [
                    format_anthropic_tool_definition(ensure_tool_definition(tool))
                    for tool in request.tools
                ]
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
        if request.stop:
            payload["stop_sequences"] = list(request.stop)
        return payload

    @staticmethod
    def _accepts_temperature(model: str) -> bool:
        """Return false for Opus 4.7+ models that reject sampling controls."""

        match = re.search(r"(?:^|-)opus-(\d+)-(\d+)(?:-|$)", model.lower())
        if match is None:
            return True
        major, minor = (int(part) for part in match.groups())
        return (major, minor) < (4, 7)

    @staticmethod
    def _response_text(message: Any) -> str:
        return "".join(
            str(get_field(block, "text", ""))
            for block in (get_field(message, "content", ()) or ())
            if get_field(block, "type") == "text"
        )

    def _parse_response(self, message: Any, request: ModelRequest) -> ModelResponse:
        if message is None:
            raise ModelProtocolError("Anthropic Messages API returned no message")
        usage = get_field(message, "usage", {}) or {}
        raw_blocks = [
            block
            for block in (get_field(message, "content", ()) or ())
            if get_field(block, "type") == "tool_use"
        ]
        org_id = str(request.metadata.get("organization_id") or "amca")
        task_id = str(request.metadata.get("task_id") or "task")
        round_idx = int(request.metadata.get("round_index", 0))
        parsed_calls = [
            parse_anthropic_tool_call(
                block,
                index=idx,
                organization_id=org_id,
                task_id=task_id,
                round_index=round_idx,
            )
            for idx, block in enumerate(raw_blocks)
        ]
        return ModelResponse(
            text=self._response_text(message),
            model=str(get_field(message, "model", self._resolved_model(request))),
            provider=self.provider,
            usage=self._usage(
                get_field(usage, "input_tokens", 0),
                get_field(usage, "output_tokens", 0),
            ),
            finish_reason=get_field(message, "stop_reason"),
            tool_calls=tuple(parsed_calls),
            provider_metadata={"response_id": get_field(message, "id")},
            raw=message,
        )


    @staticmethod
    def _classify_error(exc: BaseException) -> type[ModelError] | None:
        """Classify an SDK/transport exception into an AMCA category.

        Anthropic SDK exception hierarchy: ``APIError`` base,
        ``APIStatusError`` (with ``status_code``), ``APITimeoutError``,
        ``APIConnectionError``, ``AuthenticationError``,
        ``PermissionDeniedError``, ``NotFoundError``,
        ``UnprocessableEntityError``, ``RateLimitError``,
        ``BadRequestError``, ``InternalServerError``.
        Resolved lazily by name so the optional ``anthropic`` package
        does not need to be installed at import time.
        """
        if isinstance(exc, (ModelConfigurationError, ModelProtocolError,
                                ModelAuthenticationError, ModelRateLimitError,
                                ModelInvalidRequestError, ModelTimeoutError,
                                ModelCancelledError)):
            return None  # already classified
        import asyncio

        if isinstance(exc, asyncio.CancelledError):
            return ModelCancelledError
        if isinstance(exc, asyncio.TimeoutError):
            return ModelTimeoutError

        try:
            from anthropic import (  # type: ignore[import-not-found]
                APIError as _AnthropicAPIError,
                APITimeoutError as _AnthropicAPITimeoutError,
                APIConnectionError as _AnthropicAPIConnectionError,
            )
        except Exception:
            _AnthropicAPIError = None  # type: ignore[assignment]
            _AnthropicAPITimeoutError = None  # type: ignore[assignment]
            _AnthropicAPIConnectionError = None  # type: ignore[assignment]
        if _AnthropicAPIError is not None and isinstance(exc, _AnthropicAPIError):
            status = getattr(exc, "status_code", None)
            if _AnthropicAPITimeoutError is not None and isinstance(exc, _AnthropicAPITimeoutError):
                return ModelTimeoutError
            if status in (401, 403):
                return ModelAuthenticationError
            if status == 429:
                return ModelRateLimitError
            if status in (400, 404, 422):
                return ModelInvalidRequestError
            if _AnthropicAPIConnectionError is not None and isinstance(exc, _AnthropicAPIConnectionError):
                return ModelProviderUnavailableError
            if status is not None and 500 <= int(status) < 600:
                return ModelProviderUnavailableError
            return ModelProviderUnavailableError
        return ModelProviderUnavailableError


    def _translate_error(self, exc: Exception) -> Exception:
        """Translate a single SDK/transport exception into the AMCA error.

        Returns the existing exception when it is already an AMCA category.
        """
        if isinstance(exc, ModelError):
            return exc
        category = self._classify_error(exc)
        if category is None:
            return exc
        message = f"Anthropic {type(exc).__name__}"
        status = getattr(exc, "status_code", None)
        if status is not None:
            message = f"{message} (HTTP {status})"
        return category(message)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        async with self._call_scope(request):
            try:
                message = await await_if_needed(
                    self._get_client(self._transport_timeout(request)).messages.create(**self._payload(request))
                )
            except Exception as exc:
                translated = self._translate_error(exc)
                if translated is exc:
                    raise
                raise translated from exc
            return self._parse_response(message, request)

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        async with self._call_scope(request):
            try:
                stream_context = self._get_client(self._transport_timeout(request)).messages.stream(
                    **self._payload(request)
                )
                async with stream_context as stream:
                    pieces: list[str] = []
                    async for delta in stream.text_stream:
                        text = str(delta)
                        if text:
                            pieces.append(text)
                            yield StreamChunk(
                                delta=text,
                                model=self._resolved_model(request),
                                provider=self.provider,
                            )
                    final = await await_if_needed(stream.get_final_message())
            except Exception as exc:
                translated = self._translate_error(exc)
                if translated is exc:
                    raise
                raise translated from exc

            response = self._parse_response(final, request)
            if not response.text and pieces:
                response = ModelResponse(
                    text="".join(pieces),
                    model=response.model,
                    provider=response.provider,
                    usage=response.usage,
                    finish_reason=response.finish_reason,
                    tool_calls=response.tool_calls,
                    provider_metadata=response.provider_metadata,
                    raw=response.raw,
                )
            yield StreamChunk(done=True, response=response)
