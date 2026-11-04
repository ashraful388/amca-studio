"""Google Gemini ``generateContent`` REST adapter.

The adapter intentionally uses the documented HTTPS API directly so importing
AMCA does not require Google's optional SDK.  API keys are resolved only when a
call is made and are sent in the ``x-goog-api-key`` header, never in a URL.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request

from amca import USER_AGENT
from amca.credentials import ProviderCredentialResolver

from .base import (
    ModelAdapter,
    ModelCapabilities,
    ModelConfigurationError,
    ModelProtocolError,
    ModelRequest,
    ModelResponse,
    ModelUnavailableError,
    StreamChunk,
    get_field,
    ModelAuthenticationError,
    ModelProviderUnavailableError,
    ModelRateLimitError,
    ModelInvalidRequestError,
    ModelTimeoutError,
    ModelCancelledError,
)
from .openai_compatible import _urlopen_without_redirects
from .tool_protocol import (
    effective_tool_support,
    ensure_tool_definition,
    format_gemini_tool_definition,
    parse_gemini_tool_call,
    build_gemini_tool_result_part,
    ToolResult,
    ToolResultState,
)


Urlopen: TypeAlias = Callable[..., Any]

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _validated_base_url(value: str, *, allow_custom: bool) -> str:
    cleaned = value.rstrip("/")
    parsed = urlparse(cleaned)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Gemini base_url must be an HTTPS URL without credentials or query")
    if not allow_custom and parsed.hostname != "generativelanguage.googleapis.com":
        raise ValueError("a custom Gemini base_url requires allow_custom_base_url=True")
    return cleaned


def _message_parts(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}]
    if isinstance(content, Mapping):
        return [dict(content)]
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        parts: list[dict[str, Any]] = []
        for item in content:
            if isinstance(item, str):
                parts.append({"text": item})
            elif isinstance(item, Mapping):
                parts.append(dict(item))
            else:
                raise TypeError("Gemini message content parts must be text or mappings")
        if parts:
            return parts
    raise TypeError("Gemini message content must be text, a part, or a list of parts")


def _function_declaration(tool: Mapping[str, Any]) -> dict[str, Any]:
    if "functionDeclarations" in tool or "function_declarations" in tool:
        return dict(tool)
    if tool.get("type") != "function":
        raise ValueError("Gemini supports only function tools in the neutral tool contract")
    function = tool.get("function", tool)
    if not isinstance(function, Mapping) or not str(function.get("name", "")).strip():
        raise ValueError("Gemini function tool requires a non-empty name")
    declaration: dict[str, Any] = {"name": str(function["name"])}
    if function.get("description") is not None:
        declaration["description"] = str(function["description"])
    parameters = function.get("parameters")
    if parameters is not None:
        if not isinstance(parameters, Mapping):
            raise TypeError("Gemini function parameters must be an object")
        declaration["parameters"] = dict(parameters)
    return declaration


def _text_and_calls(
    data: Any,
    organization_id: str = "amca",
    task_id: str = "task",
    round_index: int = 0,
) -> tuple[str, tuple[Mapping[str, Any], ...], str | None]:
    candidates = get_field(data, "candidates", ()) or ()
    if not candidates:
        feedback = get_field(data, "promptFeedback", {}) or {}
        reason = get_field(feedback, "blockReason")
        suffix = f": {reason}" if reason else ""
        raise ModelProtocolError(f"Gemini response has no candidates{suffix}")
    candidate = candidates[0]
    content = get_field(candidate, "content", {}) or {}
    parts = get_field(content, "parts", ()) or ()
    text: list[str] = []
    calls: list[Mapping[str, Any]] = []
    for part in parts:
        part_text = get_field(part, "text")
        if part_text is not None:
            text.append(str(part_text))
        call = get_field(part, "functionCall")
        if call is None:
            call = get_field(part, "function_call")
        if call is not None:
            parsed = parse_gemini_tool_call(
                part,
                index=len(calls),
                organization_id=organization_id,
                task_id=task_id,
                round_index=round_index,
            )
            calls.append(parsed)
    return "".join(text), tuple(calls), get_field(candidate, "finishReason")



class GeminiAdapter(ModelAdapter):
    """Calls Google's documented Gemini content-generation REST API."""

    def __init__(
        self,
        *,
        model: str,
        adapter_id: str | None = None,
        api_key: str | None = None,
        api_key_env: str = "GEMINI_API_KEY",
        base_url: str = _DEFAULT_BASE_URL,
        allow_custom_base_url: bool = False,
        timeout: float = 120.0,
        context_window: int | None = None,
        input_cost_per_million: float = 0.0,
        output_cost_per_million: float = 0.0,
        urlopen_fn: Urlopen | None = None,
        credential_resolver: ProviderCredentialResolver | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        validated = _validated_base_url(base_url, allow_custom=allow_custom_base_url)
        super().__init__(
            adapter_id=adapter_id or f"gemini:{model}",
            provider="gemini",
            model=model,
            capabilities=ModelCapabilities(
                supports_streaming=True,
                supports_tools=True,
                supports_vision=True,
                supports_audio=True,
                supports_json=True,
                supports_system_messages=True,
                is_local=False,
                context_window=context_window,
                input_modalities=("text", "image", "audio", "video", "pdf"),
            ),
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
            metadata={"base_url": validated},
        )
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._base_url = validated
        self._timeout = timeout
        self._urlopen = urlopen_fn or _urlopen_without_redirects
        self._credential_resolver = credential_resolver

    def _resolve_api_key(self) -> str:
        key = (
            self._credential_resolver.resolve(
                provider=self.provider,
                environment_variable=self._api_key_env,
                explicit=self._api_key,
            )
            if self._credential_resolver is not None
            else self._api_key if self._api_key is not None else os.environ.get(self._api_key_env)
        )
        if not key:
            raise ModelConfigurationError(
                f"Gemini credential is unavailable; set {self._api_key_env} before calling"
            )
        return key

    def _endpoint(self, request: ModelRequest, *, stream: bool) -> str:
        model = self._resolved_model(request)
        if model.startswith("models/"):
            model = model[7:]
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return f"{self._base_url}/models/{quote(model, safe='') }:{method}"

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        payload = dict(request.extra)
        thinking_budget = payload.pop("thinking_budget", None)
        reserved = {
            "contents",
            "systemInstruction",
            "system_instruction",
            "generationConfig",
            "generation_config",
            "tools",
            "toolConfig",
            "tool_config",
        }
        if reserved.intersection(payload):
            raise ValueError("request.extra cannot override normalized Gemini fields")

        system_parts: list[dict[str, Any]] = []
        if request.system:
            system_parts.append({"text": request.system})
        contents: list[dict[str, Any]] = []
        for message in request.messages:
            role = str(message["role"]).lower()
            if role in {"system", "developer"}:
                system_parts.extend(_message_parts(message["content"]))
                continue
            if role == "tool":
                call_id = str(message.get("tool_call_id") or message.get("call_id") or "")
                tool_name = str(message.get("name") or message.get("tool_name") or "tool")
                content = str(message.get("content", ""))
                res = ToolResult(
                    call_id=call_id,
                    name=tool_name,
                    state=ToolResultState.SUCCESS if not message.get("is_error") else ToolResultState.EXECUTION_ERROR,
                    content=content,
                )
                part = build_gemini_tool_result_part(res)
                contents.append({"role": "user", "parts": [part]})
                continue
            if role == "assistant":
                role = "model"
            if role not in {"user", "model"}:
                raise ValueError(f"Gemini does not support neutral message role {role!r}")
            contents.append({"role": role, "parts": _message_parts(message["content"])})
        if not contents:
            raise ValueError("Gemini request requires at least one user or model message")
        payload["contents"] = contents
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        generation: dict[str, Any] = {}
        if request.temperature is not None:
            generation["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            generation["maxOutputTokens"] = request.max_output_tokens
        if request.stop:
            generation["stopSequences"] = list(request.stop)
        if thinking_budget is not None:
            if (
                isinstance(thinking_budget, bool)
                or not isinstance(thinking_budget, int)
                or thinking_budget < -1
            ):
                raise ValueError("Gemini thinking_budget must be an integer of -1 or greater")
            generation["thinkingConfig"] = {"thinkingBudget": thinking_budget}
        if generation:
            payload["generationConfig"] = generation

        if request.tools:
            if effective_tool_support(self.capabilities().supports_tools, self._resolved_model(request)):
                declarations: list[dict[str, Any]] = []
                for tool in request.tools:
                    declarations.append(format_gemini_tool_definition(ensure_tool_definition(tool)))
                if declarations:
                    payload["tools"] = [{"functionDeclarations": declarations}]
        if request.tool_choice is not None:
            choice = str(request.tool_choice).lower()

            modes = {"auto": "AUTO", "none": "NONE", "required": "ANY", "any": "ANY"}
            if choice not in modes:
                raise ValueError("Gemini tool_choice must be auto, none, required, or any")
            payload["toolConfig"] = {"functionCallingConfig": {"mode": modes[choice]}}
        return payload

    def _http_request(self, request: ModelRequest, *, stream: bool) -> Request:
        return Request(
            self._endpoint(request, stream=stream),
            data=json.dumps(self._payload(request), ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
                # See amca.USER_AGENT: a blocked client signature would make
                # a working provider fail.
                "User-Agent": USER_AGENT,
                "x-goog-api-key": self._resolve_api_key(),
            },
            method="POST",
        )


    @staticmethod
    def _classify_http_error(exc: Exception):
        """Map HTTPError/URLError into a concrete AMCA category.

        Returns the AMCA error class to raise, or ``None`` if the exception
        should propagate unchanged.  Categories chosen per the M1.2 work
        order: 401/403 -> auth, 429 -> rate limit, 400/404/422 -> invalid
        request, 5xx -> unavailable, URLError-timeout -> timeout, other
        URLError -> unavailable.
        """
        if isinstance(exc, HTTPError):
            code = exc.code
            if code in (401, 403):
                return ModelAuthenticationError
            if code == 429:
                return ModelRateLimitError
            if code in (400, 404, 422):
                return ModelInvalidRequestError
            if code is not None and 500 <= int(code) < 600:
                return ModelProviderUnavailableError
            return ModelProviderUnavailableError
        if isinstance(exc, URLError):
            # urllib raises URLError("timed out") for socket timeout
            reason = getattr(exc, "reason", None)
            if isinstance(reason, TimeoutError) or "timed out" in str(exc):
                return ModelTimeoutError
            return ModelProviderUnavailableError
        return None

    @staticmethod
    def _raise_transport_error(exc: Exception) -> None:
        provider_label = GeminiAdapter.__name__
        mapped = GeminiAdapter._classify_http_error(exc)
        if mapped is ModelAuthenticationError:
            raise ModelAuthenticationError(
                f"{provider_label} returned HTTP {getattr(exc, 'code', '?')}"
            ) from exc
        if mapped is ModelRateLimitError:
            raise ModelRateLimitError(
                f"{provider_label} rate limited (HTTP 429)"
            ) from exc
        if mapped is ModelInvalidRequestError:
            raise ModelInvalidRequestError(
                f"{provider_label} rejected request (HTTP {getattr(exc, 'code', '?')})"
            ) from exc
        if mapped is ModelTimeoutError:
            raise ModelTimeoutError(
                f"{provider_label} transport timeout"
            ) from exc
        if mapped is ModelProviderUnavailableError:
            if isinstance(exc, HTTPError):
                raise ModelProviderUnavailableError(
                    f"{provider_label} returned HTTP {getattr(exc, 'code', '?')}"
                ) from exc
            raise ModelProviderUnavailableError(
                f"could not reach {provider_label}"
            ) from exc
        raise exc

    def _post_json(self, request: ModelRequest) -> Any:
        try:
            with self._urlopen(
                self._http_request(request, stream=False),
                timeout=self._transport_timeout(request),
            ) as response:
                raw = response.read()
        except (HTTPError, URLError) as exc:
            self._raise_transport_error(exc)
        # A body that is not JSON is a protocol failure and must surface as a
        # model error rather than a raw JSONDecodeError the caller cannot
        # classify.
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelProtocolError(
                "Gemini returned a body that is not valid JSON"
            ) from exc

    def _parse_response(self, data: Any, request: ModelRequest) -> ModelResponse:
        org_id = str(request.metadata.get("organization_id") or "amca")
        task_id = str(request.metadata.get("task_id") or "task")
        round_idx = int(request.metadata.get("round_index", 0))
        text, calls, finish_reason = _text_and_calls(
            data, organization_id=org_id, task_id=task_id, round_index=round_idx
        )
        usage = get_field(data, "usageMetadata", {}) or {}
        return ModelResponse(
            text=text,
            model=str(get_field(data, "modelVersion", self._resolved_model(request))),
            provider=self.provider,
            usage=self._usage(
                get_field(usage, "promptTokenCount", 0),
                get_field(usage, "candidatesTokenCount", 0),
            ),
            finish_reason=finish_reason,
            tool_calls=calls,

            provider_metadata={"response_id": get_field(data, "responseId")},
            raw=data,
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        async with self._call_scope(request):
            data = await asyncio.to_thread(self._post_json, request)
            return self._parse_response(data, request)

    def _stream_worker(
        self,
        request: ModelRequest,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[tuple[str, Any]],
        timeout: float,
    ) -> None:
        try:
            with self._urlopen(
                self._http_request(request, stream=True), timeout=timeout
            ) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    data = json.loads(line)
                    loop.call_soon_threadsafe(queue.put_nowait, ("data", data))
        except (HTTPError, URLError) as exc:
            try:
                self._raise_transport_error(exc)
            except Exception as normalized:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", normalized))
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

    async def _stream_impl(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        worker = asyncio.create_task(
            asyncio.to_thread(
                self._stream_worker,
                request,
                loop,
                queue,
                self._transport_timeout(request),
            )
        )
        text_parts: list[str] = []
        calls: list[Mapping[str, Any]] = []
        last_data: Any = {}
        finish_reason: str | None = None
        org_id = str(request.metadata.get("organization_id") or "amca")
        task_id = str(request.metadata.get("task_id") or "task")
        round_idx = int(request.metadata.get("round_index", 0))
        try:
            while True:
                kind, value = await queue.get()
                if kind == "error":
                    raise value
                if kind == "done":
                    break
                last_data = value
                delta, delta_calls, candidate_finish = _text_and_calls(
                    value, organization_id=org_id, task_id=task_id, round_index=round_idx
                )
                if delta:
                    text_parts.append(delta)
                    yield StreamChunk(
                        delta=delta,
                        model=str(get_field(value, "modelVersion", self._resolved_model(request))),
                        provider=self.provider,
                        raw=value,
                    )
                calls.extend(delta_calls)
                finish_reason = candidate_finish or finish_reason
        finally:

            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

        usage = get_field(last_data, "usageMetadata", {}) or {}
        response = ModelResponse(
            text="".join(text_parts),
            model=str(get_field(last_data, "modelVersion", self._resolved_model(request))),
            provider=self.provider,
            usage=self._usage(
                get_field(usage, "promptTokenCount", 0),
                get_field(usage, "candidatesTokenCount", 0),
            ),
            finish_reason=finish_reason,
            tool_calls=tuple(calls),
            provider_metadata={"response_id": get_field(last_data, "responseId")},
            raw=last_data,
        )
        yield StreamChunk(done=True, response=response, raw=last_data)

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        async with self._call_scope(request):
            async for chunk in self._stream_impl(request):
                yield chunk


__all__ = ["GeminiAdapter"]
