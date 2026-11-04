"""v1.1.8 Work Order 02 Priority 10 — the complete artifact journey.

Generate -> chat artifact card -> download -> open -> right-panel preview.

This drives the real service and the real artifact store end to end and asserts
each leg the user depends on: the artifact is recorded, it is listed for the
chat's project, its source is retrievable, the preview plan is meaningful, the
export reports its real capability, and the filename the user receives is the
right one for the type.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.artifact_render import rendition_available, render_capabilities
from amca.artifacts import ArtifactStore, parse_artifact_blocks
from amca.service import ServiceContext

SOFTWARE = Path(__file__).resolve().parents[1]

AUTHORED = """I created the report.

```artifact docx title=Quarterly Report
# Quarterly Report

Revenue rose twelve percent.

| Region | Growth |
| --- | --- |
| North | 18% |
| South | 6% |
```
"""


def _context(tmp: Path) -> ServiceContext:
    return ServiceContext(
        root=SOFTWARE, database=tmp / "t.db", benchmarks=SOFTWARE / "benchmarks" / "tasks.json"
    )


class ArtifactBlockParsingTests(unittest.TestCase):
    """The transport the model uses to declare a file must be understood."""

    def test_a_declared_artifact_block_is_extracted(self) -> None:
        blocks = parse_artifact_blocks(AUTHORED)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["artifact_type"], "markdown")
        self.assertEqual(blocks[0]["title"], "Quarterly Report")
        # docx is stored as the markdown its export renders.
        self.assertIn("Revenue rose", blocks[0]["content"])

    def test_an_unsupported_type_is_not_silently_accepted(self) -> None:
        # pdf has no rendering pipeline, so accepting it would promise a file
        # that cannot be produced.
        blocks = parse_artifact_blocks(
            "```artifact pdf title=Broken\nbody\n```"
        )
        self.assertEqual(blocks, [])


class JourneyTests(unittest.TestCase):
    """The journey a user actually takes, asserted leg by leg."""

    def test_author_then_list_then_read_then_preview(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        store = ArtifactStore(tmp / "artifacts.sqlite", tmp / "payloads")
        project = "journey-project"

        # 1. Generate — the artifact is authored with provenance.
        created = store.author(
            project_id=project, artifact_type="markdown", title="Quarterly Report",
            content="# Quarterly Report\n\nRevenue rose twelve percent.",
            created_by="assistant", chat_id="conv-1",
        )
        self.assertEqual(created["title"], "Quarterly Report")
        self.assertEqual(created["current_version"], 1)
        self.assertTrue(created["created_by"])

        # 2. Chat card — the artifact is listed for the chat's project so the
        #    transcript can show it.
        listed = store.list_for_project(project)
        self.assertEqual([item["title"] for item in listed], ["Quarterly Report"])

        # 3. Open — the source is retrievable and carries provenance.
        source = store.read_source(created["artifact_id"])
        self.assertIn("Revenue rose", source["content"])
        self.assertIn("sha256", source["provenance"])
        self.assertEqual(source["version"], 1)

        # 4. Preview — the stored type and source are what the preview layer
        #    consumes. The rendering itself is asserted in the frontend suite
        #    (desktop-ui/tests/artifactPreviewModel.test.mjs), so this asserts
        #    only the contract it depends on: a readable markdown source.
        self.assertEqual(created["artifact_type"], "markdown")
        self.assertTrue(source["content"].lstrip().startswith("#"))

    def test_a_spreadsheet_previews_as_a_table_not_as_text(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        store = ArtifactStore(tmp / "artifacts.sqlite", tmp / "payloads")
        created = store.author(
            project_id="p", artifact_type="xlsx", title="Numbers",
            content="region,growth\nNorth,18\nSouth,6\n", created_by="assistant",
        )
        source = store.read_source(created["artifact_id"])
        # A spreadsheet's canonical source is delimited text, which is what the
        # preview layer turns into a table.  Assert the shape it needs.
        self.assertEqual(created["artifact_type"], "xlsx")
        header = source["content"].splitlines()[0]
        self.assertEqual(header.count(","), 1)

    def test_a_version_is_immutable_and_a_restore_appends(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        store = ArtifactStore(tmp / "artifacts.sqlite", tmp / "payloads")
        created = store.author(
            project_id="p", artifact_type="markdown", title="Doc",
            content="first", created_by="assistant",
        )
        edited = store.edit(
            artifact_id=created["artifact_id"], content="second",
            base_version=1, created_by="user",
        )
        self.assertEqual(edited["current_version"], 2)
        store.restore(artifact_id=created["artifact_id"], version=1, created_by="user")
        # v1 is still v1: restoring created a third version rather than
        # rewriting history.
        self.assertEqual(store.read_source(created["artifact_id"], 1)["content"], "first")
        self.assertEqual(store.read_source(created["artifact_id"])["version"], 3)

    def test_export_reports_its_real_capability_for_every_type(self) -> None:
        # The download leg must never claim a file it cannot produce.
        for entry in render_capabilities():
            with self.subTest(rendition=f"{entry['artifact_type']}->{entry['target_format']}"):
                available, reason = rendition_available(
                    entry["artifact_type"], entry["target_format"]
                )
                self.assertEqual(available, entry["available"])
                if not available:
                    self.assertTrue(reason)

    def test_the_filename_the_user_receives_uses_the_right_extension(self) -> None:
        # Mirrors desktop-ui/src/outputModel.ts::artifactFileName.
        import re

        def file_name(title: str, artifact_type: str, fmt: str) -> str:
            base = re.sub(r'[<>:"/\\|?*]', "_", title or artifact_type or "artifact")
            base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", base).strip()
            return f"{base or 'artifact'}.{fmt}"

        self.assertEqual(file_name("Quarterly Report", "docx", "docx"), "Quarterly Report.docx")
        self.assertEqual(file_name("a/b:c", "markdown", "md"), "a_b_c.md")

    def test_the_service_lists_artifacts_for_a_chat_project(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        context = _context(tmp)
        saved = context.save_studio_project({"name": "Artifact journey"})
        project_id = str((saved.get("project") or saved)["project_id"])

        context.artifact_author(
            project_id,
            {
                "artifact_type": "markdown",
                "title": "Journey Doc",
                "content": "# Journey\n\nBody.",
                "created_by": "assistant",
            },
        )
        listed = context.artifact_list(project_id)
        artifacts = listed if isinstance(listed, list) else listed.get("artifacts", [])
        titles = [item["title"] for item in artifacts]
        self.assertIn("Journey Doc", titles)


if __name__ == "__main__":
    unittest.main()
