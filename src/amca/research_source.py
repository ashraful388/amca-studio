"""R1.1 Research source/evidence foundation.

Canonical provenance-safe substrate for AMCA Studio research:

    ResearchSession -> ResearchSource -> SourceSnapshot
        -> EvidenceChunk -> CitationReference

Truth rules frozen for R1.1:
- SOURCE != SOURCE SNAPSHOT: sources are mutable identities; snapshots are
  immutable content captures and evidence/citations pin exact snapshots;
- extractive evidence only: a chunk must appear verbatim in its snapshot's
  authoritative content or creation is rejected;
- citations never silently retarget newer snapshots;
- freshness fields are data (observed/retrieved/published/modified), never
  subjective scores; no truth/trust scores are produced;
- zero network access; local-file origins are project-contained (traversal,
  absolute paths and sensitive files are rejected); credentials are stripped
  from persisted provenance;
- everything lives in the canonical amca.db beside the other tables.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

MAX_INLINE_CONTENT_BYTES = 64 * 1024
MAX_METADATA_JSON_CHARS = 4000
MAX_QUERY_LENGTH = 512
MAX_EVIDENCE_CHARS = 8000

SENSITIVE_PATTERNS = (
    ".env", "credential", "secret", "token", ".pem", ".key", "id_rsa",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid(prefix: str) -> str:
    import uuid
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class ResearchError(ValueError):
    """Rejected research operation (integrity/provenance/scope violation)."""


class SourceKind(str, Enum):
    WEB = "web"
    LOCAL_FILE = "local_file"
    ARTIFACT = "artifact"
    CONNECTOR = "connector"
    USER_SUPPLIED = "user_supplied"
    ACADEMIC = "academic"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"


def _strip_credentials(data: Any) -> Any:
    if isinstance(data, dict):
        cleaned = {}
        for key, value in sorted(data.items(), key=lambda item: str(item[0])):
            lowered = str(key).lower()
            if any(t in lowered for t in ("secret", "token", "cookie", "authorization", "credential", "api_key", "apikey", "bearer")):
                continue
            cleaned[str(key)] = _strip_credentials(value)
        return cleaned
    if isinstance(data, list):
        return [_strip_credentials(item) for item in data]
    return data


def normalize_local_origin(origin: str, *, allow_sensitive: bool = False) -> str:
    """Project-contained stable identity for a local file origin."""

    pure = PurePosixPath(origin.replace("\\", "/"))
    parts = []
    for part in pure.parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ResearchError(f"local traversal rejected: {origin!r}")
        parts.append(part)
    if not parts:
        raise ResearchError(f"empty local origin: {origin!r}")
    if pure.is_absolute() or (len(origin) > 1 and origin[1] == ":"):
        raise ResearchError(f"absolute external path rejected: {origin!r}")
    name = PurePosixPath(parts[-1]).name.lower()
    if not allow_sensitive and any(p in name for p in SENSITIVE_PATTERNS):
        raise ResearchError(f"sensitive resource rejected: {origin!r}")
    return "/".join(parts)


class ResearchStore:
    """Durable research graph in the canonical amca.db."""

    def __init__(self, database: Path, *,
                 storage_root: Path | None = None) -> None:
        self.database = Path(database)
        self.storage_root = (
            Path(storage_root) if storage_root
            else self.database.parent / "research-content"
        )
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS research_sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    purpose TEXT,
                    status TEXT NOT NULL,
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_sources (
                    source_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES research_sessions(session_id),
                    project_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    canonical_origin TEXT NOT NULL,
                    title TEXT NOT NULL,
                    publisher TEXT,
                    content_type TEXT,
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_source_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES research_sources(source_id),
                    project_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    retrieved_at TEXT,
                    published_at TEXT,
                    modified_at TEXT,
                    content_digest TEXT NOT NULL,
                    content_type TEXT,
                    byte_length INTEGER NOT NULL,
                    content_reference TEXT,
                    inline_content TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    retrieval_provenance_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL REFERENCES research_source_snapshots(snapshot_id),
                    project_id TEXT NOT NULL,
                    locator_json TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    extraction_method TEXT NOT NULL,
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_citations (
                    citation_id TEXT PRIMARY KEY,
                    evidence_id TEXT NOT NULL REFERENCES research_evidence(evidence_id),
                    source_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    display_label TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_extractions (
                    extraction_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL REFERENCES research_source_snapshots(snapshot_id),
                    project_id TEXT NOT NULL,
                    extractor TEXT NOT NULL,
                    extractor_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    segment_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_search_events (
                    search_event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_id TEXT,
                    provider_id TEXT NOT NULL,
                    provider_version TEXT NOT NULL,
                    query TEXT NOT NULL,
                    options_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_reason TEXT,
                    result_count INTEGER NOT NULL DEFAULT 0,
                    actual_search_cost_usd REAL,
                    tool_request_id TEXT,
                    tool_execution_id TEXT
                );
                CREATE TABLE IF NOT EXISTS research_search_results (
                    search_result_id TEXT PRIMARY KEY,
                    search_event_id TEXT NOT NULL REFERENCES research_search_events(search_event_id),
                    rank INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    snippet TEXT,
                    provider_result_id TEXT,
                    provider_relevance REAL,
                    published_at TEXT,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_extracted_segments (
                    segment_id TEXT PRIMARY KEY,
                    extraction_id TEXT NOT NULL REFERENCES research_extractions(extraction_id),
                    seq INTEGER NOT NULL,
                    locator_json TEXT NOT NULL DEFAULT '{{}}',
                    text TEXT NOT NULL
                );
            """)

    # -- sessions ------------------------------------------------------

    def create_session(self, *, project_id: str, title: str,
                       purpose: str | None = None,
                       provenance: dict[str, Any] | None = None) -> dict[str, Any]:
        session_id = _uuid("rs")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_sessions
                   (session_id, project_id, title, purpose, status,
                    provenance_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'active', ?, ?, ?)""",
                (session_id, project_id, title, purpose,
                 json.dumps(_strip_credentials(provenance or {})), now, now))
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_sessions WHERE session_id = ?",
                (session_id,)).fetchone()
        return dict(row) if row else None

    def complete_session(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE research_sessions SET status='completed', updated_at=? "
                "WHERE session_id = ?", (_now(), session_id))

    def list_sessions(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_sessions WHERE project_id = ? "
                "ORDER BY created_at LIMIT 100", (project_id,)).fetchall()
        return [dict(row) for row in rows]

    # -- sources -------------------------------------------------------

    def create_source(self, session_id: str, *, source_kind: str,
                      canonical_origin: str, title: str,
                      publisher: str | None = None,
                      content_type: str | None = None,
                      provenance: dict[str, Any] | None = None) -> dict[str, Any]:
        session = self.get_session(session_id)
        if session is None:
            raise ResearchError(f"unknown session: {session_id!r}")
        kind = SourceKind(source_kind.lower())
        if kind is SourceKind.LOCAL_FILE:
            canonical_origin = normalize_local_origin(canonical_origin)
        if kind is SourceKind.ARTIFACT and "@" not in canonical_origin:
            raise ResearchError(
                "artifact origin must be artifact_id@version identity")
        clean_provenance = json.dumps(_strip_credentials(provenance or {}))
        source_id = _uuid("src")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_sources
                   (source_id, session_id, project_id, source_kind,
                    canonical_origin, title, publisher, content_type,
                    provenance_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source_id, session_id, session["project_id"],
                 kind.value, canonical_origin, title, publisher,
                 content_type, clean_provenance, _now()))
        return self.get_source(source_id, project_id=session["project_id"])

    def get_source(self, source_id: str, *, project_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_sources WHERE source_id = ? AND project_id = ?",
                (source_id, project_id)).fetchone()
        record = dict(row) if row else None
        if record:
            record["provenance"] = json.loads(record.pop("provenance_json"))
        return record

    def list_sources(self, session_id: str, *, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_sources WHERE session_id = ? AND project_id = ? "
                "ORDER BY created_at LIMIT 500", (session_id, project_id)).fetchall()
        return [dict(row) for row in rows]

    # -- snapshots -----------------------------------------------------

    def _store_content(self, digest: str, content: bytes) -> tuple[str | None, str]:
        if len(content) <= MAX_INLINE_CONTENT_BYTES:
            try:
                return content.decode("utf-8"), ""
            except UnicodeDecodeError:
                pass
        reference_dir = self.storage_root / "snapshots"
        reference_dir.mkdir(parents=True, exist_ok=True)
        reference = reference_dir / f"{digest}.bin"
        reference.write_bytes(content)
        return None, str(reference.relative_to(self.storage_root))

    def add_snapshot(self, source_id: str, *, project_id: str,
                     observed_at: str, retrieved_at: str | None = None,
                     published_at: str | None = None,
                     modified_at: str | None = None,
                     content: bytes, content_type: str | None = None,
                     metadata: dict[str, Any] | None = None,
                     retrieval_provenance: dict[str, Any] | None = None) -> dict[str, Any]:
        source = self.get_source(source_id, project_id=project_id)
        if source is None:
            raise ResearchError(f"unknown source: {source_id!r}")
        metadata_json = json.dumps(_strip_credentials(metadata or {}), sort_keys=True)
        if len(metadata_json) > MAX_METADATA_JSON_CHARS:
            raise ResearchError("snapshot metadata exceeds bound")
        digest = _digest(content)
        inline, reference = self._store_content(digest, content)
        provenance_json = json.dumps(_strip_credentials(retrieval_provenance or {}))
        snapshot_id = _uuid("snap")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_source_snapshots
                   (snapshot_id, source_id, project_id, observed_at, retrieved_at,
                    published_at, modified_at, content_digest, content_type,
                    byte_length, content_reference, inline_content,
                    metadata_json, retrieval_provenance_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_id, source_id, project_id, observed_at, retrieved_at,
                 published_at, modified_at, digest, content_type,
                 len(content), reference or None, inline, metadata_json,
                 provenance_json, _now()))
        return self.get_snapshot(snapshot_id, project_id=project_id)

    def get_snapshot(self, snapshot_id: str, *, project_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_source_snapshots WHERE snapshot_id = ? "
                "AND project_id = ?", (snapshot_id, project_id)).fetchone()
        return self._snapshot_record(row) if row else None

    def list_snapshots(self, source_id: str, *, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_source_snapshots WHERE source_id = ? "
                "AND project_id = ? ORDER BY observed_at", (source_id, project_id)).fetchall()
        return [self._snapshot_record(row) for row in rows]

    def find_duplicate_content(self, source_id: str, *, project_id: str,
                               content: bytes) -> str | None:
        digest = _digest(content)
        for snapshot in self.list_snapshots(source_id, project_id=project_id):
            if snapshot["content_digest"] == digest:
                return snapshot["snapshot_id"]
        return None

    def snapshot_bytes(self, snapshot_row: dict[str, Any]) -> bytes:
        """Byte-accurate authoritative content for an immutable snapshot."""

        if snapshot_row.get("inline_content") is not None:
            return str(snapshot_row["inline_content"]).encode("utf-8")
        reference = snapshot_row.get("content_reference")
        if reference:
            return (self.storage_root / reference).read_bytes()
        return b""

    def snapshot_content(self, snapshot_row: dict[str, Any]) -> str:
        if snapshot_row.get("inline_content") is not None:
            return str(snapshot_row["inline_content"])
        reference = snapshot_row.get("content_reference")
        if reference:
            return (self.storage_root / reference).read_text(encoding="utf-8", errors="replace")
        return ""

    @staticmethod
    def _snapshot_record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["metadata"] = json.loads(record.pop("metadata_json"))
        record["retrieval_provenance"] = json.loads(record.pop("retrieval_provenance_json"))
        return record

    # -- evidence ------------------------------------------------------

    def add_evidence(self, snapshot_id: str, *, project_id: str,
                     locator: dict[str, Any], excerpt: str,
                     extraction_method: str = "extractive") -> dict[str, Any]:
        snapshot = self.get_snapshot(snapshot_id, project_id=project_id)
        if snapshot is None:
            raise ResearchError(f"unknown snapshot: {snapshot_id!r}")
        if len(excerpt) > MAX_EVIDENCE_CHARS:
            raise ResearchError("evidence chunk exceeds character bound")
        if extraction_method != "extractive":
            raise ResearchError("R1.1 supports extractive evidence only")
        content = self.snapshot_content(snapshot)
        # Exact-occurrence validation (R1.2 hardening): the locator must
        # identify THE occurrence represented by this evidence - either the
        # caller supplies start/end offsets, which are verified against the
        # authoritative content, or the unique first occurrence is recorded.
        locator = dict(locator or {})
        if "start" in locator and "end" in locator:
            start, end = int(locator["start"]), int(locator["end"])
            if content[start:end] != excerpt:
                raise ResearchError(
                    "locator position does not contain the evidence excerpt")
        else:
            index = content.find(excerpt)
            if index < 0:
                raise ResearchError(
                    "extractive evidence does not match snapshot content")
            locator["start"] = index
            locator["end"] = index + len(excerpt)
        locator_json = json.dumps(locator, sort_keys=True)
        if len(locator_json) > 1000:
            raise ResearchError("locator exceeds bound")
        evidence_id = _uuid("ev")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_evidence
                   (evidence_id, snapshot_id, project_id, locator_json,
                    excerpt, content_digest, extraction_method,
                    provenance_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?)""",
                (evidence_id, snapshot_id, project_id,
                 locator_json, excerpt,
                 hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                 extraction_method, _now()))
        return self.get_evidence(evidence_id, project_id=project_id)

    def get_evidence(self, evidence_id: str, *, project_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_evidence WHERE evidence_id = ? "
                "AND project_id = ?", (evidence_id, project_id)).fetchone()
        return self._evidence_record(row) if row else None

    def list_evidence(self, snapshot_id: str, *, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_evidence WHERE snapshot_id = ? "
                "AND project_id = ? ORDER BY created_at LIMIT 200",
                (snapshot_id, project_id)).fetchall()
        return [self._evidence_record(row) for row in rows]

    @staticmethod
    def _evidence_record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["locator"] = json.loads(record.pop("locator_json"))
        return record

    # -- search events -------------------------------------------------

    def record_search_event(self, *, project_id: str,
                            session_id: str | None, provider_id: str,
                            provider_version: str, query: str,
                            options: dict[str, Any], started_at: str,
                            completed_at: str, status: str,
                            results: list[dict[str, Any]],
                            error_reason: str | None = None,
                            tool_request_id: str | None = None,
                            tool_execution_id: str | None = None) -> dict[str, Any]:
        """Persist one normalized provider-search observation (bounded)."""

        event_id = _uuid("sev")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_search_events
                   (search_event_id, project_id, session_id, provider_id,
                    provider_version, query, options_json, started_at,
                    completed_at, status, error_reason, result_count,
                    actual_search_cost_usd, tool_request_id, tool_execution_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                (event_id, project_id, session_id, provider_id,
                 provider_version, query[:MAX_QUERY_LENGTH],
                 json.dumps(_strip_credentials(options), sort_keys=True),
                 started_at, completed_at, status, error_reason,
                 len(results), tool_request_id, tool_execution_id))
            for index, item in enumerate(results[:50]):
                connection.execute(
                    """INSERT INTO research_search_results
                       (search_result_id, search_event_id, rank, title, url,
                        snippet, provider_result_id, provider_relevance,
                        published_at, observed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (_uuid("sres"), event_id, item.get("rank", index + 1),
                     str(item.get("title", ""))[:300],
                     str(item.get("url", ""))[:600],
                     str(item.get("snippet", ""))[:400],
                     item.get("provider_result_id"),
                     item.get("provider_relevance"),
                     item.get("published_at"), item.get("observed_at", now)))
        return self.get_search_event(event_id)

    def get_search_event(self, search_event_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_search_events WHERE search_event_id = ?",
                (search_event_id,)).fetchone()
        return dict(row) if row else None

    def get_search_result(self, search_result_id: str, *,
                          project_id: str | None = None) -> dict[str, Any] | None:
        """The durable normalized result observation (authoritative fetch
        identity for R2.1: its stored URL is THE fetch target).

        When project_id is given, results observed under a DIFFERENT project
        are invisible - cross-project fetch identities do not exist."""
        with self._connect() as connection:
            if project_id is None:
                row = connection.execute(
                    "SELECT * FROM research_search_results "
                    "WHERE search_result_id = ?",
                    (search_result_id,)).fetchone()
            else:
                row = connection.execute(
                    """SELECT r.* FROM research_search_results r
                       JOIN research_search_events e
                         ON e.search_event_id = r.search_event_id
                       WHERE r.search_result_id = ? AND e.project_id = ?""",
                    (search_result_id, project_id)).fetchone()
        return dict(row) if row else None

    def list_search_events(self, project_id: str,
                           limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_search_events WHERE project_id = ? "
                "ORDER BY started_at DESC LIMIT ?", (project_id, int(limit))).fetchall()
        return [dict(row) for row in rows]

    # -- citations -----------------------------------------------------

    def add_citation(self, evidence_id: str, *, project_id: str,
                     display_label: str) -> dict[str, Any]:
        evidence = self.get_evidence(evidence_id, project_id=project_id)
        if evidence is None:
            raise ResearchError(f"unknown evidence: {evidence_id!r}")
        snapshot = self.get_snapshot(evidence["snapshot_id"], project_id=project_id)
        if snapshot is None:
            raise ResearchError("citation integrity error: missing snapshot")
        citation_id = _uuid("cite")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_citations
                   (citation_id, evidence_id, source_id, snapshot_id,
                    project_id, display_label, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (citation_id, evidence_id, snapshot["source_id"],
                 evidence["snapshot_id"], project_id, display_label[:120],
                 _now()))
        return self.get_citation(citation_id, project_id=project_id)

    def get_citation(self, citation_id: str, *, project_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_citations WHERE citation_id = ? "
                "AND project_id = ?", (citation_id, project_id)).fetchone()
        return dict(row) if row else None

    def list_citations(self, session_id: str, *, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT c.* FROM research_citations c
                   JOIN research_evidence e ON e.evidence_id = c.evidence_id
                   JOIN research_source_snapshots s ON s.snapshot_id = e.snapshot_id
                   JOIN research_sources src ON src.source_id = s.source_id
                   WHERE src.session_id = ? AND c.project_id = ?
                   ORDER BY c.created_at LIMIT 200""",
                (session_id, project_id)).fetchall()
        return [dict(row) for row in rows]
