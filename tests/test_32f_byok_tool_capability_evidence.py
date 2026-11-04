"""32F follow-up: verified tool-capability evidence for BYOK/cloud models.

Proves, entirely offline with a scripted transport:

1. an unknown model keeps the fail-closed default: the provider payload omits
   ``tools`` AND the exact reason is recorded (never a silent drop);
2. a bounded probe answered with a real tool call records verified-supported,
   after which the tool catalog IS offered and a governed tool call executes;
3. a provider that rejects the tools field records verified-unsupported, after
   which the catalog stays omitted with a truthful reason;
4. an inconclusive or unrelated failure records nothing at all (no fabricated
   capability evidence);
5. the governed chat wrapper states the withheld-catalog reason instead of
   letting the run look like the model simply chose not to call a tool.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.governed_chat import GovernedChatAdapter
from amca.models.base import ModelRequest, ModelResponse
from amca.models.openai_compatible import OpenAICompatibleAdapter
from amca.models.scripted import ScriptedAdapter
from amca.models.tool_capability_evidence import (
    DEFAULT_TOOL_CAPABILITY_EVIDENCE,
    record_tool_capability,
    tool_capability_drop_reason,
    tool_capability_record,
    tool_capability_verdict,
    tool_catalog_drop,
)
from amca.models.tool_protocol import wire_tool_name
from amca.service import ServiceContext

BYOK_PROVIDER = "openai-compatible"
BYOK_MODEL = "glm-5.3-flash"
PROJECT_ID = "studio-32f-byok-tools"
CONVERSATION_ID = "32f-byok-tools"

GOVERNED_TOOL = {
    "type": "function",
    "name": "filesystem.read",
    "description": "Read content from a file within granted scope.",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
}


class _FakeHTTPResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> bool:
        return False


def _chat_body(*, tool_call: dict | None = None, text: str = "") -> bytes:
    message: dict = {"role": "assistant", "content": text}
    if tool_call is not None:
        message["tool_calls"] = [tool_call]
    return json.dumps(
        {
            "id": "chatcmpl-1",
            "model": BYOK_MODEL,
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    ).encode("utf-8")


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _http_error(code: int, body: str) -> HTTPError:
    return HTTPError(
        f"https://api.example.com/v1/chat/completions",
        code,
        "Bad Request",
        None,
        io.BytesIO(body.encode("utf-8")),
    )


class _Transport:
    """Scripted OpenAI-compatible transport; every call is recorded."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.payloads: list[dict] = []

    def __call__(self, request, timeout=None):  # noqa: ANN001
        self.payloads.append(json.loads(request.data.decode("utf-8")))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return _FakeHTTPResponse(response)


class ByokToolCapabilityEvidence(unittest.TestCase):
    def setUp(self) -> None:
        DEFAULT_TOOL_CAPABILITY_EVIDENCE.clear()
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        benchmarks = root / "benchmarks" / "tasks.json"
        benchmarks.parent.mkdir(parents=True, exist_ok=True)
        benchmarks.write_text("[]", encoding="utf-8")
        self.context = ServiceContext(
            root=root, database=root / "amca.db", benchmarks=benchmarks
        )
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "release-notes.md").write_text(
            "release notes body", encoding="utf-8"
        )
        # v1.2.0: a chat inside a Project is addressed by the Project's id and
        # the Project carries the folder.  A standalone conv- chat has none.
        self.context.conversations.create(
            title="32F BYOK tool capability proof",
            conversation_id=CONVERSATION_ID,
        )
        self.context.studio_project_store.save(
            {
                "project_id": PROJECT_ID,
                "name": "32F BYOK tool capability proof",
                "description": "",
                "instructions": "",
                "template": "general",
                "root_path": str(self.workspace),
            }
        )

    def tearDown(self) -> None:
        DEFAULT_TOOL_CAPABILITY_EVIDENCE.clear()
        try:
            self.temporary.cleanup()
        except Exception:
            pass

    def _adapter(self, transport: _Transport) -> OpenAICompatibleAdapter:
        return OpenAICompatibleAdapter(
            model=BYOK_MODEL,
            provider=BYOK_PROVIDER,
            base_url="https://api.example.com/v1",
            allow_remote=True,
            api_key="test-key",
            urlopen_fn=transport,
        )

    # -- 1. fail-closed default, but never silent ------------------------

    def test_unknown_byok_model_omits_tools_and_records_the_reason(self) -> None:
        transport = _Transport([_chat_body(text="I cannot access local files.")])
        adapter = self._adapter(transport)
        self.assertIsNone(tool_capability_verdict(BYOK_PROVIDER, BYOK_MODEL))
        payload = adapter._payload(
            ModelRequest(messages="read release-notes.md", tools=(GOVERNED_TOOL,)),
            stream=False,
        )
        self.assertNotIn("tools", payload)
        drop = tool_catalog_drop(BYOK_PROVIDER, BYOK_MODEL)
        self.assertIsNotNone(drop, "the withheld catalog was not recorded")
        self.assertIn("unverified", drop.reason)
        self.assertEqual(
            tool_capability_drop_reason(BYOK_PROVIDER, BYOK_MODEL), drop.reason
        )
        # A known model keeps the static table's verdict: no drop at all.
        self.assertEqual(
            tool_capability_drop_reason("openai", "gpt-4o"), ""
        )

    # -- 2. verified supported via a real probe ---------------------------

    def test_probe_tool_call_verifies_support_and_then_tools_are_offered(self) -> None:
        transport = _Transport(
            [_chat_body(tool_call=_tool_call("amca_tool_capability_probe", {}))]
        )
        adapter = self._adapter(transport)
        record = asyncio.run(adapter.probe_tool_capability())
        self.assertIsNotNone(record, "probe did not record evidence")
        self.assertTrue(record.supported)
        self.assertEqual(record.source, "probe_tool_call")
        self.assertTrue(tool_capability_verdict(BYOK_PROVIDER, BYOK_MODEL))
        # The probe request itself carried the tool catalog.
        self.assertIn("tools", transport.payloads[0])
        payload = adapter._payload(
            ModelRequest(messages="read release-notes.md", tools=(GOVERNED_TOOL,)),
            stream=False,
        )
        self.assertIn("tools", payload)
        # The wire name is the transport-safe alias; the canonical dotted name
        # stays in force everywhere else (see wire_tool_name).
        self.assertEqual(
            payload["tools"][0]["function"]["name"],
            wire_tool_name("filesystem.read"),
        )
        self.assertEqual(payload["tools"][0]["function"]["name"], "filesystem_read")
        # A verified model no longer reports a withheld catalog.
        self.assertEqual(tool_capability_drop_reason(BYOK_PROVIDER, BYOK_MODEL), "")

    # -- 3. verified unsupported via provider rejection --------------------

    def test_provider_rejection_of_tools_records_unsupported(self) -> None:
        transport = _Transport(
            [_http_error(400, '{"error":{"message":"tools are not supported"}}')]
        )
        adapter = self._adapter(transport)
        with self.assertRaises(Exception):
            asyncio.run(
                adapter.generate(
                    ModelRequest(messages="read it", tools=(GOVERNED_TOOL,))
                )
            )
        record = tool_capability_record(BYOK_PROVIDER, BYOK_MODEL)
        self.assertIsNotNone(record)
        self.assertFalse(record.supported)
        self.assertEqual(record.source, "provider_rejection")
        payload = adapter._payload(
            ModelRequest(messages="read it", tools=(GOVERNED_TOOL,)), stream=False
        )
        self.assertNotIn("tools", payload)
        self.assertIn(
            "does not support tool calling",
            tool_capability_drop_reason(BYOK_PROVIDER, BYOK_MODEL),
        )

    def test_probe_rejection_records_unsupported(self) -> None:
        transport = _Transport(
            [_http_error(422, '{"error":{"message":"unknown field tools"}}')]
        )
        adapter = self._adapter(transport)
        record = asyncio.run(adapter.probe_tool_capability())
        self.assertIsNotNone(record)
        self.assertFalse(record.supported)

    # -- 4. no fabricated evidence ----------------------------------------

    def test_unrelated_failure_records_nothing(self) -> None:
        transport = _Transport([_http_error(400, '{"error":{"message":"bad model"}}')])
        adapter = self._adapter(transport)
        with self.assertRaises(Exception):
            asyncio.run(
                adapter.generate(ModelRequest(messages="hi", tools=(GOVERNED_TOOL,)))
            )
        self.assertIsNone(tool_capability_record(BYOK_PROVIDER, BYOK_MODEL))
        payload = adapter._payload(
            ModelRequest(messages="hi", tools=(GOVERNED_TOOL,)), stream=False
        )
        self.assertNotIn("tools", payload)

    def test_inconclusive_probe_records_nothing(self) -> None:
        transport = _Transport([_chat_body(text="I can answer in text.")])
        adapter = self._adapter(transport)
        self.assertIsNone(asyncio.run(adapter.probe_tool_capability()))
        self.assertIsNone(tool_capability_record(BYOK_PROVIDER, BYOK_MODEL))

    # -- 5. verified capability + governed wrapper = tool really executes ---

    def test_verified_byok_model_executes_a_governed_tool(self) -> None:
        self.context.set_local_permission_profile(
            {
                "project_id": PROJECT_ID,
                "profile": "standard",
                "workspace_root": str(self.workspace),
            }
        )
        self.context.create_local_grant(
            {
                "project_id": PROJECT_ID,
                "root_path": str(self.workspace),
                "capabilities": ["list", "read", "stat", "search"],
                "reason": "32F BYOK governed tool proof",
            }
        )
        # Verified by the provider itself (a real probe answer with a tool call).
        record_tool_capability(
            BYOK_PROVIDER,
            BYOK_MODEL,
            supported=True,
            source="probe_tool_call",
            detail="test fixture: provider answered a probe with a tool call",
        )
        transport = _Transport(
            [
                _chat_body(
                    tool_call=_tool_call(
                        "filesystem.read", {"path": "release-notes.md"}, "call_read"
                    )
                ),
                _chat_body(text="release-notes.md says: release notes body"),
            ]
        )
        adapter = self._adapter(transport)
        governed = GovernedChatAdapter(
            adapter, project_id=PROJECT_ID, service=self.context
        )
        response = asyncio.run(
            governed.generate(
                ModelRequest(messages="read release-notes.md and quote it")
            )
        )
        self.assertIn("release notes body", response.text)
        # The governed tool actually executed: the result reached the provider.
        self.assertIn("tools", transport.payloads[0])
        continuation = transport.payloads[1]
        tool_messages = [m for m in continuation["messages"] if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1, continuation["messages"])
        self.assertIn("release notes body", tool_messages[0]["content"])
        self.assertEqual(tool_messages[0]["tool_call_id"], "call_read")

    # -- 6. the silent case is surfaced by the governed wrapper ------------

    def test_governed_wrapper_states_why_no_tools_were_offered(self) -> None:
        self.context.set_local_permission_profile(
            {
                "project_id": PROJECT_ID,
                "profile": "standard",
                "workspace_root": str(self.workspace),
            }
        )
        transport = _Transport(
            [_chat_body(text="I cannot access local files on your machine.")]
        )
        adapter = self._adapter(transport)
        governed = GovernedChatAdapter(
            adapter, project_id=PROJECT_ID, service=self.context
        )
        response = asyncio.run(
            governed.generate(ModelRequest(messages="read release-notes.md"))
        )
        self.assertNotIn("tools", transport.payloads[0])
        self.assertIn("no governed tools were offered", response.text)
        self.assertIn("unverified", response.text)
        self.assertIn(
            "unverified",
            str(response.provider_metadata.get("tool_catalog_withheld", "")),
        )

    # -- 7. a structured committee payload is never annotated --------------

    def test_structured_payload_is_not_annotated(self) -> None:
        self.context.set_local_permission_profile(
            {
                "project_id": PROJECT_ID,
                "profile": "standard",
                "workspace_root": str(self.workspace),
            }
        )
        payload = json.dumps({"summary": "committee answer", "events": []})
        transport = _Transport([_chat_body(text=payload)])
        adapter = self._adapter(transport)
        governed = GovernedChatAdapter(
            adapter, project_id=PROJECT_ID, service=self.context
        )
        response = asyncio.run(governed.generate(ModelRequest(messages="status?")))
        self.assertEqual(json.loads(response.text)["summary"], "committee answer")
        self.assertIn(
            "unverified",
            str(response.provider_metadata.get("tool_catalog_withheld", "")),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class AntiFabricationCompoundClaim(unittest.TestCase):
    """The integrity gate must judge each clause against its own evidence.

    A verified compound report ("read notes.txt and created the file") must be
    preserved, while a fabricated compound claim ("created report.docx and read
    notes.txt") must still be intercepted — clause scoping must not weaken the
    gate.
    """

    LEDGER = (
        {"name": "filesystem.list", "action": "list", "state": "success", "path": ".", "content": "{}"},
        {"name": "filesystem.read", "action": "read", "state": "success", "path": "notes.txt", "content": "body"},
        {"name": "filesystem.write", "action": "create", "state": "success", "path": "created-by-model.txt", "content": "created"},
    )

    def _reconcile(self, text: str):
        from amca.anti_fabrication import reconcile_anti_fabrication

        return reconcile_anti_fabrication(text, executed_tools=self.LEDGER)

    def test_verified_compound_report_is_preserved(self) -> None:
        for text in (
            "I read notes.txt and created the file.",
            "I listed the folder, read notes.txt, searched it and created the file.",
            "I read notes.txt, then created the summary file.",
            "I created the file and read notes.txt.",
        ):
            with self.subTest(text=text):
                reconciled, altered = self._reconcile(text)
                self.assertFalse(altered, reconciled)
                self.assertEqual(reconciled, text)

    def test_fabricated_compound_claim_is_still_intercepted(self) -> None:
        text = "I created report.docx and read notes.txt."
        reconciled, altered = self._reconcile(text)
        self.assertTrue(altered)
        self.assertEqual(reconciled, "That operation was not executed.")

    def test_unverified_single_claim_is_still_intercepted(self) -> None:
        for text in (
            "I created a new file named test.py with the test cases.",
            "I deleted the obsolete file temp.log from the directory.",
            "I executed the command `npm test` and all 15 test suites passed.",
        ):
            with self.subTest(text=text):
                reconciled, altered = self._reconcile(text)
                self.assertTrue(altered, reconciled)

    def test_no_evidence_at_all_still_intercepts_everything(self) -> None:
        from amca.anti_fabrication import reconcile_anti_fabrication

        text = "I read notes.txt and created the file."
        reconciled, altered = reconcile_anti_fabrication(text, executed_tools=())
        self.assertTrue(altered)
        self.assertEqual(reconciled, "That operation was not executed.")
