"""Gate-2 fix (manager decision 1a): an artifact-only model reply must keep
its assistant transcript entry.

The reply text is exactly one valid artifact fence. Fence stripping leaves
empty text, which used to fail transcript message validation and silently
drop the assistant response; the governed adapter now keeps a minimal
"Created artifact: <title>." entry and the artifact is authored exactly once.
"""

from __future__ import annotations

import asyncio
import gc
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.governed_chat import GovernedChatAdapter
from amca.models import ModelRequest, ModelResponse, ScriptedAdapter
from amca.service import RunRegistry, ServiceContext

SOFTWARE = Path(__file__).resolve().parents[1]

FENCE_ONLY = """```artifact md title=Gate Two Demo
# Gate Two Demo

Authored during installed-product acceptance.
```"""


class ArtifactOnlyReplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        benchmarks = root / "benchmarks" / "tasks.json"
        benchmarks.parent.mkdir(parents=True, exist_ok=True)
        benchmarks.write_text("[]", encoding="utf-8")
        self.root = root
        self.context = ServiceContext(
            root=SOFTWARE,
            database=root / "amca.db",
            benchmarks=benchmarks,
        )
        scripted = ScriptedAdapter(
            scripts=[ModelResponse(provider="custom", model="deepseek-v4.1-flash", text=FENCE_ONLY)],
            provider="custom",
            model="deepseek-v4.1-flash",
        )
        self.governed = GovernedChatAdapter(
            scripted, project_id="proj-artifact-only", service=self.context
        )

    def tearDown(self) -> None:
        # Windows: a sqlite connection finalizing during rmtree holds the
        # database file briefly; collect first, then tolerate the race.
        gc.collect()
        shutil.rmtree(self.temp_dir.name, ignore_errors=True)

    def test_artifact_only_reply_keeps_a_created_artifact_entry(self) -> None:
        response = asyncio.run(
            self.governed.generate(
                ModelRequest(messages="make the demo artifact")
            )
        )
        self.assertEqual(response.text, "Created artifact: Gate Two Demo.")
        artifacts = self.context.artifact_store.list_for_project(
            "proj-artifact-only"
        )
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["title"], "Gate Two Demo")

    def test_multiple_artifacts_in_one_reply_name_all_of_them(self) -> None:
        """Manager closure matrix: multiple artifacts in one reply."""
        multi = (
            "```artifact md title=Report Alpha\n# Alpha\n```\n"
            "```artifact md title=Report Beta\n# Beta\n```\n"
        )
        self.governed.inner = ScriptedAdapter(
            scripts=[
                ModelResponse(
                    provider="custom",
                    model="deepseek-v4.1-flash",
                    text=multi,
                )
            ],
            provider="custom",
            model="deepseek-v4.1-flash",
        )
        response = asyncio.run(
            self.governed.generate(ModelRequest(messages="make both reports"))
        )
        self.assertEqual(response.text, "Created artifact: Report Alpha, Report Beta.")
        artifacts = self.context.artifact_store.list_for_project(
            "proj-artifact-only"
        )
        self.assertEqual(len(artifacts), 2)
        self.assertEqual(
            sorted(item["title"] for item in artifacts),
            ["Report Alpha", "Report Beta"],
        )

    def test_malformed_unterminated_fence_is_left_untouched(self) -> None:
        """Manager closure matrix: a malformed/unterminated fence is not an
        instruction — no fallback fires, nothing is authored, the reply is
        kept as the model wrote it."""
        broken = "```artifact md title=Never Closes\nbody without a closing fence"
        self.governed.inner = ScriptedAdapter(
            scripts=[
                ModelResponse(
                    provider="custom",
                    model="deepseek-v4.1-flash",
                    text=broken,
                )
            ],
            provider="custom",
            model="deepseek-v4.1-flash",
        )
        response = asyncio.run(
            self.governed.generate(ModelRequest(messages="try the broken fence"))
        )
        self.assertEqual(response.text, broken)
        self.assertEqual(
            self.context.artifact_store.list_for_project("proj-artifact-only"),
            [],
        )

    def test_exactly_once_artifact_creation_per_reply(self) -> None:
        """Manager closure matrix: exactly-once creation — one reply, one
        artifact row, and the fallback summary cannot re-author anything."""
        response = asyncio.run(
            self.governed.generate(
                ModelRequest(messages="make the demo artifact")
            )
        )
        self.assertEqual(response.text, "Created artifact: Gate Two Demo.")
        # The fallback summary itself carries no fence, so the run-record
        # import path cannot author a duplicate.
        from amca.artifacts import parse_artifact_blocks

        self.assertEqual(parse_artifact_blocks(response.text), [])
        artifacts = self.context.artifact_store.list_for_project(
            "proj-artifact-only"
        )
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(len({item["title"] for item in artifacts}), 1)

    def test_fallback_entry_persists_across_restart_and_reload(self) -> None:
        """Manager closure matrix: the fallback assistant entry persists in
        the conversation store across an application restart (a new
        ServiceContext on the same database) and a transcript reload returns
        it unchanged."""
        from amca.service import ServiceContext as NewContext

        conv = self.context.conversations.create(title="Restart persistence")
        conversation_id = conv["conversation_id"]
        self.context.conversations.finish_run(
            conversation_id,
            run_id="run-restart-check",
            role="assistant",
            content="Created artifact: Gate Two Demo.",
            meta="deepseek-v4.1-flash · Succeeded",
            status="idle",
            project_id=f"conv-{conversation_id}",
        )
        # Simulate an application restart: a brand-new context on the same
        # database file.
        revived = NewContext(
            root=SOFTWARE,
            database=self.root / "amca.db",
            benchmarks=self.root / "benchmarks" / "tasks.json",
        )
        reloaded = revived.conversations.get(conversation_id)
        assistant_entries = [
            message
            for message in reloaded["messages"]
            if message["role"] == "assistant"
        ]
        self.assertEqual(len(assistant_entries), 1)
        self.assertEqual(
            assistant_entries[0]["content"],
            "Created artifact: Gate Two Demo.",
        )

    def test_reply_with_surrounding_prose_keeps_the_prose(self) -> None:
        """The fallback must not overwrite non-empty replies."""
        scripted = ScriptedAdapter(
            scripts=[
                ModelResponse(
                    provider="custom",
                    model="deepseek-v4.1-flash",
                    text="Here is the demo.\n" + FENCE_ONLY,
                )
            ],
            provider="custom",
            model="deepseek-v4.1-flash",
        )
        self.governed.inner = scripted
        response = asyncio.run(
            self.governed.generate(
                ModelRequest(messages="make the demo artifact and explain it")
            )
        )
        self.assertTrue(response.text.startswith("Here is the demo."))
        self.assertNotIn("Created artifact:", response.text)
        self.assertIn("Here is the demo.", response.text)


if __name__ == "__main__":
    unittest.main()


class PublicationLevelFallbackTests(unittest.TestCase):
    """Manager closure matrix: the service publication path
    (ServiceContext._public_response) must also keep the Created-artifact
    entry — the run-record summary carries the fence, the reconciler runs
    with authored-artifact evidence, and an artifact-only reply resolves to
    the minimal naming sentence instead of an unevidenced-claim strip."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        gc.collect()
        shutil.rmtree(self.temp_dir.name, ignore_errors=True)

    def test_public_response_names_authored_artifacts(self) -> None:
        context = ServiceContext(
            root=SOFTWARE,
            database=self.root / "pub.db",
            benchmarks=SOFTWARE / "benchmarks" / "tasks.json",
        )
        fence_only = (
            "```artifact md title=Closure Build Demo\n"
            "# Closure Build Demo\n"
            "Authored on build 468adf5c.\n```"
        )
        public_text = RunRegistry._public_response(
            type(
                "Job",
                (),
                {
                    "result": {
                        "calls": [
                            {
                                "adapter_id": "provider.x:deepseek-v4.1-flash",
                                "status": "completed",
                                "round": 1,
                                "summary": fence_only,
                            }
                        ]
                    },
                    "error": None,
                    "provenance": {},
                    "conversation_id": "conv-demo",
                },
            )()
        )
        self.assertEqual(public_text, "Created artifact: Closure Build Demo.")
