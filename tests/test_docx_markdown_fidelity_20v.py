"""Focused test suite for Work Order 20V: DOCX Markdown-Fidelity Recovery.

Validates two defect fixes:
  Defect A – Intra-word underscores (``my_var_name``) must remain literal,
             NOT parsed as emphasis.
  Defect B – Inline code spans (`` `my_*value*_x` ``) must protect their
             contents from emphasis parsing; backtick delimiters stripped;
             code content rendered with code styling (Consolas font).

Also confirms:
  - Valid emphasis (_italic_, *italic*, **bold**, __bold__) still works.
  - Headings, lists, fenced code, Unicode/Bangla render correctly.
  - DOCX output is valid OOXML and parseable.
  - DOCX is re-ingestible via AMCA's native _docx parser.
  - 20K PPTX/XLSX graceful failure preserved (non-regression).
"""

from __future__ import annotations

import io
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import docx  # type: ignore[import-not-found]

from amca.artifact_render import ArtifactExporter, _markdown_to_docx_bytes
from amca.artifacts import ArtifactStore
from amca.asset_ingestion import _docx


class _DocxTestBase(unittest.TestCase):
    """Shared scaffold: creates a temp dir, ArtifactStore, and ArtifactExporter."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="amca-20v-"))
        self.store = ArtifactStore(
            database=self.tmp / "artifacts.sqlite3",
            payloads_root=self.tmp / "payloads",
        )
        self.renditions = self.tmp / "renditions"
        self.exporter = ArtifactExporter(self.store, renditions_root=self.renditions)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── helpers ──────────────────────────────────────────────────────────
    def _render_md_to_doc(self, markdown_text: str) -> docx.Document:
        """Render markdown to an in-memory python-docx Document."""
        raw = _markdown_to_docx_bytes(markdown_text)
        return docx.Document(io.BytesIO(raw))

    def _all_runs(self, doc: docx.Document):
        """Return all runs across all paragraphs."""
        return [r for p in doc.paragraphs for r in p.runs]

    def _plain_text(self, doc: docx.Document) -> str:
        """Concatenate all paragraph texts."""
        return "\n".join(p.text for p in doc.paragraphs)


# ═══════════════════════════════════════════════════════════════════════
# DEFECT A — Intra-word underscores
# ═══════════════════════════════════════════════════════════════════════


class TestIntraWordUnderscoresPreserved(_DocxTestBase):
    """my_var_name and snake_case_identifier must keep underscores literally."""

    def test_single_underscore_intraword(self) -> None:
        doc = self._render_md_to_doc("my_var_name")
        self.assertIn("my_var_name", self._plain_text(doc))
        # No run should be italic
        for r in self._all_runs(doc):
            self.assertFalse(r.italic, f"Run '{r.text}' should not be italic")

    def test_multiple_underscores_intraword(self) -> None:
        doc = self._render_md_to_doc("snake_case_identifier")
        self.assertIn("snake_case_identifier", self._plain_text(doc))
        for r in self._all_runs(doc):
            self.assertFalse(r.italic, f"Run '{r.text}' should not be italic")

    def test_double_underscore_intraword(self) -> None:
        doc = self._render_md_to_doc("my__dunder__value")
        text = self._plain_text(doc)
        self.assertIn("my__dunder__value", text)

    def test_mixed_underscores_and_valid_emphasis(self) -> None:
        """_italic_ should be italic but my_var should stay literal."""
        doc = self._render_md_to_doc("See _italic_ and my_var_name here")
        text = self._plain_text(doc)
        # The word with underscores must be literal
        self.assertIn("my_var_name", text)
        # _italic_ should produce an italic run
        italic_runs = [r for r in self._all_runs(doc) if r.italic]
        self.assertTrue(
            any(r.text == "italic" for r in italic_runs),
            f"Expected italic run 'italic', got {[r.text for r in italic_runs]}",
        )

    def test_underscore_at_word_boundary_is_italic(self) -> None:
        doc = self._render_md_to_doc("_valid italic_")
        italic_runs = [r for r in self._all_runs(doc) if r.italic]
        self.assertEqual(len(italic_runs), 1)
        self.assertEqual(italic_runs[0].text, "valid italic")

    def test_asterisk_italic_still_works(self) -> None:
        doc = self._render_md_to_doc("*valid italic*")
        italic_runs = [r for r in self._all_runs(doc) if r.italic]
        self.assertEqual(len(italic_runs), 1)
        self.assertEqual(italic_runs[0].text, "valid italic")

    def test_bold_double_asterisk_still_works(self) -> None:
        doc = self._render_md_to_doc("**bold text**")
        bold_runs = [r for r in self._all_runs(doc) if r.bold]
        self.assertEqual(len(bold_runs), 1)
        self.assertEqual(bold_runs[0].text, "bold text")

    def test_bold_double_underscore_still_works(self) -> None:
        doc = self._render_md_to_doc("__bold text__")
        bold_runs = [r for r in self._all_runs(doc) if r.bold]
        self.assertEqual(len(bold_runs), 1)
        self.assertEqual(bold_runs[0].text, "bold text")


# ═══════════════════════════════════════════════════════════════════════
# DEFECT B — Inline code span protection
# ═══════════════════════════════════════════════════════════════════════


class TestInlineCodeSpanProtection(_DocxTestBase):
    """Backtick code spans must protect contents from emphasis parsing."""

    def test_code_span_with_underscores(self) -> None:
        doc = self._render_md_to_doc("Use `code_with_underscores` here")
        text = self._plain_text(doc)
        self.assertIn("code_with_underscores", text)
        # No backtick characters in document text
        self.assertNotIn("`", text)
        # The code content must not be italic
        for r in self._all_runs(doc):
            if "code_with_underscores" in r.text:
                self.assertFalse(r.italic, "Code span content should not be italic")
                self.assertEqual(r.font.name, "Consolas")

    def test_code_span_with_asterisks(self) -> None:
        doc = self._render_md_to_doc("`my_*value*_x`")
        text = self._plain_text(doc)
        self.assertIn("my_*value*_x", text)
        self.assertNotIn("`", text)
        for r in self._all_runs(doc):
            if "my_*value*_x" in r.text:
                self.assertFalse(r.italic, "Code span must not be italic")
                self.assertFalse(r.bold, "Code span must not be bold")
                self.assertEqual(r.font.name, "Consolas")

    def test_code_span_font_styling(self) -> None:
        doc = self._render_md_to_doc("`inline_code`")
        runs = self._all_runs(doc)
        code_runs = [r for r in runs if r.text == "inline_code"]
        self.assertEqual(len(code_runs), 1)
        self.assertEqual(code_runs[0].font.name, "Consolas")

    def test_multiple_code_spans_in_line(self) -> None:
        doc = self._render_md_to_doc("Use `foo_bar` and `baz_qux` together")
        text = self._plain_text(doc)
        self.assertIn("foo_bar", text)
        self.assertIn("baz_qux", text)
        self.assertNotIn("`", text)

    def test_code_span_mixed_with_emphasis(self) -> None:
        """Code spans + valid emphasis in same line."""
        doc = self._render_md_to_doc("See **bold** and `code_var` and _italic_")
        text = self._plain_text(doc)
        self.assertIn("code_var", text)
        self.assertNotIn("`", text)
        bold_runs = [r for r in self._all_runs(doc) if r.bold]
        italic_runs = [r for r in self._all_runs(doc) if r.italic]
        code_runs = [r for r in self._all_runs(doc) if r.font.name == "Consolas"]
        self.assertTrue(any(r.text == "bold" for r in bold_runs))
        self.assertTrue(any(r.text == "italic" for r in italic_runs))
        self.assertTrue(any(r.text == "code_var" for r in code_runs))


# ═══════════════════════════════════════════════════════════════════════
# COMBINED VECTORS — mixed text
# ═══════════════════════════════════════════════════════════════════════


class TestCombinedMarkdownFidelity(_DocxTestBase):
    """Complex lines with all three patterns: intra-word, code span, emphasis."""

    def test_all_three_in_one_line(self) -> None:
        md = "Call my_func_name with `arg_*val*_1` and see _result_ here"
        doc = self._render_md_to_doc(md)
        text = self._plain_text(doc)
        # Intra-word preserved
        self.assertIn("my_func_name", text)
        # Code span protected
        self.assertIn("arg_*val*_1", text)
        self.assertNotIn("`", text)
        # Valid emphasis
        italic_runs = [r for r in self._all_runs(doc) if r.italic]
        self.assertTrue(any(r.text == "result" for r in italic_runs))


# ═══════════════════════════════════════════════════════════════════════
# UNICODE / BANGLA — formatting markers adjacent to non-Latin text
# ═══════════════════════════════════════════════════════════════════════


class TestUnicodeBanglaFidelity(_DocxTestBase):
    """Bangla / Unicode text adjacent to formatting markers."""

    def test_bangla_text_preserved(self) -> None:
        doc = self._render_md_to_doc("বাংলা ভাষা")
        self.assertIn("বাংলা ভাষা", self._plain_text(doc))

    def test_bangla_with_emphasis(self) -> None:
        doc = self._render_md_to_doc("**বোল্ড** এবং *ইটালিক*")
        bold_runs = [r for r in self._all_runs(doc) if r.bold]
        italic_runs = [r for r in self._all_runs(doc) if r.italic]
        self.assertTrue(any("বোল্ড" in r.text for r in bold_runs))
        self.assertTrue(any("ইটালিক" in r.text for r in italic_runs))

    def test_unicode_symbols_preserved(self) -> None:
        doc = self._render_md_to_doc("café, naïve, 世界, →, ✅")
        text = self._plain_text(doc)
        for sym in ("café", "naïve", "世界", "→", "✅"):
            self.assertIn(sym, text)


# ═══════════════════════════════════════════════════════════════════════
# OOXML VALIDITY + REINGESTABILITY
# ═══════════════════════════════════════════════════════════════════════


class TestDocxOOXMLValidity(_DocxTestBase):
    """Exported DOCX is valid OOXML, parseable, and re-ingestible."""

    FULL_MARKDOWN = (
        "# Title\n\n"
        "Paragraph with my_var_name and `code_span_val` and _italic_ text.\n\n"
        "- Bullet one\n"
        "1. Numbered one\n\n"
        "```python\ndef f(): pass\n```\n\n"
        "বাংলা ভাষা\n"
    )

    def test_valid_ooxml_archive(self) -> None:
        raw = _markdown_to_docx_bytes(self.FULL_MARKDOWN)
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            names = zf.namelist()
            self.assertIn("[Content_Types].xml", names)
            self.assertIn("word/document.xml", names)
            self.assertIn("_rels/.rels", names)
            ct = zf.read("[Content_Types].xml")
            self.assertIn(b"wordprocessingml.document.main+xml", ct)

    def test_parseable_by_python_docx(self) -> None:
        raw = _markdown_to_docx_bytes(self.FULL_MARKDOWN)
        doc = docx.Document(io.BytesIO(raw))
        self.assertGreater(len(doc.paragraphs), 0)

    def test_reingestible_via_amca_docx_parser(self) -> None:
        raw = _markdown_to_docx_bytes(self.FULL_MARKDOWN)
        result = _docx(raw)
        self.assertEqual(result.status, "text_extracted")
        chunks_text = " ".join(c.text for c in result.chunks)
        self.assertIn("Title", chunks_text)
        self.assertIn("my_var_name", chunks_text)

    def test_exporter_roundtrip(self) -> None:
        """Full exporter roundtrip: author → export → parse → validate."""
        art = self.store.author(
            project_id="proj_20v",
            artifact_type="markdown",
            title="20V Roundtrip",
            content=self.FULL_MARKDOWN,
            created_by="user:tester",
        )
        result = self.exporter.export(
            project_id="proj_20v",
            artifact_id=art["artifact_id"],
            target_format="docx",
        )
        self.assertEqual(result["status"], "exported")
        doc = docx.Document(result["path"])
        text = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("my_var_name", text)
        self.assertIn("code_span_val", text)
        self.assertNotIn("`", text)


# ═══════════════════════════════════════════════════════════════════════
# NON-REGRESSION: 20K PPTX/XLSX graceful failure
# ═══════════════════════════════════════════════════════════════════════


class TestPptxXlsxGracefulFailurePreserved(_DocxTestBase):
    """20K's optional-dependency crash recovery is still intact."""

    def test_pptx_converter_unavailable_when_missing(self) -> None:
        from unittest import mock

        art = self.store.author(
            project_id="proj_20v",
            artifact_type="pptx",
            title="Test Deck",
            content='{"slides":[{"title":"Slide 1","bullets":["a"]}]}',
            created_by="user:tester",
        )
        with mock.patch(
            "amca.artifact_render._json_to_pptx_bytes",
            side_effect=ImportError("No module named 'pptx'"),
        ):
            result = self.exporter.export(
                project_id="proj_20v",
                artifact_id=art["artifact_id"],
                target_format="pptx",
            )
        self.assertEqual(result["status"], "converter_unavailable")
        self.assertIn("python-pptx", result["reason"])

    def test_xlsx_converter_unavailable_when_missing(self) -> None:
        from unittest import mock

        art = self.store.author(
            project_id="proj_20v",
            artifact_type="xlsx",
            title="Test Sheet",
            content="a,b\n1,2\n",
            created_by="user:tester",
        )
        with mock.patch(
            "amca.artifact_render._csv_to_xlsx_bytes",
            side_effect=ImportError("No module named 'openpyxl'"),
        ):
            result = self.exporter.export(
                project_id="proj_20v",
                artifact_id=art["artifact_id"],
                target_format="xlsx",
            )
        self.assertEqual(result["status"], "converter_unavailable")
        self.assertIn("openpyxl", result["reason"])


if __name__ == "__main__":
    unittest.main()
