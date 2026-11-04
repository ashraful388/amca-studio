"""Local Ollama ``/api/chat`` model adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .base import (
    ModelAdapter,
    ModelCapabilities,
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
from .openai_compatible import _urlopen_without_redirects, _validate_http_base_url
from .tool_protocol import (
    effective_tool_support,
    ensure_tool_definition,
    format_ollama_tool_definition,
    known_model_tool_support,
    parse_ollama_tool_call,
    build_ollama_tool_result_message,
    ToolCall,
    ToolResult,
    ToolResultState,
)
from .tool_capability_evidence import (
    record_tool_calls_observed,
    record_tool_capability,
    record_tool_catalog_drop,
    tool_capability_drop_reason,
    tool_capability_verdict,
)

Urlopen: TypeAlias = Callable[..., Any]


class OllamaAdapter(ModelAdapter):
    """Calls a loopback Ollama daemon without requiring the Ollama Python SDK."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        adapter_id: str | None = None,
        timeout: float = 120.0,
        allow_remote: bool = False,
        context_window: int | None = None,
        urlopen_fn: Urlopen | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        base_url = _validate_http_base_url(base_url, allow_remote=allow_remote)
        super().__init__(
            adapter_id=adapter_id or f"ollama:{model}",
            provider="ollama",
            model=model,
            capabilities=ModelCapabilities(
                supports_streaming=True,
                supports_tools=True,
                supports_vision=True,
                supports_json=True,
                is_local=not allow_remote,
                context_window=context_window,
                input_modalities=("text", "image"),
            ),
            metadata={"base_url": base_url},
        )
        self._base_url = base_url
        self._timeout = timeout
        self._urlopen = urlopen_fn or _urlopen_without_redirects
        # Verified per-model tool capability evidence from the serving runtime
        # (Ollama ``/api/show``).  ``None``/absent means "not verified" and
        # keeps the historical fail-closed static-table behavior.
        self._declared_tool_capability: dict[str, bool | None] = {}
        self._capability_probe_lock = asyncio.Lock()

    # -- verified local tool capability ----------------------------------

    def _show_request(self, model: str) -> Request:
        return Request(
            f"{self._base_url}/api/show",
            data=json.dumps({"model": model}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    def _probe_declared_tool_support(self, model: str) -> bool | None:
        """Ask the runtime which capabilities this exact model declares.

        Ollama reports ``capabilities`` (for example ``["completion", "tools"]``)
        in ``POST /api/show``.  The probe never raises and never guesses: any
        transport error, malformed payload, or runtime that does not report
        capabilities yields ``None`` so the caller keeps failing closed.  A
        declaration that IS reported is recorded as verified evidence.
        """
        timeout = min(10.0, max(1.0, float(self._timeout)))
        try:
            with self._urlopen(self._show_request(model), timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception:
            return None
        capabilities = get_field(data, "capabilities", None)
        if not isinstance(capabilities, (list, tuple)):
            return None
        if not all(isinstance(item, str) for item in capabilities):
            return None
        supported = "tools" in {item.strip().lower() for item in capabilities}
        record_tool_capability(
            self.provider,
            model,
            supported=supported,
            source="runtime_api_show",
            detail=(
                "Ollama /api/show declared capabilities: "
                + ", ".join(sorted({item.strip().lower() for item in capabilities}))
            ),
        )
        return supported

    async def _ensure_declared_tool_support(self, model: str) -> bool | None:
        """Return (and cache) verified tool capability for ``model``.

        Only models the static table does not already classify are probed, so
        curated entries keep their exact verdict and an unknown model is still
        fail-closed unless the runtime itself declares tool support.
        """
        if known_model_tool_support(model) is not None:
            return self._declared_tool_capability.get(model)
        if model in self._declared_tool_capability:
            return self._declared_tool_capability[model]
        shared = tool_capability_verdict(self.provider, model)
        if shared is not None:
            self._declared_tool_capability[model] = shared
            return shared
        async with self._capability_probe_lock:
            if model not in self._declared_tool_capability:
                self._declared_tool_capability[model] = await asyncio.to_thread(
                    self._probe_declared_tool_support, model
                )
        return self._declared_tool_capability[model]

    def _payload(self, request: ModelRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = dict(request.extra)
        reserved = {"model", "messages", "stream", "options", "tools"}
        if reserved.intersection(payload):
            raise ValueError("request.extra cannot override normalized request fields")

        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for message in request.messages:
            msg_dict = dict(message)
            role = msg_dict.get("role")
            if role == "tool":
                tool_name = str(msg_dict.get("name") or msg_dict.get("tool_name") or "")
                content = str(msg_dict.get("content", ""))
                res = ToolResult(
                    call_id=str(msg_dict.get("tool_call_id") or msg_dict.get("call_id") or ""),
                    name=tool_name,
                    state=ToolResultState.SUCCESS if not msg_dict.get("is_error") else ToolResultState.EXECUTION_ERROR,
                    content=content,
                )
                messages.append(build_ollama_tool_result_message(res))
            else:
                messages.append(msg_dict)

        payload.update(
            {
                "model": self._resolved_model(request),
                "messages": messages,
                "stream": stream,
            }
        )
        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            options["num_predict"] = request.max_output_tokens
        if request.stop:
            options["stop"] = list(request.stop)
        if options:
            payload["options"] = options
        if request.tools:
            resolved = self._resolved_model(request)
            if effective_tool_support(
                self.capabilities().supports_tools,
                resolved,
                True,
                self._declared_tool_capability.get(resolved)
                if resolved in self._declared_tool_capability
                else tool_capability_verdict(self.provider, resolved),
            ):
                payload["tools"] = [
                    format_ollama_tool_definition(ensure_tool_definition(tool))
                    for tool in request.tools
                ]
            else:
                # Never drop the catalog silently: the exact truthful reason is
                # recorded so the UI / governed chat can state it.
                reason = tool_capability_drop_reason(self.provider, resolved)
                if reason:
                    record_tool_catalog_drop(self.provider, resolved, reason)
        return payload


    def _http_request(self, payload: Mapping[str, Any]) -> Request:
        return Request(
            f"{self._base_url}/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
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
        provider_label = OllamaAdapter.__name__
        mapped = OllamaAdapter._classify_http_error(exc)
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

    def _post_json(self, payload: Mapping[str, Any], *, timeout: float) -> Any:
        try:
            with self._urlopen(self._http_request(payload), timeout=timeout) as response:
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
                "Ollama returned a body that is not valid JSON"
            ) from exc

    def _parse_response(self, data: Any, request: ModelRequest) -> ModelResponse:
        message = get_field(data, "message")
        if message is None:
            raise ModelProtocolError("Ollama response is missing message")
        text = get_field(message, "content", "") or ""
        usage = self._usage(
            get_field(data, "prompt_eval_count", 0),
            get_field(data, "eval_count", 0),
        )
        org_id = str(request.metadata.get("organization_id") or "amca")
        task_id = str(request.metadata.get("task_id") or "task")
        round_idx = int(request.metadata.get("round_index", 0))
        calls = get_field(message, "tool_calls", ()) or ()
        parsed_calls = []
        for idx, call in enumerate(calls):
            if isinstance(call, Mapping):
                parsed = parse_ollama_tool_call(
                    call, index=idx, organization_id=org_id, task_id=task_id, round_index=round_idx
                )
                parsed_calls.append(parsed)
        if any(isinstance(item, ToolCall) for item in parsed_calls):
            # Real evidence: the local runtime answered with tool calls.
            record_tool_calls_observed(
                self.provider, self._resolved_model(request), count=len(parsed_calls)
            )
        return ModelResponse(
            text=str(text),
            model=str(get_field(data, "model", self._resolved_model(request))),
            provider=self.provider,
            usage=usage,
            finish_reason=get_field(data, "done_reason"),
            tool_calls=tuple(parsed_calls),
            raw=data,
        )


    async def generate(self, request: ModelRequest) -> ModelResponse:
        async with self._call_scope(request):
            if request.tools:
                # Verified capability evidence, fetched at most once per model
                # per adapter, before the tool catalog is built into the payload.
                await self._ensure_declared_tool_support(
                    self._resolved_model(request)
                )
            data = await asyncio.to_thread(
                self._post_json,
                self._payload(request, stream=False),
                timeout=self._transport_timeout(request),
            )
            return self._parse_response(data, request)

    def _stream_worker(
        self,
        payload: Mapping[str, Any],
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[tuple[str, Any]],
        timeout: float,
    ) -> None:
        try:
            with self._urlopen(self._http_request(payload), timeout=timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if line:
                        loop.call_soon_threadsafe(
                            queue.put_nowait, ("data", json.loads(line))
                        )
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        async with self._call_scope(request):
            loop = asyncio.get_running_loop()
            if request.tools:
                await self._ensure_declared_tool_support(
                    self._resolved_model(request)
                )
            queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
            worker = asyncio.create_task(
                asyncio.to_thread(
                    self._stream_worker,
                    self._payload(request, stream=True),
                    loop,
                    queue,
                    self._transport_timeout(request),
                )
            )
            pieces: list[str] = []
            model = self._resolved_model(request)
            usage = self._usage(0, 0)
            finish_reason: str | None = None
            tool_calls: list[Mapping[str, Any]] = []
            terminal_raw: Any = None
            try:
                while True:
                    kind, data = await queue.get()
                    if kind == "error":
                        self._raise_transport_error(data)
                    if kind == "done":
                        break
                    terminal_raw = data
                    model = str(get_field(data, "model", model))
                    message = get_field(data, "message", {}) or {}
                    delta = get_field(message, "content", "") or ""
                    raw_calls = get_field(message, "tool_calls", ()) or ()
                    tool_calls.extend(
                        call for call in raw_calls if isinstance(call, Mapping)
                    )
                    if get_field(data, "done", False):
                        finish_reason = get_field(data, "done_reason")
                        usage = self._usage(
                            get_field(data, "prompt_eval_count", 0),
                            get_field(data, "eval_count", 0),
                        )
                    if delta:
                        pieces.append(str(delta))
                        yield StreamChunk(
                            delta=str(delta),
                            model=model,
                            provider=self.provider,
                            raw=data,
                        )
                await worker
            except BaseException:
                worker.cancel()
                raise
            org_id = str(request.metadata.get("organization_id") or "amca")
            task_id = str(request.metadata.get("task_id") or "task")
            round_idx = int(request.metadata.get("round_index", 0))
            parsed_calls = [
                parse_ollama_tool_call(call, index=idx, organization_id=org_id, task_id=task_id, round_index=round_idx)
                for idx, call in enumerate(tool_calls)
            ]
            response = ModelResponse(
                text="".join(pieces),
                model=model,
                provider=self.provider,
                usage=usage,
                finish_reason=finish_reason,
                tool_calls=tuple(parsed_calls),
                raw=terminal_raw,
            )
            yield StreamChunk(done=True, response=response)

