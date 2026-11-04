"""Adversarial tests for wire tool-name aliasing (review + security findings).

Covers the cases the first review round raised:
- the reverse map is bound to the catalog ACTUALLY transmitted, so a model
  that was offered no tools can never have a call restored to a canonical name;
- a long dotted name is bounded to the endpoint's 64-character limit and stays
  distinct from a sibling sharing its prefix;
- a named-function ``tool_choice`` cannot put a dotted name back on the wire;
- governed dispatch refuses a tool the run never offered;
- the capability probe, which transmits outside the gate, still records
  evidence instead of refusing its own call.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from amca.models.base import ToolCall, ToolResultState
from amca.models.openai_compatible import ModelRequest, OpenAICompatibleAdapter
from amca.models.tool_protocol import (
    ToolDefinition,
    wire_tool_name,
    wire_tool_name_map,
)

WRITE = ToolDefinition(
    name="filesystem.write",
    description="Write or create a file with given text content.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    },
)
READ = ToolDefinition(
    name="filesystem.read",
    description="Read content from a file within granted scope.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
)


def _adapter(model: str = "gpt-4o") -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        model=model,
        adapter_id="test-adapter",
        provider="custom",
        base_url="https://provider.example/v1",
        allow_remote=True,
        api_key="unit-test-key",
    )


def _request(tools=(WRITE, READ), messages=None, **kwargs) -> ModelRequest:
    return ModelRequest(
        messages=messages or [{"role": "user", "content": "do it"}],
        tools=tuple(tools),
        temperature=0.0,
        max_output_tokens=64,
        **kwargs,
    )


def _tool_call_body(name: str) -> dict:
    return {
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
                                "name": name,
                                "arguments": json.dumps({"path": "p.txt"}),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {},
    }


class WithheldCatalogTests(unittest.TestCase):
    """A model that was not offered a catalog has no authority to call one."""

    def _unverified(self, suffix: str = "a") -> OpenAICompatibleAdapter:
        # An unknown model id keeps the capability gate closed (fail-closed).
        # The suffix keeps each test off the shared capability ledger.
        return _adapter(model=f"model-without-registry-record-{suffix}")

    def test_catalog_is_withheld_for_an_unverified_model(self):
        payload = self._unverified("payload")._payload(_request(), stream=False)
        self.assertNotIn("tools", payload)

    def test_wire_alias_call_is_refused_when_nothing_was_sent(self):
        adapter = self._unverified("wire")
        response = adapter._parse_response(
            _tool_call_body("filesystem_write"), _request()
        )
        self.assertEqual(tuple(response.tool_calls), ())

    def test_canonical_dotted_call_is_refused_when_nothing_was_sent(self):
        adapter = self._unverified("dotted")
        response = adapter._parse_response(
            _tool_call_body("filesystem.write"), _request()
        )
        self.assertEqual(tuple(response.tool_calls), ())

    def test_refusal_is_recorded_not_silent(self):
        from amca.models.tool_capability_evidence import tool_catalog_drop

        adapter = self._unverified("recorded")
        adapter._parse_response(_tool_call_body("filesystem_write"), _request())
        drop = tool_catalog_drop("custom", "model-without-registry-record-recorded")
        self.assertIsNotNone(drop)
        self.assertIn("no catalog was transmitted", drop.reason)

    def test_probe_call_still_counts_although_capability_is_unverified(self):
        from amca.models.tool_capability_evidence import tool_capability_verdict

        adapter = self._unverified("probe")
        sent: list[dict] = []

        def fake_urlopen(request, timeout=None):
            sent.append(json.loads(request.data.decode("utf-8")))
            response = mock.MagicMock()
            response.read.return_value = json.dumps(
                _tool_call_body("amca_tool_capability_probe")
            ).encode("utf-8")
            response.__enter__ = lambda self: self
            response.__exit__ = lambda self, *a: False
            return response

        adapter._urlopen = fake_urlopen
        record = asyncio.run(adapter.probe_tool_capability())
        self.assertIsNotNone(record, "probe stopped recording its own evidence")
        self.assertTrue(record.supported)
        self.assertEqual(record.source, "probe_tool_call")
        self.assertEqual(
            sent[0]["tools"][0]["function"]["name"], "amca_tool_capability_probe"
        )
        self.assertIs(
            tool_capability_verdict("custom", "model-without-registry-record-probe"),
            True,
        )


class WireNameBoundTests(unittest.TestCase):
    def test_long_dotted_name_is_bounded_to_64_chars(self):
        self.assertLessEqual(len(wire_tool_name("a" * 62 + ".bb")), 64)

    def test_truncated_names_stay_distinct(self):
        # A canonical name is capped at 64 chars upstream, so the truncation
        # branch is defence for an over-long name: it must still bound the wire
        # name and keep two long siblings apart.
        long_a = "a." * 40 + "one"
        long_b = "a." * 40 + "two"
        self.assertLessEqual(len(wire_tool_name(long_a)), 64)
        self.assertLessEqual(len(wire_tool_name(long_b)), 64)
        self.assertNotEqual(wire_tool_name(long_a), wire_tool_name(long_b))

    def test_empty_name_fails_closed(self):
        with self.assertRaises(ValueError):
            wire_tool_name("")


class ToolChoiceAliasTests(unittest.TestCase):
    def test_named_function_tool_choice_is_aliased(self):
        request = _request(
            tool_choice={"type": "function", "function": {"name": "filesystem.write"}}
        )
        payload = _adapter()._payload(request, stream=False)
        self.assertEqual(payload["tool_choice"]["function"]["name"], "filesystem_write")

    def test_bare_tool_choice_passes_through(self):
        payload = _adapter()._payload(_request(tool_choice="auto"), stream=False)
        self.assertEqual(payload["tool_choice"], "auto")


class OfferedCatalogDispatchTests(unittest.TestCase):
    """Governed dispatch refuses a tool the run never offered."""

    def _governed(self):
        from amca.governed_chat import GovernedChatAdapter
        from amca.models.scripted import ScriptedAdapter

        class _Store:
            @staticmethod
            def profile(project_id: str) -> dict:
                return {"profile": "standard", "workspace_root": "D:/test"}

        class _Service:
            local_permission_store = _Store()

        inner = ScriptedAdapter([{"text": "", "tool_calls": []}])
        return GovernedChatAdapter(
            inner=inner, project_id="proj-test", service=_Service()
        )

    def _call(self, name: str) -> ToolCall:
        return ToolCall(call_id="c9", name=name, arguments={})

    def test_tool_outside_offered_catalog_is_refused(self):
        governed = self._governed()
        governed._offered_tool_names = frozenset({"filesystem.read"})
        result = governed._dispatch_one_tool(self._call("filesystem.write"))
        self.assertEqual(result.state, ToolResultState.UNKNOWN_TOOL)
        self.assertIn("not offered", result.content)

    def test_offered_name_passes_the_membership_gate(self):
        """A name in the offered set is judged by the normal action lookup.

        The name used here is deliberately absent from the global action map,
        so the refusal that comes back must be the ordinary "not part of the
        governed catalog" one, never the membership refusal.
        """
        governed = self._governed()
        governed._offered_tool_names = frozenset({"filesystem.read", "custom.thing"})
        result = governed._dispatch_one_tool(self._call("custom.thing"))
        self.assertEqual(result.state, ToolResultState.UNKNOWN_TOOL)
        self.assertIn("not part of the governed AMCA catalog", result.content)


class FrozenTransmissionDecisionTests(unittest.TestCase):
    """The catalog decision is frozen for the life of one request.

    The capability ledger is process-wide and mutable, and participants run
    concurrently for the same provider+model, so a decision re-read between
    payload build and response parse would let a request act on a catalog it
    never sent.
    """

    def test_frozen_decision_overrides_a_later_ledger_change(self):
        from amca.models.tool_capability_evidence import record_tool_capability

        adapter = _adapter()  # known tool-capable model
        request = _request()

        # Freeze "no catalog" for this request...
        frozen_false = adapter._parse_response(
            _tool_call_body("filesystem_write"), request, transmits=False
        )
        self.assertEqual(tuple(frozen_false.tool_calls), ())

        # ...even though the ledger now says the model supports tools.
        record_tool_capability(
            adapter.provider, "gpt-4o", supported=True, source="provider_tool_call"
        )
        still_false = adapter._parse_response(
            _tool_call_body("filesystem_write"), request, transmits=False
        )
        self.assertEqual(tuple(still_false.tool_calls), ())

        frozen_true = adapter._parse_response(
            _tool_call_body("filesystem_write"), request, transmits=True
        )
        self.assertEqual(
            [c["name"] for c in frozen_true.tool_calls], ["filesystem.write"]
        )

    def test_nameless_echoed_call_does_not_abort_the_payload(self):
        request = _request(
            messages=[
                {"role": "user", "content": "do it"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": ""}},
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {
                                "name": "filesystem.write",
                                "arguments": "{}",
                            },
                        },
                    ],
                },
            ]
        )
        payload = _adapter()._payload(request, stream=False)
        echoed = payload["messages"][1]["tool_calls"]
        self.assertEqual([c["function"]["name"] for c in echoed], ["filesystem_write"])

    def test_all_nameless_echoes_drop_the_key_entirely(self):
        request = _request(
            messages=[
                {"role": "user", "content": "do it"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": ""}}
                    ],
                },
            ]
        )
        payload = _adapter()._payload(request, stream=False)
        self.assertNotIn("tool_calls", payload["messages"][1])


class ForgedTransmissionMarkerTests(unittest.TestCase):
    """No caller may assert that a catalog was transmitted."""

    def test_metadata_marker_cannot_open_the_gate(self):
        adapter = self_unverified = _adapter(model="model-without-registry-record-x")
        request = ModelRequest(
            messages=[{"role": "user", "content": "do it"}],
            tools=(WRITE, READ),
            temperature=0.0,
            max_output_tokens=64,
            metadata={"tools_transmitted": True},
        )
        payload = adapter._payload(request, stream=False)
        self.assertNotIn("tools", payload)
        response = adapter._parse_response(
            _tool_call_body("filesystem_write"), request
        )
        self.assertEqual(tuple(response.tool_calls), ())

    def test_probe_still_verifies_without_the_marker(self):
        adapter = _adapter(model="model-without-registry-record-y")
        sent: list[dict] = []

        def fake_urlopen(request, timeout=None):
            sent.append(json.loads(request.data.decode("utf-8")))
            response = mock.MagicMock()
            response.read.return_value = json.dumps(
                _tool_call_body("amca_tool_capability_probe")
            ).encode("utf-8")
            response.__enter__ = lambda self: self
            response.__exit__ = lambda self, *a: False
            return response

        adapter._urlopen = fake_urlopen
        record = asyncio.run(adapter.probe_tool_capability())
        self.assertIsNotNone(record)
        self.assertTrue(record.supported)
        self.assertEqual(
            sent[0]["tools"][0]["function"]["name"], "amca_tool_capability_probe"
        )


if __name__ == "__main__":
    unittest.main()
