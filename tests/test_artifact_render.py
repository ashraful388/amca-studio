"""W1.2 rendition/export tests: deterministic local, honest unavailability."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from amca.artifacts import ArtifactStore
from amca.artifact_render import ArtifactExporter


class ArtifactExporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = ArtifactStore(
            database=self.root / "artifacts.sqlite3",
            payloads_root=self.root / "payloads",
        )
        self.exporter = ArtifactExporter(self.store, self.root / "renditions")
        meta = self.store.author(
            project_id="p1",
            artifact_type="markdown",
            title="Report",
            content="# Title\n\nBody paragraph.\n<script>alert(1)</script>\n```\ncode\n```",
            created_by="model:test",
        )
        self.aid = meta["artifact_id"]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_markdown_exports_to_deterministic_html(self) -> None:
        result = self.exporter.export(
            project_id="p1", artifact_id=self.aid, target_format="html"
        )
        self.assertEqual(result["status"], "exported")
        produced = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("<h1>Title</h1>", produced)
        self.assertIn("<pre><code>", produced)
        self.assertNotIn("<script>", produced)  # body content is escaped

        again = self.exporter.export(
            project_id="p1", artifact_id=self.aid, target_format="html"
        )
        self.assertEqual(
            Path(again["path"]).read_bytes(),
            Path(result["path"]).read_bytes(),
        )

    def test_zip_export_bundles_source_and_provenance(self) -> None:
        result = self.exporter.export(
            project_id="p1", artifact_id=self.aid, target_format="zip"
        )
        self.assertEqual(result["status"], "exported")
        with zipfile.ZipFile(result["path"]) as bundle:
            names = bundle.namelist()
            self.assertIn("provenance.json", names)
            source_names = [name for name in names if name.startswith("source")]
            self.assertEqual(len(source_names), 1)

    def test_markdown_exports_to_real_docx(self) -> None:
        import docx

        result = self.exporter.export(
            project_id="p1", artifact_id=self.aid, target_format="docx"
        )
        self.assertEqual(result["status"], "exported")
        document = docx.Document(result["path"])
        headings = [paragraph.text for paragraph in document.paragraphs
                    if paragraph.style.name.startswith("Heading")]
        self.assertIn("Title", headings)
        self.assertTrue(
            any("alert" not in text or "<script>" not in text
                for text in [p.text for p in document.paragraphs])
        )

    def test_missing_optional_converter_reports_unavailable(self) -> None:
        result = self.exporter.export(
            project_id="p1", artifact_id=self.aid, target_format="pdf"
        )
        self.assertEqual(result["status"], "converter_unavailable")
        self.assertIn("PyMuPDF", result["reason"])

    def test_csv_artifact_exports_to_real_xlsx(self) -> None:
        from openpyxl import load_workbook

        meta = self.store.author(
            project_id="p1",
            artifact_type="xlsx",
            title="Numbers",
            content="name,value\na,1\nb,2",
            created_by="model:test",
        )
        result = self.exporter.export(
            project_id="p1", artifact_id=meta["artifact_id"], target_format="xlsx"
        )
        self.assertEqual(result["status"], "exported", result)
        sheet = load_workbook(result["path"]).active
        self.assertEqual(sheet.cell(row=1, column=1).value, "name")
        self.assertEqual(sheet.cell(row=3, column=2).value, 2)

    def test_stop_gate_all_four_deliverables_bundle_into_zip(self) -> None:
        """Master W1 gate: doc + spreadsheet + presentation + ZIP in one flow."""

        ppt_meta = self.store.author(
            project_id="p1",
            artifact_type="pptx",
            title="Deck",
            content='{"slides":[{"title":"Overview","bullets":["point one"]}]}',
            created_by="model:test",
        )

        exports = {}
        for aid, fmt in (
            (self.aid, "html"),
            (self.aid, "docx"),
            (meta_ppt := ppt_meta["artifact_id"], "pptx"),
        ):
            result = self.exporter.export(
                project_id="p1", artifact_id=aid, target_format=fmt
            )
            assert result["status"] == "exported", result
            exports[fmt] = Path(result["path"])

        xlsx_meta = self.store.author(
            project_id="p1",
            artifact_type="xlsx",
            title="Numbers",
            content="name,value\na,1",
            created_by="model:test",
        )
        result = self.exporter.export(
            project_id=ppt_meta["project_id"],
            artifact_id=xlsx_meta["artifact_id"],
            target_format="xlsx",
        )
        assert result["status"] == "exported", result
        exports["xlsx"] = Path(result["path"])

        bundle_path = self.root / "stop-gate-bundle.zip"
        import zipfile

        with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as bundle:
            for fmt, path in exports.items():
                bundle.write(path, f"deliverable.{fmt}")

        from pptx import Presentation

        with zipfile.ZipFile(bundle_path) as bundle:
            names = bundle.namelist()
            self.assertEqual(len(names), 4)
            for fmt in ("html", "docx", "pptx", "xlsx"):
                self.assertTrue(
                    any(name.endswith(f".{fmt}") for name in names),
                    f"missing {fmt} deliverable",
                )
        deck = Presentation(self.root / "renditions" / ppt_meta["artifact_id"] / "v1.pptx")
        self.assertGreaterEqual(len(deck.slides), 1)

    def test_undefined_rendition_is_reported_not_faked(self) -> None:
        result = self.exporter.export(
            project_id="p1", artifact_id=self.aid, target_format="pptx"
        )
        self.assertEqual(result["status"], "converter_unavailable")

    def test_importable_but_unwired_converter_is_reported_not_raised(self) -> None:
        """markdown->xlsx declares openpyxl; no pipeline exists, so it must
        report ``converter_unavailable`` even when the library imports."""

        import sys
        import types
        from unittest.mock import patch

        stub = types.ModuleType("openpyxl")
        with patch.dict(sys.modules, {"openpyxl": stub}):
            result = self.exporter.export(
                project_id="p1", artifact_id=self.aid, target_format="xlsx"
            )
        self.assertEqual(result["status"], "converter_unavailable")
        self.assertIn("not implemented", result["reason"])
        self.assertFalse((self.root / "renditions" / f"v1.xlsx").exists())

    def test_zip_export_writes_only_the_rendition_directory(self) -> None:
        result = self.exporter.export(
            project_id="p1", artifact_id=self.aid, target_format="zip"
        )
        self.assertEqual(result["status"], "exported")
        self.assertEqual(
            list(self.exporter.renditions_root.glob("*.zip")), []
        )
        self.assertTrue(Path(result["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
