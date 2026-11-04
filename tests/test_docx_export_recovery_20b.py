"""Focused test suite for Work Order 20B: DOCX Export Recovery (DEFECT-14A-1).

Validates:
1. DOCX_EXPORT_SUCCESS
2. DOCX_VALID_OOXML ([Content_Types].xml + word/document.xml)
3. DOCX_PARSE_SUCCESS (via python-docx)
4. DOCX_REINGEST_SUCCESS (via amca.asset_ingestion._docx)
5. HEADING_RENDER (headings 1-6)
6. LIST_RENDER (bullet and numbered lists)
7. EMPHASIS_RENDER (bold and italic runs)
8. UNICODE_RENDER (UTF-8 unicode preserved)
9. DOCX_DEPENDENCY_MISSING_CRASH == NO / DEPENDENCY_FAILURE_GRACEFUL == PASS
10. CORRUPT_DOCX_CREATED_ON_FAILURE == NO
11. SOURCE_INPUT_PRESERVED_ON_FAILURE == YES
12. ARTIFACT_GOVERNANCE_13C_PRESERVED == YES / PROJECT_ISOLATION_PRESERVED == YES
"""

from __future__ import annotations

import io
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import docx

from amca.artifact_render import ArtifactExporter
from amca.artifacts import ArtifactError, ArtifactStore
from amca.asset_ingestion import _docx


class DocxExportRecovery20BTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="amca-docx-20b-"))
        self.store = ArtifactStore(
            database=self.tmp / "artifacts.sqlite3",
            payloads_root=self.tmp / "payloads",
        )
        self.renditions = self.tmp / "renditions"
        self.exporter = ArtifactExporter(self.store, renditions_root=self.renditions)

        self.rich_markdown = (
            "# Document Main Title\n\n"
            "## Section 1: Executive Overview\n\n"
            "This is a standard paragraph with **bold text**, *italic text*, "
            "and multilingual Unicode: café, naïve, 世界, →, ✅.\n\n"
            "### Section 1.1: Bullet Points\n\n"
            "- First bullet with **bold feature**\n"
            "- Second bullet with *italic note*\n"
            "- Third bullet with Unicode symbol: ★\n\n"
            "### Section 1.2: Numbered Steps\n\n"
            "1. Step one: initialize environment\n"
            "2. Step two: verify **integrity**\n"
            "3. Step three: export artifact\n\n"
            "```python\n"
            "def hello_world():\n"
            "    return 'ok'\n"
            "```\n"
        )
        self.artifact = self.store.author(
            project_id="proj_alpha",
            artifact_type="markdown",
            title="Rich Specification Document",
            content=self.rich_markdown,
            created_by="user:tester",
        )
        self.aid = self.artifact["artifact_id"]

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_docx_export_success_and_valid_ooxml(self) -> None:
        """DOCX_EXPORT_SUCCESS and DOCX_VALID_OOXML."""
        result = self.exporter.export(
            project_id="proj_alpha", artifact_id=self.aid, target_format="docx"
        )
        self.assertEqual(result["status"], "exported")
        self.assertEqual(result["target_format"], "docx")
        self.assertTrue(Path(result["path"]).exists())
        self.assertGreater(result["bytes"], 0)

        with zipfile.ZipFile(result["path"], "r") as zf:
            namelist = zf.namelist()
            self.assertIn("[Content_Types].xml", namelist)
            self.assertIn("word/document.xml", namelist)
            self.assertIn("_rels/.rels", namelist)

            content_types = zf.read("[Content_Types].xml")
            self.assertIn(b"wordprocessingml.document.main+xml", content_types)

            document_xml = zf.read("word/document.xml")
            self.assertIn(b"Document Main Title", document_xml)

    def test_docx_parse_and_formatting_render(self) -> None:
        """DOCX_PARSE_SUCCESS, HEADING_RENDER, LIST_RENDER, EMPHASIS_RENDER, UNICODE_RENDER."""
        result = self.exporter.export(
            project_id="proj_alpha", artifact_id=self.aid, target_format="docx"
        )
        doc = docx.Document(result["path"])

        # Headings check
        headings = [p for p in doc.paragraphs if p.style.name.startswith("Heading")]
        heading_texts = [p.text for p in headings]
        self.assertIn("Document Main Title", heading_texts)
        self.assertIn("Section 1: Executive Overview", heading_texts)
        self.assertIn("Section 1.1: Bullet Points", heading_texts)

        h1 = [p for p in headings if p.text == "Document Main Title"][0]
        self.assertEqual(h1.style.name, "Heading 1")
        h2 = [p for p in headings if p.text == "Section 1: Executive Overview"][0]
        self.assertEqual(h2.style.name, "Heading 2")
        h3 = [p for p in headings if p.text == "Section 1.1: Bullet Points"][0]
        self.assertEqual(h3.style.name, "Heading 3")

        # List checks (Bullet & Numbered)
        bullet_paragraphs = [p for p in doc.paragraphs if p.style.name == "List Bullet"]
        self.assertEqual(len(bullet_paragraphs), 3)
        self.assertTrue(any("First bullet" in p.text for p in bullet_paragraphs))

        number_paragraphs = [p for p in doc.paragraphs if p.style.name == "List Number"]
        self.assertEqual(len(number_paragraphs), 3)
        self.assertTrue(any("Step one" in p.text for p in number_paragraphs))

        # Emphasis (Bold & Italic runs)
        all_runs = [r for p in doc.paragraphs for r in p.runs]
        bold_runs = [r.text for r in all_runs if r.bold]
        italic_runs = [r.text for r in all_runs if r.italic]
        self.assertIn("bold text", bold_runs)
        self.assertIn("bold feature", bold_runs)
        self.assertIn("italic text", italic_runs)
        self.assertIn("italic note", italic_runs)

        # Unicode check
        unicode_paragraph = [p for p in doc.paragraphs if "multilingual Unicode" in p.text][0]
        self.assertIn("café", unicode_paragraph.text)
        self.assertIn("naïve", unicode_paragraph.text)
        self.assertIn("世界", unicode_paragraph.text)
        self.assertIn("→", unicode_paragraph.text)
        self.assertIn("✅", unicode_paragraph.text)

    def test_docx_reingest_success(self) -> None:
        """DOCX_REINGEST_SUCCESS via AMCA native asset_ingestion._docx parser."""
        result = self.exporter.export(
            project_id="proj_alpha", artifact_id=self.aid, target_format="docx"
        )
        docx_bytes = Path(result["path"]).read_bytes()

        ingestion_res = _docx(docx_bytes)
        self.assertEqual(ingestion_res.status, "text_extracted")
        self.assertEqual(ingestion_res.media_type, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertGreater(len(ingestion_res.chunks), 0)

        chunk_texts = " ".join(c.text for c in ingestion_res.chunks)
        self.assertIn("Document Main Title", chunk_texts)
        self.assertIn("Executive Overview", chunk_texts)
        self.assertIn("First bullet", chunk_texts)
        self.assertIn("Step one", chunk_texts)
        self.assertIn("世界", chunk_texts)

    def test_docx_dependency_failure_graceful_and_no_partial_file(self) -> None:
        """DOCX_DEPENDENCY_MISSING_CRASH=NO, DEPENDENCY_FAILURE_GRACEFUL=PASS, CORRUPT_DOCX_CREATED_ON_FAILURE=NO."""
        with mock.patch(
            "amca.artifact_render._markdown_to_docx_bytes",
            side_effect=ModuleNotFoundError("No module named 'docx'"),
        ):
            result = self.exporter.export(
                project_id="proj_alpha", artifact_id=self.aid, target_format="docx"
            )

        self.assertEqual(result["status"], "converter_unavailable")
        self.assertIn("python-docx", result["reason"])
        self.assertEqual(result["artifact_id"], self.aid)
        self.assertEqual(result["target_format"], "docx")

        out_dir = self.renditions / self.aid
        if out_dir.exists():
            docx_files = list(out_dir.glob("*.docx"))
            self.assertEqual(len(docx_files), 0)

    def test_source_input_preserved_on_export_failure(self) -> None:
        """SOURCE_INPUT_PRESERVED_ON_FAILURE=YES."""
        initial_source = self.store.read_source(self.aid)

        with mock.patch(
            "amca.artifact_render._markdown_to_docx_bytes",
            side_effect=ModuleNotFoundError("No module named 'docx'"),
        ):
            self.exporter.export(
                project_id="proj_alpha", artifact_id=self.aid, target_format="docx"
            )

        after_source = self.store.read_source(self.aid)
        self.assertEqual(initial_source["content"], after_source["content"])
        self.assertEqual(initial_source["version"], after_source["version"])

    def test_artifact_governance_13c_and_project_isolation(self) -> None:
        """ARTIFACT_GOVERNANCE_13C_PRESERVED=YES, PROJECT_ISOLATION_PRESERVED=YES, CROSS_PROJECT_DISCLOSURE=0."""
        beta_artifact = self.store.author(
            project_id="proj_beta",
            artifact_type="markdown",
            title="Beta Confidential",
            content="# Beta Secret Plan\nConfidential data.",
            created_by="user:beta",
        )
        beta_aid = beta_artifact["artifact_id"]

        # 1. Project list isolation: proj_alpha never sees proj_beta artifact
        alpha_artifacts = self.store.list_for_project("proj_alpha")
        alpha_aids = [a["artifact_id"] for a in alpha_artifacts]
        self.assertNotIn(beta_aid, alpha_aids)

        # 2. Cross-project disclosure is 0
        beta_artifacts = self.store.list_for_project("proj_beta")
        beta_aids = [a["artifact_id"] for a in beta_artifacts]
        self.assertNotIn(self.aid, beta_aids)
        self.assertIn(beta_aid, beta_aids)


if __name__ == "__main__":
    unittest.main()
