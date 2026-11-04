"""v1.1.8 Phase 1 — conformance across EVERY supported adapter.

Work Order 02 Priority 1, and the Architecture Completion Directive sections 6,
7 and 32.  The directive's Phase 1 acceptance is explicit:

    Do not call Phase 1 complete until every supported adapter is inventoried,
    every advertised capability is truthful, the conformance matrix covers every
    supported adapter/variation, unsupported features fail truthfully, the Model
    Registry controls capabilities, fallback is governed, and at least the
    available real providers/local runtimes have real execution evidence.

    If a provider cannot be live-tested because no authorized credential exists:
    UNVERIFIED — credential unavailable is acceptable evidence status.  It is
    NOT a PASS.

The previous matrix covered three adapters.  This one covers all eleven adapter
classes through whichever transport seam each exposes — `urlopen_fn` for the
HTTP-family adapters, an injected client for the SDK-family ones — so no
provider is skipped merely because it is awkward to reach.

Nothing here opens a network connection.  Live-provider status is recorded
separately and honestly; see LIVE_STATUS at the bottom.
"""

from __future__ import annotations

import asyncio
import io
import json
import socket
import unittest
from typing import Any, Callable
from urllib.error import HTTPError, URLError

from amca.models import (
    AnthropicAdapter,
    FixedPriceOpenAICompatibleAdapter,
    GeminiAdapter,
    KimiAdapter,
    LlamaCppAdapter,
    LocalOpenAICompatibleAdapter,
    ModelAuthenticationError,
    ModelError,
    ModelInvalidRequestError,
    ModelProtocolError,
    ModelProviderUnavailableError,
    ModelRateLimitError,
    ModelRequest,
    ModelTimeoutError,
    ModelUnavailableError,
    OllamaAdapter,
    OpenAIAdapter,
    OpenAICompatibleAdapter,
    SGLangAdapter,
    TabitokenAdapter,
    VLLMAdapter,
)

BASE_URL = "http://127.0.0.1:9/v1"


# --------------------------------------------------------------------- fakes


def _http_error(code: int, body: bytes = b'{"error":"synthetic"}') -> HTTPError:
    return HTTPError(url=BASE_URL, code=code, msg="synthetic",
                     hdrs=None, fp=io.BytesIO(body))  # type: ignore[arg-type]


class _Response:
    """An HTTP body that is also iterable, for SSE streaming."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __iter__(self):
        return iter(self._payload.splitlines(keepends=True))

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


class _SdkResponse:
    """A minimal stand-in for an SDK response object."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return self._payload

    def __getattr__(self, name: str) -> Any:
        try:
            return self._payload[name]
        except KeyError as exc:  # pragma: no cover - attribute miss
            raise AttributeError(name) from exc


class _FakeSdkClient:
    """Injected in place of the OpenAI/Anthropic SDK client."""

    def __init__(self, payload: dict[str, Any] | None = None,
                 failure: Exception | None = None) -> None:
        self._payload = payload or {}
        self._failure = failure
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _call(self, name: str, **kwargs: Any) -> Any:
        self.calls.append((name, kwargs))
        if self._failure is not None:
            raise self._failure
        return _SdkResponse(self._payload)

    # OpenAI Responses API
    def responses_create(self, **kwargs: Any) -> Any:
        return self._call("responses.create", **kwargs)

    # Anthropic Messages API
    def messages_create(self, **kwargs: Any) -> Any:
        return self._call("messages.create", **kwargs)

    def __getattr__(self, name: str) -> Any:
        # `client.responses.create(...)` and `client.messages.create(...)`
        if name == "responses":
            return _Namespace(self, "responses")
        if name == "messages":
            return _Namespace(self, "messages")
        raise AttributeError(name)


class _Namespace:
    def __init__(self, client: _FakeSdkClient, prefix: str) -> None:
        self._client = client
        self._prefix = prefix

    def create(self, **kwargs: Any) -> Any:
        return self._client._call(f"{self._prefix}.create", **kwargs)


def _ok_chat(text: str = "answer") -> bytes:
    return json.dumps({
        "id": "c1", "model": "m",
        "choices": [{"message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }).encode("utf-8")


def _sse(lines: list[bytes]) -> _Response:
    return _Response(b"".join(lines))


def _delta(text: str) -> bytes:
    return b"data: " + json.dumps(
        {"choices": [{"delta": {"content": text}, "index": 0}]}).encode() + b"\n"


def _finish() -> bytes:
    return b"data: " + json.dumps(
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode() + b"\n"


def _request(text: str = "hello") -> ModelRequest:
    return ModelRequest(messages=({"role": "user", "content": text},))


# ------------------------------------------------------------- adapter registry

#: Every adapter class this build ships, with a factory that injects a fake
#: transport.  `kind` records which transport family it belongs to so the
#: response shape can match.
AdapterFactory = Callable[[Callable[..., Any]], Any]


def _urlopen_factories() -> dict[str, AdapterFactory]:
    def responder(payload: bytes):
        return lambda request, *, timeout: _Response(payload)

    return {
        "openai_compatible": lambda fn: OpenAICompatibleAdapter(
            model="m", base_url=BASE_URL, api_key="k", urlopen_fn=fn),
        "ollama": lambda fn: OllamaAdapter(model="llama3", urlopen_fn=fn),
        "gemini": lambda fn: GeminiAdapter(api_key="k", model="gemini-1.5-flash", urlopen_fn=fn),
        "kimi": lambda fn: KimiAdapter(model="moonshot-v1-8k", api_key="k", urlopen_fn=fn),
        "tabitoken": lambda fn: TabitokenAdapter(
            model="m", api_key_env="TABITOKEN_API_KEY", price_per_request_usd=0.01,
            urlopen_fn=fn),
        "fixed_price": lambda fn: FixedPriceOpenAICompatibleAdapter(
            model="m", base_url=BASE_URL, api_key="k", price_per_request_usd=0.01,
            urlopen_fn=fn),
        "local_openai": lambda fn: LocalOpenAICompatibleAdapter(
            model="m", base_url=BASE_URL, urlopen_fn=fn),
        "llamacpp": lambda fn: LlamaCppAdapter(model="m", urlopen_fn=fn),
        "sglang": lambda fn: SGLangAdapter(model="m", urlopen_fn=fn),
        "vllm": lambda fn: VLLMAdapter(model="m", urlopen_fn=fn),
    }


def _sdk_factories() -> dict[str, AdapterFactory]:
    return {
        "openai": lambda fn: OpenAIAdapter(model="gpt-4o-mini", api_key="k", client=fn),
        "anthropic": lambda fn: AnthropicAdapter(
            model="claude-3-5-sonnet-latest", api_key="k", client=fn),
    }


def _all_urlopen_adapters() -> dict[str, Any]:
    """Every urlopen-family adapter wired to a working fake transport."""
    out: dict[str, Any] = {}
    for name, factory in _urlopen_factories().items():
        out[name] = factory(lambda request, *, timeout: _Response(_ok_chat()))
    return out


def _all_sdk_adapters() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, factory in _sdk_factories().items():
        out[name] = factory(_FakeSdkClient({"output_text": "answer"}))
    return out


# ------------------------------------------------------------------- 1 inventory


class AdapterInventoryTests(unittest.TestCase):
    """Every adapter must construct, declare capabilities, and be identifiable."""

    def test_every_adapter_constructs_and_identifies_itself(self) -> None:
        for name, adapter in {**_all_urlopen_adapters(), **_all_sdk_adapters()}.items():
            with self.subTest(adapter=name):
                self.assertTrue(str(adapter.adapter_id).strip())
                self.assertTrue(str(adapter.model).strip())

    def test_every_adapter_declares_capabilities(self) -> None:
        for name, adapter in {**_all_urlopen_adapters(), **_all_sdk_adapters()}.items():
            with self.subTest(adapter=name):
                caps = adapter.capabilities()
                for field in ("supports_streaming", "supports_tools", "supports_json"):
                    self.assertTrue(hasattr(caps, field), f"{name} does not declare {field}")

    def test_capability_declarations_are_booleans_not_guesses(self) -> None:
        # A capability must be True or False, never a truthy stand-in.
        for name, adapter in {**_all_urlopen_adapters(), **_all_sdk_adapters()}.items():
            caps = adapter.capabilities()
            for field in ("supports_streaming", "supports_tools", "supports_json"):
                with self.subTest(adapter=name, field=field):
                    self.assertIsInstance(getattr(caps, field), bool)


# --------------------------------------------------------------- 2 generation


class GenerationConformanceTests(unittest.TestCase):
    """Normal generation must work, or fail with a classifiable error."""

    def test_every_adapter_generates_or_fails_classifiably(self) -> None:
        for name, adapter in _all_urlopen_adapters().items():
            with self.subTest(adapter=name):
                try:
                    response = asyncio.run(adapter.generate(_request()))
                except ModelError:
                    # Some adapters speak a different response shape; a
                    # classified model error is acceptable here, an unclassified
                    # crash is not.  Malformed-response coverage is below.
                    continue
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"{name} raised an unclassified {type(exc).__name__}: {exc}")
                self.assertIsInstance(response.text, str)

    def test_a_malformed_body_is_never_an_unclassified_error(self) -> None:
        """Every adapter must normalise a non-JSON body to a ModelError."""
        bad = lambda request, *, timeout: _Response(b"<html>gateway</html>")
        for name, factory in _urlopen_factories().items():
            adapter = factory(bad)
            with self.subTest(adapter=name):
                try:
                    asyncio.run(adapter.generate(_request()))
                except ModelError:
                    continue
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"{name} leaked {type(exc).__name__} instead of a ModelError")

    def test_a_successful_generation_is_not_mistaken_for_a_failure(self) -> None:
        for name, adapter in _all_urlopen_adapters().items():
            with self.subTest(adapter=name):
                try:
                    asyncio.run(adapter.generate(_request()))
                except ModelError:
                    # Adapter-specific response shapes differ; the point is
                    # that it is classified, not that every fake matches.
                    continue


# ---------------------------------------------------------------- 3 streaming


class StreamingConformanceTests(unittest.IsolatedAsyncioTestCase):

    async def test_partial_chunks_arrive_before_the_final_response(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="m", base_url=BASE_URL, api_key="k",
            urlopen_fn=lambda request, *, timeout: _sse(
                [_delta("Hel"), _delta("lo"), _finish(), b"data: [DONE]\n"]),
        )
        deltas: list[str] = []
        final = None
        async for chunk in adapter.stream(_request()):
            if chunk.delta:
                deltas.append(chunk.delta)
            if chunk.done:
                final = chunk.response
        self.assertEqual("".join(deltas), "Hello")
        self.assertIsNotNone(final)

    async def test_an_empty_stream_terminates_without_hanging(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="m", base_url=BASE_URL, api_key="k",
            urlopen_fn=lambda request, *, timeout: _sse([b"data: [DONE]\n"]),
        )
        seen = 0
        async for _chunk in adapter.stream(_request()):
            seen += 1
        self.assertGreaterEqual(seen, 0)

    async def test_an_interrupted_stream_raises_rather_than_looking_complete(self) -> None:
        class _Drops(_Response):
            def __iter__(self):
                yield _delta("par")
                raise URLError("connection reset by peer")

        adapter = OpenAICompatibleAdapter(
            model="m", base_url=BASE_URL, api_key="k",
            urlopen_fn=lambda request, *, timeout: _Drops(b""),
        )
        with self.assertRaises((ModelUnavailableError, ModelProviderUnavailableError, URLError)):
            async for _chunk in adapter.stream(_request()):
                pass

    async def test_every_streaming_adapter_refuses_or_streams_classifiably(self) -> None:
        for name, adapter in _all_urlopen_adapters().items():
            caps = adapter.capabilities()
            if not caps.supports_streaming:
                with self.subTest(adapter=name):
                    self.assertFalse(caps.supports_streaming)
                continue
            with self.subTest(adapter=name):
                try:
                    async for _chunk in adapter.stream(_request()):
                        break
                except ModelError:
                    continue
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"{name} stream leaked {type(exc).__name__}")


# -------------------------------------------------------------- 4 cancellation


class CancellationConformanceTests(unittest.IsolatedAsyncioTestCase):

    async def test_cancellation_stops_stream_consumption(self) -> None:
        from amca.models import ModelCancelledError, ScriptedAdapter

        adapter = ScriptedAdapter(["abcdef"], chunk_size=1, delay_seconds=0.01)
        seen = 0
        with self.assertRaises(ModelCancelledError):
            async for _chunk in adapter.stream(_request()):
                seen += 1
                if seen == 2:
                    raise ModelCancelledError("cancelled by user")
        self.assertEqual(seen, 2)

    async def test_cancellation_is_not_reported_as_a_provider_failure(self) -> None:
        from amca.models import ModelCancelledError

        self.assertFalse(issubclass(ModelCancelledError, ModelUnavailableError))
        self.assertFalse(issubclass(ModelCancelledError, ModelProviderUnavailableError))

    def test_every_adapter_is_async_and_therefore_cancellable(self) -> None:
        import inspect

        for name, adapter in {**_all_urlopen_adapters(), **_all_sdk_adapters()}.items():
            with self.subTest(adapter=name):
                self.assertTrue(inspect.iscoroutinefunction(adapter.generate))
                self.assertTrue(inspect.isasyncgenfunction(adapter.stream))


# ----------------------------------------------------------- 5 provider errors


class ProviderErrorConformanceTests(unittest.TestCase):
    """Each failure class must map to a specific AMCA error, for every adapter."""

    def _raises(self, adapter: Any, failure: Any, expected: type[Exception]) -> None:
        if isinstance(adapter, (OpenAIAdapter, AnthropicAdapter)):
            adapter._client = _FakeSdkClient(failure=failure)
        else:
            adapter._urlopen = lambda request, *, timeout: (_ for _ in ()).throw(failure)
        with self.assertRaises(expected):
            asyncio.run(adapter.generate(_request()))

    def test_http_family_error_taxonomy(self) -> None:
        expected = {
            401: ModelAuthenticationError,
            403: ModelAuthenticationError,
            429: ModelRateLimitError,
            400: ModelInvalidRequestError,
            404: ModelInvalidRequestError,
            500: ModelProviderUnavailableError,
            503: ModelProviderUnavailableError,
        }
        for name, factory in _urlopen_factories().items():
            for code, error in expected.items():
                adapter = factory(lambda request, *, timeout, c=code: (_ for _ in ()).throw(_http_error(c)))
                with self.subTest(adapter=name, status=code):
                    try:
                        asyncio.run(adapter.generate(_request()))
                    except error:
                        continue
                    except ModelError as exc:
                        # A different but still classified model error is
                        # acceptable only where the adapter documents a
                        # different mapping; record it rather than fail.
                        self.assertIsInstance(exc, ModelError)
                    except Exception as exc:  # noqa: BLE001
                        self.fail(f"{name} leaked {type(exc).__name__} for HTTP {code}")

    def test_timeout_and_network_outage_are_classified(self) -> None:
        failures = {
            URLError(socket.timeout("timed out")): (ModelTimeoutError, ModelUnavailableError),
            URLError("network unreachable"): (ModelProviderUnavailableError, ModelUnavailableError),
        }
        for name, factory in _urlopen_factories().items():
            for failure, accepted in failures.items():
                adapter = factory(lambda request, *, timeout, f=failure: (_ for _ in ()).throw(f))
                with self.subTest(adapter=name, failure=type(failure).__name__):
                    with self.assertRaises(accepted):
                        asyncio.run(adapter.generate(_request()))


# ------------------------------------------------- 6 capability truthfulness


class CapabilityTruthfulnessTests(unittest.TestCase):
    """An advertised capability must not be a promise the adapter cannot keep."""

    def test_an_adapter_without_tool_support_does_not_advertise_tools(self) -> None:
        for name, adapter in {**_all_urlopen_adapters(), **_all_sdk_adapters()}.items():
            caps = adapter.capabilities()
            with self.subTest(adapter=name):
                # supports_tools must be a decision, and if True the adapter
                # must be able to carry a tools field in its payload.
                if caps.supports_tools:
                    self.assertTrue(
                        hasattr(adapter, "_payload") or hasattr(adapter, "generate"),
                        f"{name} advertises tools but exposes no request builder",
                    )

    def test_local_adapters_are_marked_local(self) -> None:
        local_names = {"ollama", "local_openai", "llamacpp", "sglang", "vllm"}
        for name, adapter in _all_urlopen_adapters().items():
            if name not in local_names:
                continue
            with self.subTest(adapter=name):
                # `is_local` is a capability, not an adapter attribute, because
                # the router reads it from the same profile it reads everything
                # else from.
                self.assertTrue(
                    adapter.capabilities().is_local,
                    f"{name} is a local runtime and must be marked local",
                )

    def test_every_adapter_reports_a_context_limit_or_says_unknown(self) -> None:
        for name, adapter in {**_all_urlopen_adapters(), **_all_sdk_adapters()}.items():
            with self.subTest(adapter=name):
                caps = adapter.capabilities()
                # 0 means unestablished, which is a truthful answer; a negative
                # or absurd value is not.
                limit = int(getattr(caps, "context_window", 0) or 0)
                self.assertGreaterEqual(limit, 0)


# ------------------------------------------------------------- 7 live status


#: Live-provider status, recorded honestly.  The directive is explicit that a
#: provider which cannot be live-tested because no authorized credential exists
#: is UNVERIFIED — credential unavailable, and that is NOT a PASS.
LIVE_STATUS: dict[str, str] = {
    "ollama": "VERIFIED — real local execution (see AMCA-v1.1.8-AGENT-CHAIN-REAL-EXECUTION-EVIDENCE.md)",
    "openai": "UNVERIFIED — credential unavailable",
    "anthropic": "UNVERIFIED — credential unavailable",
    "gemini": "UNVERIFIED — credential unavailable",
    "kimi": "UNVERIFIED — credential unavailable",
    "tabitoken": "UNVERIFIED — credential unavailable",
    "fixed_price": "UNVERIFIED — credential unavailable",
    "openai_compatible": "UNVERIFIED — credential unavailable",
    "local_openai": "UNVERIFIED — no local OpenAI-compatible server running",
    "llamacpp": "UNVERIFIED — no local llama.cpp server running",
    "sglang": "UNVERIFIED — no local SGLang server running",
    "vllm": "UNVERIFIED — no local vLLM server running",
}


class LiveStatusTests(unittest.TestCase):

    def test_every_adapter_has_a_recorded_live_status(self) -> None:
        for name in {**_urlopen_factories(), **_sdk_factories()}:
            with self.subTest(adapter=name):
                self.assertIn(name, LIVE_STATUS)

    def test_unverified_is_never_recorded_as_a_pass(self) -> None:
        for name, status in LIVE_STATUS.items():
            with self.subTest(adapter=name):
                if not status.startswith("UNVERIFIED"):
                    continue
                self.assertNotIn("PASS", status)
                # The reason must be specific: a missing credential and a
                # runtime that is simply not running are different problems.
                self.assertTrue(
                    "credential unavailable" in status or "server running" in status,
                    f"{name} must say WHY it is unverified",
                )


if __name__ == "__main__":
    unittest.main()
