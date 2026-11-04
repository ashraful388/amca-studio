"""Conformance and regression test suite for Work Order 20D.

Tests:
17E1: Canonical Tool Types (ToolDefinition, ToolCall, ToolResult, deterministic synthetic IDs, fail-closed validation)
17E2: Adapter Normalization & Reinjection (OpenAI, Anthropic, Gemini, Ollama, streaming safety)
17E3: Effective Tool Capability Truth (intersection logic, known model truth, router defect fix)
"""

from __future__ import annotations

import json
import unittest

from amca.models.tool_protocol import (
    MAX_ARGUMENT_BYTES,
    MAX_ARGUMENT_DEPTH,
    ToolDefinition,
    ToolCall,
    ToolResult,
    ToolResultState,
    build_synthetic_call_id,
    calculate_nesting_depth,
    validate_and_normalize_arguments,
    format_openai_tool_definition,
    format_openai_compatible_tool_definition,
    format_anthropic_tool_definition,
    format_gemini_tool_definition,
    format_ollama_tool_definition,
    parse_openai_tool_call,
    parse_anthropic_tool_call,
    parse_gemini_tool_call,
    parse_ollama_tool_call,
    build_openai_tool_result_message,
    build_openai_compatible_tool_result_message,
    build_anthropic_tool_result_block,
    build_gemini_tool_result_part,
    build_ollama_tool_result_message,
    KNOWN_MODEL_TOOL_SUPPORT,
    effective_tool_support,
)
from amca.models.base import ModelCapabilities
from amca.intelligence.cross_provider_router import (
    _capabilities_from_adapter,
    _model_hub_provider_to_routing_candidates,
)


class ToolProtocol20DTests(unittest.TestCase):
    # ==========================================================================
    # 17E1 Tests: Canonical Types & Validation
    # ==========================================================================

    def test_tool_definition_validation(self) -> None:
        """TOOL_DEFINITION_CANONICAL: valid name/schema accepted, invalid rejected."""
        valid_def = ToolDefinition(
            name="filesystem.search",
            description="Search workspace text",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            strict=True,
        )
        self.assertEqual(valid_def.name, "filesystem.search")
        self.assertEqual(valid_def.description, "Search workspace text")
        self.assertTrue(valid_def.strict)

        # Invalid name containing uppercase or spaces
        with self.assertRaises(ValueError):
            ToolDefinition(
                name="Filesystem.Search",
                description="desc",
                parameters={},
            )
        with self.assertRaises(ValueError):
            ToolDefinition(
                name="invalid name with spaces",
                description="desc",
                parameters={},
            )

        # Invalid non-dict parameters
        with self.assertRaises(ValueError):
            ToolDefinition(
                name="filesystem.read",
                description="desc",
                parameters="not_a_dict",  # type: ignore
            )

    def test_synthetic_call_id_determinism_golden_vectors(self) -> None:
        """SYNTHETIC_IDS_DETERMINISTIC: match exact 17D1 Section C golden vectors."""
        # 1. o-vec|t-vec|0|0|filesystem.search -> amca-call-998bf36701833f35
        id1 = build_synthetic_call_id("o-vec", "t-vec", 0, 0, "filesystem.search")
        self.assertEqual(id1, "amca-call-998bf36701833f35")

        # 2. o-vec|t-vec|0|1|command.propose_argv -> amca-call-eb5937f37b43ef3f
        id2 = build_synthetic_call_id("o-vec", "t-vec", 0, 1, "command.propose_argv")
        self.assertEqual(id2, "amca-call-eb5937f37b43ef3f")

        # 3. o-vec|t-vec|1|0|filesystem.search -> amca-call-1b87dd5cd8f6b639
        id3 = build_synthetic_call_id("o-vec", "t-vec", 1, 0, "filesystem.search")
        self.assertEqual(id3, "amca-call-1b87dd5cd8f6b639")

        # 4. o-vec|t-vec|0|0|filesystem.write -> amca-call-50f12c5e85bc26d2
        id4 = build_synthetic_call_id("o-vec", "t-vec", 0, 0, "filesystem.write")
        self.assertEqual(id4, "amca-call-50f12c5e85bc26d2")

    def test_malformed_arguments_fail_closed(self) -> None:
        """MALFORMED_ARGUMENTS_EXECUTABLE=NO: malformed JSON returns MALFORMED_REQUEST."""
        raw_truncated = '{"query": '
        args, err = validate_and_normalize_arguments(raw_truncated, call_id="c1", tool_name="test")
        self.assertIsNone(args)
        self.assertIsNotNone(err)
        self.assertEqual(err.state, ToolResultState.MALFORMED_REQUEST)
        self.assertTrue(err.is_error)

    def test_scalar_arguments_fail_closed(self) -> None:
        """SCALAR_ARGUMENTS_EXECUTABLE=NO: scalar or list root returns MALFORMED_REQUEST."""
        # String scalar
        args1, err1 = validate_and_normalize_arguments('"TODO"', call_id="c1", tool_name="test")
        self.assertIsNone(args1)
        self.assertEqual(err1.state, ToolResultState.MALFORMED_REQUEST)

        # Number scalar
        args2, err2 = validate_and_normalize_arguments(42, call_id="c1", tool_name="test")
        self.assertIsNone(args2)
        self.assertEqual(err2.state, ToolResultState.MALFORMED_REQUEST)

        # List root
        args3, err3 = validate_and_normalize_arguments([1, 2, 3], call_id="c1", tool_name="test")
        self.assertIsNone(args3)
        self.assertEqual(err3.state, ToolResultState.MALFORMED_REQUEST)

    def test_overdepth_arguments_fail_closed(self) -> None:
        """OVERDEPTH_ARGUMENTS_EXECUTABLE=NO: nesting depth > 6 is rejected."""
        # Depth 6 is allowed
        d6 = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
        self.assertEqual(calculate_nesting_depth(d6), 6)
        args_d6, err_d6 = validate_and_normalize_arguments(d6)
        self.assertIsNotNone(args_d6)
        self.assertIsNone(err_d6)

        # Depth 7 is rejected
        d7 = {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}
        self.assertEqual(calculate_nesting_depth(d7), 7)
        args_d7, err_d7 = validate_and_normalize_arguments(d7)
        self.assertIsNone(args_d7)
        self.assertIsNotNone(err_d7)
        self.assertEqual(err_d7.state, ToolResultState.MALFORMED_REQUEST)
        self.assertIn("exceeds limit", err_d7.content)

    def test_oversized_arguments_fail_closed(self) -> None:
        """OVERSIZED_ARGUMENTS_EXECUTABLE=NO: arguments > 16KB are rejected."""
        large_args = {"data": "x" * (MAX_ARGUMENT_BYTES + 10)}
        args, err = validate_and_normalize_arguments(large_args)
        self.assertIsNone(args)
        self.assertIsNotNone(err)
        self.assertEqual(err.state, ToolResultState.MALFORMED_REQUEST)
        self.assertIn("exceeds limit", err.content)

    def test_tool_result_canonical(self) -> None:
        """TOOL_RESULT_CANONICAL: status and is_error derivation."""
        res_ok = ToolResult(
            call_id="call-1",
            name="filesystem.search",
            state=ToolResultState.SUCCESS,
            content=json.dumps({"ok": True, "summary": "found 2 files"}),
        )
        self.assertFalse(res_ok.is_error)

        res_denied = ToolResult(
            call_id="call-1",
            name="filesystem.search",
            state=ToolResultState.DENIED,
            content=json.dumps({"ok": False, "state": "denied"}),
        )
        self.assertTrue(res_denied.is_error)

    # ==========================================================================
    # 17E2 Tests: Adapter Normalization & Reinjection
    # ==========================================================================

    def test_openai_normalization_and_reinjection(self) -> None:
        """OPENAI_NORMALIZATION=PASS & OPENAI_TOOL_RESULT_REINJECTION=PASS & PROVIDER_IDS_PRESERVED=YES."""
        raw_call = {
            "id": "call_openai_123",
            "type": "function",
            "function": {
                "name": "filesystem.search",
                "arguments": '{"query": "TODO", "mode": "regex"}',
            },
        }
        res = parse_openai_tool_call(raw_call, index=0)
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.call_id, "call_openai_123")
        self.assertEqual(res.provider_call_id, "call_openai_123")
        self.assertEqual(res.name, "filesystem.search")
        self.assertEqual(res.arguments, {"query": "TODO", "mode": "regex"})

        # Test reinjection builder
        result = ToolResult(
            call_id=res.call_id,
            name=res.name,
            state=ToolResultState.SUCCESS,
            content='{"ok": true, "matches": 5}',
        )
        msg_resp = build_openai_tool_result_message(result)
        self.assertEqual(msg_resp["type"], "function_call_output")
        self.assertEqual(msg_resp["call_id"], "call_openai_123")
        self.assertEqual(msg_resp["output"], '{"ok": true, "matches": 5}')

        msg_chat = build_openai_compatible_tool_result_message(result)
        self.assertEqual(msg_chat["role"], "tool")
        self.assertEqual(msg_chat["tool_call_id"], "call_openai_123")

    def test_anthropic_normalization_and_reinjection(self) -> None:
        """ANTHROPIC_NORMALIZATION=PASS & ANTHROPIC_TOOL_RESULT_REINJECTION=PASS."""
        raw_call = {
            "type": "tool_use",
            "id": "tu_anthropic_456",
            "name": "filesystem.search",
            "input": {"query": "TODO", "mode": "regex"},
        }
        res = parse_anthropic_tool_call(raw_call, index=0)
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.call_id, "tu_anthropic_456")
        self.assertEqual(res.provider_call_id, "tu_anthropic_456")
        self.assertEqual(res.arguments, {"query": "TODO", "mode": "regex"})

        # Reinjection block
        result = ToolResult(
            call_id=res.call_id,
            name=res.name,
            state=ToolResultState.SUCCESS,
            content='{"ok": true}',
        )
        block = build_anthropic_tool_result_block(result)
        self.assertEqual(block["type"], "tool_result")
        self.assertEqual(block["tool_use_id"], "tu_anthropic_456")
        self.assertFalse(block["is_error"])

        # Reinjection error block
        err_result = ToolResult(
            call_id=res.call_id,
            name=res.name,
            state=ToolResultState.DENIED,
            content='{"ok": false, "state": "denied"}',
        )
        err_block = build_anthropic_tool_result_block(err_result)
        self.assertTrue(err_block["is_error"])

    def test_gemini_normalization_and_reinjection(self) -> None:
        """GEMINI_NORMALIZATION=PASS & GEMINI_TOOL_RESULT_REINJECTION=PASS."""
        raw_call = {
            "functionCall": {
                "name": "filesystem.search",
                "args": {"query": "TODO", "limit": 10},
            }
        }
        res = parse_gemini_tool_call(
            raw_call, index=0, organization_id="o-vec", task_id="t-vec", round_index=0
        )
        self.assertIsInstance(res, ToolCall)
        self.assertTrue(res.call_id.startswith("amca-call-"))
        self.assertIsNone(res.provider_call_id)
        self.assertEqual(res.arguments, {"query": "TODO", "limit": 10})

        # Reinjection part
        result = ToolResult(
            call_id=res.call_id,
            name=res.name,
            state=ToolResultState.SUCCESS,
            content=json.dumps({"ok": True, "summary": "done"}),
        )
        part = build_gemini_tool_result_part(result)
        self.assertIn("functionResponse", part)
        self.assertEqual(part["functionResponse"]["name"], "filesystem.search")
        self.assertEqual(part["functionResponse"]["response"]["ok"], True)

    def test_ollama_normalization_and_reinjection(self) -> None:
        """OLLAMA_NORMALIZATION=PASS & OLLAMA_TOOL_RESULT_REINJECTION=PASS."""
        raw_call = {
            "function": {
                "name": "filesystem.search",
                "arguments": {"query": "TODO"},
            }
        }
        res = parse_ollama_tool_call(
            raw_call, index=0, organization_id="o-vec", task_id="t-vec", round_index=0
        )
        self.assertIsInstance(res, ToolCall)
        self.assertTrue(res.call_id.startswith("amca-call-"))
        self.assertEqual(res.arguments, {"query": "TODO"})

        # Reinjection message
        result = ToolResult(
            call_id=res.call_id,
            name=res.name,
            state=ToolResultState.SUCCESS,
            content='{"ok": true}',
        )
        msg = build_ollama_tool_result_message(result)
        self.assertEqual(msg["role"], "tool")
        self.assertEqual(msg["tool_name"], "filesystem.search")

    def test_partial_stream_not_executable(self) -> None:
        """PARTIAL_STREAM_TOOLCALL_EXECUTABLE=NO."""
        # Simulated streaming fragment with incomplete JSON
        stream_raw = {
            "id": "stream_1",
            "type": "function",
            "function": {
                "name": "filesystem.search",
                "arguments": '{"query": "TO',
            },
        }
        res = parse_openai_tool_call(stream_raw)
        self.assertIsInstance(res, ToolResult)
        self.assertEqual(res.state, ToolResultState.MALFORMED_REQUEST)
        self.assertTrue(res.is_error)

    # ==========================================================================
    # 17E3 Tests: Effective Tool Capability Truth & Router
    # ==========================================================================

    def test_qwen3_tool_support_true(self) -> None:
        """QWEN3_1_7B_CURRENT_TOOL_SUPPORT=YES."""
        self.assertTrue(effective_tool_support(True, "qwen3:1.7b", True))

    def test_dolphin_llama3_tool_support_false(self) -> None:
        """DOLPHIN_LLAMA3_8B_CURRENT_TOOL_SUPPORT=NO."""
        self.assertFalse(effective_tool_support(True, "dolphin-llama3:8b", True))

    def test_unknown_model_fail_closed(self) -> None:
        """UNKNOWN_TOOL_SUPPORT_EXECUTABLE=NO."""
        self.assertFalse(effective_tool_support(True, "unknown-custom-model:latest", True))
        self.assertFalse(effective_tool_support(True, "arbitrary-ai:3b", True))

    def test_capability_intersection(self) -> None:
        """CAPABILITY_INTERSECTION=PASS: requires adapter and model and route all True."""
        # adapter False -> False
        self.assertFalse(effective_tool_support(False, "qwen3:1.7b", True))
        # route False -> False
        self.assertFalse(effective_tool_support(True, "qwen3:1.7b", False))
        # model unsupported -> False
        self.assertFalse(effective_tool_support(True, "dolphin-llama3:8b", True))
        # all True -> True
        self.assertTrue(effective_tool_support(True, "qwen3:1.7b", True))

    def test_router_defect_fix(self) -> None:
        """Verify cross_provider_router correctly surfaces supports_tools for supported models."""
        from amca.intelligence.contracts import ContractMetadata, Sensitivity
        from amca.credentials import MemoryCredentialStore

        meta_factory = lambda cid: ContractMetadata(
            record_id=cid,
            correlation_id="c1",
            producer="test",
            created_at="2026-09-10T00:00:00Z",
            sensitivity=Sensitivity.INTERNAL,
        )
        cred_store = MemoryCredentialStore()

        provider_qwen = {
            "id": "prov-qwen",
            "provider_kind": "ollama",
            "model_ids": ["qwen3:1.7b"],
            "endpoint": "http://127.0.0.1:11434",
            "status": "connected_metadata_only",
        }
        candidates_qwen = _model_hub_provider_to_routing_candidates(
            meta_factory, provider_qwen, [], cred_store
        )
        self.assertEqual(len(candidates_qwen), 1)
        self.assertIn("supports_tools", candidates_qwen[0].capabilities)

        provider_dolphin = {
            "id": "prov-dolphin",
            "provider_kind": "ollama",
            "model_ids": ["dolphin-llama3:8b"],
            "endpoint": "http://127.0.0.1:11434",
            "status": "connected_metadata_only",
        }
        candidates_dolphin = _model_hub_provider_to_routing_candidates(
            meta_factory, provider_dolphin, [], cred_store
        )
        self.assertEqual(len(candidates_dolphin), 1)
        self.assertNotIn("supports_tools", candidates_dolphin[0].capabilities)


if __name__ == "__main__":
    unittest.main()
