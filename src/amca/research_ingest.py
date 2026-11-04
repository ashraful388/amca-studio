"""R1.2 local ingestion, extraction, and lexical search for the research
substrate.

- Ingests local files (contained, sensitive-rejected, symlink-escape
  rejected) into sources + immutable snapshots.
- Extracts bounded, locator-bearing text segments from txt/md/json/html/pdf/
  docx/xlsx/pptx with truthful per-extraction status; unsupported formats
  remain valid sources with an UNSUPPORTED extraction status.
- Office archives are zip-bomb guarded (decompressed size / entry count /
  compression ratio); malformed or encrypted PDFs fail truthfully; XLSX
  formulas are preserved verbatim, never evaluated.
- Persists extractions/segments in canonical amca.db and maintains an FTS5
  lexical index as a DERIVABLE projection (rebuildable, never provenance).
- Zero network access.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

from amca.research_source import (
    MAX_EVIDENCE_CHARS,
    ResearchError,
    ResearchStore,
    normalize_local_origin,
)

EXTRACTOR_ID = "amca.research.ingest"
EXTRACTOR_VERSION = "r1.2-1.0.0"
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 500
MAX_ARCHIVE_RATIO = 200
MAX_PDF_PAGES = 500


class ExtractionStatus(str, Enum):
    OK = "ok"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    ENCRYPTED = "encrypted"
    NO_TEXT = "no_text"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid(prefix: str) -> str:
    import uuid
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass(slots=True)
class Segment:
    locator: dict[str, Any]
    text: str


class ResearchIngestor:
    """Local ingestion + extraction + search over the research substrate."""

    def __init__(self, store: ResearchStore, *, base_dir: Path | None = None) -> None:
        self.store = store
        self.base_dir = Path(base_dir).resolve() if base_dir else None

    # ------------------------------------------------------------------
    # ingestion

    def ingest_local_file(self, *, session_id: str, project_id: str,
                          relative_path: str,
                          title: str | None = None) -> dict[str, Any]:
        origin = normalize_local_origin(relative_path)
        if self.base_dir is not None:
            candidate = (self.base_dir / relative_path).resolve()
            if not str(candidate).startswith(str(self.base_dir)):
                raise ResearchError(f"symlink escape rejected: {relative_path!r}")
            if not candidate.is_file():
                raise ResearchError(f"file not found: {relative_path!r}")
            content = candidate.read_bytes()
        else:
            content = relative_path.encode("utf-8")
        if len(content) > MAX_SOURCE_BYTES:
            raise ResearchError("source exceeds maximum ingest size")
        source = self.store.create_source(
            session_id, source_kind="local_file", canonical_origin=origin,
            title=title or Path(origin).name)
        snapshot = self.store.add_snapshot(
            source["source_id"], project_id=project_id,
            observed_at=_now(), retrieved_at=_now(),
            content=content,
            content_type=_content_type(origin))
        return {"source": source, "snapshot": snapshot}

    def ingest_user_content(self, *, session_id: str, project_id: str,
                            title: str, text: str) -> dict[str, Any]:
        source = self.store.create_source(
            session_id, source_kind="user_supplied",
            canonical_origin=f"user-supplied:{title[:60]}", title=title)
        snapshot = self.store.add_snapshot(
            source["source_id"], project_id=project_id,
            observed_at=_now(), content=text.encode("utf-8"),
            content_type="text/plain")
        return {"source": source, "snapshot": snapshot}

    def ingest_artifact_snapshot(self, *, session_id: str, project_id: str,
                                 artifact_id: str, version: int,
                                 content: bytes, title: str,
                                 content_type: str | None = None) -> dict[str, Any]:
        source = self.store.create_source(
            session_id, source_kind="artifact",
            canonical_origin=f"{artifact_id}@v{version}", title=title)
        snapshot = self.store.add_snapshot(
            source["source_id"], project_id=project_id,
            observed_at=_now(), content=content, content_type=content_type)
        return {"source": source, "snapshot": snapshot}

    # ------------------------------------------------------------------
    # extraction

    def extract(self, snapshot_id: str, *, project_id: str) -> dict[str, Any]:
        snapshot = self.store.get_snapshot(snapshot_id, project_id=project_id)
        if snapshot is None:
            raise ResearchError(f"unknown snapshot: {snapshot_id!r}")
        source = self.store.get_source(snapshot["source_id"],
                                       project_id=project_id)
        suffix = Path(source["canonical_origin"]).suffix.lower()
        if not suffix:
            # Fall back to the declared content type for extension-less
            # origins such as web acquisitions.
            content_type = (snapshot.get("content_type") or "").lower()
            type_map = {
                "text/html": ".html", "application/json": ".json",
                "application/pdf": ".pdf", "text/plain": ".txt",
                "text/markdown": ".md",
            }
            for candidate, mapped in type_map.items():
                if candidate in content_type:
                    suffix = mapped
                    break
        status, segments, error = self._extract_segments(suffix, snapshot)
        extraction_id = _uuid("ext")
        now = _now()
        with self.store._connect() as connection:
            connection.execute(
                """INSERT INTO research_extractions
                   (extraction_id, snapshot_id, project_id, extractor,
                    extractor_version, status, error, segment_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (extraction_id, snapshot_id, project_id, EXTRACTOR_ID,
                 EXTRACTOR_VERSION, status.value if isinstance(status, ExtractionStatus) else status,
                 error, len(segments), now))
            for seq, segment in enumerate(segments):
                connection.execute(
                    """INSERT INTO research_extracted_segments
                       (segment_id, extraction_id, seq, locator_json, text)
                       VALUES (?, ?, ?, ?, ?)""",
                    (_uuid("seg"), extraction_id, seq,
                     json.dumps(segment.locator, sort_keys=True),
                     segment.text[:MAX_EVIDENCE_CHARS]))
        return {
            "extraction_id": extraction_id,
            "status": status.value if isinstance(status, ExtractionStatus) else status,
            "error": error,
            "segment_count": len(segments),
            "segments": segments,
        }

    def _extract_segments(self, suffix: str, snapshot) \
            -> tuple[ExtractionStatus, list[Segment], str | None]:
        content = self.store.snapshot_bytes(snapshot)
        text_content = content.decode("utf-8", errors="replace")
        try:
            if suffix == ".txt":
                return ExtractionStatus.OK, [Segment({"type": "whole"}, text_content)], None
            if suffix == ".md":
                return ExtractionStatus.OK, [Segment({"type": "whole"}, text_content)], None
            if suffix == ".json":
                doc = json.loads(content or b"{}")
                pretty = json.dumps(doc, indent=2, sort_keys=True, default=str)
                return ExtractionStatus.OK, [Segment({"type": "whole"}, pretty)], None
            if suffix in (".html", ".htm"):
                return ExtractionStatus.OK, [Segment({"type": "whole"},
                                                     _html_to_text(text_content))], None
            if suffix == ".pdf":
                return self._extract_pdf(content)
            if suffix == ".docx":
                return self._extract_office_guarded(suffix, content)
            if suffix in (".xlsx", ".xlsm"):
                return self._extract_office_guarded(suffix, content)
            if suffix == ".pptx":
                return self._extract_office_guarded(suffix, content)
            return ExtractionStatus.UNSUPPORTED, [], None
        except ResearchError as exc:
            # Guard violations are authoritative local failures.
            return ExtractionStatus.FAILED, [], str(exc)
        except Exception as exc:
            return ExtractionStatus.FAILED, [], f"{type(exc).__name__}: {exc}"

    def _validate_office_archive(self, content: bytes) -> None:
        """Zip-bomb / traversal guards for OOXML archives (fails closed)."""

        import io as _io

        total_uncompressed = 0
        with zipfile.ZipFile(_io.BytesIO(content)) as archive:
            names = archive.namelist()
            if len(names) > MAX_ARCHIVE_ENTRIES:
                raise ResearchError(
                    f"archive exceeds entry bound ({len(names)})")
            for info in archive.infolist():
                candidate = PurePosixPath(info.filename)
                if any(part == ".." for part in candidate.parts):
                    raise ResearchError(
                        f"archive traversal entry rejected: {info.filename}")
                if info.is_dir():
                    continue
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_SOURCE_BYTES:
                    raise ResearchError("archive decompressed size exceeds bound")
            if len(content) > 0:
                ratio = total_uncompressed / max(1, len(content))
                if ratio > MAX_ARCHIVE_RATIO:
                    raise ResearchError(
                        f"archive compression ratio suspicious ({ratio:.0f}x)")

    def _extract_office_guarded(self, suffix: str, content: bytes):
        self._validate_office_archive(content)
        return self._OFFICE_EXTRACTORS[suffix](self, content)

    def _extract_pdf(self, content: bytes):
        from pypdf import PdfReader
        import io

        reader = PdfReader(io.BytesIO(content))
        if getattr(reader, "is_encrypted", False):
            return ExtractionStatus.ENCRYPTED, [], "pdf is encrypted"
        page_count = len(reader.pages)
        if page_count > MAX_PDF_PAGES:
            return (ExtractionStatus.FAILED, [],
                    f"pdf exceeds page bound ({page_count})")
        segments = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                segments.append(Segment({"type": "page", "page": index}, text))
        if not segments:
            return (ExtractionStatus.NO_TEXT, [],
                    "no extractable text (possibly scanned images)")
        return ExtractionStatus.OK, segments, None

    def _extract_docx(self, content: bytes):
        import io

        import docx
        document = docx.Document(io.BytesIO(content))
        segments: list[Segment] = []
        for index, paragraph in enumerate(document.paragraphs):
            if paragraph.text.strip():
                segments.append(Segment(
                    {"type": "paragraph", "index": index}, paragraph.text))
        for t_index, table in enumerate(document.tables):
            rows_text = ["\t".join(cell.text for cell in row.cells)
                         for row in table.rows]
            segments.append(Segment({"type": "table", "table": t_index},
                                    "\n".join(rows_text)))
        return ExtractionStatus.OK, segments, None

    def _extract_xlsx(self, content: bytes):
        import io

        import openpyxl
        workbook = openpyxl.load_workbook(
            io.BytesIO(content), data_only=False, read_only=True)
        segments: list[Segment] = []
        for sheet in workbook.worksheets:
            lines = [f"# sheet: {sheet.title}"]
            formulas = []
            for row in sheet.iter_rows():
                cells = []
                for cell in row:
                    value = cell.value
                    if value is None:
                        continue
                    if isinstance(value, str) and value.startswith("="):
                        formulas.append(f"{cell.coordinate}={value}")
                        cells.append(value)
                    else:
                        cells.append(str(value))
                if cells:
                    lines.append("\t".join(cells))
            provenance = f"formulas_preserved={len(formulas)}"
            segments.append(Segment({"type": "sheet", "sheet": sheet.title,
                                     "provenance": provenance},
                                    "\n".join(lines)))
        return ExtractionStatus.OK, segments, None

    def _extract_pptx(self, content: bytes):
        import io

        from pptx import Presentation
        presentation = Presentation(io.BytesIO(content))
        segments: list[Segment] = []
        for number, slide in enumerate(presentation.slides, start=1):
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text_frame.text.strip():
                    texts.append(shape.text_frame.text.strip())
            if texts:
                segments.append(Segment({"type": "slide", "slide": number},
                                        "\n".join(texts)))
        return ExtractionStatus.OK, segments, None

    def _extract_docx_guarded(self, content):
        return self._extract_docx(content)

    def _extract_xlsx_guarded(self, content):
        return self._extract_xlsx(content)

    def _extract_pptx_guarded(self, content):
        return self._extract_pptx(content)

    # ------------------------------------------------------------------
    # lexical search (FTS5 rebuildable projection)

    def rebuild_index(self, project_id: str) -> int:
        with self.store._connect() as connection:
            connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS research_fts USING "
                "fts5(text, project_id UNINDEXED, session_id UNINDEXED, "
                "source_kind UNINDEXED, segment_ref UNINDEXED)")
            connection.execute("DELETE FROM research_fts WHERE project_id = ?",
                               (project_id,))
            rows = connection.execute(
                """SELECT seg.segment_id AS sid, seg.text AS text,
                          ext.project_id AS pid, src.session_id AS sess,
                          src.source_kind AS kind
                   FROM research_extracted_segments seg
                   JOIN research_extractions ext ON ext.extraction_id = seg.extraction_id
                   JOIN research_source_snapshots snap ON snap.snapshot_id = ext.snapshot_id
                   JOIN research_sources src ON src.source_id = snap.source_id
                   WHERE ext.project_id = ? AND ext.status = 'ok'""",
                (project_id,)).fetchall()
            count = 0
            for row in rows:
                connection.execute(
                    "INSERT INTO research_fts (text, project_id, session_id,"
                    " source_kind, segment_ref) VALUES (?, ?, ?, ?, ?)",
                    (row["text"], row["pid"], row["sess"], row["kind"],
                     row["sid"]))
                count += 1
        return count

    def search(self, query: str, *, project_id: str,
               session_id: str | None = None,
               source_kind: str | None = None,
               limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        results = []
        with self.store._connect() as connection:
            try:
                cursor = connection.execute(
                    """SELECT segment_ref, snippet(research_fts, 0, '[', ']', '…', 12)
                         FROM research_fts
                        WHERE research_fts MATCH ? AND project_id = ?
                          AND (? IS NULL OR session_id = ?)
                          AND (? IS NULL OR source_kind = ?)
                        LIMIT ?""",
                    (query, project_id, session_id, session_id,
                     source_kind, source_kind, limit))
                matches = cursor.fetchall()
            except sqlite3.OperationalError:
                return []
            for row in matches:
                segment = connection.execute(
                    "SELECT * FROM research_extracted_segments WHERE segment_id = ?",
                    (row["segment_ref"],)).fetchone()
                if segment is None:
                    continue
                results.append({
                    "segment_ref": row["segment_ref"],
                    "snippet": row[1],
                    "excerpt": segment["text"][:MAX_EVIDENCE_CHARS],
                })
        return results

    def evidence_from_search_hit(self, hit: dict[str, Any], *,
                                 project_id: str) -> dict[str, Any]:
        """Turn a search hit into validated extractive evidence."""
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_extracted_segments WHERE segment_id = ?",
                (hit["segment_ref"],)).fetchone()
        if row is None:
            raise ResearchError("stale/corrupt search result rejected")
        segment_text = row["text"]
        excerpt = hit.get("excerpt") or ""
        start = segment_text.find(excerpt[:120]) if excerpt else -1
        if start < 0:
            raise ResearchError("stale/corrupt search result rejected")
        locator = json.loads(row["locator_json"])
        locator.update({"start": start, "end": start + len(excerpt)})
        snapshot_id = connection_snapshot_of_segment(self.store, hit["segment_ref"])
        return self.store.add_evidence(
            snapshot_id, project_id=project_id, locator=locator, excerpt=excerpt)


def connection_snapshot_of_segment(store: ResearchStore, segment_id: str) -> str:
    with store._connect() as connection:
        row = connection.execute(
            "SELECT snapshot_id FROM research_extractions WHERE extraction_id = "
            "(SELECT extraction_id FROM research_extracted_segments WHERE segment_id = ?)",
            (segment_id,)).fetchone()
    if row is None:
        raise ResearchError("search result references missing extraction")
    return row["snapshot_id"]


def _html_to_text(content: str) -> str:
    from html.parser import HTMLParser

    class TextExtractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.pieces: list[str] = []

        def handle_data(self, data: str) -> None:
            if data.strip():
                self.pieces.append(data.strip())

    parser = TextExtractor()
    parser.feed(content)
    return "\n".join(parser.pieces)


def _content_type(origin: str) -> str | None:
    mapping = {
        ".txt": "text/plain", ".md": "text/markdown", ".json": "application/json",
        ".html": "text/html", ".htm": "text/html", ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    return mapping.get(Path(origin).suffix.lower())


ResearchIngestor._OFFICE_EXTRACTORS = {
    ".docx": ResearchIngestor._extract_docx,
    ".xlsx": ResearchIngestor._extract_xlsx,
    ".xlsm": ResearchIngestor._extract_xlsx,
    ".pptx": ResearchIngestor._extract_pptx,
}
