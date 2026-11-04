"""Production wiring conformance tests for Work Order 20D1.

Validates the concrete adapter production paths:
1. Ollama production request payload, tool call parsing, and tool result reinjection.
2. Gemini production request payload, tool call parsing, and tool result reinjection.
3. Anthropic production request payload, tool call parsing, and tool result reinjection.
4. OpenAI production request payload, tool call parsing, and tool result reinjection.
5. OpenAI-compatible production request payload, tool call parsing, and streaming fragment assembly.
6. Capability truth checks: UNKNOWN_SUPPORT_GETS_TOOLS=NO, UNSUPPORTED_MODEL_GETS_TOOLS=NO.
7. Preserved provider IDs vs deterministic synthetic IDs.
8. Malformed arguments fail closed (MALFORMED_ARGUMENTS_EXECUTABLE=NO).
9. Partial stream tool-call non-execution (PARTIAL_STREAM_TOOLCALL_EXECUTABLE=NO).
"""

from __future__ import annotations

import asyncio
from io import BytesIO
import json
from typing import Any
import unittest

from amca.models.base import ModelRequest, ModelResponse, StreamChunk
from amca.models.tool_protocol import (
    ToolDefinition,
    ToolCall,
    ToolResult,
    ToolResultState,
    build_synthetic_call_id,
    effective_tool_support,
)
from amca.models.openai import OpenAIAdapter
from amca.models.openai_compatible import OpenAICompatibleAdapter
from amca.models.anthropic import AnthropicAdapter
from amca.models.gemini import GeminiAdapter
from amca.models.ollama import OllamaAdapter


class MockHttpResponse:
    def __init__(self, data: bytes | str, status: int = 200) -> None:
        self._raw = data.encode("utf-8") if isinstance(data, str) else data
        self._stream = BytesIO(self._raw)
        self.status = status

    def read(self, *args: Any) -> bytes:
        return self._stream.read(*args)

    def __iter__(self):
        return iter(self._stream.readlines())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def make_mock_urlopen(response_json: dict[str, Any]):
    def _mock_urlopen(req, timeout=120.0):
        return MockHttpResponse(json.dumps(response_json))
    return _mock_urlopen


class ToolCallProductionWiring20D1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.sample_tool = ToolDefinition(
            name="filesystem.search",
            description="Search files in workspace",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            strict=True,
        )

    # =========================================================================
    # 1. Ollama Production Path
    # =========================================================================

    def test_ollama_production_request_wiring_supported_model(self) -> None:
        """Ollama request with qwen3:1.7b attaches formatted tool definition."""
        adapter = OllamaAdapter(model="qwen3:1.7b")
        req = ModelRequest(messages="Search files", tools=[self.sample_tool])
        payload = adapter._payload(req, stream=False)
        self.assertIn("tools", payload)
        self.assertEqual(len(payload["tools"]), 1)
        tool_spec = payload["tools"][0]
        self.assertEqual(tool_spec["type"], "function")
        self.assertEqual(tool_spec["function"]["name"], "filesystem.search")

    def test_ollama_production_request_wiring_unsupported_model(self) -> None:
        """Ollama request with dolphin-llama3:8b omits tools (UNSUPPORTED_MODEL_GETS_TOOLS=NO)."""
        adapter = OllamaAdapter(model="dolphin-llama3:8b")
        req = ModelRequest(messages="Search files", tools=[self.sample_tool])
        payload = adapter._payload(req, stream=False)
        self.assertNotIn("tools", payload)

    def test_ollama_production_request_wiring_unknown_model(self) -> None:
        """Ollama request with unlisted model omits tools (UNKNOWN_SUPPORT_GETS_TOOLS=NO)."""
        adapter = OllamaAdapter(model="arbitrary-custom-model:latest")
        req = ModelRequest(messages="Search files", tools=[self.sample_tool])
        payload = adapter._payload(req, stream=False)
        self.assertNotIn("tools", payload)

    def test_ollama_production_tool_result_reinjection(self) -> None:
        """Ollama request message loop reinjects tool results as role: tool with tool_name."""
        adapter = OllamaAdapter(model="qwen3:1.7b")
        req = ModelRequest(
            messages=[
                {"role": "user", "content": "Search files"},
                {"role": "tool", "tool_call_id": "c1", "name": "filesystem.search", "content": '{"ok": true}'},
            ]
        )
        payload = adapter._payload(req, stream=False)
        self.assertEqual(payload["messages"][1]["role"], "tool")
        self.assertEqual(payload["messages"][1]["tool_name"], "filesystem.search")
        self.assertEqual(payload["messages"][1]["content"], '{"ok": true}')

    def test_ollama_production_response_parsing_synthetic_id(self) -> None:
        """Ollama response parsing produces canonical ToolCall with deterministic synthetic ID."""
        fake_response = {
            "model": "qwen3:1.7b",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "filesystem.search",
                            "arguments": {"query": "TODO"},
                        }
                    }
                ],
            },
            "prompt_eval_count": 10,
            "eval_count": 12,
        }
        adapter = OllamaAdapter(model="qwen3:1.7b", urlopen_fn=make_mock_urlopen(fake_response))
        req = ModelRequest(
            messages="Search files",
            metadata={"organization_id": "o-vec", "task_id": "t-vec", "round_index": 0},
        )
        resp = asyncio.run(adapter.generate(req))
        self.assertEqual(len(resp.tool_calls), 1)
        call = resp.tool_calls[0]
        self.assertEqual(call["name"], "filesystem.search")
        self.assertEqual(call["arguments"], {"query": "TODO"})
        expected_id = build_synthetic_call_id("o-vec", "t-vec", 0, 0, "filesystem.search")
        self.assertEqual(call["call_id"], expected_id)
        self.assertTrue(call["call_id"].startswith("amca-call-"))

    # =========================================================================
    # 2. Gemini Production Path
    # =========================================================================

    def test_gemini_production_request_wiring_supported_model(self) -> None:
        """Gemini request with gemini-1.5-pro attaches functionDeclarations."""
        adapter = GeminiAdapter(model="gemini-1.5-pro", api_key="sk-gemini-test")
        req = ModelRequest(messages="Search files", tools=[self.sample_tool])
        payload = adapter._payload(req)
        self.assertIn("tools", payload)
        self.assertIn("functionDeclarations", payload["tools"][0])
        decl = payload["tools"][0]["functionDeclarations"][0]
        self.assertEqual(decl["name"], "filesystem.search")

    def test_gemini_production_request_wiring_unknown_model(self) -> None:
        """Gemini request with unknown model omits functionDeclarations (UNKNOWN_SUPPORT_GETS_TOOLS=NO)."""
        adapter = GeminiAdapter(model="unknown-gemini-model-v99", api_key="sk-gemini-test")
        req = ModelRequest(messages="Search files", tools=[self.sample_tool])
        payload = adapter._payload(req)
        self.assertNotIn("tools", payload)

    def test_gemini_production_tool_result_reinjection(self) -> None:
        """Gemini request converts role: tool into user functionResponse part (never role: tool)."""
        adapter = GeminiAdapter(model="gemini-1.5-pro", api_key="sk-gemini-test")
        req = ModelRequest(
            messages=[
                {"role": "user", "content": "Search files"},
                {"role": "tool", "tool_call_id": "c1", "name": "filesystem.search", "content": '{"ok": true}'},
            ]
        )
        payload = adapter._payload(req)
        roles = [c["role"] for c in payload["contents"]]
        self.assertNotIn("tool", roles)
        self.assertEqual(roles, ["user", "user"])
        fn_resp_part = payload["contents"][1]["parts"][0]
        self.assertIn("functionResponse", fn_resp_part)
        self.assertEqual(fn_resp_part["functionResponse"]["name"], "filesystem.search")

    def test_gemini_production_response_parsing_synthetic_id(self) -> None:
        """Gemini response parsing produces canonical ToolCall with deterministic synthetic ID."""
        fake_response = {
            "modelVersion": "gemini-1.5-pro",
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "filesystem.search",
                                    "args": {"query": "TODO"},
                                }
                            }
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 15, "candidatesTokenCount": 8},
        }
        adapter = GeminiAdapter(
            model="gemini-1.5-pro",
            api_key="sk-gemini-test",
            urlopen_fn=make_mock_urlopen(fake_response),
        )
        req = ModelRequest(
            messages="Search files",
            metadata={"organization_id": "o-vec", "task_id": "t-vec", "round_index": 0},
        )
        resp = asyncio.run(adapter.generate(req))
        self.assertEqual(len(resp.tool_calls), 1)
        call = resp.tool_calls[0]
        self.assertEqual(call["name"], "filesystem.search")
        self.assertEqual(call["arguments"], {"query": "TODO"})
        expected_id = build_synthetic_call_id("o-vec", "t-vec", 0, 0, "filesystem.search")
        self.assertEqual(call["call_id"], expected_id)

    # =========================================================================
    # 3. Anthropic Production Path
    # =========================================================================

    def test_anthropic_production_request_wiring_supported_model(self) -> None:
        """Anthropic request with claude-3-5-sonnet attaches input_schema tool definition."""
        adapter = AnthropicAdapter(model="claude-3-5-sonnet", api_key="sk-ant-test")
        req = ModelRequest(messages="Search files", tools=[self.sample_tool])
        payload = adapter._payload(req)
        self.assertIn("tools", payload)
        self.assertEqual(payload["tools"][0]["name"], "filesystem.search")
        self.assertIn("input_schema", payload["tools"][0])

    def test_anthropic_production_request_wiring_unsupported_model(self) -> None:
        """Anthropic request with claude-2 omits tools (UNSUPPORTED_MODEL_GETS_TOOLS=NO)."""
        adapter = AnthropicAdapter(model="claude-2", api_key="sk-ant-test")
        req = ModelRequest(messages="Search files", tools=[self.sample_tool])
        payload = adapter._payload(req)
        self.assertNotIn("tools", payload)

    def test_anthropic_production_tool_result_reinjection(self) -> None:
        """Anthropic request message loop reinjects tool results as tool_result content block."""
        adapter = AnthropicAdapter(model="claude-3-5-sonnet", api_key="sk-ant-test")
        req = ModelRequest(
            messages=[
                {"role": "user", "content": "Search files"},
                {"role": "tool", "tool_call_id": "call_ant_1", "name": "filesystem.search", "content": '{"ok": true}'},
            ]
        )
        payload = adapter._payload(req)
        self.assertEqual(payload["messages"][1]["role"], "user")
        block = payload["messages"][1]["content"][0]
        self.assertEqual(block["type"], "tool_result")
        self.assertEqual(block["tool_use_id"], "call_ant_1")
        self.assertEqual(block["content"], '{"ok": true}')

    def test_anthropic_production_response_parsing_preserves_provider_id(self) -> None:
        """Anthropic response parsing preserves provider call ID (PROVIDER_IDS_PRESERVED=YES)."""
        adapter = AnthropicAdapter(model="claude-3-5-sonnet", api_key="sk-ant-test")
        fake_message = {
            "id": "msg_123",
            "model": "claude-3-5-sonnet-20241022",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 20, "output_tokens": 15},
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_01A09q90tc1q09cJ5Ur332h1",
                    "name": "filesystem.search",
                    "input": {"query": "TODO"},
                }
            ],
        }
        req = ModelRequest(messages="Search files")
        resp = adapter._parse_response(fake_message, req)
        self.assertEqual(len(resp.tool_calls), 1)
        call = resp.tool_calls[0]
        self.assertEqual(call["name"], "filesystem.search")
        self.assertEqual(call["call_id"], "toolu_01A09q90tc1q09cJ5Ur332h1")
        self.assertEqual(call["provider_call_id"], "toolu_01A09q90tc1q09cJ5Ur332h1")

    # =========================================================================
    # 4. OpenAI Production Path
    # =========================================================================

    def test_openai_production_request_wiring_supported_model(self) -> None:
        """OpenAI request with gpt-4o attaches formatted tool definition."""
        adapter = OpenAIAdapter(model="gpt-4o", api_key="sk-openai-test")
        req = ModelRequest(messages="Search files", tools=[self.sample_tool])
        payload = adapter._payload(req)
        self.assertIn("tools", payload)
        self.assertEqual(payload["tools"][0]["name"], "filesystem.search")
        self.assertEqual(payload["tools"][0]["type"], "function")

    def test_openai_production_request_wiring_unknown_model(self) -> None:
        """OpenAI request with unknown model omits tools (UNKNOWN_SUPPORT_GETS_TOOLS=NO)."""
        adapter = OpenAIAdapter(model="unknown-ai-model-x", api_key="sk-openai-test")
        req = ModelRequest(messages="Search files", tools=[self.sample_tool])
        payload = adapter._payload(req)
        self.assertNotIn("tools", payload)

    def test_openai_production_tool_result_reinjection(self) -> None:
        """OpenAI Responses API reinjects tool results as function_call_output items."""
        adapter = OpenAIAdapter(model="gpt-4o", api_key="sk-openai-test")
        req = ModelRequest(
            messages=[
                {"role": "user", "content": "Search files"},
                {"role": "tool", "tool_call_id": "call_oai_1", "name": "filesystem.search", "content": '{"ok": true}'},
            ]
        )
        payload = adapter._payload(req)
        inputs = payload["input"]
        self.assertEqual(inputs[1]["type"], "function_call_output")
        self.assertEqual(inputs[1]["call_id"], "call_oai_1")
        self.assertEqual(inputs[1]["output"], '{"ok": true}')

    def test_openai_production_response_parsing_preserves_provider_id(self) -> None:
        """OpenAI response parsing preserves provider call ID (PROVIDER_IDS_PRESERVED=YES)."""
        adapter = OpenAIAdapter(model="gpt-4o", api_key="sk-openai-test")
        fake_response = {
            "id": "resp_123",
            "model": "gpt-4o-2024-08-06",
            "status": "completed",
            "usage": {"input_tokens": 12, "output_tokens": 10},
            "output": [
                {
                    "type": "function_call",
                    "id": "call_9876543210",
                    "name": "filesystem.search",
                    "arguments": '{"query": "TODO"}',
                }
            ],
        }
        req = ModelRequest(messages="Search files")
        resp = adapter._parse_response(fake_response, req)
        self.assertEqual(len(resp.tool_calls), 1)
        call = resp.tool_calls[0]
        self.assertEqual(call["name"], "filesystem.search")
        self.assertEqual(call["call_id"], "call_9876543210")
        self.assertEqual(call["provider_call_id"], "call_9876543210")

    # =========================================================================
    # 5. OpenAI-Compatible & Streaming Assembly Path
    # =========================================================================

    def test_openai_compatible_production_request_and_response(self) -> None:
        """OpenAI-compatible adapter formats tool definition and parses tool calls."""
        fake_response = {
            "id": "chatcmpl_123",
            "model": "gpt-4o",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_compat_1",
                                "type": "function",
                                "function": {
                                    "name": "filesystem.search",
                                    "arguments": '{"query": "TODO"}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8},
        }
        adapter = OpenAICompatibleAdapter(
            model="gpt-4o",
            base_url="http://127.0.0.1:8080/v1",
            urlopen_fn=make_mock_urlopen(fake_response),
        )
        req = ModelRequest(messages="Search files", tools=[self.sample_tool])
        resp = asyncio.run(adapter.generate(req))
        self.assertEqual(len(resp.tool_calls), 1)
        call = resp.tool_calls[0]
        self.assertEqual(call["name"], "filesystem.search")
        self.assertEqual(call["call_id"], "call_compat_1")
        self.assertEqual(call["provider_call_id"], "call_compat_1")

    def test_openai_compatible_streaming_fragment_assembly(self) -> None:
        """Streaming deltas accumulate by index; intermediate chunks do NOT yield executable tool calls."""
        stream_chunks = [
            {"id": "c1", "model": "gpt-4o", "choices": [{"delta": {"content": "Thinking..."}, "finish_reason": None}]},
            {"id": "c1", "model": "gpt-4o", "choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_str_1", "type": "function", "function": {"name": "filesystem.search", "arguments": '{"query": '}}]}, "finish_reason": None}]},
            {"id": "c1", "model": "gpt-4o", "choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"TODO"}'}}]}, "finish_reason": "tool_calls"}]},
        ]
        stream_lines = [f"data: {json.dumps(c)}\n" for c in stream_chunks] + ["data: [DONE]\n"]


        def _mock_stream_urlopen(req, timeout=120.0):
            return MockHttpResponse("".join(stream_lines))

        adapter = OpenAICompatibleAdapter(
            model="gpt-4o",
            base_url="http://127.0.0.1:8080/v1",
            urlopen_fn=_mock_stream_urlopen,
        )
        req = ModelRequest(messages="Search files")

        chunks: list[StreamChunk] = []
        async def _run_stream():
            async for chunk in adapter.stream(req):
                chunks.append(chunk)

        asyncio.run(_run_stream())

        # Intermediate chunks must NOT contain executable tool calls
        for chunk in chunks[:-1]:
            self.assertEqual(chunk.tool_calls, ())
            self.assertFalse(chunk.done)

        # Terminal chunk contains complete assembled and validated tool call
        terminal = chunks[-1]
        self.assertTrue(terminal.done)
        self.assertIsNotNone(terminal.response)
        resp = terminal.response
        self.assertEqual(len(resp.tool_calls), 1)
        call = resp.tool_calls[0]
        self.assertEqual(call["name"], "filesystem.search")
        self.assertEqual(call["arguments"], {"query": "TODO"})
        self.assertEqual(call["call_id"], "call_str_1")

    # =========================================================================
    # 6. Malformed Arguments Fail-Closed Verification
    # =========================================================================

    def test_malformed_arguments_fail_closed_production_path(self) -> None:
        """MALFORMED_ARGUMENTS_EXECUTABLE=NO: malformed JSON produces MALFORMED_REQUEST with is_error=True."""
        adapter = OpenAIAdapter(model="gpt-4o", api_key="sk-test")
        malformed_response = {
            "id": "resp_err",
            "model": "gpt-4o",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "id": "call_bad",
                    "name": "filesystem.search",
                    "arguments": '{"query": ',  # truncated JSON
                }
            ],
        }
        req = ModelRequest(messages="Search files")
        resp = adapter._parse_response(malformed_response, req)
        self.assertEqual(len(resp.tool_calls), 1)
        call = resp.tool_calls[0]
        self.assertTrue(call["is_error"])
        self.assertEqual(call["state"], "malformed_request")
        self.assertEqual(call["arguments"], {})

    def test_scalar_arguments_fail_closed_production_path(self) -> None:
        """SCALAR_ARGUMENTS_EXECUTABLE=NO: scalar root produces MALFORMED_REQUEST with is_error=True."""
        adapter = OpenAIAdapter(model="gpt-4o", api_key="sk-test")
        scalar_response = {
            "id": "resp_err",
            "model": "gpt-4o",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "id": "call_bad_scalar",
                    "name": "filesystem.search",
                    "arguments": '"plain string not object"',
                }
            ],
        }
        req = ModelRequest(messages="Search files")
        resp = adapter._parse_response(scalar_response, req)
        self.assertEqual(len(resp.tool_calls), 1)
        call = resp.tool_calls[0]
        self.assertTrue(call["is_error"])
        self.assertEqual(call["state"], "malformed_request")


if __name__ == "__main__":
    unittest.main()
