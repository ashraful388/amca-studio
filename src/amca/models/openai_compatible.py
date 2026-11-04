"""Generic OpenAI-compatible chat-completions adapter.

This implementation uses :mod:`urllib` instead of an optional SDK, making it
appropriate for local servers such as llama.cpp, vLLM, and SGLang.  Blocking
network I/O runs in worker threads so the collaboration kernel remains async.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from amca import PROVIDER_REQUEST_TIMEOUT_SECONDS, USER_AGENT
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
    as_mapping,
    get_field,
    ModelAuthenticationError,
    ModelProviderUnavailableError,
    ModelRateLimitError,
    ModelInvalidRequestError,
    ModelTimeoutError,
    ModelCancelledError,
)
from .tool_protocol import (
    effective_tool_support,
    ensure_tool_definition,
    format_openai_compatible_tool_definition,
    parse_openai_tool_call,
    build_openai_compatible_tool_result_message,
    canonical_tool_name,
    wire_tool_name,
    wire_tool_name_map,
    ToolResult,
    ToolResultState,
)
from .tool_capability_evidence import (
    ToolCapabilityRecord,
    record_tool_calls_observed,
    record_tool_capability,
    record_tool_catalog_drop,
    tool_catalog_drop,
    rejection_mentions_tools,
    tool_capability_drop_reason,
    tool_capability_verdict,
)

Urlopen: TypeAlias = Callable[..., Any]



class _RejectRedirects(HTTPRedirectHandler):
    """Keep a validated endpoint from redirecting a request elsewhere."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _urlopen_without_redirects(request: Request, *, timeout: float) -> Any:
    """Open one request without following redirects or forwarding credentials."""

    return build_opener(_RejectRedirects()).open(request, timeout=timeout)


def _validate_http_base_url(base_url: str, *, allow_remote: bool) -> str:
    cleaned = base_url.rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    if not allow_remote and parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError(
            "non-loopback base_url requires allow_remote=True; this prevents "
            "accidental local-data transmission"
        )
    return cleaned


def _response_text(choice: Any) -> str:
    message = get_field(choice, "message", {})
    content = get_field(message, "content", "")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif get_field(part, "type") in {"text", "output_text"}:
                parts.append(str(get_field(part, "text", "")))
        return "".join(parts)
    return str(content)


def _wire_tool_choice(tool_choice: Any) -> Any:
    """Alias a named-function tool_choice to the transport-safe name.

    A bare ``"auto"``/``"none"``/``"required"`` passes through untouched; a
    ``{"type": "function", "function": {"name": ...}}`` choice would otherwise
    put a dotted canonical name back on the wire and reintroduce the very
    rejection the alias exists to prevent.
    """
    if isinstance(tool_choice, Mapping):
        function = tool_choice.get("function")
        if isinstance(function, Mapping) and function.get("name"):
            aliased = dict(tool_choice)
            function_map = dict(function)
            function_map["name"] = wire_tool_name(str(function_map["name"]))
            aliased["function"] = function_map
            return aliased
    return tool_choice


def _wire_tool_definition(defn: Any) -> dict[str, Any]:
    """Format a tool definition with the transport-safe function name."""
    formatted = format_openai_compatible_tool_definition(defn)
    formatted["function"]["name"] = wire_tool_name(defn.name)
    return formatted


def _restore_tool_name(call: Any, name_map: Mapping[str, str]) -> Any:
    """Return a tool call with its wire name mapped back to the canonical one.

    An empty map (no catalog was sent) or an unknown name is left untouched, so
    the governed router still refuses it as an unknown tool.
    """
    if not name_map or not isinstance(call, Mapping):
        return call
    function = call.get("function")
    if not isinstance(function, Mapping):
        return call
    restored = dict(call)
    function_map = dict(function)
    function_map["name"] = canonical_tool_name(
        str(function_map.get("name") or ""), name_map
    )
    restored["function"] = function_map
    return restored


def _chat_tool_calls(
    choice: Any,
    organization_id: str = "amca",
    task_id: str = "task",
    round_index: int = 0,
    name_map: Mapping[str, str] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    message = get_field(choice, "message", {})
    calls = get_field(message, "tool_calls", ()) or ()
    parsed_calls = [
        parse_openai_tool_call(
            _restore_tool_name(call, name_map or {}),
            index=idx,
            organization_id=organization_id,
            task_id=task_id,
            round_index=round_index,
        )
        for idx, call in enumerate(calls)
    ]
    return tuple(parsed_calls)



class OpenAICompatibleAdapter(ModelAdapter):
    """Adapter for an OpenAI-compatible ``/chat/completions`` endpoint."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        adapter_id: str | None = None,
        provider: str = "openai-compatible",
        api_key: str | None = None,
        api_key_env: str | None = None,
        timeout: float = PROVIDER_REQUEST_TIMEOUT_SECONDS,
        allow_remote: bool = False,
        capabilities: ModelCapabilities | None = None,
        input_cost_per_million: float = 0.0,
        output_cost_per_million: float = 0.0,
        default_headers: Mapping[str, str] | None = None,
        urlopen_fn: Urlopen | None = None,
        credential_resolver: ProviderCredentialResolver | None = None,
        credential_provider: str | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        super().__init__(
            adapter_id=adapter_id or f"{provider}:{model}",
            provider=provider,
            model=model,
            capabilities=capabilities
            or ModelCapabilities(
                supports_streaming=True,
                supports_tools=True,
                supports_json=True,
                is_local=not allow_remote,
            ),
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
            metadata={"base_url": _validate_http_base_url(base_url, allow_remote=allow_remote)},
        )
        self._base_url = _validate_http_base_url(base_url, allow_remote=allow_remote)
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._timeout = timeout
        self._default_headers = dict(default_headers or {})
        # The initial URL was policy-checked above. Following a redirect would
        # let a loopback endpoint transmit the prompt (and possibly a bearer
        # token) to an unvalidated destination. Injected transports remain an
        # explicit testing/embedding escape hatch.
        self._urlopen = urlopen_fn or _urlopen_without_redirects
        self._credential_resolver = credential_resolver
        self._credential_provider = credential_provider or provider
        #: Bounded body of the most recent HTTP error, used only to decide
        #: whether the provider rejected the request *because of* the tools
        #: field (verified-unsupported evidence).  Never persisted.
        self._last_error_body = ""

    def _resolve_api_key(self) -> str | None:
        """Read a configured environment variable only at call time."""

        if self._credential_resolver is not None and self._api_key_env is not None:
            return self._credential_resolver.resolve(
                provider=self._credential_provider,
                environment_variable=self._api_key_env,
                explicit=self._api_key,
            )
        if self._api_key is not None:
            return self._api_key
        if self._api_key_env is not None:
            return os.environ.get(self._api_key_env)
        return None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # Python's default User-Agent is on some gateway blocklists
            # (Cloudflare answers it with HTTP 403), which would make a
            # working provider fail at run time. AMCA identifies itself.
            "User-Agent": USER_AGENT,
            **self._default_headers,
        }
        api_key = self._resolve_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _transmits_tool_catalog(self, request: ModelRequest) -> bool:
        """True when this request's tool catalog may be put on the wire.

        The model registry/evidence gate is the authority: a model whose tool
        capability is unknown or verified-unsupported is not offered any tool,
        and therefore has no wire name to be restored from on the way back.
        """
        if not request.tools:
            return False
        resolved = self._resolved_model(request)
        return bool(
            effective_tool_support(
                self.capabilities().supports_tools,
                resolved,
                True,
                tool_capability_verdict(self.provider, resolved),
            )
        )

    def _effective_wire_name_map(
        self, request: ModelRequest, *, transmits: bool | None = None
    ) -> dict[str, str]:
        """Canonical -> wire map for the catalog this request transmits.

        ``transmits`` is the decision frozen by the caller for this request; it
        is re-derived only when a caller did not freeze it.
        """
        if transmits is None:
            transmits = self._transmits_tool_catalog(request)
        if not transmits:
            return {}
        return wire_tool_name_map(request.tools)

    def _refuse_unsolicited_tool_calls(
        self, request: ModelRequest, *, transmits: bool | None = None
    ) -> bool:
        """True when a returned tool call must not reach the caller.

        A model that was not offered a catalog has no authority to call any
        tool.  Such a call is dropped here rather than handed on with a
        canonical name, and the drop is recorded so the refusal is visible
        instead of silent.
        """
        if transmits is None:
            transmits = self._transmits_tool_catalog(request)
        if not request.tools or transmits:
            return False
        resolved = self._resolved_model(request)
        # The capability reason already recorded ("unverified", "verified
        # unsupported", ...) is the truthful one the UI must keep showing, so
        # an unsolicited call is only noted when nothing has been recorded yet.
        if tool_catalog_drop(self.provider, resolved) is None:
            record_tool_catalog_drop(
                self.provider,
                resolved,
                "provider returned tool calls although no catalog was transmitted",
            )
        return True

    def _payload(
        self,
        request: ModelRequest,
        *,
        stream: bool,
        transmits: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = dict(request.extra)
        reserved = {
            "model",
            "messages",
            "stream",
            "temperature",
            "max_tokens",
            "max_completion_tokens",
            "tools",
            "tool_choice",
            "stop",
        }
        if reserved.intersection(payload):
            raise ValueError("request.extra cannot override normalized request fields")

        # Function names are aliased on the wire only; canonical dotted names
        # stay in force everywhere else.  The map is the one derived from the
        # catalog that is actually transmitted, so an echoed transcript can
        # never carry a name the endpoint was never offered.
        if transmits is None:
            transmits = self._transmits_tool_catalog(request)
        name_map = self._effective_wire_name_map(request, transmits=transmits)

        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for message in request.messages:
            msg = dict(message)
            role = msg.get("role")
            if role == "tool":
                call_id = str(msg.get("tool_call_id") or msg.get("call_id") or "")
                res = ToolResult(
                    call_id=call_id,
                    name=str(msg.get("name") or msg.get("tool_name") or "tool"),
                    state=ToolResultState.SUCCESS if not msg.get("is_error") else ToolResultState.EXECUTION_ERROR,
                    content=str(msg.get("content", "")),
                )
                messages.append(build_openai_compatible_tool_result_message(res))
            else:
                if name_map and role == "assistant" and msg.get("tool_calls"):
                    echoed = []
                    for call in msg.get("tool_calls") or ():
                        call_map = dict(call) if isinstance(call, Mapping) else {}
                        function = call_map.get("function")
                        if isinstance(function, Mapping):
                            raw_name = str(function.get("name") or "")
                            if not raw_name:
                                # A provider emitted a nameless call.  It was
                                # already refused by the governed layer, and a
                                # nameless entry cannot be replayed on the wire.
                                continue
                            function = dict(function)
                            function["name"] = wire_tool_name(raw_name)
                            call_map["function"] = function
                        echoed.append(call_map)
                    msg["tool_calls"] = echoed
                    if not echoed:
                        msg.pop("tool_calls", None)
                messages.append(msg)
        payload.update(
            {
                "model": self._resolved_model(request),
                "messages": messages,
                "stream": stream,
            }
        )
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        if request.tools:
            if transmits:
                payload["tools"] = [
                    _wire_tool_definition(ensure_tool_definition(tool))
                    for tool in request.tools
                ]
            else:
                # Never drop the catalog silently: the exact truthful reason is
                # recorded so the UI / governed chat can state it.
                resolved = self._resolved_model(request)
                reason = tool_capability_drop_reason(self.provider, resolved)
                if reason:
                    record_tool_catalog_drop(self.provider, resolved, reason)
        if request.tool_choice is not None:
            payload["tool_choice"] = _wire_tool_choice(request.tool_choice)
        if request.stop:
            payload["stop"] = list(request.stop)

        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _http_request(self, payload: Mapping[str, Any]) -> Request:
        endpoint = f"{self._base_url}/chat/completions"
        return Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
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
        provider_label = OpenAICompatibleAdapter.__name__
        mapped = OpenAICompatibleAdapter._classify_http_error(exc)
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

    @staticmethod
    def _error_body(exc: Exception) -> str:
        """Best-effort bounded body of a provider error response."""
        read = getattr(exc, "read", None)
        if not callable(read):
            return ""
        try:
            raw = read()
        except Exception:
            return ""
        if isinstance(raw, bytes):
            return raw.decode("utf-8", "replace")[:2_000]
        return str(raw)[:2_000]

    def _record_tool_rejection(self, request: ModelRequest) -> None:
        """Record verified-unsupported when the provider rejected the tools field.

        Only an invalid-request status whose body names the tools/function
        calling field is treated as evidence.  Every other failure stays
        unverified, which keeps the model fail-closed instead of inventing a
        verdict.
        """
        if not request.tools:
            return
        body = self._last_error_body
        if not rejection_mentions_tools(body):
            return
        record_tool_capability(
            self.provider,
            self._resolved_model(request),
            supported=False,
            source="provider_rejection",
            detail=body,
        )

    #: HTTP statuses worth one bounded retry: transient provider-side
    #: failures (gateway timeouts, overloaded 5xx).  Auth, invalid-request
    #: and rate-limit (429) errors are NOT retried: replaying them cannot
    #: succeed and burns quota.  Kept at module level so tests and
    #: subclasses share the same policy.
    _TRANSIENT_RETRY_STATUS = frozenset({408, 425, 500, 502, 503, 504, 524})

    @staticmethod
    def _is_transient_http_error(exc: Exception) -> bool:
        """Whether a transport error is worth one bounded retry."""
        if isinstance(exc, HTTPError):
            try:
                return int(exc.code or 0) in OpenAICompatibleAdapter._TRANSIENT_RETRY_STATUS
            except (TypeError, ValueError):
                return False
        return False

    def _post_json(self, payload: Mapping[str, Any], *, timeout: float) -> Any:
        try:
            with self._urlopen(self._http_request(payload), timeout=timeout) as response:
                raw = response.read()
        except (HTTPError, URLError) as exc:
            if self._is_transient_http_error(exc):
                # One bounded retry with a short pause: transient gateway /
                # overload failures (Cloudflare 524 included) often clear on
                # a second attempt, and a single retry cannot become a storm.
                import time as _time

                _time.sleep(2.0)
                try:
                    with self._urlopen(self._http_request(payload), timeout=timeout) as response:
                        raw = response.read()
                except (HTTPError, URLError) as retry_exc:
                    self._last_error_body = self._error_body(retry_exc) if isinstance(retry_exc, HTTPError) else ""
                    self._raise_transport_error(retry_exc)
            else:
                self._last_error_body = self._error_body(exc) if isinstance(exc, HTTPError) else ""
                self._raise_transport_error(exc)
        # A body that is not JSON is a provider protocol failure, not a caller
        # bug.  A raw JSONDecodeError used to escape here, so the failure could
        # not be classified as a model error and the message was not actionable.
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._last_error_body = raw[:2_000].decode("utf-8", "replace")
            raise ModelProtocolError(
                f"{type(self).__name__} returned a body that is not valid JSON"
            ) from exc

    def _parse_response(
        self,
        data: Any,
        request: ModelRequest,
        *,
        transmits: bool | None = None,
    ) -> ModelResponse:
        choices = get_field(data, "choices", ()) or ()
        if not choices:
            raise ModelProtocolError("chat-completions response has no choices")
        choice = choices[0]
        usage = get_field(data, "usage", {}) or {}
        input_tokens = get_field(usage, "prompt_tokens", 0)
        output_tokens = get_field(usage, "completion_tokens", 0)
        org_id = str(request.metadata.get("organization_id") or "amca")
        task_id = str(request.metadata.get("task_id") or "task")
        round_idx = int(request.metadata.get("round_index", 0))
        name_map = self._effective_wire_name_map(request, transmits=transmits)
        tool_calls = (
            ()
            if self._refuse_unsolicited_tool_calls(request, transmits=transmits)
            else _chat_tool_calls(
                choice,
                organization_id=org_id,
                task_id=task_id,
                round_index=round_idx,
                name_map=name_map,
            )
        )
        if tool_calls:
            # Real evidence: this endpoint answered with tool calls.
            record_tool_calls_observed(
                self.provider, self._resolved_model(request), count=len(tool_calls)
            )
        return ModelResponse(
            text=_response_text(choice),
            model=str(get_field(data, "model", self._resolved_model(request))),
            provider=self.provider,
            usage=self._usage(input_tokens, output_tokens),
            finish_reason=get_field(choice, "finish_reason"),
            tool_calls=tool_calls,
            provider_metadata={"response_id": get_field(data, "id")},
            raw=data,
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        async with self._call_scope(request):
            # Frozen for this request: the capability ledger is process-wide and
            # mutable, so a concurrent run must not change what THIS request put
            # on the wire between building the payload and reading the reply.
            transmits = self._transmits_tool_catalog(request)
            try:
                data = await asyncio.to_thread(
                    self._post_json,
                    self._payload(request, stream=False, transmits=transmits),
                    timeout=self._transport_timeout(request),
                )
            except ModelInvalidRequestError:
                # The provider rejected the request; if the rejection names the
                # tools field, that is verified-unsupported evidence.
                self._record_tool_rejection(request)
                raise
            return self._parse_response(data, request, transmits=transmits)

    async def probe_tool_capability(
        self, *, timeout_seconds: float | None = None
    ) -> ToolCapabilityRecord | None:
        """Bounded, explicit verification that this endpoint accepts tools.

        Sends exactly one small request that offers a single no-argument
        ``amca_tool_capability_probe`` tool and asks the model to call it:

        * a response carrying a tool call  -> verified supported;
        * an invalid-request rejection naming the tools field
          -> verified unsupported;
        * anything else (plain answer, transport failure, unrelated error)
          -> no record at all, so the model stays fail-closed.

        This is a provider call and therefore MUST be invoked only from an
        already-authorized path (the desktop model-verification flow that owns
        the user's transmission approval).  Nothing in AMCA calls it
        automatically.
        """
        probe_tool = {
            "type": "function",
            "name": "amca_tool_capability_probe",
            "description": (
                "Report that governed tool calling is available. Call this tool."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
        request = ModelRequest(
            messages=(
                {
                    "role": "user",
                    "content": (
                        "Call the amca_tool_capability_probe tool now. "
                        "Do not answer in text."
                    ),
                },
            ),
            tools=(probe_tool,),
            temperature=0.0,
            max_output_tokens=64,
        )
        if timeout_seconds is not None:
            request = replace(request, timeout_seconds=float(timeout_seconds))
        payload = self._payload(request, stream=False)
        # The probe is the verification, so the probe request itself must carry
        # the tool catalog even while the capability is still unverified.  Its
        # own call must not be mistaken for an unsolicited one, so the parse is
        # told explicitly that this request did put its catalog on the wire.
        # The decision travels as an argument, not through request metadata,
        # so no caller can forge it.
        payload["tools"] = [
            _wire_tool_definition(ensure_tool_definition(probe_tool))
        ]
        try:
            data = await asyncio.to_thread(
                self._post_json, payload, timeout=self._transport_timeout(request)
            )
        except ModelInvalidRequestError:
            body = self._last_error_body
            if rejection_mentions_tools(body):
                return record_tool_capability(
                    self.provider,
                    self._resolved_model(request),
                    supported=False,
                    source="provider_rejection",
                    detail=body,
                )
            return None
        except Exception:
            return None
        response = self._parse_response(data, request, transmits=True)
        if response.tool_calls:
            return record_tool_capability(
                self.provider,
                self._resolved_model(request),
                supported=True,
                source="probe_tool_call",
                detail="bounded probe request was answered with a tool call",
            )
        return None

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
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    data = json.loads(line)
                    loop.call_soon_threadsafe(queue.put_nowait, ("data", data))
        except (HTTPError, URLError) as exc:
            self._last_error_body = self._error_body(exc) if isinstance(exc, HTTPError) else ""
            try:
                self._raise_transport_error(exc)
            except Exception as normalized:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", normalized))
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        loop = asyncio.get_running_loop()

        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        async with self._call_scope(request):
            transmits = self._transmits_tool_catalog(request)
            worker = asyncio.create_task(
                asyncio.to_thread(
                    self._stream_worker,
                    self._payload(request, stream=True, transmits=transmits),
                    loop,
                    queue,
                    self._transport_timeout(request),
                )
            )
            pieces: list[str] = []
            model = self._resolved_model(request)
            usage = self._usage(0, 0)
            finish_reason: str | None = None
            response_id: str | None = None
            tool_call_deltas: dict[int, dict[str, Any]] = {}
            try:
                while True:
                    kind, data = await queue.get()
                    if kind == "error":
                        try:
                            self._raise_transport_error(data)
                        except ModelInvalidRequestError:
                            # Rejection naming the tools field -> verified unsupported.
                            self._record_tool_rejection(request)
                            raise
                    if kind == "done":
                        break
                    response_id = get_field(data, "id", response_id)
                    model = str(get_field(data, "model", model))
                    raw_usage = get_field(data, "usage")
                    if raw_usage:
                        usage = self._usage(
                            get_field(raw_usage, "prompt_tokens", 0),
                            get_field(raw_usage, "completion_tokens", 0),
                        )
                    choices = get_field(data, "choices", ()) or ()
                    if not choices:
                        continue
                    choice = choices[0]
                    finish_reason = get_field(choice, "finish_reason", finish_reason)
                    delta_obj = get_field(choice, "delta", {}) or {}
                    delta = get_field(delta_obj, "content", "") or ""
                    raw_calls = get_field(delta_obj, "tool_calls", ()) or ()
                    for call in raw_calls:
                        call_map = as_mapping(call)
                        idx = call_map.get("index", len(tool_call_deltas))
                        if idx not in tool_call_deltas:
                            tool_call_deltas[idx] = {
                                "id": call_map.get("id"),
                                "type": call_map.get("type", "function"),
                                "function": {
                                    "name": "",
                                    "arguments": "",
                                },
                            }
                        entry = tool_call_deltas[idx]
                        if call_map.get("id"):
                            entry["id"] = call_map["id"]
                        if call_map.get("type"):
                            entry["type"] = call_map["type"]
                        fn = call_map.get("function") or {}
                        if fn.get("name"):
                            entry["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            entry["function"]["arguments"] += fn["arguments"]
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
            name_map = self._effective_wire_name_map(request, transmits=transmits)
            if self._refuse_unsolicited_tool_calls(request, transmits=transmits):
                name_map = {}
                tool_call_deltas = {}
            parsed_tool_calls = [
                parse_openai_tool_call(
                    _restore_tool_name(tool_call_deltas[idx], name_map),
                    index=idx,
                    organization_id=org_id,
                    task_id=task_id,
                    round_index=round_idx,
                )
                for idx in sorted(tool_call_deltas)
            ]
            if parsed_tool_calls:
                # Real evidence: this endpoint answered with tool calls.
                record_tool_calls_observed(
                    self.provider, self._resolved_model(request), count=len(parsed_tool_calls)
                )
            response = ModelResponse(
                text="".join(pieces),
                model=model,
                provider=self.provider,
                usage=usage,
                finish_reason=finish_reason,
                tool_calls=tuple(parsed_tool_calls),
                provider_metadata={"response_id": response_id},
            )
            yield StreamChunk(done=True, response=response)


class LocalOpenAICompatibleAdapter(OpenAICompatibleAdapter):
    """Convenience alias with local-only URL protection enabled."""

    def __init__(self, *, model: str, base_url: str, **kwargs: Any) -> None:
        kwargs.setdefault("allow_remote", False)
        kwargs.setdefault("provider", "local-openai-compatible")
        super().__init__(model=model, base_url=base_url, **kwargs)
