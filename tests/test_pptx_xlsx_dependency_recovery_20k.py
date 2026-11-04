"""Tests for Work Order 20K: PPTX/XLSX optional-dependency crash recovery.

Validates:
- PPTX_EXPORT_WITH_DEPENDENCY=PASS
- PPTX_MISSING_DEPENDENCY_CRASH=NO
- PPTX_DEPENDENCY_FAILURE_GRACEFUL=PASS
- PPTX_CORRUPT_FILE_ON_FAILURE=NO
- XLSX_EXPORT_WITH_DEPENDENCY=PASS
- XLSX_MISSING_DEPENDENCY_CRASH=NO
- XLSX_DEPENDENCY_FAILURE_GRACEFUL=PASS
- XLSX_CORRUPT_FILE_ON_FAILURE=NO
- DOCX_EXPORT_PRESERVED=PASS
- DOCX_DEPENDENCY_FAILURE_PRESERVED=PASS
- SOURCE_INPUT_PRESERVED_ON_FAILURE=YES
- ARTIFACT_GOVERNANCE_13C_PRESERVED=YES
- PROJECT_ISOLATION_PRESERVED=YES
- CROSS_PROJECT_DISCLOSURE=0
"""

from __future__ import annotations

import importlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from amca.artifacts import ArtifactStore
from amca.artifact_render import ArtifactExporter


def _has_module(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


PPTX_SOURCE = '{"slides":[{"title":"20K Presentation","bullets":["bullet 1","bullet 2"]}]}'
XLSX_SOURCE = "item,qty,price\nwidget,10,2.5\ngadget,5,12.0\n"
DOCX_SOURCE = "# 20K Document\n\nPreserved DOCX rendering test.\n\n- item 1\n- item 2\n"


class PptxXlsxDependencyRecovery20KTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.store = ArtifactStore(
            database=self.root / "artifacts.sqlite3",
            payloads_root=self.root / "payloads",
        )
        self.renditions = self.root / "renditions"
        self.exporter = ArtifactExporter(self.store, renditions_root=self.renditions)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_pptx_export_with_dependency_valid(self) -> None:
        """PPTX_EXPORT_WITH_DEPENDENCY=PASS."""
        if not _has_module("pptx"):
            self.skipTest("python-pptx not available in test environment")

        art = self.store.author(
            project_id="p1",
            artifact_type="pptx",
            title="SlideDeck",
            content=PPTX_SOURCE,
            created_by="agent:test",
        )
        res = self.exporter.export(
            project_id="p1", artifact_id=art["artifact_id"], target_format="pptx"
        )
        self.assertEqual(res["status"], "exported")
        data = Path(res["path"]).read_bytes()
        self.assertEqual(data[:2], b"PK", "PPTX must be a ZIP/OOXML package")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            self.assertIn("[Content_Types].xml", names)
            self.assertIn("ppt/presentation.xml", names)
            slide_parts = [n for n in names if n.startswith("ppt/slides/slide")]
            self.assertTrue(slide_parts)

    def test_pptx_missing_dependency_graceful_recovery(self) -> None:
        """PPTX_MISSING_DEPENDENCY_CRASH=NO, PPTX_DEPENDENCY_FAILURE_GRACEFUL=PASS, PPTX_CORRUPT_FILE_ON_FAILURE=NO."""
        art = self.store.author(
            project_id="p1",
            artifact_type="pptx",
            title="SlideDeck",
            content=PPTX_SOURCE,
            created_by="agent:test",
        )
        before_source = self.store.read_source(art["artifact_id"], None)

        with mock.patch.dict(sys.modules, {"pptx": None}):
            uncaught = None
            try:
                res = self.exporter.export(
                    project_id="p1", artifact_id=art["artifact_id"], target_format="pptx"
                )
            except Exception as exc:
                uncaught = exc
                res = None

        self.assertIsNone(uncaught, f"PPTX export must not crash when python-pptx is missing: {uncaught}")
        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "converter_unavailable")
        self.assertIn("python-pptx", res["reason"])
        self.assertEqual(res["artifact_id"], art["artifact_id"])
        self.assertEqual(res["target_format"], "pptx")

        # Confirm no corrupt/partial file exists in renditions
        out_dir = self.renditions / art["artifact_id"]
        if out_dir.exists():
            pptx_files = list(out_dir.glob("*.pptx"))
            self.assertEqual(len(pptx_files), 0, "No partial .pptx must be written on failure")

        # Confirm source input in store preserved
        after_source = self.store.read_source(art["artifact_id"], None)
        self.assertEqual(before_source["content"], after_source["content"])
        self.assertEqual(before_source["version"], after_source["version"])

    def test_xlsx_export_with_dependency_valid(self) -> None:
        """XLSX_EXPORT_WITH_DEPENDENCY=PASS."""
        if not _has_module("openpyxl"):
            self.skipTest("openpyxl not available in test environment")

        art = self.store.author(
            project_id="p1",
            artifact_type="xlsx",
            title="Spreadsheet",
            content=XLSX_SOURCE,
            created_by="agent:test",
        )
        res = self.exporter.export(
            project_id="p1", artifact_id=art["artifact_id"], target_format="xlsx"
        )
        self.assertEqual(res["status"], "exported")
        data = Path(res["path"]).read_bytes()
        self.assertEqual(data[:2], b"PK", "XLSX must be a ZIP/OOXML package")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            self.assertIn("[Content_Types].xml", names)
            self.assertIn("xl/workbook.xml", names)

    def test_xlsx_missing_dependency_graceful_recovery(self) -> None:
        """XLSX_MISSING_DEPENDENCY_CRASH=NO, XLSX_DEPENDENCY_FAILURE_GRACEFUL=PASS, XLSX_CORRUPT_FILE_ON_FAILURE=NO."""
        art = self.store.author(
            project_id="p1",
            artifact_type="xlsx",
            title="Spreadsheet",
            content=XLSX_SOURCE,
            created_by="agent:test",
        )
        before_source = self.store.read_source(art["artifact_id"], None)

        with mock.patch.dict(sys.modules, {"openpyxl": None}):
            uncaught = None
            try:
                res = self.exporter.export(
                    project_id="p1", artifact_id=art["artifact_id"], target_format="xlsx"
                )
            except Exception as exc:
                uncaught = exc
                res = None

        self.assertIsNone(uncaught, f"XLSX export must not crash when openpyxl is missing: {uncaught}")
        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "converter_unavailable")
        self.assertIn("openpyxl", res["reason"])
        self.assertEqual(res["artifact_id"], art["artifact_id"])
        self.assertEqual(res["target_format"], "xlsx")

        # Confirm no corrupt/partial file exists in renditions
        out_dir = self.renditions / art["artifact_id"]
        if out_dir.exists():
            xlsx_files = list(out_dir.glob("*.xlsx"))
            self.assertEqual(len(xlsx_files), 0, "No partial .xlsx must be written on failure")

        # Confirm source input in store preserved
        after_source = self.store.read_source(art["artifact_id"], None)
        self.assertEqual(before_source["content"], after_source["content"])
        self.assertEqual(before_source["version"], after_source["version"])

    def test_docx_export_preserved(self) -> None:
        """DOCX_EXPORT_PRESERVED=PASS, DOCX_DEPENDENCY_FAILURE_PRESERVED=PASS."""
        art = self.store.author(
            project_id="p1",
            artifact_type="markdown",
            title="Document",
            content=DOCX_SOURCE,
            created_by="agent:test",
        )

        if _has_module("docx"):
            res = self.exporter.export(
                project_id="p1", artifact_id=art["artifact_id"], target_format="docx"
            )
            self.assertEqual(res["status"], "exported")
            data = Path(res["path"]).read_bytes()
            self.assertEqual(data[:2], b"PK")

        with mock.patch.dict(sys.modules, {"docx": None}):
            res_missing = self.exporter.export(
                project_id="p1", artifact_id=art["artifact_id"], target_format="docx"
            )
            self.assertEqual(res_missing["status"], "converter_unavailable")
            self.assertIn("python-docx", res_missing["reason"])

    def test_source_input_preserved_on_failure(self) -> None:
        """SOURCE_INPUT_PRESERVED_ON_FAILURE=YES."""
        pptx_art = self.store.author(
            project_id="p1", artifact_type="pptx", title="d", content=PPTX_SOURCE, created_by="t"
        )
        xlsx_art = self.store.author(
            project_id="p1", artifact_type="xlsx", title="s", content=XLSX_SOURCE, created_by="t"
        )

        with mock.patch.dict(sys.modules, {"pptx": None, "openpyxl": None}):
            self.exporter.export(project_id="p1", artifact_id=pptx_art["artifact_id"], target_format="pptx")
            self.exporter.export(project_id="p1", artifact_id=xlsx_art["artifact_id"], target_format="xlsx")

        pptx_after = self.store.read_source(pptx_art["artifact_id"])
        xlsx_after = self.store.read_source(xlsx_art["artifact_id"])
        self.assertEqual(pptx_after["content"], PPTX_SOURCE)
        self.assertEqual(xlsx_after["content"], XLSX_SOURCE)

    def test_artifact_governance_and_project_isolation(self) -> None:
        """ARTIFACT_GOVERNANCE_13C_PRESERVED=YES, PROJECT_ISOLATION_PRESERVED=YES, CROSS_PROJECT_DISCLOSURE=0."""
        art_p1 = self.store.author(
            project_id="proj_alpha", artifact_type="pptx", title="P1 Deck", content=PPTX_SOURCE, created_by="agent:1"
        )
        art_p2 = self.store.author(
            project_id="proj_beta", artifact_type="xlsx", title="P2 Sheet", content=XLSX_SOURCE, created_by="agent:2"
        )

        meta_p1 = self.store.get(art_p1["artifact_id"])
        meta_p2 = self.store.get(art_p2["artifact_id"])

        self.assertEqual(meta_p1["project_id"], "proj_alpha")
        self.assertEqual(meta_p2["project_id"], "proj_beta")
        self.assertEqual(meta_p1["created_by"], "agent:1")
        self.assertEqual(meta_p2["created_by"], "agent:2")

        p1_list = self.store.list_for_project("proj_alpha")
        p2_list = self.store.list_for_project("proj_beta")
        p1_ids = {a["artifact_id"] for a in p1_list}
        p2_ids = {a["artifact_id"] for a in p2_list}

        self.assertIn(art_p1["artifact_id"], p1_ids)
        self.assertNotIn(art_p2["artifact_id"], p1_ids)
        self.assertIn(art_p2["artifact_id"], p2_ids)
        self.assertNotIn(art_p1["artifact_id"], p2_ids)


if __name__ == "__main__":
    unittest.main()
