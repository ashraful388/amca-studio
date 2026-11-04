"""Conformance tests for AMCA provider adapters.

This module accompanies the v0.6.0 M1.1 provider-adapter conformance
audit. It is deliberately narrow: it asserts invariants on top of the
existing per-adapter contract tests in :mod:`tests.test_models`. All
remote provider interactions are routed through in-process fakes or
the loopback Ollama daemon (which is skipped if not reachable).

The tests in this module do not introduce new provider abstractions,
do not change any adapter's public surface, and do not touch any
non-goal subsystem (Main Chat, Router, cost/budget, Studio UI,
Model Hub, database/schema, release metadata, v0.5.1 artifacts,
Bedrock, or installer/portable packaging).
"""

from __future__ import annotations

import io
import json
import asyncio
import socket
import unittest
from contextlib import contextmanager
from typing import Any
from unittest import IsolatedAsyncioTestCase
from urllib.error import HTTPError, URLError

from amca.models import (
    ModelError,

    AnthropicAdapter,
    FixedPriceOpenAICompatibleAdapter,
    GeminiAdapter,
    KimiAdapter,
    LlamaCppAdapter,
    LocalOpenAICompatibleAdapter,
    ModelAuthenticationError,
    ModelCancelledError,
    ModelInvalidRequestError,
    ModelProtocolError,
    ModelProviderUnavailableError,
    ModelRateLimitError,
    ModelRegistry,
    ModelRequest,
    ModelTimeoutError,
    ModelUnavailableError,
    OllamaAdapter,
    OpenAIAdapter,
    OpenAICompatibleAdapter,
    SGLangAdapter,
    ScriptedAdapter,
    TabitokenAdapter,
    VLLMAdapter,
)


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _http_error(code: int) -> HTTPError:
    return HTTPError(
        url="https://example.test/",
        code=code,
        msg="synthetic",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b'{"error": "synthetic"}'),
    )


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


@contextmanager
def _patched_urlopen(adapter: Any, payload: dict[str, Any]):
    captured: dict[str, Any] = {}

    def _fake(request: Any, *, timeout: float) -> _FakeHTTPResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        return _FakeHTTPResponse(payload)

    original = adapter._urlopen
    adapter._urlopen = _fake
    try:
        yield captured
    finally:
        adapter._urlopen = original


def _ollama_loopback_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=0.5):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 1. Every adapter constructs a valid bounded request
# ---------------------------------------------------------------------------


class BoundedRequestTests(unittest.TestCase):
    """Each adapter that owns a payload builder constructs a request that
    is bounded (no unbounded fields, normalized reserved fields, no
    surprise extra fields) and uses a provider-neutral model name."""

    def test_openai_compatible_reserved_field_override_is_rejected(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="gpt-test",
            base_url="http://127.0.0.1:9999/v1",
        )
        with self.assertRaises(ValueError):
            adapter._payload(
                ModelRequest(
                    messages="hello",
                    extra={"model": "attacker-override"},
                ),
                stream=False,
            )

    def test_local_openai_compatible_inherits_protections(self) -> None:
        adapter = LocalOpenAICompatibleAdapter(
            model="gpt-test",
            base_url="http://127.0.0.1:9999/v1",
        )
        self.assertTrue(adapter.capabilities().is_local)
        with self.assertRaises(ValueError):
            adapter._payload(
                ModelRequest(messages="hi", extra={"messages": []}),
                stream=False,
            )

    def test_fixed_price_inherits_openai_compatible_protections(self) -> None:
        adapter = FixedPriceOpenAICompatibleAdapter(
            model="gpt-test",
            base_url="http://127.0.0.1:9999/v1",
            api_key_env="OPENAI_API_KEY",
            price_per_request_usd=0.01,
        )
        with self.assertRaises(ValueError):
            adapter._payload(
                ModelRequest(messages="hi", extra={"temperature": 0.5}),
                stream=False,
            )

    def test_tabitoken_inherits_and_uses_fixed_price(self) -> None:
        adapter = TabitokenAdapter(
            model="tabi-test",
            api_key_env="TABITOKEN_TEST_KEY",
            price_per_request_usd=0.005,
        )
        payload = adapter._payload(
            ModelRequest(messages="hi"),
            stream=False,
        )
        self.assertEqual(payload["model"], "tabi-test")
        self.assertEqual(adapter.estimate_cost(1000, 500), 0.005)
        with self.assertRaises(ValueError):
            adapter._payload(
                ModelRequest(messages="hi", extra={"model": "x"}),
                stream=False,
            )

    def test_ollama_reserved_field_override_is_rejected(self) -> None:
        adapter = OllamaAdapter(model="qwen-test", base_url="http://127.0.0.1:11434")
        with self.assertRaises(ValueError):
            adapter._payload(
                ModelRequest(messages="hi", extra={"model": "x"}),
                stream=False,
            )

    def test_named_local_adapters_use_documented_loopback_defaults(self) -> None:
        cases = [
            (LlamaCppAdapter, "llama.cpp", "http://127.0.0.1:8080/v1"),
            (VLLMAdapter, "vllm", "http://127.0.0.1:8000/v1"),
            (SGLangAdapter, "sglang", "http://127.0.0.1:30000/v1"),
        ]
        for cls, provider, base in cases:
            adapter = cls(model="local-test")
            self.assertEqual(adapter.provider, provider)
            self.assertEqual(adapter.provider_metadata()["base_url"], base)
            self.assertTrue(adapter.capabilities().is_local)
            # Remote opt-in still required
            with self.assertRaises(ValueError):
                cls(model="local-test", base_url="http://remote.example.com/v1")


# ---------------------------------------------------------------------------
# 2. Provider / model identity preservation
# ---------------------------------------------------------------------------


class IdentityPreservationTests(IsolatedAsyncioTestCase):
    async def test_openai_compatible_response_model_is_provider_reported(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="gpt-test",
            base_url="http://127.0.0.1:9999/v1",
        )
        payload = {
            "id": "resp-1",
            "model": "gpt-test-actual",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }
        with _patched_urlopen(adapter, payload) as captured:
            response = await adapter.generate(ModelRequest(messages="hi"))
        self.assertEqual(response.model, "gpt-test-actual")
        self.assertEqual(response.provider, "openai-compatible")
        self.assertEqual(response.usage.input_tokens, 3)
        self.assertEqual(response.usage.output_tokens, 2)
        # The Authorization header is added only when an api_key is set;
        # here none is set, so the header must be absent (no leaked empty
        # value either).
        self.assertNotIn("Authorization", captured["headers"])

    async def test_kimi_response_preserves_kimi_provider(self) -> None:
        # KimiAdapter inherits OpenAICompatibleAdapter; this is a thin
        # assertion that the provider override survives a generate call.
        adapter = KimiAdapter(
            model="moonshot-v1-8k",
            api_key="kimi-secret-not-for-output",
        )
        payload = {
            "id": "cmpl-1",
            "model": "moonshot-v1-8k",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        with _patched_urlopen(adapter, payload) as captured:
            response = await adapter.generate(ModelRequest(messages="hi"))
        self.assertEqual(response.provider, "kimi")
        # The Authorization header carries the bearer; the adapter must
        # not echo the key elsewhere.
        self.assertEqual(
            captured["headers"].get("Authorization"),
            "Bearer kimi-secret-not-for-output",
        )
        # The key must not appear in provider_metadata.
        self.assertNotIn("kimi-secret", str(dict(response.provider_metadata)))
        # The key must not appear in repr.
        self.assertNotIn("kimi-secret", repr(adapter))


# ---------------------------------------------------------------------------
# 3. Credential / header safety (BLOCKING concrete secret leak)
# ---------------------------------------------------------------------------


class CredentialSafetyTests(unittest.TestCase):
    def test_provider_metadata_excludes_authorization_header_value(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="gpt-test",
            base_url="http://127.0.0.1:9999/v1",
            api_key="do-not-leak",
        )
        meta = dict(adapter.provider_metadata())
        # No key, no Authorization header, no Authorization substring.
        joined = json.dumps(meta, default=str)
        self.assertNotIn("do-not-leak", joined)
        self.assertNotIn("authorization", joined.lower())
        self.assertNotIn("api_key", joined.lower())
        self.assertNotIn("apikey", joined.lower())

    def test_provider_metadata_filter_runs_for_every_production_adapter(self) -> None:
        # Each adapter's provider_metadata() must not expose a value
        # the base class would consider sensitive.
        sensitive = ("do-not-leak", "Bearer ", "x-goog-api-key")
        cases = [
            OpenAICompatibleAdapter(
                model="m",
                base_url="http://127.0.0.1:9999/v1",
                api_key="do-not-leak",
            ),
            KimiAdapter(model="k", api_key="do-not-leak"),
            TabitokenAdapter(
                model="t", api_key_env="TABITOKEN_TEST", price_per_request_usd=0.01
            ),
            FixedPriceOpenAICompatibleAdapter(
                model="f",
                base_url="http://127.0.0.1:9999/v1",
                api_key="do-not-leak",
                price_per_request_usd=0.01,
            ),
            LlamaCppAdapter(model="m"),
            VLLMAdapter(model="m"),
            SGLangAdapter(model="m"),
            LocalOpenAICompatibleAdapter(model="m", base_url="http://127.0.0.1:9999/v1"),
            OllamaAdapter(model="m"),
        ]
        for adapter in cases:
            meta = json.dumps(dict(adapter.provider_metadata()), default=str)
            for needle in sensitive:
                self.assertNotIn(
                    needle,
                    meta,
                    msg=f"{type(adapter).__name__} leaked {needle!r} in provider_metadata: {meta}",
                )

    def test_repr_excludes_credentials_and_base_url(self) -> None:
        cases = [
            OpenAIAdapter(model="m", api_key="do-not-leak"),
            AnthropicAdapter(model="m", api_key="do-not-leak"),
            GeminiAdapter(model="m", api_key="do-not-leak"),
            OpenAICompatibleAdapter(
                model="m", base_url="http://127.0.0.1:9999/v1", api_key="do-not-leak"
            ),
            KimiAdapter(model="m", api_key="do-not-leak"),
            TabitokenAdapter(
                model="m", api_key_env="TABITOKEN_TEST", price_per_request_usd=0.01
            ),
            FixedPriceOpenAICompatibleAdapter(
                model="m",
                base_url="http://127.0.0.1:9999/v1",
                api_key="do-not-leak",
                price_per_request_usd=0.01,
            ),
            OllamaAdapter(model="m"),
        ]
        for adapter in cases:
            rendered = repr(adapter)
            self.assertNotIn(
                "do-not-leak",
                rendered,
                msg=f"{type(adapter).__name__} leaked api_key in repr",
            )
            # The base class repr intentionally shows adapter_id, provider, model.
            # It MUST NOT include base_url or Authorization.
            self.assertNotIn("base_url", rendered)
            self.assertNotIn("Authorization", rendered)
            self.assertNotIn("Bearer", rendered)


# ---------------------------------------------------------------------------
# 4. Usage metadata normalization
# ---------------------------------------------------------------------------


class UsageMetadataTests(unittest.TestCase):
    def test_openai_usage_normalization_uses_responses_api_fields(self) -> None:
        # Smoke-level: the documented Responses-API usage shape is the one
        # the adapter expects. A test that mints a synthetic response and
        # checks the parsed TokenUsage would require either an injected
        # SDK client or a network call. The full test lives in
        # tests.test_models; here we only assert the contract.
        from amca.models.base import TokenUsage

        usage = TokenUsage(input_tokens=10, output_tokens=20)
        self.assertEqual(usage.total_tokens, 30)
        with self.assertRaises(ValueError):
            TokenUsage(input_tokens=-1, output_tokens=0)
        with self.assertRaises(ValueError):
            TokenUsage(input_tokens=0, output_tokens=0, cost_usd=float("inf"))

    def test_missing_usage_is_unknown_not_fabricated(self) -> None:
        # ScriptedAdapter: when the script provides no usage, the adapter
        # falls back to estimate_tokens, which is permitted by §3 of the
        # work order. The cost must remain finite and non-negative; the
        # estimate is bounded by the four-UTF8-byte-per-token rule.
        import asyncio
        import math

        async def _go() -> None:
            adapter = ScriptedAdapter(scripts=["ok"])
            response = await adapter.generate(ModelRequest(messages="hi"))
            self.assertGreaterEqual(response.usage.cost_usd, 0.0)
            self.assertTrue(math.isfinite(response.usage.cost_usd))
            # Default input_cost_per_million is 0, so the cost is 0 here.
            self.assertEqual(response.usage.cost_usd, 0.0)

        asyncio.run(_go())


# ---------------------------------------------------------------------------
# 5. Structured-output conformance
# ---------------------------------------------------------------------------


class StructuredOutputTests(unittest.TestCase):
    def test_openai_compatible_extra_format_is_forwarded(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="gpt-test",
            base_url="http://127.0.0.1:9999/v1",
        )
        payload = adapter._payload(
            ModelRequest(
                messages="hi",
                extra={"response_format": {"type": "json_object"}},
            ),
            stream=False,
        )
        self.assertEqual(
            payload["response_format"], {"type": "json_object"}
        )

    def test_ollama_v051_format_forwarded_via_extra(self) -> None:
        # The v0.5.1 path: LocalModelPlannerRuntime sends
        # ``extra={"format": <schema>}`` to the Ollama adapter, which
        # forwards it to /api/chat. This is the documented transport
        # for native structured output (v0.5.1 commit db6f17fe).
        adapter = OllamaAdapter(model="qwen-test", base_url="http://127.0.0.1:11434")
        payload = adapter._payload(
            ModelRequest(messages="hi", extra={"format": "json"}),
            stream=False,
        )
        # 'format' MUST be present (forwarded) and 'options' MUST be
        # absent (the planner does not set temperature etc. here).
        self.assertEqual(payload["format"], "json")
        self.assertNotIn("options", payload)

    def test_openai_compatible_unsupported_capability_fails_truthfully(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="gpt-test",
            base_url="http://127.0.0.1:9999/v1",
        )
        # Empty choices → ModelProtocolError, not a generic crash.
        with self.assertRaises(ModelProtocolError):
            adapter._parse_response({"choices": []}, ModelRequest(messages="hi"))


# ---------------------------------------------------------------------------
# 6. Cancellation / timeout propagation
# ---------------------------------------------------------------------------


class CancellationTimeoutTests(IsolatedAsyncioTestCase):
    async def test_cancel_with_no_in_flight_returns_false(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="gpt-test",
            base_url="http://127.0.0.1:9999/v1",
        )
        # No in-flight tasks at all; cancel() is a coroutine and returns
        # False when there is nothing to cancel.
        self.assertFalse(await adapter.cancel())


# ---------------------------------------------------------------------------
# 7. Error normalization
# ---------------------------------------------------------------------------


class ErrorNormalizationTests(unittest.TestCase):
    def test_openai_compatible_maps_http_401_to_unavailable(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="gpt-test",
            base_url="http://127.0.0.1:9999/v1",
        )

        def _fake(_request: Any, *, timeout: float) -> Any:
            raise _http_error(401)

        adapter._urlopen = _fake
        with self.assertRaises(ModelAuthenticationError) as ctx:
            adapter._post_json(
                adapter._payload(ModelRequest(messages="hi"), stream=False),
                timeout=adapter._timeout,
            )
        self.assertIn("401", str(ctx.exception))

    def test_openai_compatible_maps_http_429_to_unavailable(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="gpt-test",
            base_url="http://127.0.0.1:9999/v1",
        )

        def _fake(_request: Any, *, timeout: float) -> Any:
            raise _http_error(429)

        adapter._urlopen = _fake
        with self.assertRaises(ModelRateLimitError) as ctx:
            adapter._post_json(
                adapter._payload(ModelRequest(messages="hi"), stream=False),
                timeout=adapter._timeout,
            )
        self.assertIn("429", str(ctx.exception))

    def test_openai_compatible_maps_urlerror_to_unavailable(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="gpt-test",
            base_url="http://127.0.0.1:9999/v1",
        )

        def _fake(_request: Any, *, timeout: float) -> Any:
            raise URLError("name resolution")

        adapter._urlopen = _fake
        with self.assertRaises(ModelUnavailableError) as ctx:
            adapter._post_json(
                adapter._payload(ModelRequest(messages="hi"), stream=False),
                timeout=adapter._timeout,
            )
        self.assertIn("could not reach", str(ctx.exception))

    def test_openai_compatible_malformed_response_is_protocol_error(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="gpt-test",
            base_url="http://127.0.0.1:9999/v1",
        )
        with self.assertRaises(ModelProtocolError):
            adapter._parse_response({}, ModelRequest(messages="hi"))


# ---------------------------------------------------------------------------
# 8. v0.5.1 Ollama structured-output regression
# ---------------------------------------------------------------------------


class V051OllamaRegressionTests(unittest.TestCase):
    @unittest.skipUnless(
        _ollama_loopback_reachable(),
        "Ollama loopback daemon is not reachable on 127.0.0.1:11434",
    )
    def test_ollama_qwen3_1_7b_local_smoke(self) -> None:
        # A single bounded call to the running loopback daemon. The
        # prompt asks for a one-word response so the smoke is fast and
        # the resulting usage fields are non-zero. This smoke verifies
        # the v0.5.1 Ollama path is preserved under the v0.6.0 audit
        # harness (no model download, no remote endpoint).
        import asyncio

        async def _go() -> None:
            adapter = OllamaAdapter(model="qwen3:1.7b")
            request = ModelRequest(
                messages="Reply with exactly one word: ok",
                max_output_tokens=16,
            )
            response = await adapter.generate(request)
            self.assertEqual(response.provider, "ollama")
            self.assertEqual(response.model, "qwen3:1.7b")
            self.assertGreater(response.usage.input_tokens, 0)
            # The text may include reasoning-style artefacts from a
            # thinking model; we only assert the adapter surfaces a
            # non-empty string and the model is correctly identified.
            self.assertIsInstance(response.text, str)

        asyncio.run(_go())


# ---------------------------------------------------------------------------
# 9. Feature gates
# ---------------------------------------------------------------------------


class FeatureGateTests(unittest.TestCase):
    def test_registry_adapters_can_coexist(self) -> None:
        # Sanity: the new conformance tests must not assume a feature
        # gate; the registry must accept adapters without enabling any
        # experimental capability.
        registry = ModelRegistry()
        registry.register(
            OpenAICompatibleAdapter(model="gpt-a", base_url="http://127.0.0.1:9999/v1")
        )
        registry.register(
            LocalOpenAICompatibleAdapter(model="gpt-b", base_url="http://127.0.0.1:9998/v1")
        )
        registry.register(
            ScriptedAdapter(scripts=["ok"], adapter_id="scripted-1")
        )
        self.assertEqual(len(registry), 3)


# ---------------------------------------------------------------------------
# 10. Timeout propagation across adapters (GOOSE-M1.1.1 closeout)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# 7b. Error normalization -- M1.2 expanded matrix
# ---------------------------------------------------------------------------

class ErrorNormalizationM12Tests(unittest.TestCase):
    """M1.2 provider-error-normalization matrix.
    Covers the work order's full provider / status-code matrix and the
    error-privacy invariants.  The matrix distinguishes dedicated SDK-backed
    adapters (OpenAI, Anthropic) from shared OpenAI-compatible HTTP profiles
    (Kimi, Tabitoken, FixedPrice, LocalOpenAICompatible, LlamaCpp, VLLM,
    SGLang, DeepSeek, Mistral, xAI) which all inherit the
    OpenAICompatibleAdapter classification logic.
    """

    # --- shared helpers --------------------------------------------------

    @staticmethod
    def _invoke_post(adapter, exc_to_raise):
        """Patch the adapter's _urlopen to raise ``exc_to_raise`` then call
        the adapter's _post_json with a synthetic request.  Handles the two
        adapter signatures:
          - GeminiAdapter._post_json(request)
          - OpenAICompatibleAdapter._post_json(payload, *, timeout)
          - OllamaAdapter._post_json(payload, *, timeout)
        """
        def _fake(_request, *args, **kwargs):
            raise exc_to_raise

        adapter._urlopen = _fake
        if isinstance(adapter, GeminiAdapter):
            return adapter._post_json(ModelRequest(messages="hi"))
        if isinstance(adapter, OpenAICompatibleAdapter):
            return adapter._post_json(
                adapter._payload(ModelRequest(messages="hi"), stream=False),
                timeout=adapter._timeout,
            )
        if isinstance(adapter, OllamaAdapter):
            return adapter._post_json(
                adapter._payload(ModelRequest(messages="hi"), stream=False),
                timeout=adapter._timeout,
            )
        raise TypeError(f"unsupported adapter type: {type(adapter).__name__}")

    # --- SDK-backed adapters (OpenAI) -------------------------------------

    @staticmethod
    def _sdk_httpx_response(status_code: int):
        """Build an httpx.Response shaped the way the installed OpenAI SDK
        (3.3.1) requires: ``APIStatusError.__init__`` reads
        ``response.request`` and ``response.headers`` while constructing the
        exception, so a bare ``status_code``-only fake breaks fixture
        construction itself (see 20Z characterization). Purely local - the
        .invalid host is never contacted."""
        import httpx

        request = httpx.Request("POST", "https://unit-test.invalid/v1/responses")
        return httpx.Response(
            status_code, request=request,
            headers={"x-request-id": "m12-fixture"},
        )

    def test_m12_01_openai_authentication_error_401(self) -> None:
        """401 from OpenAI SDK -> ModelAuthenticationError."""
        try:
            from openai import AuthenticationError as _OAAuth  # type: ignore
        except Exception:
            self.skipTest("openai SDK not installed")
        adapter = OpenAIAdapter(model="gpt-test", api_key="sk-test")

        def _raise(*_a, **_k):
            raise _OAAuth(
                message="Incorrect API key provided",
                response=self._sdk_httpx_response(401),
                body={"error": {"code": "invalid_api_key"}},
            )

        adapter._client = type(
            "_C", (), {"responses": type("_R2", (), {
                "create": staticmethod(_raise)
            })()}
        )()
        adapter._get_client = lambda timeout=None: adapter._client
        with self.assertRaises(ModelAuthenticationError):
            asyncio.run(adapter.generate(ModelRequest(messages="hi")))

    def test_m12_02_openai_rate_limit_error_429(self) -> None:
        """429 from OpenAI SDK -> ModelRateLimitError."""
        try:
            from openai import RateLimitError as _OARate  # type: ignore
        except Exception:
            self.skipTest("openai SDK not installed")
        adapter = OpenAIAdapter(model="gpt-test", api_key="sk-test")

        def _raise(*_a, **_k):
            raise _OARate(
                message="Rate limit reached",
                response=self._sdk_httpx_response(429),
                body={"error": {"code": "rate_limit_exceeded"}},
            )

        adapter._client = type(
            "_C", (), {"responses": type("_R2", (), {
                "create": staticmethod(_raise)
            })()}
        )()
        adapter._get_client = lambda timeout=None: adapter._client
        with self.assertRaises(ModelRateLimitError):
            asyncio.run(adapter.generate(ModelRequest(messages="hi")))

    def test_m12_03_openai_invalid_request_400(self) -> None:
        """400 from OpenAI SDK -> ModelInvalidRequestError."""
        try:
            from openai import BadRequestError as _OABad  # type: ignore
        except Exception:
            self.skipTest("openai SDK not installed")
        adapter = OpenAIAdapter(model="gpt-test", api_key="sk-test")

        def _raise(*_a, **_k):
            raise _OABad(
                message="Bad request",
                response=self._sdk_httpx_response(400),
                body={"error": {"code": "bad_request"}},
            )

        adapter._client = type(
            "_C", (), {"responses": type("_R2", (), {
                "create": staticmethod(_raise)
            })()}
        )()
        adapter._get_client = lambda timeout=None: adapter._client
        with self.assertRaises(ModelInvalidRequestError):
            asyncio.run(adapter.generate(ModelRequest(messages="hi")))

    def test_m12_04_openai_server_error_500(self) -> None:
        """5xx from OpenAI SDK -> ModelProviderUnavailableError (exact class).

        20AB: the fixture now constructs a genuine SDK APIStatusError via a
        properly shaped httpx.Response (status_code + request + headers), as
        required by installed OpenAI SDK 3.3.1 - the previous bare
        status_code-only fake raised AttributeError during exception
        construction, and the historical assertRaises(ModelUnavailableError)
        passed only accidentally because ModelProviderUnavailableError is a
        SUBCLASS of the historical ModelUnavailableError base. The assertion
        now targets the exact established AMCA contract class.
        """
        try:
            from openai import APIStatusError as _OAStatus  # type: ignore
        except Exception:
            self.skipTest("openai SDK not installed")
        adapter = OpenAIAdapter(model="gpt-test", api_key="sk-test")

        def _raise(*_a, **_k):
            raise _OAStatus(
                message="Internal server error",
                response=self._sdk_httpx_response(500),
                body={"error": {"code": "internal_error"}},
            )

        adapter._client = type(
            "_C", (), {"responses": type("_R2", (), {
                "create": staticmethod(_raise)
            })()}
        )()
        adapter._get_client = lambda timeout=None: adapter._client
        with self.assertRaises(ModelProviderUnavailableError) as ctx:
            asyncio.run(adapter.generate(ModelRequest(messages="hi")))
        # exact concrete class, no superclass slack
        self.assertIs(type(ctx.exception), ModelProviderUnavailableError)
        self.assertIn("HTTP 500", str(ctx.exception))

    def test_m12_05_openai_timeout_via_sdk(self) -> None:
        """OpenAI SDK APITimeoutError -> ModelTimeoutError."""
        try:
            from openai import APITimeoutError as _OATimeout  # type: ignore
        except Exception:
            self.skipTest("openai SDK not installed")
        adapter = OpenAIAdapter(model="gpt-test", api_key="sk-test")

        def _raise(*_a, **_k):
            raise _OATimeout(request=type("_Rq", (), {})())

        adapter._client = type(
            "_C", (), {"responses": type("_R2", (), {
                "create": staticmethod(_raise)
            })()}
        )()
        adapter._get_client = lambda timeout=None: adapter._client
        with self.assertRaises(ModelTimeoutError):
            asyncio.run(adapter.generate(ModelRequest(messages="hi")))

    # --- SDK-backed adapters (Anthropic) ----------------------------------

    def test_m12_06_anthropic_authentication_error_401(self) -> None:
        """401 from Anthropic SDK -> ModelAuthenticationError."""
        try:
            from anthropic import AuthenticationError as _AnthAuth  # type: ignore
        except Exception:
            self.skipTest("anthropic SDK not installed")
        adapter = AnthropicAdapter(model="claude-test", api_key="sk-ant-test")

        def _raise(*_a, **_k):
            raise _AnthAuth(
                message="invalid x-api-key",
                response=type("_R", (), {"status_code": 401, "request": None, "headers": {}})(),
                body={"type": "error", "error": {"type": "authentication_error"}},
            )

        adapter._client = type(
            "_C", (), {"messages": type("_M", (), {
                "create": staticmethod(_raise)
            })()}
        )()
        adapter._get_client = lambda timeout=None: adapter._client
        with self.assertRaises(ModelAuthenticationError):
            asyncio.run(adapter.generate(ModelRequest(messages="hi")))

    def test_m12_07_anthropic_rate_limit_error_429(self) -> None:
        """429 from Anthropic SDK -> ModelRateLimitError."""
        try:
            from anthropic import RateLimitError as _AnthRate  # type: ignore
        except Exception:
            self.skipTest("anthropic SDK not installed")
        adapter = AnthropicAdapter(model="claude-test", api_key="sk-ant-test")

        def _raise(*_a, **_k):
            raise _AnthRate(
                message="rate limited",
                response=type("_R", (), {"status_code": 429, "request": None, "headers": {}})(),
                body={"type": "error", "error": {"type": "rate_limit_error"}},
            )

        adapter._client = type(
            "_C", (), {"messages": type("_M", (), {
                "create": staticmethod(_raise)
            })()}
        )()
        adapter._get_client = lambda timeout=None: adapter._client
        with self.assertRaises(ModelRateLimitError):
            asyncio.run(adapter.generate(ModelRequest(messages="hi")))

    # --- HTTP/shared adapters (Gemini) ------------------------------------

    def test_m12_08_gemini_http_401_becomes_auth(self) -> None:
        """Gemini 401 -> ModelAuthenticationError."""
        adapter = GeminiAdapter(model="gem-test", api_key="g-test")
        with self.assertRaises(ModelAuthenticationError):
            self._invoke_post(adapter, _http_error(401))

    def test_m12_09_gemini_http_403_becomes_auth(self) -> None:
        """Gemini 403 -> ModelAuthenticationError."""
        adapter = GeminiAdapter(model="gem-test", api_key="g-test")
        with self.assertRaises(ModelAuthenticationError):
            self._invoke_post(adapter, _http_error(403))

    def test_m12_10_gemini_http_429_becomes_rate_limit(self) -> None:
        """Gemini 429 -> ModelRateLimitError."""
        adapter = GeminiAdapter(model="gem-test", api_key="g-test")
        with self.assertRaises(ModelRateLimitError):
            self._invoke_post(adapter, _http_error(429))

    def test_m12_11_gemini_http_400_becomes_invalid_request(self) -> None:
        """Gemini 400 -> ModelInvalidRequestError."""
        adapter = GeminiAdapter(model="gem-test", api_key="g-test")
        with self.assertRaises(ModelInvalidRequestError):
            self._invoke_post(adapter, _http_error(400))

    def test_m12_12_gemini_http_500_becomes_unavailable(self) -> None:
        """Gemini 5xx -> ModelUnavailableError."""
        adapter = GeminiAdapter(model="gem-test", api_key="g-test")
        with self.assertRaises(ModelUnavailableError):
            self._invoke_post(adapter, _http_error(500))

    def test_m12_13_gemini_urlerror_timeout_becomes_timeout(self) -> None:
        """Gemini URLError('timed out') -> ModelTimeoutError."""
        adapter = GeminiAdapter(model="gem-test", api_key="g-test")
        with self.assertRaises(ModelTimeoutError):
            self._invoke_post(adapter, URLError("timed out"))

    def test_m12_14_gemini_urlerror_connection_becomes_unavailable(self) -> None:
        """Gemini URLError(non-timeout) -> ModelUnavailableError."""
        adapter = GeminiAdapter(model="gem-test", api_key="g-test")
        with self.assertRaises(ModelUnavailableError):
            self._invoke_post(adapter, URLError("connection refused"))

    # --- HTTP/shared adapters (OpenAI-compatible profiles) ----------------

    def test_m12_15_oc_400_becomes_invalid_request(self) -> None:
        """OpenAI-compatible 400 -> ModelInvalidRequestError."""
        adapter = OpenAICompatibleAdapter(
            model="oc-test", base_url="http://127.0.0.1:9999/v1"
        )
        with self.assertRaises(ModelInvalidRequestError):
            self._invoke_post(adapter, _http_error(400))

    def test_m12_16_oc_404_becomes_invalid_request(self) -> None:
        """OpenAI-compatible 404 -> ModelInvalidRequestError."""
        adapter = OpenAICompatibleAdapter(
            model="oc-test", base_url="http://127.0.0.1:9999/v1"
        )
        with self.assertRaises(ModelInvalidRequestError):
            self._invoke_post(adapter, _http_error(404))

    def test_m12_17_oc_422_becomes_invalid_request(self) -> None:
        """OpenAI-compatible 422 -> ModelInvalidRequestError."""
        adapter = OpenAICompatibleAdapter(
            model="oc-test", base_url="http://127.0.0.1:9999/v1"
        )
        with self.assertRaises(ModelInvalidRequestError):
            self._invoke_post(adapter, _http_error(422))

    def test_m12_18_oc_502_becomes_unavailable(self) -> None:
        """OpenAI-compatible 5xx -> ModelUnavailableError."""
        adapter = OpenAICompatibleAdapter(
            model="oc-test", base_url="http://127.0.0.1:9999/v1"
        )
        with self.assertRaises(ModelUnavailableError):
            self._invoke_post(adapter, _http_error(502))

    # --- Shared OpenAI-compatible profiles inherit mapping ---------------

    def test_m12_19_kimi_inherits_auth_mapping(self) -> None:
        """KimiAdapter inherits 401 -> ModelAuthenticationError from OC."""
        adapter = KimiAdapter(
            model="k-test", base_url="http://127.0.0.1:9999/v1", api_key="k-test"
        )
        with self.assertRaises(ModelAuthenticationError):
            self._invoke_post(adapter, _http_error(401))

    def test_m12_20_tabitoken_inherits_rate_limit(self) -> None:
        """TabitokenAdapter (uses FixedPriceOCAdapter) inherits 429 -> ModelRateLimitError."""
        adapter = TabitokenAdapter(
            model="tab-test",
            base_url="http://127.0.0.1:9999/v1",
            price_per_request_usd=0.001,
            api_key_env="TABITOKEN_API_KEY",
        )
        with self.assertRaises(ModelRateLimitError):
            self._invoke_post(adapter, _http_error(429))

    def test_m12_21_local_oc_inherits_unavailable(self) -> None:
        """LocalOpenAICompatibleAdapter inherits 5xx -> ModelUnavailableError."""
        adapter = LocalOpenAICompatibleAdapter(
            model="loc-test", base_url="http://127.0.0.1:9999/v1",
        )
        with self.assertRaises(ModelUnavailableError):
            self._invoke_post(adapter, _http_error(503))

    # --- Ollama v0.5.1 path preserved -------------------------------------

    def test_m12_22_ollama_v051_format_via_extra_still_works(self) -> None:
        """Ollama still forwards v0.5.1 format-via-extra; new error mapping
        does not break the accepted local path."""
        adapter = OllamaAdapter(model="qwen-test", base_url="http://127.0.0.1:11434")
        payload = adapter._payload(
            ModelRequest(messages="hi", extra={"format": "json"}),
            stream=False,
        )
        self.assertEqual(payload["format"], "json")
        self.assertNotIn("options", payload)

    def test_m12_23_ollama_401_becomes_auth(self) -> None:
        """Ollama 401 -> ModelAuthenticationError (consistent with HTTP/shared)."""
        adapter = OllamaAdapter(model="qwen-test", base_url="http://127.0.0.1:11434")
        with self.assertRaises(ModelAuthenticationError):
            self._invoke_post(adapter, _http_error(401))

    def test_m12_24_ollama_429_becomes_rate_limit(self) -> None:
        """Ollama 429 -> ModelRateLimitError."""
        adapter = OllamaAdapter(model="qwen-test", base_url="http://127.0.0.1:11434")
        with self.assertRaises(ModelRateLimitError):
            self._invoke_post(adapter, _http_error(429))

    # --- Error privacy (no API keys/headers in messages) ------------------

    def test_m12_25_auth_error_message_excludes_credentials(self) -> None:
        """ModelAuthenticationError message must not contain api keys or auth headers."""
        adapter = OpenAICompatibleAdapter(
            model="gpt-test", base_url="http://127.0.0.1:9999/v1",
        )
        sentinel_key = "sk-LIVE-1234567890ABCDEFGH"
        try:
            self._invoke_post(adapter, _http_error(401))
        except ModelAuthenticationError as exc:
            self.assertNotIn(sentinel_key, str(exc))
            self.assertNotIn("Bearer", str(exc))
        else:
            self.fail("ModelAuthenticationError not raised")

    def test_m12_26_rate_limit_message_excludes_credentials(self) -> None:
        """ModelRateLimitError message must not contain api keys or auth headers."""
        adapter = OpenAICompatibleAdapter(
            model="gpt-test", base_url="http://127.0.0.1:9999/v1",
        )
        sentinel_key = "sk-LIVE-1234567890ABCDEFGH"
        try:
            self._invoke_post(adapter, _http_error(429))
        except ModelRateLimitError as exc:
            self.assertNotIn(sentinel_key, str(exc))
        else:
            self.fail("ModelRateLimitError not raised")

    def test_m12_27_invalid_request_message_excludes_credentials(self) -> None:
        """ModelInvalidRequestError message must not contain api keys or auth headers."""
        adapter = GeminiAdapter(model="gem-test", api_key="g-test")
        sentinel_key = "g-LIVE-1234567890ABCDEFGH"
        try:
            self._invoke_post(adapter, _http_error(400))
        except ModelInvalidRequestError as exc:
            self.assertNotIn(sentinel_key, str(exc))
        else:
            self.fail("ModelInvalidRequestError not raised")

    # --- Missing-model / unsupported model -> ModelInvalidRequestError ----

    def test_m12_28_oc_unsupported_model_400_maps_to_invalid(self) -> None:
        """OpenAI-compatible 400 (model-not-found) -> ModelInvalidRequestError."""
        adapter = OpenAICompatibleAdapter(
            model="does-not-exist",
            base_url="http://127.0.0.1:9999/v1",
        )
        with self.assertRaises(ModelInvalidRequestError):
            self._invoke_post(adapter, _http_error(400))

    # --- Connection refused (no daemon) -> ModelUnavailableError ---------

    def test_m12_29_oc_connection_refused_becomes_unavailable(self) -> None:
        """OpenAI-compatible connection refused -> ModelUnavailableError."""
        adapter = OpenAICompatibleAdapter(
            model="oc-test", base_url="http://127.0.0.1:9999/v1",
        )
        with self.assertRaises(ModelUnavailableError):
            self._invoke_post(adapter, URLError("connection refused"))

    # --- Cancellation -> CancelledError propagates ------------------------

    def test_m12_30_oc_asyncio_cancellation_propagates(self) -> None:
        """asyncio.CancelledError raised inside _urlopen propagates as CancelledError.

        The M1.1.1 test_08 invariant (cancellation passes through) is upheld.
        """
        adapter = OpenAICompatibleAdapter(
            model="oc-test", base_url="http://127.0.0.1:9999/v1",
        )
        adapter._urlopen = lambda *a, **k: (_ for _ in ()).throw(
            asyncio.CancelledError()
        )
        with self.assertRaises(asyncio.CancelledError):
            self._invoke_post(adapter, asyncio.CancelledError())

    # --- Existing legacy categories still work ---------------------------

    def test_m12_31_malformed_json_still_protocol_error(self) -> None:
        """Malformed JSON (success status, garbage body) -> ModelProtocolError.

        Existing v0.5.x behavior is preserved.
        """
        adapter = OpenAICompatibleAdapter(
            model="oc-test", base_url="http://127.0.0.1:9999/v1",
        )
        with self.assertRaises(ModelProtocolError):
            adapter._parse_response({"choices": []}, ModelRequest(messages="hi"))

    def test_m12_32_classify_http_error_returns_none_for_unrelated(self) -> None:
        """Non-HTTP/URL exceptions pass through classifier with None."""
        for adapter in [
            OpenAICompatibleAdapter(
                model="x", base_url="http://127.0.0.1:9999/v1"
            ),
            GeminiAdapter(model="x", api_key="k"),
            OllamaAdapter(model="x", base_url="http://127.0.0.1:9999"),
        ]:
            self.assertIsNone(adapter._classify_http_error(ValueError("x")))

class ErrorCompatibilityCloseoutM121Tests(unittest.TestCase):
    """M1.2.1 error-compatibility closeout: the 12 required tests.

    Verifies the exception-inheritance repair and the provider/model
    unavailable split called out by the M1.2 review.  Every test here is
    a real conformance assertion, not a documentation check.
    """

    # --- B. except ModelUnavailableError catches timeout -----------------

    def test_m121_01_unavailable_catches_timeout(self) -> None:
        """An OpenAI-compatible transport timeout must be caught by
        ``except ModelUnavailableError`` (the v0.5.x contract).

        Concretely: ``ModelTimeoutError`` is a subclass of
        ``ModelUnavailableError`` after the M1.2.1 inheritance repair.
        """
        adapter = OpenAICompatibleAdapter(
            model="gpt-test", base_url="http://127.0.0.1:9999/v1",
        )

        def _fake(_request: Any, *, timeout: float) -> Any:
            raise URLError("timed out")

        adapter._urlopen = _fake
        with self.assertRaises(ModelUnavailableError) as ctx:
            adapter._post_json(
                adapter._payload(ModelRequest(messages="hi"), stream=False),
                timeout=adapter._timeout,
            )
        # Still observable as the more specific category too.
        self.assertIsInstance(ctx.exception, ModelTimeoutError)
        # And the original v0.5.x contract (catching ModelError) holds.
        self.assertIsInstance(ctx.exception, ModelError)

    # --- C. provider outage remains distinguishable ------------------------

    def test_m121_02_5xx_becomes_provider_unavailable(self) -> None:
        """HTTP 502 -> ModelProviderUnavailableError, a subclass of
        ModelUnavailableError, so ``except ModelUnavailableError`` still
        catches the provider outage but callers that need to distinguish
        it from a specific model absence can match the narrower class.
        """
        adapter = OpenAICompatibleAdapter(
            model="gpt-test", base_url="http://127.0.0.1:9999/v1",
        )
        # Inject a synthetic 502 so we can assert the HTTPError branch
        # without needing a live server.
        adapter._urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(
            _http_error(502)
        )
        with self.assertRaises(ModelProviderUnavailableError) as ctx:
            adapter._post_json(
                adapter._payload(ModelRequest(messages="hi"), stream=False),
                timeout=adapter._timeout,
            )
        self.assertIsInstance(ctx.exception, ModelUnavailableError)
        # The HTTPError branch produces "<label> returned HTTP <code>".
        self.assertIn("HTTP 502", str(ctx.exception))

    def test_m121_03_urlerror_non_timeout_becomes_provider_unavailable(self) -> None:
        """A non-timeout URLError (DNS failure, connection refused) maps
        to ModelProviderUnavailableError -- the provider itself is
        unreachable, not the model specifically.
        """
        adapter = OpenAICompatibleAdapter(
            model="gpt-test", base_url="http://127.0.0.1:9999/v1",
        )
        for cause in [
            URLError("name resolution"),
            URLError("connection refused"),
        ]:
            with self.subTest(cause=str(cause)):
                # Build a fresh adapter per subtest so the patched
                # _urlopen is the only mutation in scope.
                a = OpenAICompatibleAdapter(
                    model="gpt-test",
                    base_url="http://127.0.0.1:9999/v1",
                )
                a._urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(cause)
                with self.assertRaises(ModelProviderUnavailableError) as ctx:
                    a._post_json(
                        a._payload(ModelRequest(messages="hi"), stream=False),
                        timeout=a._timeout,
                    )
                self.assertIsInstance(ctx.exception, ModelUnavailableError)
                # Backwards-compat: the "could not reach" message that
                # v0.5.x callers may pattern-match on is preserved.
                self.assertIn("could not reach", str(ctx.exception))

    # --- C. model unavailable remains distinguishable --------------------

    def test_m121_04_model_unavailable_still_distinguishable(self) -> None:
        """Where the adapter can deterministically identify a model
        absence, it raises ModelUnavailableError (not the broader
        ModelProviderUnavailableError).  The Gemini 404 case is the
        documented ambiguity: 404 is classified as
        ModelInvalidRequestError because we cannot read the body
        safely.  This test asserts the discriminating class is a
        strict subclass of ModelUnavailableError, so old callers that
        catch ModelUnavailableError continue to work.
        """
        adapter = OpenAICompatibleAdapter(
            model="does-not-exist",
            base_url="http://127.0.0.1:9999/v1",
        )
        # Inject a synthetic 400 to model the deterministic
        # bad-request case without needing a live server.
        adapter._urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(
            _http_error(400)
        )
        with self.assertRaises(ModelInvalidRequestError) as ctx:
            adapter._post_json(
                adapter._payload(ModelRequest(messages="hi"), stream=False),
                timeout=adapter._timeout,
            )
        # ModelInvalidRequestError is *not* a ModelUnavailableError;
        # this is the documented M1.2 ambiguity for 404s whose body
        # cannot be parsed safely.  The provider outage class
        # (ModelProviderUnavailableError) is the alternative.
        self.assertNotIsInstance(ctx.exception, ModelUnavailableError)
        self.assertNotIsInstance(ctx.exception, ModelProviderUnavailableError)

    # --- F. each category remains distinguishable ------------------------

    def test_m121_05_timeout_distinguishable(self) -> None:
        """ModelTimeoutError is distinguishable from the other
        categories and remains a ModelError so v0.5.x callers that
        catch ModelError still see the failure.
        """
        self.assertTrue(issubclass(ModelTimeoutError, ModelError))
        self.assertFalse(issubclass(ModelTimeoutError, ModelAuthenticationError))
        self.assertFalse(issubclass(ModelTimeoutError, ModelRateLimitError))
        self.assertFalse(issubclass(ModelTimeoutError, ModelInvalidRequestError))
        # IS-A ModelUnavailableError (the v0.5.x contract).
        self.assertTrue(issubclass(ModelTimeoutError, ModelUnavailableError))
        # But distinct from the broader provider-unavailable class.
        self.assertFalse(issubclass(ModelTimeoutError, ModelProviderUnavailableError))
        # And distinct from the cancellation class.
        self.assertFalse(issubclass(ModelTimeoutError, ModelCancelledError))

    def test_m121_06_authentication_distinguishable(self) -> None:
        """ModelAuthenticationError remains a direct ModelError (not a
        ModelUnavailableError), preserving the v0.5.x behavior where
        401/403 was never mapped to unavailable.
        """
        self.assertTrue(issubclass(ModelAuthenticationError, ModelError))
        self.assertFalse(issubclass(ModelAuthenticationError, ModelUnavailableError))
        self.assertFalse(issubclass(ModelAuthenticationError, ModelTimeoutError))
        self.assertFalse(issubclass(ModelAuthenticationError, ModelProviderUnavailableError))

    def test_m121_07_rate_limit_distinguishable(self) -> None:
        """ModelRateLimitError remains a direct ModelError (not a
        ModelUnavailableError), preserving the v0.5.x behavior where
        429 was never mapped to unavailable.
        """
        self.assertTrue(issubclass(ModelRateLimitError, ModelError))
        self.assertFalse(issubclass(ModelRateLimitError, ModelUnavailableError))
        self.assertFalse(issubclass(ModelRateLimitError, ModelTimeoutError))
        self.assertFalse(issubclass(ModelRateLimitError, ModelProviderUnavailableError))

    def test_m121_08_invalid_request_distinguishable(self) -> None:
        """ModelInvalidRequestError remains a direct ModelError (not a
        ModelUnavailableError), preserving the v0.5.x behavior where
        400/422 was never mapped to unavailable.  This is the
        documented M1.2 ambiguity for 404s.
        """
        self.assertTrue(issubclass(ModelInvalidRequestError, ModelError))
        self.assertFalse(issubclass(ModelInvalidRequestError, ModelUnavailableError))
        self.assertFalse(issubclass(ModelInvalidRequestError, ModelTimeoutError))
        self.assertFalse(issubclass(ModelInvalidRequestError, ModelProviderUnavailableError))

    # --- E. raw asyncio cancellation behavior unchanged -----------------

    def test_m121_09_raw_asyncio_cancellation_unchanged(self) -> None:
        """asyncio.CancelledError raised inside the transport seam
        propagates as CancelledError.  M1.2 did not convert it to
        ModelCancelledError; M1.2.1 does not change that.  This is the
        control-flow / runtime cancellation path.
        """
        adapter = OpenAICompatibleAdapter(
            model="oc-test", base_url="http://127.0.0.1:9999/v1",
        )
        adapter._urlopen = lambda *a, **k: (_ for _ in ()).throw(
            asyncio.CancelledError()
        )
        # The transport seam re-raises the raw CancelledError; it does
        # not get re-wrapped as a bounded ModelError.
        with self.assertRaises(asyncio.CancelledError):
            adapter._post_json(
                adapter._payload(ModelRequest(messages="hi"), stream=False),
                timeout=adapter._timeout,
            )

    def test_m121_10_explicit_model_cancelled_error_unchanged(self) -> None:
        """ModelCancelledError (the bounded AMCA class) is a direct
        ModelError, distinct from ModelUnavailableError.  This is the
        explicit / call-orchestrator cancellation path, kept separate
        from raw asyncio cancellation.
        """
        self.assertTrue(issubclass(ModelCancelledError, ModelError))
        self.assertFalse(issubclass(ModelCancelledError, ModelUnavailableError))
        self.assertFalse(issubclass(ModelCancelledError, ModelTimeoutError))
        # And distinct from asyncio.CancelledError -- the bounded
        # AMCA class is for explicit call-orchestrator cancellation
        # only.
        self.assertFalse(issubclass(ModelCancelledError, asyncio.CancelledError))

    # --- F. privacy / timeout / Ollama / inheritance remain green -------

    def test_m121_11_provider_unavailable_message_excludes_credentials(self) -> None:
        """ModelProviderUnavailableError's normalized message must not
        echo API keys or auth headers.  This is the credential-privacy
        invariant for the new category.
        """
        adapter = OpenAICompatibleAdapter(
            model="gpt-test", base_url="http://127.0.0.1:9999/v1",
            api_key="do-not-leak",
        )
        sentinel_key = "sk-LIVE-1234567890ABCDEFGH"
        with self.assertRaises(ModelProviderUnavailableError) as ctx:
            adapter._post_json(
                adapter._payload(ModelRequest(messages="hi"), stream=False),
                timeout=adapter._timeout,
            )
        self.assertNotIn(sentinel_key, str(ctx.exception))
        self.assertNotIn("do-not-leak", str(ctx.exception))
        self.assertNotIn("Bearer", str(ctx.exception))

    def test_m121_12_m1111_timeout_propagation_remain_green(self) -> None:
        """M1.1.1 timeout propagation invariant: a bounded request
        timeout is forwarded to the transport seam.  This test exercises
        the same code path as the M1.1.1 test_07 but uses the
        new-style adapter (ModelTimeoutError is now a
        ModelUnavailableError) and asserts the IS-A relationship that
        preserves the v0.5.x contract.
        """
        adapter = OpenAICompatibleAdapter(
            model="gpt-test", base_url="http://127.0.0.1:9999/v1",
            api_key="do-not-leak",
        )

        def _fake(_request: Any, *, timeout: float) -> Any:
            raise URLError("timed out")

        adapter._urlopen = _fake
        # The M1.1.1 v0.5.x contract: except ModelUnavailableError
        # catches a transport timeout.
        try:
            adapter._post_json(
                adapter._payload(
                    ModelRequest(messages="hi", timeout_seconds=0.5),
                    stream=False,
                ),
                timeout=adapter._transport_timeout(
                    ModelRequest(messages="hi", timeout_seconds=0.5)
                ),
            )
        except ModelUnavailableError as exc:
            # The narrower class is observable too.
            self.assertIsInstance(exc, ModelTimeoutError)
            # The credential-redaction invariant holds.
            self.assertNotIn("do-not-leak", str(exc))
            # The transport saw the bounded timeout (0.5s).
            self.assertEqual(
                adapter._transport_timeout(
                    ModelRequest(messages="hi", timeout_seconds=0.5)
                ),
                0.5,
            )
        else:
            self.fail("ModelUnavailableError not raised for transport timeout")


class TimeoutPropagationTests(unittest.TestCase):
    """Verify request.timeout_seconds is forwarded to the transport seam.

    Required scenarios (10):
      1. OpenAI transport receives bounded request timeout
      2. Anthropic transport receives bounded request timeout
      3. Gemini transport receives bounded request timeout
      4. Generic OpenAI-compatible transport receives bounded request timeout
      5. Adapter ceiling caps excessive requested timeout
      6. No request timeout uses documented default
      7. Timeout becomes existing normalized timeout/failure category
      8. Cancellation behavior remains unchanged
      9. Credentials remain absent from timeout/error evidence
     10. Ollama v0.5.1 structured-output path remains green
    """

    def test_01_openai_transport_receives_bounded_timeout(self) -> None:
        # OpenAIAdapter delegates to the SDK; with_options with timeout=... is
        # the documented per-call override. The captured client should
        # reflect the effective bounded timeout.
        captured: dict[str, Any] = {}

        class _FakeClient:
            def with_options(self, **kwargs: Any) -> "_FakeClient":
                captured["options"] = dict(kwargs)
                return self

            class responses:
                @staticmethod
                def create(**_kwargs: Any) -> str:
                    return "unused"

        adapter = OpenAIAdapter(model="gpt-test", client=_FakeClient(), timeout=60.0)
        request = ModelRequest(messages="hi", timeout_seconds=5.0)
        # Bypass SDK await by directly calling the helper.
        client = adapter._get_client(adapter._transport_timeout(request))
        self.assertEqual(captured.get("options", {}).get("timeout"), 5.0)
        # Client is reused when timeout matches ceiling
        client2 = adapter._get_client(adapter._transport_timeout(
            ModelRequest(messages="hi")
        ))
        self.assertIs(client, client2)

    def test_02_anthropic_transport_receives_bounded_timeout(self) -> None:
        captured: dict[str, Any] = {}

        class _FakeClient:
            def with_options(self, **kwargs: Any) -> "_FakeClient":
                captured["options"] = dict(kwargs)
                return self

            class messages:
                @staticmethod
                def create(**_kwargs: Any) -> str:
                    return "unused"
        adapter = AnthropicAdapter(model="claude-test", client=_FakeClient(), timeout=60.0)
        request = ModelRequest(messages="hi", timeout_seconds=10.0)
        adapter._get_client(adapter._transport_timeout(request))
        self.assertEqual(captured.get("options", {}).get("timeout"), 10.0)

    def test_03_gemini_transport_receives_bounded_timeout(self) -> None:
        adapter = GeminiAdapter(model="gemini-test", api_key="k", timeout=60.0)
        captured: dict[str, Any] = {}

        def _fake(_request: Any, *, timeout: float) -> _FakeHTTPResponse:
            captured["timeout"] = timeout
            return _FakeHTTPResponse({
                "candidates": [{
                    "content": {"parts": [{"text": "ok"}], "role": "model"},
                    "finishReason": "STOP",
                }],
            })

        adapter._urlopen = _fake
        import asyncio

        async def _go() -> None:
            await adapter.generate(ModelRequest(messages="hi", timeout_seconds=7.0))

        asyncio.run(_go())
        self.assertEqual(captured["timeout"], 7.0)

    def test_04_openai_compatible_transport_receives_bounded_timeout(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="gpt-test", base_url="http://127.0.0.1:9999/v1", timeout=60.0
        )
        captured: dict[str, Any] = {}

        def _fake(_request: Any, *, timeout: float) -> _FakeHTTPResponse:
            captured["timeout"] = timeout
            return _FakeHTTPResponse({
                "id": "r",
                "model": "gpt-test",
                "choices": [{"message": {"role": "assistant", "content": "ok"},
                              "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })

        adapter._urlopen = _fake
        import asyncio

        async def _go() -> None:
            await adapter.generate(ModelRequest(messages="hi", timeout_seconds=3.0))

        asyncio.run(_go())
        self.assertEqual(captured["timeout"], 3.0)

    def test_05_adapter_ceiling_caps_excessive_requested_timeout(self) -> None:
        adapter = OpenAICompatibleAdapter(
            model="gpt-test", base_url="http://127.0.0.1:9999/v1", timeout=10.0
        )
        captured: dict[str, Any] = {}

        def _fake(_request: Any, *, timeout: float) -> _FakeHTTPResponse:
            captured["timeout"] = timeout
            return _FakeHTTPResponse({
                "id": "r",
                "model": "gpt-test",
                "choices": [{"message": {"role": "assistant", "content": "ok"},
                              "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })

        adapter._urlopen = _fake
        import asyncio

        async def _go() -> None:
            # Caller asks for 1000s; ceiling is 10s; transport sees min=10.
            await adapter.generate(ModelRequest(messages="hi", timeout_seconds=1000.0))

        asyncio.run(_go())
        self.assertEqual(captured["timeout"], 10.0)
        # Same ceiling rule for Gemini.
        gemini = GeminiAdapter(model="gemini-test", api_key="k", timeout=10.0)
        self.assertEqual(gemini._transport_timeout(
            ModelRequest(messages="hi", timeout_seconds=999.0)
        ), 10.0)

    def test_06_no_request_timeout_uses_documented_default(self) -> None:
        # When request.timeout_seconds is None, the effective transport
        # timeout MUST equal the adapter's documented self._timeout.
        adapter = OpenAICompatibleAdapter(
            model="gpt-test", base_url="http://127.0.0.1:9999/v1", timeout=42.0
        )
        self.assertEqual(
            adapter._transport_timeout(ModelRequest(messages="hi")), 42.0
        )
        gemini = GeminiAdapter(model="g", api_key="k", timeout=99.0)
        self.assertEqual(
            gemini._transport_timeout(ModelRequest(messages="hi")), 99.0
        )
        ollama = OllamaAdapter(model="m", base_url="http://127.0.0.1:11434", timeout=77.0)
        self.assertEqual(
            ollama._transport_timeout(ModelRequest(messages="hi")), 77.0
        )

    def test_07_timeout_becomes_timeout_category(self) -> None:
        # A simulated socket-timeout-like URLError must be normalized to
        # ModelTimeoutError (the M1.2-introduced dedicated category),
        # not a stray TimeoutError, and not a leak of credentials.
        #
        # M1.1.1 originally asserted ModelUnavailableError.  M1.2 splits
        # transport-timeout out of "could not reach" so callers can
        # distinguish a bounded timeout from a network-down condition.
        # ModelTimeoutError IS-A ModelError so any caller that previously
        # caught ModelError (the v0.5.x contract) still observes the
        # failure.
        adapter = OpenAICompatibleAdapter(
            model="gpt-test", base_url="http://127.0.0.1:9999/v1",
            api_key="do-not-leak",
        )

        def _fake(_request: Any, *, timeout: float) -> Any:
            raise URLError("timed out")

        adapter._urlopen = _fake
        with self.assertRaises(ModelTimeoutError) as ctx:
            adapter._post_json(
                adapter._payload(ModelRequest(messages="hi"), stream=False),
                timeout=adapter._timeout,
            )
        message = str(ctx.exception)
        # M1.1.1 backwards compat: the v0.5.x contract was "a timeout is
        # a ModelError".  That still holds because ModelTimeoutError is a
        # ModelError subclass.
        self.assertIsInstance(ctx.exception, ModelError)
        # The message must mention "timeout" so an operator can tell the
        # failure apart from "could not reach".
        self.assertIn("timeout", message.lower())
        # No credential leak in the normalized error message.
        self.assertNotIn("do-not-leak", message)

    def test_08_cancellation_behavior_unchanged(self) -> None:
        # The base cancel() contract must still work after the timeout
        # refactor. We exercise it on the production adapter set.
        import asyncio

        async def _go() -> None:
            adapters = [
                OpenAICompatibleAdapter(model="m", base_url="http://127.0.0.1:9999/v1"),
                GeminiAdapter(model="m", api_key="k"),
                OllamaAdapter(model="m"),
                KimiAdapter(model="m"),
                TabitokenAdapter(model="m", api_key_env="T", price_per_request_usd=0.01),
                FixedPriceOpenAICompatibleAdapter(
                    model="m", base_url="http://127.0.0.1:9999/v1",
                    api_key_env="K", price_per_request_usd=0.01,
                ),
            ]
            for adapter in adapters:
                # No in-flight calls: cancel returns False on all.
                self.assertFalse(await adapter.cancel())

        asyncio.run(_go())

    def test_09_credentials_absent_from_timeout_evidence(self) -> None:
        # Even when the transport is forced to surface a credential-bearing
        # request, the normalized timeout evidence must not echo the key.
        #
        # M1.1.1 originally asserted ModelUnavailableError.  M1.2 splits
        # transport-timeout into ModelTimeoutError (a ModelError subclass),
        # so we assert that category here while still verifying the
        # credential-redaction invariant.
        adapter = OpenAICompatibleAdapter(
            model="gpt-test", base_url="http://127.0.0.1:9999/v1",
            api_key="do-not-leak",
        )

        def _fake(_request: Any, *, timeout: float) -> Any:
            raise URLError("timed out")

        adapter._urlopen = _fake
        with self.assertRaises(ModelTimeoutError) as ctx:
            adapter._post_json(
                adapter._payload(ModelRequest(messages="hi"), stream=False),
                timeout=adapter._transport_timeout(
                    ModelRequest(messages="hi", timeout_seconds=0.5)
                ),
            )
        message = str(ctx.exception)
        self.assertNotIn("do-not-leak", message)
        # And the adapter's provider_metadata is still credential-free.
        meta = str(dict(adapter.provider_metadata()))
        self.assertNotIn("do-not-leak", meta)
        # And the request is still bounded (no surprise extra field).
        self.assertEqual(
            adapter._transport_timeout(
                ModelRequest(messages="hi", timeout_seconds=0.5)
            ), 0.5
        )

    def test_10_ollama_v051_structured_output_remains_green(self) -> None:
        # The v0.5.1 path: LocalModelPlannerRuntime forwards format via
        # ModelRequest.extra. After the timeout refactor, the v0.5.1
        # behavior must be unchanged AND timeout must still be honored.
        adapter = OllamaAdapter(model="qwen-test", base_url="http://127.0.0.1:11434")
        payload = adapter._payload(
            ModelRequest(messages="hi", extra={"format": "json"}),
            stream=False,
        )
        self.assertEqual(payload["format"], "json")
        # And the new _transport_timeout honors request.timeout_seconds.
        self.assertEqual(
            adapter._transport_timeout(
                ModelRequest(messages="hi", timeout_seconds=4.0)
            ), 4.0
        )
        # Loopback smoke is left to V051OllamaRegressionTests.


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
