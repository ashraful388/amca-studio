"""OPENCODE-PS2 20E: PPTX/XLSX export failure-vector characterization.

Tests-only acceptance pack. No production source changes.

Desired contract per the 20E work order:
    uncaught exception            = NO
    partial/corrupt output        = NO
    structured converter failure  = YES

Each dependency-missing test asserts the DESIRED contract, so a failure of
the test is direct evidence that the production behavior violates the
contract (i.e. that a repair order is needed).
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
from amca.assets import AssetStore


def _build():
    tmp = Path(tempfile.mkdtemp(prefix="ps2-20e-"))
    store = ArtifactStore(database=tmp / "a.sqlite3", payloads_root=tmp / "payloads")
    exporter = ArtifactExporter(store, renditions_root=tmp / "renditions")
    assets = AssetStore(root=tmp / "assets")
    return tmp, store, exporter, assets


PPTX_SOURCE = '{"slides":[{"title":"PS2-20E Deck","bullets":["alpha","beta"]}]}'
XLSX_SOURCE = "name,value\nalpha,1\nbeta,2\n"


def _have(mod: str) -> bool:
    try:
        importlib.import_module(mod)
        return True
    except ImportError:
        return False


class PptxExportVectors(unittest.TestCase):
    def test_pptx_export_with_dependency_valid_ooxml(self):
        if not _have("pptx"):
            self.skipTest("python-pptx not installed in this environment")
        tmp, store, exporter, assets = _build()
        art = store.author(project_id="p1", artifact_type="pptx",
                           title="deck", content=PPTX_SOURCE, created_by="t")
        # read source back to prove input preserved
        before = store.read_source(art["artifact_id"], None)
        result = exporter.export(project_id="p1", artifact_id=art["artifact_id"],
                                 target_format="pptx")
        self.assertEqual(result["status"], "exported", result)
        data = Path(result["path"]).read_bytes()
        self.assertGreater(result.get("bytes", 0), 0)
        self.assertEqual(data[:2], b"PK", "not a ZIP/OOXML container")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            self.assertIn("[Content_Types].xml", names)
            self.assertIn("ppt/presentation.xml", names)
            slide_names = [n for n in names if n.startswith("ppt/slides/slide")]
            self.assertTrue(slide_names, "no slide parts found")
            payload = b"".join(zf.read(n) for n in slide_names)
            self.assertIn(b"PS2-20E Deck", payload, "slide content missing")
        after = store.read_source(art["artifact_id"], None)
        self.assertEqual(before["content"], after["content"],
                         "SOURCE_INPUT_UNCHANGED_ON_FAILURE violated")

    def test_pptx_reingest_ooxml(self):
        if not _have("pptx"):
            self.skipTest("python-pptx not installed in this environment")
        tmp, store, exporter, assets = _build()
        art = store.author(project_id="p1", artifact_type="pptx",
                           title="deck", content=PPTX_SOURCE, created_by="t")
        result = exporter.export(project_id="p1", artifact_id=art["artifact_id"],
                                 target_format="pptx")
        record = assets.import_bytes(project_id="p1", filename="deck.pptx",
                                     data=Path(result["path"]).read_bytes(),
                                     source="test")
        self.assertIn(record.extraction_status, ("text_extracted", "metadata_only"))

    def test_pptx_missing_dependency_graceful_contract(self):
        # Simulate python-pptx absence AND prove the desired contract.
        tmp, store, exporter, assets = _build()
        art = store.author(project_id="p1", artifact_type="pptx",
                           title="deck", content=PPTX_SOURCE, created_by="t")
        before = store.read_source(art["artifact_id"], None)
        rend_before = list((tmp / "renditions").rglob("*"))
        with mock.patch.dict(sys.modules, {"pptx": None}):
            try:
                result = exporter.export(project_id="p1",
                                         artifact_id=art["artifact_id"],
                                         target_format="pptx")
                uncaught = None
            except Exception as exc:  # noqa: BLE001 - characterizing behavior
                result = None
                uncaught = exc
        # Contract: uncaught exception = NO
        self.assertIsNone(
            uncaught,
            f"PPTX_MISSING_DEPENDENCY_CURRENT_BEHAVIOR=UNCAUGHT_CRASH "
            f"({type(uncaught).__name__}: {uncaught})",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.get("status"), "converter_unavailable", result)
        # Contract: no partial/corrupt output file was left behind
        rend_after = list((tmp / "renditions").rglob("*"))
        self.assertEqual(len(rend_before), len(rend_after),
                         "partial/corrupt output written on failure")
        for f in rend_after:
            if f.is_file():
                self.assertNotIn(".pptx", f.suffix,
                                 "partial .pptx artifact on failure")
        # Source untouched
        after = store.read_source(art["artifact_id"], None)
        self.assertEqual(before["content"], after["content"])


class XlsxExportVectors(unittest.TestCase):
    def test_xlsx_export_with_dependency_valid_ooxml(self):
        if not _have("openpyxl"):
            self.skipTest("openpyxl not installed in this environment")
        tmp, store, exporter, assets = _build()
        art = store.author(project_id="p1", artifact_type="xlsx",
                           title="sheet", content=XLSX_SOURCE, created_by="t")
        result = exporter.export(project_id="p1", artifact_id=art["artifact_id"],
                                 target_format="xlsx")
        self.assertEqual(result["status"], "exported", result)
        data = Path(result["path"]).read_bytes()
        self.assertEqual(data[:2], b"PK", "not a ZIP/OOXML container")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            self.assertIn("[Content_Types].xml", names)
            self.assertIn("xl/workbook.xml", names)

    def test_xlsx_reingest(self):
        if not _have("openpyxl"):
            self.skipTest("openpyxl not installed in this environment")
        tmp, store, exporter, assets = _build()
        art = store.author(project_id="p1", artifact_type="xlsx",
                           title="sheet", content=XLSX_SOURCE, created_by="t")
        result = exporter.export(project_id="p1", artifact_id=art["artifact_id"],
                                 target_format="xlsx")
        record = assets.import_bytes(project_id="p1", filename="sheet.xlsx",
                                     data=Path(result["path"]).read_bytes(),
                                     source="test")
        self.assertIn(record.extraction_status, ("text_extracted", "metadata_only"))

    def test_xlsx_missing_dependency_graceful_contract(self):
        tmp, store, exporter, assets = _build()
        art = store.author(project_id="p1", artifact_type="xlsx",
                           title="sheet", content=XLSX_SOURCE, created_by="t")
        before = store.read_source(art["artifact_id"], None)
        rend_before = list((tmp / "renditions").rglob("*"))
        with mock.patch.dict(sys.modules, {"openpyxl": None}):
            try:
                result = exporter.export(project_id="p1",
                                         artifact_id=art["artifact_id"],
                                         target_format="xlsx")
                uncaught = None
            except Exception as exc:  # noqa: BLE001
                result = None
                uncaught = exc
        self.assertIsNone(
            uncaught,
            f"XLSX_MISSING_DEPENDENCY_CURRENT_BEHAVIOR=UNCAUGHT_CRASH "
            f"({type(uncaught).__name__}: {uncaught})",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.get("status"), "converter_unavailable", result)
        rend_after = list((tmp / "renditions").rglob("*"))
        self.assertEqual(len(rend_before), len(rend_after),
                         "partial/corrupt output written on failure")
        after = store.read_source(art["artifact_id"], None)
        self.assertEqual(before["content"], after["content"])


if __name__ == "__main__":
    unittest.main()
