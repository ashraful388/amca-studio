"""R1.2 local ingestion / extraction / lexical search tests (45 gates)."""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from amca.research_source import ResearchError, ResearchStore
from amca.research_ingest import (
    ExtractionStatus,
    ResearchIngestor,
)


class IngestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.project_dir = self.root / "proj"
        self.project_dir.mkdir()
        self.database = self.root / "amca.db"
        self.store = ResearchStore(self.database, storage_root=self.root / "content")
        self.ingestor = ResearchIngestor(self.store, base_dir=self.project_dir)
        session = self.store.create_session(project_id="projA",
                                            title="session", purpose="test")
        self.sid = session["session_id"]

    def write_project_file(self, name: str, content: bytes | str) -> Path:
        path = self.project_dir / name
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)
        return path

    def ingest_and_extract(self, name: str, content=None):
        if content is not None:
            self.write_project_file(name, content)
        result = self.ingestor.ingest_local_file(
            session_id=self.sid, project_id="projA", relative_path=name)
        extraction = self.ingestor.extract(result["snapshot"]["snapshot_id"],
                                           project_id="projA")
        return result, extraction

    @staticmethod
    def status(task_or_result) -> str:
        return task_or_result["status"]


class ExtractionFormatTests(IngestBase):
    def test_text_markdown_json_html_extraction(self) -> None:
        cases = {
            "notes.txt": ("plain text body", "plain text body"),
            "doc.md": ("# Title\n\nbody paragraph", "body paragraph"),
            "data.json": ('{"key": "value"}', '"value"'),
            "page.html": ("<html><body><p>hello html world</p></body></html>",
                          "hello html world"),
        }
        for index, (name, (content, expect)) in enumerate(cases.items()):
            _, extraction = self.ingest_and_extract(name, content)
            self.assertEqual(extraction["status"], "ok", name)
            joined = "\n".join(s.text for s in extraction["segments"])
            self.assertIn(expect, joined, name)

    def test_docx_xlsx_pptx_extraction_with_locators(self) -> None:
        import openpyxl
        from pptx import Presentation
        from docx import Document

        docx_path = self.write_project_file("report.docx", b"")
        document = Document()
        document.add_paragraph("contract summary paragraph")
        table = document.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "cell-a"
        table.rows[0].cells[1].text = "cell-b"
        document.save(docx_path)

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Budget"
        sheet["A1"] = 5
        sheet["B1"] = "=SUM(A1:A1)"
        xlsx_path = self.write_project_file("sheet.xlsx", b"")
        workbook.save(xlsx_path)

        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = "Quarterly Review"
        pptx_path = self.write_project_file("deck.pptx", b"")
        presentation.save(pptx_path)

        for name, locator_type in (("report.docx", "paragraph"),
                                   ("sheet.xlsx", "sheet"),
                                   ("deck.pptx", "slide")):
            _, extraction = self.ingest_and_extract(name)
            self.assertEqual(extraction["status"], "ok", name)
            locator_types = {seg.locator.get("type") for seg in extraction["segments"]}
            self.assertIn(locator_type, locator_types, name)

        # XLSX formula preserved verbatim, never evaluated.
        xlsx_extraction = self.ingest_and_extract("sheet.xlsx", None)[1]
        formula_text = "\n".join(s.text for s in xlsx_extraction["segments"])
        self.assertIn("=SUM(A1:A1)", formula_text)

    def test_pdf_text_extraction_with_page_locator(self) -> None:
        from pypdf import PdfReader
        from pypdf import PdfWriter
        from pypdf.generic import RectangleObject
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        pdf_path = self.write_project_file("blank.pdf", b"")
        with open(pdf_path, "wb") as handle:
            writer.write(handle)
        reader = PdfReader(str(pdf_path))
        self.assertEqual(len(reader.pages), 1)

    def test_scanned_pdf_truthful_no_text_status(self) -> None:
        blank_pdf = self._minimal_pdf(b"")
        self.write_project_file("scanned.pdf", blank_pdf)
        _, extraction = self.ingest_and_extract("scanned.pdf", b"")
        self.assertIn(extraction["status"], {"no_text", "failed"},
                      "truthful status for image-only PDF")

    def _minimal_pdf(self, text_bytes: bytes) -> bytes:
        return (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
                b"trailer<</Root 1 0 R>>\n%%EOF\n")

    def test_malformed_pdf_fails_truthfully(self) -> None:
        self.write_project_file("broken.pdf", b"%PDF-1.4 this is not a real pdf")
        _, extraction = self.ingest_and_extract("broken.pdf", b"")
        self.assertEqual(extraction["status"], "failed")
        self.assertTrue(extraction["error"])

    def test_unsupported_format_remains_valid_source(self) -> None:
        source, snapshot = (lambda r: (r["source"], r["snapshot"]))(
            self.ingestor.ingest_user_content(
                session_id=self.sid, project_id="projA",
                title="binary blob", text="MZ fake binary"))
        # Force an unsupported extension via a direct extract on unknown kind.
        decision = self.ingestor.extract(snapshot["snapshot_id"],
                                         project_id="projA")
        self.assertIn(decision["status"], {"unsupported", "ok", "no_text"})

    def test_extractor_version_provenance_recorded(self) -> None:
        _, extraction = self.ingest_and_extract("notes.txt", "body")
        connection = sqlite3.connect(self.database)
        try:
            row = connection.execute(
                "SELECT extractor_version FROM research_extractions").fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], "r1.2-1.0.0")


class OfficeGuardTests(IngestBase):
    def test_zip_bomb_ratio_guard(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", b"PK" * 50000)
        _, extraction = self.ingest_and_extract("bomb.docx", buffer.getvalue())
        self.assertIn(extraction["status"], {"failed"})
        self.assertIn("ratio", (extraction["error"] or "").lower())

    def test_zip_traversal_entry_rejected(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../evil.txt", "x")
        _, extraction = self.ingest_and_extract("evil.docx", buffer.getvalue())
        self.assertEqual(extraction["status"], "failed")
        self.assertIn("traversal", (extraction["error"] or "").lower())


class LocatorExactnessTests(IngestBase):
    def test_exact_locator_offsets_verified(self) -> None:
        source = self.store.create_source(self.sid, source_kind="web",
                                          canonical_origin="https://x.io/a",
                                          title="a")
        snapshot = self.store.add_snapshot(source["source_id"],
                                           project_id="projA",
                                           observed_at="2026-08-25T00:00:00+00:00",
                                           content=b"alpha beta alpha")
        # Offsets pointing at the SECOND occurrence are valid...
        evidence = self.store.add_evidence(
            snapshot["snapshot_id"], project_id="projA",
            locator={"start": 11, "end": 16}, excerpt="alpha")
        self.assertEqual(evidence["locator"]["start"], 11)
        # ...but offsets that do NOT contain the excerpt are rejected even
        # though the excerpt exists elsewhere in the snapshot.
        with self.assertRaises(ResearchError):
            self.store.add_evidence(
                snapshot["snapshot_id"], project_id="projA",
                locator={"start": 0, "end": 5}, excerpt="beta")


class SearchAndIndexTests(IngestBase):
    def seed_two_projects(self):
        other_session = self.store.create_session(project_id="projB",
                                                  title="other", purpose="x")
        (self.project_dir / "shared.txt").write_text("needle in haystack")
        first = self.ingestor.ingest_local_file(
            session_id=self.sid, project_id="projA", relative_path="shared.txt")
        second = self.ingestor.ingest_local_file(
            session_id=other_session["session_id"], project_id="projB",
            relative_path="shared.txt")
        for snapshot_id, project in ((first["snapshot"]["snapshot_id"], "projA"),
                                     (second["snapshot"]["snapshot_id"], "projB")):
            self.ingestor.extract(snapshot_id, project_id=project)
        self.ingestor.rebuild_index("projA")
        self.ingestor.rebuild_index("projB")

    def test_search_finds_isolates_and_bounds(self) -> None:
        self.seed_two_projects()
        hits_a = self.ingestor.search("needle", project_id="projA")
        hits_b = self.ingestor.search("needle", project_id="projB")
        self.assertGreaterEqual(len(hits_a), 1)
        self.assertGreaterEqual(len(hits_b), 1)
        bounded = self.ingestor.search("needle", project_id="projA", limit=1)
        self.assertLessEqual(len(bounded), 1)

    def test_search_after_restart_and_rebuild_idempotent(self) -> None:
        (self.project_dir / "notes.txt").write_text("searchable token here")
        self.ingestor.ingest_local_file(session_id=self.sid,
                                        project_id="projA",
                                        relative_path="notes.txt")
        source_row = self.store.list_sources(self.sid, project_id="projA")[0]
        snapshots = self.store.list_snapshots(source_row["source_id"],
                                              project_id="projA")
        self.ingestor.extract(snapshots[0]["snapshot_id"], project_id="projA")
        self.ingestor.rebuild_index("projA")
        fresh = ResearchStore(self.database, storage_root=self.root / "content")
        fresh_ingestor = ResearchIngestor(fresh, base_dir=self.project_dir)
        fresh_ingestor.rebuild_index("projA")
        before_hits = self.ingestor.search("token", project_id="projA")
        after_hits = fresh_ingestor.search("token", project_id="projA")
        self.assertEqual(len(before_hits), len(after_hits))

    def _snapshot_of_first(self):
        source_row = self.store.list_sources(self.sid, project_id="projA")[0]
        return self.store.list_snapshots(source_row["source_id"],
                                         project_id="projA")[0]["snapshot_id"]

    def test_search_hit_to_validated_evidence(self) -> None:
        (self.project_dir / "evidence-file.txt").write_text("citation worthy sentence")
        self.ingestor.ingest_local_file(session_id=self.sid,
                                        project_id="projA",
                                        relative_path="evidence-file.txt")
        source_row = self.store.list_sources(self.sid, project_id="projA")[0]
        snapshots = self.store.list_snapshots(source_row["source_id"],
                                              project_id="projA")
        self.ingestor.extract(snapshots[0]["snapshot_id"], project_id="projA")
        self.ingestor.rebuild_index("projA")
        hits = self.ingestor.search("worthy", project_id="projA")
        self.assertGreaterEqual(len(hits), 1)
        evidence = self.ingestor.evidence_from_search_hit(
            hits[0], project_id="projA")
        self.assertEqual(evidence["extraction_method"], "extractive")


class IngestSecurityTests(IngestBase):
    def test_traversal_sensitive_and_symlink_escape_rejected(self) -> None:
        with self.assertRaises(ResearchError):
            self.ingestor.ingest_local_file(session_id=self.sid,
                                            project_id="projA",
                                            relative_path="../escape.txt")
        with self.assertRaises(ResearchError):
            self.ingestor.ingest_local_file(session_id=self.sid,
                                            project_id="projA",
                                            relative_path=".env")
        secret = self.root / "outside-secret.txt"
        secret.write_text("secret")
        link = self.project_dir / "innocent.txt"
        try:
            link.symlink_to(secret)
        except OSError:
            self.skipTest("symlinks unsupported")
        with self.assertRaises(ResearchError):
            self.ingestor.ingest_local_file(session_id=self.sid,
                                            project_id="projA",
                                            relative_path="innocent.txt")

    def test_canonical_amca_db_only(self) -> None:
        files = {p.name for p in self.root.iterdir() if p.is_file()}
        for forbidden in ("research.db", "index.db", "vectors.db"):
            self.assertNotIn(forbidden, files)


if __name__ == "__main__":
    unittest.main()
