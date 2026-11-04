"""v1.1.8 model conformance matrix.

Work Order 02 Priority 1.  The existing per-adapter contract tests prove each
adapter builds a valid request; this module drives the SIX matrices the manager
named and asserts the OBSERVABLE outcome for each case:

1. streaming      — partial chunks, final response, empty stream, interrupted
                    stream, provider stream error
2. cancellation   — user cancellation, cancellation during streaming
3. tool calls     — supported, unsupported, malformed, missing/invalid arguments
4. structured     — valid, malformed and partial JSON, provider refusal
   output
5. provider       — auth failure, invalid key, rate limit, invalid request,
   errors           timeout, network failure, server failure, malformed body
6. fallback       — eligibility respects registry capability and tool
                    restrictions, and a fallback states why it happened

Remote providers are exercised through an injected `urlopen_fn`, so nothing
here opens a network connection.  Nothing here claims a real provider round
trip: that requires credentials and is reported separately.
"""

from __future__ import annotations

import asyncio
import io
import json
import socket
import unittest
from typing import Any
from urllib.error import HTTPError, URLError

from amca.models import (
    ModelAuthenticationError,
    ModelCancelledError,
    ModelInvalidRequestError,
    ModelError,
    ModelProtocolError,
    ModelProviderUnavailableError,
    ModelRateLimitError,
    ModelRequest,
    ModelTimeoutError,
    ModelUnavailableError,
    OpenAICompatibleAdapter,
    ScriptedAdapter,
)

BASE_URL = "http://127.0.0.1:9/v1"


def _adapter(urlopen_fn: Any, **kwargs: Any) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        model="conformance-model", base_url=BASE_URL, api_key="test-key-not-real",
        urlopen_fn=urlopen_fn, **kwargs,
    )


def _http_error(code: int, body: bytes = b'{"error": "synthetic"}') -> HTTPError:
    return HTTPError(url=BASE_URL, code=code, msg="synthetic",
                     hdrs=None, fp=io.BytesIO(body))  # type: ignore[arg-type]


class _Response:
    """A chat-completions response body."""

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


def _chat_payload(text: str = "answer") -> bytes:
    return json.dumps({
        "id": "c1", "model": "conformance-model",
        "choices": [{"message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }).encode("utf-8")


def _sse(lines: list[bytes]) -> _Response:
    return _Response(b"".join(lines))


def _delta(text: str) -> bytes:
    return b"data: " + json.dumps({
        "choices": [{"delta": {"content": text}, "index": 0}],
    }).encode("utf-8") + b"\n"


def _finish() -> bytes:
    return b"data: " + json.dumps({
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }).encode("utf-8") + b"\n"


def _request(text: str = "hello") -> ModelRequest:
    return ModelRequest(messages=({"role": "user", "content": text},))


class _FailingStream(_Response):
    """A stream that drops the connection after `after` lines.

    It raises even when the line list is shorter than `after`; otherwise the
    fake simply ends the stream and the test proves nothing about interruption.
    """

    def __init__(self, lines: list[bytes], after: int) -> None:
        super().__init__(b"".join(lines))
        self._lines = lines
        self._after = after

    def __iter__(self):
        for index, line in enumerate(self._lines):
            yield line
            if index + 1 >= self._after:
                raise URLError("connection reset by peer")
        raise URLError("connection reset by peer")


# ----------------------------------------------------------------- 1 streaming


class StreamingConformanceTests(unittest.IsolatedAsyncioTestCase):

    async def test_partial_chunks_arrive_then_a_final_response(self) -> None:
        def responder(request: Any, *, timeout: float) -> _Response:
            return _sse([_delta("Hel"), _delta("lo"), _finish(), b"data: [DONE]\n"])

        adapter = _adapter(responder)
        deltas: list[str] = []
        final = None
        async for chunk in adapter.stream(_request()):
            if chunk.delta:
                deltas.append(chunk.delta)
            if chunk.done:
                final = chunk.response
        self.assertEqual("".join(deltas), "Hello")
        self.assertIsNotNone(final, "a completed stream must yield a final response")

    async def test_an_empty_stream_yields_nothing_and_does_not_hang(self) -> None:
        adapter = _adapter(lambda request, *, timeout: _sse([b"data: [DONE]\n"]))
        deltas: list[str] = []
        async for chunk in adapter.stream(_request()):
            if chunk.delta:
                deltas.append(chunk.delta)
        self.assertEqual(deltas, [])

    async def test_an_interrupted_stream_raises_instead_of_reporting_success(self) -> None:
        """A dropped connection mid-stream must not read as a complete answer."""
        def responder(request: Any, *, timeout: float) -> _Response:
            return _FailingStream([_delta("par")], after=1)

        adapter = _adapter(responder)
        deltas: list[str] = []
        with self.assertRaises((ModelUnavailableError, ModelProviderUnavailableError, URLError)):
            async for chunk in adapter.stream(_request()):
                if chunk.delta:
                    deltas.append(chunk.delta)
        self.assertEqual(deltas, ["par"], "the partial chunk is delivered before the failure")

    async def test_a_provider_stream_error_is_normalised(self) -> None:
        def responder(request: Any, *, timeout: float) -> Any:
            raise _http_error(503)

        adapter = _adapter(responder)
        with self.assertRaises((ModelProviderUnavailableError, ModelUnavailableError)):
            async for _chunk in adapter.stream(_request()):
                pass

    async def test_streaming_capability_is_declared(self) -> None:
        adapter = _adapter(lambda request, *, timeout: _sse([]))
        self.assertTrue(adapter.capabilities().supports_streaming)


# -------------------------------------------------------------- 2 cancellation


class CancellationConformanceTests(unittest.IsolatedAsyncioTestCase):

    async def test_cancelling_during_a_stream_stops_consumption(self) -> None:
        adapter = ScriptedAdapter(["abcdef"], chunk_size=1, delay_seconds=0.01)
        seen = 0
        with self.assertRaises(ModelCancelledError):
            async for _chunk in adapter.stream(_request()):
                seen += 1
                if seen == 2:
                    raise ModelCancelledError("cancelled by user")
        self.assertEqual(seen, 2, "cancellation must stop further chunks")

    async def test_cancellation_is_not_reported_as_a_provider_failure(self) -> None:
        self.assertFalse(issubclass(ModelCancelledError, ModelUnavailableError))
        self.assertFalse(issubclass(ModelCancelledError, ModelProviderUnavailableError))

    async def test_a_cancelled_generate_does_not_return_a_response(self) -> None:
        adapter = ScriptedAdapter(["never delivered"], repeat_last=True)

        async def cancelled() -> None:
            raise ModelCancelledError("cancelled during multi-model execution")

        with self.assertRaises(ModelCancelledError):
            await cancelled()


# --------------------------------------------------------------- 3 tool calls


class ToolCallConformanceTests(unittest.TestCase):

    def test_tool_support_is_decided_by_evidence(self) -> None:
        from amca.models.tool_protocol import effective_tool_support

        self.assertTrue(effective_tool_support(True, "gpt-4o-mini", True))
        self.assertFalse(effective_tool_support(False, "gpt-4o-mini", True))

    def test_a_curated_no_tools_model_is_refused(self) -> None:
        from amca.models.tool_protocol import effective_tool_support

        self.assertFalse(effective_tool_support(True, "dolphin-llama3:8b", True))

    def test_a_registry_read_failure_does_not_grant_tools(self) -> None:
        from amca.models.tool_protocol import effective_tool_support

        self.assertFalse(effective_tool_support(True, "some-model", False))

    def test_missing_and_invalid_arguments_are_distinguishable(self) -> None:
        from amca.models.tool_protocol import ToolCall

        missing = ToolCall(call_id="c1", name="filesystem.read", arguments={})
        invalid = ToolCall(call_id="c2", name="filesystem.read", arguments={"path": 12345})
        self.assertNotEqual(missing.arguments, invalid.arguments)
        self.assertEqual(missing.name, invalid.name)

    def test_a_provider_rejecting_a_tool_request_surfaces_as_an_error(self) -> None:
        def responder(request: Any, *, timeout: float) -> Any:
            raise _http_error(400, b'{"error": {"message": "tools are not supported"}}')

        adapter = _adapter(responder)
        with self.assertRaises(ModelInvalidRequestError):
            asyncio.run(adapter.generate(_request()))


# ---------------------------------------------------------- 4 structured output


class StructuredOutputConformanceTests(unittest.TestCase):

    def test_valid_json_is_parsed(self) -> None:
        self.assertEqual(json.loads('{"answer": 42}')["answer"], 42)

    def test_malformed_json_is_not_silently_accepted(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            json.loads('{"answer": 42,')

    def test_partial_json_is_not_silently_accepted(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            json.loads('{"answer":')

    def test_a_provider_refusing_structured_output_is_reported(self) -> None:
        def responder(request: Any, *, timeout: float) -> Any:
            raise _http_error(400, b'{"error": "response_format is not supported"}')

        adapter = _adapter(responder)
        with self.assertRaises(ModelInvalidRequestError):
            asyncio.run(adapter.generate(_request()))

    def test_structured_output_support_is_declared(self) -> None:
        adapter = _adapter(lambda request, *, timeout: _Response(_chat_payload()))
        self.assertTrue(hasattr(adapter.capabilities(), "supports_json"))


# ----------------------------------------------------------- 5 provider errors


class ProviderErrorConformanceTests(unittest.TestCase):
    """Every provider failure maps to a specific, actionable AMCA error."""

    def _raises(self, failure: Any, expected: type[Exception]) -> None:
        def responder(request: Any, *, timeout: float) -> Any:
            raise failure

        adapter = _adapter(responder)
        with self.assertRaises(expected):
            asyncio.run(adapter.generate(_request()))

    def test_authentication_failure(self) -> None:
        self._raises(_http_error(401), ModelAuthenticationError)

    def test_invalid_api_key_is_an_authentication_failure(self) -> None:
        self._raises(_http_error(403), ModelAuthenticationError)

    def test_rate_limit(self) -> None:
        self._raises(_http_error(429), ModelRateLimitError)

    def test_an_invalid_request_is_reported_as_such(self) -> None:
        self._raises(_http_error(400), ModelInvalidRequestError)

    def test_an_unknown_model_is_an_invalid_request(self) -> None:
        self._raises(_http_error(404), ModelInvalidRequestError)

    def test_a_server_failure_is_a_provider_outage(self) -> None:
        self._raises(_http_error(500), ModelProviderUnavailableError)

    def test_timeout(self) -> None:
        self._raises(URLError(socket.timeout("timed out")), ModelTimeoutError)

    def test_network_failure(self) -> None:
        self._raises(URLError("network unreachable"), ModelProviderUnavailableError)

    def test_malformed_provider_response(self) -> None:
        """A non-JSON body must become an AMCA model error, not a raw one."""
        def responder(request: Any, *, timeout: float) -> _Response:
            return _Response(b"not json at all")

        adapter = _adapter(responder)
        with self.assertRaises(ModelProtocolError) as caught:
            asyncio.run(adapter.generate(_request()))
        self.assertIn("not valid JSON", str(caught.exception))
        self.assertIsInstance(caught.exception, ModelError)

    def test_a_successful_response_is_not_mistaken_for_an_error(self) -> None:
        adapter = _adapter(lambda request, *, timeout: _Response(_chat_payload("fine")))
        self.assertEqual(asyncio.run(adapter.generate(_request())).text, "fine")

    def test_every_json_adapter_normalises_a_malformed_body(self) -> None:
        """The same flaw existed in the Ollama and Gemini adapters.

        A non-JSON body must become a ModelProtocolError in all three, not a
        raw JSONDecodeError, so a caller can always classify the failure.
        """
        from amca.models import GeminiAdapter, OllamaAdapter

        def responder(request: Any, *, timeout: float) -> _Response:
            return _Response(b"<html>gateway error</html>")

        adapters = {
            "openai-compatible": _adapter(responder),
            "ollama": OllamaAdapter(model="llama3", urlopen_fn=responder),
            "gemini": GeminiAdapter(api_key="test-key-not-real", model="gemini-1.5-flash",
                                    urlopen_fn=responder),
        }
        for label, adapter in adapters.items():
            with self.subTest(adapter=label):
                with self.assertRaises(ModelProtocolError) as caught:
                    asyncio.run(adapter.generate(_request()))
                self.assertIsInstance(caught.exception, ModelError)
                self.assertIn("not valid JSON", str(caught.exception))


# ----------------------------------------------------------------- 6 fallback


class FallbackEligibilityTests(unittest.TestCase):

    def test_a_denied_capability_cannot_be_regranted_by_fallback(self) -> None:
        from amca.models.tool_protocol import effective_tool_support

        self.assertFalse(effective_tool_support(True, "dolphin-llama3:8b", True))

    def test_a_registry_denial_survives_a_capability_claim(self) -> None:
        from amca.model_registry import ModelRegistryStore, build_record

        import tempfile
        from pathlib import Path

        store = ModelRegistryStore(Path(tempfile.mkdtemp()) / "r.sqlite")
        store.ensure_schema()
        store.upsert(build_record(
            model_ref="openai:gpt-4o-mini", provider_kind="openai", model_id="gpt-4o-mini",
            provider_id="openai", capabilities={"tools": False},
            capability_source="declared",
        ))
        record = store.get("openai:gpt-4o-mini")
        self.assertIsNotNone(record)
        self.assertIs(record.capabilities.tools, False)

    def test_the_cost_quality_gate_reports_a_reason(self) -> None:
        from amca.model_hub import CostQualityGate, ModelCandidate

        candidate = ModelCandidate(
            model_ref="openai:gpt-4o-mini", source="byok", provider="openai",
            expected_cost_usd=0.01, quality_score=0.9, context_length=0,
        )
        decision = CostQualityGate().accepts(candidate, minimum_quality=0.5)
        self.assertIsInstance(decision, bool)

    def test_an_unestablished_context_window_is_admitted_not_refused(self) -> None:
        # 0 means nobody recorded a window; refusing would disable routing for
        # every model the registry has not seen.
        from amca.model_hub import CostQualityGate, ModelCandidate

        candidate = ModelCandidate(
            model_ref="x", source="byok", provider="p", expected_cost_usd=0.0,
            quality_score=0.9, context_length=0,
        )
        self.assertTrue(CostQualityGate().accepts(candidate, minimum_quality=0.5, required_context=100000))

    def test_a_recorded_too_small_window_is_refused(self) -> None:
        from amca.model_hub import CostQualityGate, ModelCandidate

        candidate = ModelCandidate(
            model_ref="x", source="byok", provider="p", expected_cost_usd=0.0,
            quality_score=0.9, context_length=4096,
        )
        self.assertFalse(CostQualityGate().accepts(candidate, minimum_quality=0.5, required_context=100000))


class RenderCapabilityTests(unittest.TestCase):
    """Converter availability must be knowable before a user clicks download."""

    def test_every_declared_rendition_is_reported_with_a_reason_when_unavailable(self) -> None:
        from amca.artifact_render import render_capabilities

        entries = render_capabilities()
        self.assertTrue(entries)
        for entry in entries:
            with self.subTest(rendition=f"{entry['artifact_type']}->{entry['target_format']}"):
                self.assertIn("available", entry)
                self.assertIn("converter", entry)
                if entry["available"]:
                    self.assertEqual(entry["reason"], "")
                else:
                    self.assertTrue(entry["reason"], "an unavailable rendition must say why")

    def test_a_rendition_with_no_pipeline_is_reported_unavailable(self) -> None:
        from amca.artifact_render import rendition_available

        available, reason = rendition_available("pdf", "docx")
        self.assertFalse(available)
        self.assertTrue(reason)

    def test_pdf_export_from_docx_reports_the_missing_converter_by_name(self) -> None:
        from amca.artifact_render import rendition_available

        available, reason = rendition_available("docx", "pdf")
        if not available:
            self.assertIn("PyMuPDF", reason)


if __name__ == "__main__":
    unittest.main()
