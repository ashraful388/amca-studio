"""OpenAI Responses API adapter with a lazy optional SDK dependency."""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

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
    format_openai_tool_definition,
    parse_openai_tool_call,
    build_openai_tool_result_message,
    ToolResult,
    ToolResultState,
)


class OpenAIAdapter(ModelAdapter):

    """Use the official asynchronous OpenAI SDK's Responses API.

    The ``openai`` package is imported, and ``OPENAI_API_KEY`` is read, only
    when the first request is made.  Tests and embedders may inject an already
    constructed client or a no-argument client factory.
    """

    def __init__(
        self,
        *,
        model: str,
        adapter_id: str | None = None,
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
        timeout: float = 120.0,
        capabilities: ModelCapabilities | None = None,
        input_cost_per_million: float = 0.0,
        output_cost_per_million: float = 0.0,
        client: Any = None,
        client_factory: Callable[[], Any] | None = None,
        credential_resolver: ProviderCredentialResolver | None = None,
        stream_close_timeout: float = 5.0,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not math.isfinite(stream_close_timeout) or stream_close_timeout <= 0:
            raise ValueError("stream_close_timeout must be finite and positive")
        super().__init__(
            adapter_id=adapter_id or f"openai:{model}",
            provider="openai",
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
        self._client = client
        self._client_factory = client_factory
        self._credential_resolver = credential_resolver
        self._stream_close_timeout = float(stream_close_timeout)
        self._client_was_injected = client is not None

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
                provider=self.provider,
                environment_variable=self._api_key_env,
                explicit=self._api_key,
            )
            if self._credential_resolver is not None
            else self._api_key or os.environ.get(self._api_key_env)
        )
        if not api_key:
            # Round 29 (Meta 401 fix): with NO credential the provider's 401
            # is a missing-key problem, not a probe-verification problem, and
            # the message must never be misread as "verification failed".
            raise ModelConfigurationError(
                "missing API key: the provider has no credential stored in "
                f"AMCA. Paste your API key in this provider's Open section "
                "and save it, or set "
                f"{self._api_key_env}"
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ModelConfigurationError(
                "OpenAIAdapter requires the optional 'openai' package"
            ) from exc
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": self._timeout,
            "max_retries": 0,
        }
        if self._base_url:
            kwargs["base_url"] = self._base_url
        self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = dict(request.extra)
        reserved = {
            "model",
            "input",
            "instructions",
            "temperature",
            "max_output_tokens",
            "tools",
            "tool_choice",
            "metadata",
        }
        if reserved.intersection(payload):
            raise ValueError("request.extra cannot override normalized request fields")
        input_items: list[dict[str, Any]] = []
        for message in request.messages:
            msg = dict(message)
            role = msg.get("role")
            if role == "tool":
                call_id = str(msg.get("tool_call_id") or msg.get("call_id") or "")
                res = ToolResult(
                    call_id=call_id,
                    name=str(msg.get("name") or "tool"),
                    state=ToolResultState.SUCCESS if not msg.get("is_error") else ToolResultState.EXECUTION_ERROR,
                    content=str(msg.get("content", "")),
                )
                input_items.append(build_openai_tool_result_message(res))
            else:
                input_items.append(msg)
        payload.update(
            {
                "model": self._resolved_model(request),
                "input": input_items,
            }
        )
        if request.system:
            payload["instructions"] = request.system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_output_tokens"] = request.max_output_tokens
        if request.tools:
            if effective_tool_support(self.capabilities().supports_tools, self._resolved_model(request)):
                payload["tools"] = [
                    format_openai_tool_definition(ensure_tool_definition(tool))
                    for tool in request.tools
                ]
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
        if request.metadata:
            payload["metadata"] = dict(request.metadata)
        return payload

    @staticmethod
    def _output_text(response: Any) -> str:
        direct = get_field(response, "output_text")
        if isinstance(direct, str):
            return direct
        pieces: list[str] = []
        for item in get_field(response, "output", ()) or ():
            if get_field(item, "type") == "message":
                for content in get_field(item, "content", ()) or ():
                    if get_field(content, "type") in {"output_text", "text"}:
                        pieces.append(str(get_field(content, "text", "")))
        return "".join(pieces)

    def _parse_response(self, response: Any, request: ModelRequest) -> ModelResponse:
        if response is None:
            raise ModelProtocolError("OpenAI Responses API returned no response")
        usage = get_field(response, "usage", {}) or {}
        status = get_field(response, "status")
        incomplete = get_field(response, "incomplete_details")
        finish_reason = get_field(incomplete, "reason") if incomplete else status
        raw_calls = [
            item
            for item in (get_field(response, "output", ()) or ())
            if get_field(item, "type") in {"function_call", "custom_tool_call"}
        ]
        org_id = str(request.metadata.get("organization_id") or "amca")
        task_id = str(request.metadata.get("task_id") or "task")
        round_idx = int(request.metadata.get("round_index", 0))
        parsed_calls = [
            parse_openai_tool_call(
                call,
                index=idx,
                organization_id=org_id,
                task_id=task_id,
                round_index=round_idx,
            )
            for idx, call in enumerate(raw_calls)
        ]
        return ModelResponse(
            text=self._output_text(response),
            model=str(get_field(response, "model", self._resolved_model(request))),
            provider=self.provider,
            usage=self._usage(
                get_field(usage, "input_tokens", 0),
                get_field(usage, "output_tokens", 0),
            ),
            finish_reason=finish_reason,
            tool_calls=tuple(parsed_calls),
            provider_metadata={
                "response_id": get_field(response, "id"),
                "status": status,
            },
            raw=response,
        )


    @staticmethod
    def _classify_error(exc: BaseException) -> type[ModelError] | None:
        """Classify a SDK/transport exception into an AMCA category.

        The OpenAI SDK exposes a documented exception hierarchy:
        ``APIError`` is the base; ``AuthenticationError``, ``RateLimitError``,
        ``BadRequestError``, ``NotFoundError``, ``APITimeoutError``,
        ``APIConnectionError``, ``InternalServerError`` derive from it.
        We resolve them lazily by name so the adapter remains usable when
        the optional ``openai`` package is not installed at runtime.
        """
        # Pass through already-classified AMCA errors.
        if isinstance(exc, (ModelConfigurationError, ModelProtocolError,
                                ModelAuthenticationError, ModelRateLimitError,
                                ModelInvalidRequestError, ModelTimeoutError,
                                ModelCancelledError)):
            return None  # signal pass-through
        # asyncio.CancelledError must be inspected before the SDK class
        # sweep because some SDKs wrap cancellations.
        import asyncio

        if isinstance(exc, asyncio.CancelledError):
            return ModelCancelledError
        if isinstance(exc, asyncio.TimeoutError):
            return ModelTimeoutError

        try:
            from openai import (  # type: ignore[import-not-found]
                APIError as _OpenAIAPIError,
                APITimeoutError as _OpenAIAPITimeoutError,
                APIConnectionError as _OpenAIAPIConnectionError,
            )
        except Exception:
            _OpenAIAPIError = None  # type: ignore[assignment]
            _OpenAIAPITimeoutError = None  # type: ignore[assignment]
            _OpenAIAPIConnectionError = None  # type: ignore[assignment]
        if _OpenAIAPIError is not None and isinstance(exc, _OpenAIAPIError):
            status = getattr(exc, "status_code", None)
            if _OpenAIAPITimeoutError is not None and isinstance(exc, _OpenAIAPITimeoutError):
                return ModelTimeoutError
            if status == 401 or status == 403:
                return ModelAuthenticationError
            if status == 429:
                return ModelRateLimitError
            if status in (400, 404, 422):
                return ModelInvalidRequestError
            if _OpenAIAPIConnectionError is not None and isinstance(exc, _OpenAIAPIConnectionError):
                return ModelProviderUnavailableError
            if status is not None and 500 <= int(status) < 600:
                return ModelProviderUnavailableError
            return ModelProviderUnavailableError
        # Generic fallback: keep the historical ModelUnavailableError
        # mapping so callers that already catch that category continue to
        # work for unknown SDK errors.
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
        message = f"OpenAI {type(exc).__name__}"
        status = getattr(exc, "status_code", None)
        if status is not None:
            message = f"{message} (HTTP {status})"
        return category(message)

    @staticmethod
    def _consume_close_result(task: asyncio.Future[Any]) -> None:
        """Retrieve late close failures without delaying cancellation."""

        if task.cancelled():
            return
        try:
            task.exception()
        except BaseException:
            pass

    async def _close_stream(self, sdk_stream: Any) -> None:
        """Bound provider stream shutdown so cancellation cannot hang AMCA."""

        close = getattr(sdk_stream, "close", None)
        if close is None:
            close = getattr(sdk_stream, "aclose", None)
        if not callable(close):
            return
        result = close()
        if not hasattr(result, "__await__"):
            return
        close_task = asyncio.ensure_future(result)
        done, _ = await asyncio.wait(
            (close_task,), timeout=self._stream_close_timeout
        )
        if close_task in done:
            await close_task
            return
        close_task.cancel()
        close_task.add_done_callback(self._consume_close_result)
        # A timed-out transport is not safe to reuse. SDK-created clients can
        # be recreated lazily from the existing secure credential resolver.
        if not self._client_was_injected:
            self._client = None

    async def generate(self, request: ModelRequest) -> ModelResponse:
        async with self._call_scope(request):
            try:
                response = await await_if_needed(
                    self._get_client(self._transport_timeout(request)).responses.create(**self._payload(request))
                )
            except Exception as exc:
                translated = self._translate_error(exc)
                if translated is exc:
                    raise
                raise translated from exc
            return self._parse_response(response, request)

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        async with self._call_scope(request):
            pieces: list[str] = []
            final: Any = None
            sdk_stream: Any = None
            try:
                sdk_stream = await await_if_needed(
                    self._get_client(self._transport_timeout(request)).responses.create(
                        **self._payload(request), stream=True
                    )
                )
                async for event in sdk_stream:
                    event_type = get_field(event, "type", "")
                    if event_type == "response.output_text.delta":
                        delta = str(get_field(event, "delta", ""))
                        if delta:
                            pieces.append(delta)
                            yield StreamChunk(
                                delta=delta,
                                model=self._resolved_model(request),
                                provider=self.provider,
                                raw=event,
                            )
                    elif event_type in {
                        "response.completed",
                        "response.incomplete",
                        "response.failed",
                    }:
                        final = get_field(event, "response", final)
                        if event_type == "response.failed":
                            raise ModelUnavailableError("OpenAI streaming response failed")
                    elif event_type == "error":
                        raise ModelUnavailableError("OpenAI streaming response failed")
            except Exception as exc:
                translated = self._translate_error(exc)
                if translated is exc:
                    raise
                raise translated from exc
            finally:
                if sdk_stream is not None:
                    await self._close_stream(sdk_stream)

            if final is None:
                response = ModelResponse(
                    text="".join(pieces),
                    model=self._resolved_model(request),
                    provider=self.provider,
                    finish_reason="stream_completed",
                )
            else:
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
