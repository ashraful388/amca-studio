"""Offline tests for the provider-neutral model layer."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from types import SimpleNamespace
from urllib.request import Request
from unittest.mock import patch

from amca.credentials import MemoryCredentialStore, ProviderCredentialResolver

from amca.models import (
    AnthropicAdapter,
    DuplicateAdapterError,
    FixedPriceOpenAICompatibleAdapter,
    GeminiAdapter,
    KimiAdapter,
    LlamaCppAdapter,
    ModelCapabilities,
    ModelConfigurationError,
    ModelRegistry,
    ModelRequest,
    ModelResponse,
    OllamaAdapter,
    OpenAIAdapter,
    OpenAICompatibleAdapter,
    ScriptedAdapter,
    SGLangAdapter,
    TokenUsage,
    TabitokenAdapter,
    UnknownAdapterError,
    VLLMAdapter,
)
from amca.models.openai_compatible import (
    _RejectRedirects,
    _urlopen_without_redirects,
)


async def collect_stream(adapter, request):
    return [chunk async for chunk in adapter.stream(request)]


class FakeHTTPResponse:
    def __init__(self, *, body=None, lines=()):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self._lines = tuple(
            line if isinstance(line, bytes) else line.encode() for line in lines
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body

    def __iter__(self):
        return iter(self._lines)


class FakeUrlopen:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        return self.responses.pop(0)


class FakeOpenAIStream:
    def __init__(self, final):
        self.final = final
        self.events = [
            SimpleNamespace(type="response.output_text.delta", delta="hel"),
            SimpleNamespace(type="response.output_text.delta", delta="lo"),
            SimpleNamespace(type="response.completed", response=final),
        ]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def __aiter__(self):
        self._iterator = iter(self.events)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration

    async def get_final_response(self):
        return self.final

    async def close(self):
        return None


class HangingCloseOpenAIStream(FakeOpenAIStream):
    def __init__(self, final):
        super().__init__(final)
        self.close_started = False

    async def close(self):
        self.close_started = True
        await asyncio.Event().wait()


class FakeOpenAIResponses:
    def __init__(self, response):
        self.response = response
        self.create_kwargs = None
        self.stream_kwargs = None

    async def create(self, **kwargs):
        if kwargs.get("stream"):
            self.stream_kwargs = kwargs
            return FakeOpenAIStream(self.response)
        self.create_kwargs = kwargs
        return self.response

    def stream(self, **kwargs):
        raise AssertionError("adapter must use the documented responses.create stream")


class FakeAnthropicStream:
    def __init__(self, message):
        self.message = message
        self.text_stream = self._text_stream()

    async def _text_stream(self):
        for delta in ("bon", "jour"):
            yield delta

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get_final_message(self):
        return self.message


class FakeAnthropicMessages:
    def __init__(self, message):
        self.message = message
        self.create_kwargs = None
        self.stream_kwargs = None

    async def create(self, **kwargs):
        self.create_kwargs = kwargs
        return self.message

    def stream(self, **kwargs):
        self.stream_kwargs = kwargs
        return FakeAnthropicStream(self.message)


class ContractTests(unittest.TestCase):
    def test_model_prices_must_be_finite(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleAdapter(
                model="m",
                base_url="http://127.0.0.1:8000/v1",
                input_cost_per_million=float("nan"),
            )

    def test_request_normalizes_and_freezes_input(self):
        original = {"role": "user", "content": "hello"}
        request = ModelRequest(
            messages=[original],
            tools=[{"type": "function", "name": "lookup"}],
            metadata={"trace": "safe"},
        )
        original["content"] = "changed"
        self.assertEqual(request.messages[0]["content"], "hello")
        with self.assertRaises(TypeError):
            request.messages[0]["content"] = "mutate"
        self.assertEqual(ModelRequest.from_prompt("prompt").messages[0]["role"], "user")

    def test_request_validation_and_usage(self):
        with self.assertRaises(ValueError):
            ModelRequest(messages=[])
        with self.assertRaises(ValueError):
            ModelRequest(messages=[{"role": "user"}])
        with self.assertRaises(ValueError):
            ModelRequest.from_prompt("x", max_output_tokens=0)
        with self.assertRaises(ValueError):
            ModelRequest.from_prompt("x", timeout_seconds=float("nan"))
        with self.assertRaises(ValueError):
            ModelRequest.from_prompt("x", timeout_seconds=0)
        with self.assertRaises(ValueError):
            TokenUsage(input_tokens=-1)
        usage = TokenUsage(3, 4, 0.01) + TokenUsage(2, 1, 0.02)
        self.assertEqual((usage.input_tokens, usage.output_tokens, usage.total_tokens), (5, 5, 10))
        self.assertAlmostEqual(usage.cost_usd, 0.03)

    def test_registry_enforces_unique_adapter_ids(self):
        first = ScriptedAdapter(["a"], adapter_id="one")
        registry = ModelRegistry([first])
        self.assertIs(registry.require("one"), first)
        with self.assertRaises(DuplicateAdapterError):
            registry.register(ScriptedAdapter(["b"], adapter_id="one"))
        with self.assertRaises(UnknownAdapterError):
            registry.require("missing")
        self.assertIs(registry.unregister("one"), first)

    def test_scripted_capabilities_can_model_local_compute(self):
        adapter = ScriptedAdapter(["a"], is_local=True)
        self.assertTrue(adapter.capabilities().is_local)

    def test_metadata_never_exposes_credentials(self):
        secret = "do-not-leak"
        adapter = OpenAIAdapter(model="gpt-test", api_key=secret)
        self.assertNotIn(secret, repr(adapter))
        self.assertNotIn(secret, repr(dict(adapter.provider_metadata())))

    def test_cloud_credentials_are_not_read_during_construction(self):
        with patch.dict(os.environ, {}, clear=True):
            openai_adapter = OpenAIAdapter(model="gpt-test")
            anthropic_adapter = AnthropicAdapter(model="claude-test")
            gemini_adapter = GeminiAdapter(model="gemini-test")
            kimi_adapter = KimiAdapter(model="kimi-test")
            self.assertIsNone(openai_adapter._client)
            self.assertIsNone(anthropic_adapter._client)
            self.assertEqual(gemini_adapter.adapter_id, "gemini:gemini-test")
            self.assertEqual(kimi_adapter.adapter_id, "kimi:kimi-test")


class ScriptedAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_and_stream_are_deterministic(self):
        adapter = ScriptedAdapter(
            ["first", ("sec", "ond")], chunk_size=2, adapter_id="fixture"
        )
        request = ModelRequest.from_prompt("question")
        response = await adapter.generate(request)
        self.assertEqual(response.text, "first")
        self.assertEqual(response.finish_reason, "stop")
        chunks = await collect_stream(adapter, request)
        self.assertEqual([chunk.delta for chunk in chunks[:-1]], ["sec", "ond"])
        self.assertTrue(chunks[-1].done)
        self.assertEqual(chunks[-1].response.text, "second")
        self.assertEqual(adapter.calls_consumed, 2)

    async def test_cancel_by_request_id(self):
        adapter = ScriptedAdapter(["late"], delay_seconds=10)
        task = asyncio.create_task(
            adapter.generate(ModelRequest.from_prompt("q", request_id="call-1"))
        )
        await asyncio.sleep(0)
        self.assertTrue(await adapter.cancel("call-1"))
        with self.assertRaises(asyncio.CancelledError):
            await task


class OfficialSDKAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.openai_response = SimpleNamespace(
            id="resp_1",
            model="gpt-test",
            output_text="hello",
            status="completed",
            incomplete_details=None,
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="lookup",
                    arguments='{"q":"x"}',
                    call_id="call_1",
                )
            ],
            usage=SimpleNamespace(input_tokens=7, output_tokens=2),
        )
        self.openai_responses = FakeOpenAIResponses(self.openai_response)
        self.openai_client = SimpleNamespace(responses=self.openai_responses)

        self.anthropic_message = SimpleNamespace(
            id="msg_1",
            model="claude-test",
            content=[
                SimpleNamespace(type="text", text="bonjour"),
                SimpleNamespace(type="tool_use", id="tool_1", name="lookup"),
            ],
            usage=SimpleNamespace(input_tokens=5, output_tokens=3),
            stop_reason="end_turn",
        )
        self.anthropic_messages = FakeAnthropicMessages(self.anthropic_message)
        self.anthropic_client = SimpleNamespace(messages=self.anthropic_messages)

    async def test_openai_responses_generate_maps_contract(self):
        adapter = OpenAIAdapter(
            model="gpt-test",
            client=self.openai_client,
            input_cost_per_million=1.0,
            output_cost_per_million=2.0,
        )
        request = ModelRequest.from_prompt(
            "hello?",
            system="be concise",
            max_output_tokens=42,
            tools=[{"type": "function", "name": "lookup"}],
            metadata={"task": "offline"},
        )
        response = await adapter.generate(request)
        self.assertEqual(response.text, "hello")
        self.assertEqual((response.input_tokens, response.output_tokens), (7, 2))
        self.assertEqual(response.tool_calls[0]["name"], "lookup")
        payload = self.openai_responses.create_kwargs
        self.assertEqual(payload["instructions"], "be concise")
        self.assertEqual(payload["max_output_tokens"], 42)
        self.assertEqual(payload["input"][0]["content"], "hello?")

    async def test_openai_responses_stream_maps_deltas_and_terminal_usage(self):
        adapter = OpenAIAdapter(model="gpt-test", client=self.openai_client)
        chunks = await collect_stream(adapter, ModelRequest.from_prompt("go"))
        self.assertEqual([chunk.delta for chunk in chunks[:-1]], ["hel", "lo"])
        self.assertTrue(chunks[-1].done)
        self.assertEqual(chunks[-1].response.text, "hello")
        self.assertEqual(chunks[-1].usage.total_tokens, 9)
        self.assertTrue(self.openai_responses.stream_kwargs["stream"])

    async def test_openai_stream_close_is_bounded(self):
        hanging = HangingCloseOpenAIStream(self.openai_response)
        responses = FakeOpenAIResponses(self.openai_response)

        async def create(**kwargs):
            responses.stream_kwargs = kwargs
            return hanging

        responses.create = create
        adapter = OpenAIAdapter(
            model="gpt-test",
            client=SimpleNamespace(responses=responses),
            stream_close_timeout=0.01,
        )
        stream = adapter.stream(ModelRequest.from_prompt("go"))
        first = await anext(stream)
        self.assertEqual(first.delta, "hel")
        await asyncio.wait_for(stream.aclose(), timeout=0.2)
        self.assertTrue(hanging.close_started)

    def test_openai_disables_sdk_retries_by_default(self):
        factory = unittest.mock.Mock(return_value=self.openai_client)
        fake_module = SimpleNamespace(AsyncOpenAI=factory)
        with patch.dict(sys.modules, {"openai": fake_module}):
            adapter = OpenAIAdapter(model="gpt-test", api_key="runtime-secret")
            self.assertIs(adapter._get_client(), self.openai_client)
        self.assertEqual(factory.call_args.kwargs["max_retries"], 0)

    def test_openai_requires_finite_positive_stream_close_timeout(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                OpenAIAdapter(
                    model="gpt-test",
                    client=self.openai_client,
                    stream_close_timeout=value,
                )

    async def test_anthropic_messages_generate_maps_contract(self):
        adapter = AnthropicAdapter(model="claude-test", client=self.anthropic_client)
        request = ModelRequest.from_prompt(
            "salut?", system="reply", max_output_tokens=55, stop=["END"]
        )
        response = await adapter.generate(request)
        self.assertEqual(response.text, "bonjour")
        self.assertEqual(response.finish_reason, "end_turn")
        self.assertEqual(response.tool_calls[0]["name"], "lookup")
        payload = self.anthropic_messages.create_kwargs
        self.assertEqual(payload["system"], "reply")
        self.assertEqual(payload["max_tokens"], 55)
        self.assertEqual(payload["stop_sequences"], ["END"])

    async def test_anthropic_messages_stream(self):
        adapter = AnthropicAdapter(model="claude-test", client=self.anthropic_client)
        chunks = await collect_stream(adapter, ModelRequest.from_prompt("go"))
        self.assertEqual([chunk.delta for chunk in chunks[:-1]], ["bon", "jour"])
        self.assertEqual(chunks[-1].response.text, "bonjour")
        self.assertEqual(chunks[-1].usage.total_tokens, 8)

    async def test_anthropic_lifts_system_role_to_top_level(self):
        adapter = AnthropicAdapter(model="claude-test", client=self.anthropic_client)
        request = ModelRequest(
            messages=[
                {"role": "system", "content": "first rule"},
                {"role": "user", "content": "go"},
            ],
            system="second rule",
        )
        await adapter.generate(request)
        payload = self.anthropic_messages.create_kwargs
        self.assertEqual(payload["system"], "second rule\n\nfirst rule")
        self.assertEqual(payload["messages"], [{"role": "user", "content": "go"}])

    def test_anthropic_disables_sdk_retries_by_default(self):
        factory = unittest.mock.Mock(return_value=self.anthropic_client)
        fake_module = SimpleNamespace(AsyncAnthropic=factory)
        with patch.dict(sys.modules, {"anthropic": fake_module}):
            adapter = AnthropicAdapter(model="claude-test", api_key="runtime-secret")
            self.assertIs(adapter._get_client(), self.anthropic_client)
        self.assertEqual(factory.call_args.kwargs["max_retries"], 0)

    def test_anthropic_omits_temperature_for_opus_47_and_later(self):
        adapter = AnthropicAdapter(model="claude-opus-4-8", client=self.anthropic_client)
        payload = adapter._payload(ModelRequest.from_prompt("go", temperature=0.2))
        self.assertNotIn("temperature", payload)

        older = AnthropicAdapter(model="claude-opus-4-6", client=self.anthropic_client)
        older_payload = older._payload(
            ModelRequest.from_prompt("go", temperature=0.2)
        )
        self.assertEqual(older_payload["temperature"], 0.2)

    async def test_missing_cloud_keys_fail_only_when_called(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ModelConfigurationError):
                await OpenAIAdapter(model="gpt-test").generate(
                    ModelRequest.from_prompt("x")
                )
            with self.assertRaises(ModelConfigurationError):
                await AnthropicAdapter(model="claude-test").generate(
                    ModelRequest.from_prompt("x")
                )


class HTTPAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_tabitoken_uses_fixed_request_price_and_explicit_headers(self):
        adapter = TabitokenAdapter(
            model="claude-opus-4-8",
            api_key_env="TABITOKEN_TEST_KEY",
            price_per_request_usd=0.5,
            urlopen_fn=FakeUrlopen(),
        )
        self.assertEqual(adapter.estimate_cost(1, 1), 0.5)
        self.assertEqual(adapter.estimate_cost(10_000, 128), 0.5)
        self.assertEqual(adapter.provider_metadata()["pricing_basis"], "fixed_per_request")
        with patch.dict(os.environ, {"TABITOKEN_TEST_KEY": "runtime-secret"}):
            request = adapter._http_request(
                adapter._payload(
                    ModelRequest.from_prompt("hello", max_output_tokens=128),
                    stream=True,
                )
            )
        self.assertEqual(request.full_url, "https://tabitoken.com/v1/chat/completions")
        self.assertEqual(request.get_header("User-agent"), "AMCA/0.1")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("Authorization"), "Bearer runtime-secret")

    def test_tabitoken_resolves_its_model_scoped_saved_credential(self):
        store = MemoryCredentialStore()
        store.set("tabitoken.claude-opus-4-8", "model-scoped-canary")
        captured = {}

        def transport(request, timeout):
            captured["authorization"] = request.headers.get("Authorization")
            return FakeHTTPResponse(
                body={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {},
                }
            )

        adapter = TabitokenAdapter(
            model="claude-opus-4-8",
            api_key_env="TABITOKEN_OPUS_4_8_API_KEY",
            price_per_request_usd=0.5,
            credential_resolver=ProviderCredentialResolver(store),
            urlopen_fn=transport,
        )
        asyncio.run(adapter.generate(ModelRequest.from_prompt("hello")))
        self.assertEqual(captured["authorization"], "Bearer model-scoped-canary")

    def test_custom_credential_name_is_used_by_cloud_adapters(self):
        store = MemoryCredentialStore()
        store.set("custom.profile123", "custom-profile-canary")
        resolver = ProviderCredentialResolver(store)
        kimi = KimiAdapter(
            model="kimi-test",
            credential_resolver=resolver,
            credential_provider="custom.profile123",
        )
        self.assertEqual(kimi._resolve_api_key(), "custom-profile-canary")

        client = object()
        factory = unittest.mock.Mock(return_value=client)
        with patch.dict(sys.modules, {"anthropic": SimpleNamespace(AsyncAnthropic=factory)}):
            anthropic = AnthropicAdapter(
                model="claude-test",
                credential_resolver=resolver,
                credential_provider="custom.profile123",
            )
            self.assertIs(anthropic._get_client(), client)
        self.assertEqual(factory.call_args.kwargs["api_key"], "custom-profile-canary")

    def test_fixed_price_compatible_adapter_reserves_exact_request_price(self):
        adapter = FixedPriceOpenAICompatibleAdapter(
            model="gateway-model",
            base_url="https://gateway.example/v1",
            provider="custom-gateway",
            allow_remote=True,
            price_per_request_usd=0.125,
        )
        self.assertEqual(adapter.estimate_cost(1, 1), 0.125)
        self.assertEqual(adapter.estimate_cost(100_000, 8_000), 0.125)
        self.assertEqual(adapter.provider_metadata()["pricing_basis"], "fixed_per_request")

    def test_named_local_backends_use_documented_loopback_defaults(self):
        expected = (
            (LlamaCppAdapter(model="m"), "llama.cpp", "http://127.0.0.1:8080/v1"),
            (VLLMAdapter(model="m"), "vllm", "http://127.0.0.1:8000/v1"),
            (SGLangAdapter(model="m"), "sglang", "http://127.0.0.1:30000/v1"),
        )
        for adapter, provider, base_url in expected:
            with self.subTest(provider=provider):
                self.assertEqual(adapter.provider, provider)
                self.assertTrue(adapter.capabilities().is_local)
                self.assertEqual(adapter.provider_metadata()["base_url"], base_url)

    def test_named_local_backend_requires_explicit_remote_opt_in(self):
        with self.assertRaises(ValueError):
            VLLMAdapter(model="m", base_url="https://inference.example.test/v1")
        remote = VLLMAdapter(
            model="m",
            base_url="https://inference.example.test/v1",
            allow_remote=True,
        )
        self.assertFalse(remote.capabilities().is_local)

    async def test_gemini_generate_maps_documented_rest_contract(self):
        fake = FakeUrlopen(
            FakeHTTPResponse(
                body={
                    "candidates": [{
                        "content": {"parts": [
                            {"text": "gem"},
                            {"functionCall": {"name": "lookup", "args": {"q": "x"}}},
                        ]},
                        "finishReason": "STOP",
                    }],
                    "usageMetadata": {
                        "promptTokenCount": 5,
                        "candidatesTokenCount": 2,
                    },
                    "modelVersion": "gemini-test-001",
                    "responseId": "response-1",
                }
            )
        )
        adapter = GeminiAdapter(
            model="gemini-test", api_key="runtime-secret", urlopen_fn=fake
        )
        response = await adapter.generate(
            ModelRequest.from_prompt(
                "question",
                system="be exact",
                temperature=0.4,
                max_output_tokens=55,
                tools=[{
                    "type": "function",
                    "function": {"name": "lookup", "parameters": {"type": "object"}},
                }],
            )
        )
        self.assertEqual((response.text, response.total_tokens), ("gem", 7))
        self.assertEqual(response.tool_calls[0]["name"], "lookup")
        request, _ = fake.requests[0]
        self.assertEqual(request.get_header("X-goog-api-key"), "runtime-secret")
        self.assertNotIn("runtime-secret", request.full_url)
        self.assertTrue(request.full_url.endswith("/models/gemini-test:generateContent"))
        payload = json.loads(request.data)
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], "be exact")
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 55)
        self.assertEqual(
            payload["tools"][0]["functionDeclarations"][0]["name"], "lookup"
        )

    def test_gemini_maps_bounded_thinking_budget(self):
        adapter = GeminiAdapter(model="gemini-2.5-flash", api_key="secret")
        request = ModelRequest.from_prompt(
            "bounded",
            max_output_tokens=128,
            extra={"thinking_budget": 0},
        )
        payload = adapter._payload(request)
        self.assertEqual(
            payload["generationConfig"]["thinkingConfig"],
            {"thinkingBudget": 0},
        )

    async def test_gemini_stream_maps_sse_and_terminal_usage(self):
        lines = [
            'data: {"candidates":[{"content":{"parts":[{"text":"a"}]}}],"modelVersion":"g"}\n',
            'data: {"candidates":[{"content":{"parts":[{"text":"b"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":2},"modelVersion":"g","responseId":"r"}\n',
        ]
        adapter = GeminiAdapter(
            model="gemini-test",
            api_key="runtime-secret",
            urlopen_fn=FakeUrlopen(FakeHTTPResponse(lines=lines)),
        )
        chunks = await collect_stream(adapter, ModelRequest.from_prompt("go"))
        self.assertEqual([chunk.delta for chunk in chunks[:-1]], ["a", "b"])
        self.assertEqual(chunks[-1].response.text, "ab")
        self.assertEqual(chunks[-1].usage.total_tokens, 5)

    async def test_gemini_requires_key_only_at_call_time(self):
        with patch.dict(os.environ, {}, clear=True):
            adapter = GeminiAdapter(model="gemini-test", urlopen_fn=FakeUrlopen())
            with self.assertRaises(ModelConfigurationError):
                await adapter.generate(ModelRequest.from_prompt("x"))

    async def test_cloud_adapter_can_resolve_key_from_os_store_abstraction(self):
        store = MemoryCredentialStore()
        store.set("gemini", "stored-runtime-secret")
        fake = FakeUrlopen(
            FakeHTTPResponse(
                body={
                    "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                    "usageMetadata": {},
                }
            )
        )
        adapter = GeminiAdapter(
            model="gemini-test",
            credential_resolver=ProviderCredentialResolver(store),
            urlopen_fn=fake,
        )
        with patch.dict(os.environ, {}, clear=True):
            response = await adapter.generate(ModelRequest.from_prompt("x"))
        self.assertEqual(response.text, "ok")
        self.assertEqual(
            fake.requests[0][0].get_header("X-goog-api-key"),
            "stored-runtime-secret",
        )

    def test_gemini_rejects_unapproved_custom_endpoint(self):
        with self.assertRaises(ValueError):
            GeminiAdapter(model="m", base_url="https://example.test/v1beta")

    def test_default_http_transport_installs_redirect_rejection(self):
        opener = SimpleNamespace(open=lambda request, timeout: (request, timeout))
        request = Request("http://127.0.0.1:8000/v1/chat/completions")
        with patch(
            "amca.models.openai_compatible.build_opener", return_value=opener
        ) as build:
            result = _urlopen_without_redirects(request, timeout=4.0)

        handler = build.call_args.args[0]
        self.assertIsInstance(handler, _RejectRedirects)
        self.assertIsNone(
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://example.com/collect",
            )
        )
        self.assertEqual(result, (request, 4.0))

    def test_default_transports_reject_redirects_but_injection_is_preserved(self):
        fake = FakeUrlopen()
        compatible = OpenAICompatibleAdapter(
            model="m", base_url="http://127.0.0.1:8000/v1"
        )
        injected = OpenAICompatibleAdapter(
            model="m", base_url="http://127.0.0.1:8000/v1", urlopen_fn=fake
        )
        ollama = OllamaAdapter(model="m")

        self.assertIs(compatible._urlopen, _urlopen_without_redirects)
        self.assertIs(ollama._urlopen, _urlopen_without_redirects)
        self.assertIs(injected._urlopen, fake)

    def test_remote_ollama_is_not_declared_local(self):
        adapter = OllamaAdapter(
            model="m", base_url="https://ollama.example.test", allow_remote=True
        )
        self.assertFalse(adapter.capabilities().is_local)

    async def test_generic_openai_compatible_generate_and_auth(self):
        fake = FakeUrlopen(
            FakeHTTPResponse(
                body={
                    "id": "chat_1",
                    "model": "local-test",
                    "choices": [
                        {
                            "message": {"content": "answer", "tool_calls": []},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 6, "completion_tokens": 2},
                }
            )
        )
        adapter = OpenAICompatibleAdapter(
            model="local-test",
            base_url="http://127.0.0.1:8000/v1",
            api_key_env="LOCAL_TEST_KEY",
            urlopen_fn=fake,
        )
        with patch.dict(os.environ, {"LOCAL_TEST_KEY": "runtime-secret"}):
            response = await adapter.generate(ModelRequest.from_prompt("question"))
        self.assertEqual(response.text, "answer")
        request, timeout = fake.requests[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer runtime-secret")
        payload = json.loads(request.data)
        self.assertEqual(payload["messages"][0]["content"], "question")
        self.assertFalse(payload["stream"])

    async def test_openai_compatible_stream(self):
        events = [
            'data: {"id":"1","model":"m","choices":[{"delta":{"content":"a"}}]}\n',
            'data: {"id":"1","model":"m","choices":[{"delta":{"content":"b"},"finish_reason":"stop"}]}\n',
            'data: {"id":"1","model":"m","choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n',
            "data: [DONE]\n",
        ]
        fake = FakeUrlopen(FakeHTTPResponse(lines=events))
        adapter = OpenAICompatibleAdapter(
            model="m", base_url="http://localhost:9000/v1", urlopen_fn=fake
        )
        chunks = await collect_stream(adapter, ModelRequest.from_prompt("x"))
        self.assertEqual([chunk.delta for chunk in chunks[:-1]], ["a", "b"])
        self.assertEqual(chunks[-1].response.text, "ab")
        self.assertEqual(chunks[-1].usage.total_tokens, 5)

    def test_local_compatible_rejects_remote_url_by_default(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleAdapter(model="m", base_url="https://example.com/v1")

    async def test_kimi_uses_openai_compatible_protocol(self):
        fake = FakeUrlopen(
            FakeHTTPResponse(
                body={
                    "model": "kimi-test",
                    "choices": [
                        {"message": {"content": "moon"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                }
            )
        )
        adapter = KimiAdapter(model="kimi-test", api_key="secret", urlopen_fn=fake)
        response = await adapter.generate(ModelRequest.from_prompt("x"))
        self.assertEqual((response.provider, response.text), ("kimi", "moon"))
        self.assertTrue(fake.requests[0][0].full_url.endswith("/chat/completions"))

    async def test_kimi_requires_key_at_call_time(self):
        with patch.dict(os.environ, {}, clear=True):
            adapter = KimiAdapter(
                model="kimi-test", urlopen_fn=FakeUrlopen()
            )
            with self.assertRaises(ModelConfigurationError):
                await adapter.generate(ModelRequest.from_prompt("x"))

    def test_kimi_k26_uses_required_temperature(self):
        adapter = KimiAdapter(
            model="kimi-k2.6",
            api_key="secret",
            urlopen_fn=FakeUrlopen(),
        )
        payload = adapter._payload(
            ModelRequest.from_prompt(
                "x", temperature=0.2, max_output_tokens=321
            ),
            stream=False,
        )
        self.assertEqual(payload["temperature"], 1.0)
        self.assertEqual(payload["max_completion_tokens"], 321)
        self.assertNotIn("max_tokens", payload)

        nonthinking = adapter._payload(
            ModelRequest.from_prompt(
                "x",
                temperature=0.2,
                extra={"thinking": {"type": "disabled"}},
            ),
            stream=False,
        )
        self.assertEqual(nonthinking["temperature"], 0.6)

    async def test_ollama_generate_and_stream(self):
        generated = {
            "model": "qwen-test",
            "message": {"role": "assistant", "content": "local"},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 4,
            "eval_count": 2,
        }
        fake_generate = FakeUrlopen(FakeHTTPResponse(body=generated))
        adapter = OllamaAdapter(model="qwen-test", urlopen_fn=fake_generate)
        response = await adapter.generate(
            ModelRequest.from_prompt("hi", timeout_seconds=0.5)
        )
        self.assertEqual(response.text, "local")
        self.assertTrue(adapter.capabilities().is_local)
        self.assertEqual(response.total_tokens, 6)
        self.assertEqual(fake_generate.requests[0][1], 0.5)

        lines = [
            '{"model":"qwen-test","message":{"content":"lo"},"done":false}\n',
            '{"model":"qwen-test","message":{"content":"cal"},"done":false}\n',
            '{"model":"qwen-test","message":{"content":""},"done":true,"done_reason":"stop","prompt_eval_count":4,"eval_count":2}\n',
        ]
        fake_stream = FakeUrlopen(FakeHTTPResponse(lines=lines))
        stream_adapter = OllamaAdapter(model="qwen-test", urlopen_fn=fake_stream)
        chunks = await collect_stream(
            stream_adapter, ModelRequest.from_prompt("hi", timeout_seconds=0.75)
        )
        self.assertEqual([chunk.delta for chunk in chunks[:-1]], ["lo", "cal"])
        self.assertEqual(chunks[-1].response.text, "local")
        self.assertEqual(chunks[-1].usage.total_tokens, 6)
        payload = json.loads(fake_stream.requests[0][0].data)
        self.assertTrue(payload["stream"])
        self.assertEqual(fake_stream.requests[0][1], 0.75)


if __name__ == "__main__":
    unittest.main()
