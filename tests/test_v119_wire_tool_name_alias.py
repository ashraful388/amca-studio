"""Wire-name aliasing for OpenAI-compatible tool transmission (v1.1.9-dev).

OpenAI-compatible endpoints enforce ``^[A-Za-z0-9_-]{1,64}$`` on the function
name, so the canonical dotted governed names (``filesystem.write``) must be
aliased on the wire and reversed on the way back.  Governance keeps the
canonical name everywhere else.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from amca.models.openai_compatible import OpenAICompatibleAdapter
from amca.models.tool_protocol import (
    ToolDefinition,
    canonical_tool_name,
    wire_tool_name,
    wire_tool_name_map,
)

WRITE = ToolDefinition(
    name="filesystem.write",
    description="Write or create a file with given text content.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
)
READ = ToolDefinition(
    name="filesystem.read",
    description="Read content from a file within granted scope.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
)


def _adapter() -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        model="gpt-4o",
        adapter_id="test-adapter",
        provider="custom",
        base_url="https://provider.example/v1",
        allow_remote=True,
        api_key="unit-test-key",
    )


def _request(tools=(WRITE, READ), messages=None):
    from amca.models.openai_compatible import ModelRequest

    return ModelRequest(
        messages=messages or [{"role": "user", "content": "write it"}],
        tools=tuple(tools),
        temperature=0.0,
        max_output_tokens=64,
    )


class WireToolNameTests(unittest.TestCase):
    def test_dotted_name_is_aliased_underscored(self):
        self.assertEqual(wire_tool_name("filesystem.write"), "filesystem_write")

    def test_already_safe_name_is_unchanged(self):
        self.assertEqual(wire_tool_name("filesystem_write"), "filesystem_write")
        self.assertEqual(wire_tool_name("amca-tool-1"), "amca-tool-1")

    def test_map_and_reverse_round_trip(self):
        mapping = wire_tool_name_map((WRITE, READ))
        self.assertEqual(
            mapping,
            {"filesystem.write": "filesystem_write", "filesystem.read": "filesystem_read"},
        )
        self.assertEqual(canonical_tool_name("filesystem_write", mapping), "filesystem.write")
        self.assertEqual(canonical_tool_name("filesystem_read", mapping), "filesystem.read")

    def test_unknown_provider_name_is_not_mapped(self):
        mapping = wire_tool_name_map((WRITE,))
        self.assertEqual(canonical_tool_name("rm_rf", mapping), "rm_rf")

    def test_collision_fails_closed(self):
        with self.assertRaises(ValueError):
            wire_tool_name_map(("fs.write", "fs-write"))


class PayloadAliasingTests(unittest.TestCase):
    def test_payload_sends_underscored_names(self):
        payload = _adapter()._payload(_request(), stream=False)
        names = [t["function"]["name"] for t in payload["tools"]]
        self.assertEqual(names, ["filesystem_write", "filesystem_read"])
        for tool in payload["tools"]:
            for char in tool["function"]["name"]:
                self.assertIn(char, "abcdefghijklmnopqrstuvwxyz"
                              "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

    def test_echoed_assistant_tool_calls_use_wire_names(self):
        messages = [
            {"role": "user", "content": "write it"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "filesystem.write", "arguments": "{}"},
                    }
                ],
            },
        ]
        payload = _adapter()._payload(_request(messages=messages), stream=False)
        echoed = payload["messages"][1]["tool_calls"][0]["function"]["name"]
        self.assertEqual(echoed, "filesystem_write")

    def test_no_tools_leaves_names_untouched(self):
        payload = _adapter()._payload(_request(tools=()), stream=False)
        self.assertNotIn("tools", payload)


class ResponseRestorationTests(unittest.TestCase):
    def test_wire_call_is_restored_to_canonical_name(self):
        adapter = _adapter()
        request = _request()
        data = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "filesystem_write",
                                    "arguments": json.dumps(
                                        {"path": "plan.txt", "content": "hi"}
                                    ),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        response = adapter._parse_response(data, request)
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0]["name"], "filesystem.write")

    def test_provider_invented_name_is_not_remapped(self):
        adapter = _adapter()
        request = _request()
        data = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "shell_exec", "arguments": "{}"},
                            }
                        ],
                    },
                }
            ],
            "usage": {},
        }
        response = adapter._parse_response(data, request)
        self.assertEqual(response.tool_calls[0]["name"], "shell_exec")

    def test_round_trip_generate_restores_name(self):
        """End-to-end adapter call: wire name out, canonical name back."""
        adapter = _adapter()
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            response = mock.MagicMock()
            response.read.return_value = json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {
                                            "name": "filesystem_write",
                                            "arguments": json.dumps(
                                                {"path": "p.txt", "content": "x"}
                                            ),
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {},
                }
            ).encode("utf-8")
            response.__enter__ = lambda self: self
            response.__exit__ = lambda self, *a: False
            return response

        adapter._urlopen = fake_urlopen
        response = asyncio.run(adapter.generate(_request()))
        sent_names = [t["function"]["name"] for t in captured["body"]["tools"]]
        self.assertEqual(sent_names, ["filesystem_write", "filesystem_read"])
        self.assertEqual(response.tool_calls[0]["name"], "filesystem.write")


if __name__ == "__main__":
    unittest.main()
