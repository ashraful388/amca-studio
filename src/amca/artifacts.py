"""Native artifact engine (milestone W1.1).

Contract per ``docs/w11-artifact-contract.md``: ONE engine for every native
output type; each version has exactly one canonical source representation;
versions are immutable append-only snapshots; restoring an old version
creates a NEW version carrying restore provenance; provenance is mandatory;
missing optional converters surface as explicit ``converter_unavailable``
results and never as fabricated output.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


ARTIFACT_TYPES = frozenset({
    "markdown", "docx", "pdf", "tex", "xlsx", "pptx", "html", "bib", "json",
})

#: Canonical source representation per type (v1).  Everything else is a
#: derived rendition produced by an installed converter.
CANONICAL_SOURCE_EXT = {
    "markdown": ".md",
    "tex": ".tex",
    "bib": ".bib",
    "json": ".json",
    "html": ".html",
    "xlsx": ".csv",
    "docx": ".json",
    "pptx": ".json",
    "pdf": ".json",
}

MAX_SOURCE_BYTES = 4 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    title TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    current_version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_versions (
    artifact_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    operation TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    bytes_path TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    provenance TEXT NOT NULL,
    PRIMARY KEY (artifact_id, version)
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ArtifactError(ValueError):
    pass


class ArtifactStore:
    """SQLite metadata + immutable on-disk payloads, local-first."""

    def __init__(self, database: Path, payloads_root: Path) -> None:
        self.database = Path(database)
        self.payloads_root = Path(payloads_root)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.payloads_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # sqlite3's own context manager only manages transactions, never
        # closes; Windows keeps the database locked if we leak handles.
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _payload_path(self, artifact_id: str, version: int, ext: str) -> Path:
        directory = self.payloads_root / artifact_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"v{version}{ext}"

    @staticmethod
    def _validate_source(artifact_type: str, content: str) -> None:
        if not isinstance(artifact_type, str) or artifact_type not in ARTIFACT_TYPES:
            raise ArtifactError(
                f"unsupported artifact type {artifact_type!r}; supported: "
                + ", ".join(sorted(ARTIFACT_TYPES))
            )
        if not isinstance(content, str):
            raise ArtifactError("artifact source must be text")
        if len(content.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ArtifactError("artifact source exceeds the size limit")

    def _write_version(
        self,
        *,
        connection: sqlite3.Connection,
        artifact_id: str,
        project_id: str,
        artifact_type: str,
        title: str,
        content: str,
        operation: str,
        created_by: str,
        provenance: dict[str, Any],
        new_artifact: bool,
    ) -> int:
        try:
            ext = CANONICAL_SOURCE_EXT[artifact_type]
        except KeyError:
            raise ArtifactError(
                f"artifact type {artifact_type!r} has no canonical source representation"
            ) from None
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM artifact_versions WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        next_version = int(row["v"]) + 1
        path = self._payload_path(artifact_id, next_version, ext)
        data = content.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        provenance_document = {
            **provenance,
            "operation": operation,
            "bytes": len(data),
            "sha256": digest,
        }
        if operation == "restore":
            restored = provenance.get("restored_from_version")
            if not isinstance(restored, int):
                raise ArtifactError("restore provenance requires restored_from_version")
            provenance_document["restored_from_version"] = restored
        # Write to a version-unique temp file first and atomically rename only
        # AFTER the metadata INSERT succeeds, so a concurrent losing racer
        # (IntegrityError on the PK) never touches the committed payload path.
        import os as _os
        import uuid as _uuid

        tmp_path = path.with_name(f"{path.name}.tmp-{_uuid.uuid4().hex}")
        tmp_path.write_bytes(data)
        try:
            connection.execute(
                "INSERT INTO artifact_versions (artifact_id, version, operation, "
                "payload_sha256, bytes_path, created_by, created_at, provenance) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact_id,
                    next_version,
                    operation,
                    digest,
                    path.name,
                    created_by,
                    _now(),
                    json.dumps(provenance_document, ensure_ascii=False),
                ),
            )
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        _os.replace(tmp_path, path)
        if new_artifact:
            connection.execute(
                "INSERT INTO artifacts (artifact_id, project_id, artifact_type, "
                "title, created_by, created_at, current_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact_id,
                    project_id,
                    artifact_type,
                    title,
                    created_by,
                    _now(),
                    next_version,
                ),
            )
        else:
            connection.execute(
                "UPDATE artifacts SET current_version = ? WHERE artifact_id = ?",
                (next_version, artifact_id),
            )
        return next_version

    def author(
        self,
        *,
        project_id: str,
        artifact_type: str,
        title: str,
        content: str,
        created_by: str,
        chat_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        self._validate_source(artifact_type, content)
        if not title.strip() or not created_by.strip():
            raise ArtifactError("authoring requires title and creator provenance")
        artifact_id = f"art-{hashlib.sha256((project_id + title + _now()).encode('utf-8')).hexdigest()[:16]}"
        with self._connect() as connection:
            version = self._write_version(
                connection=connection,
                artifact_id=artifact_id,
                project_id=project_id,
                artifact_type=artifact_type,
                title=title.strip(),
                content=content,
                operation="author",
                created_by=created_by.strip(),
                provenance={"chat_id": chat_id, "task_id": task_id},
                new_artifact=True,
            )
        return self.get(artifact_id)

    def list_for_project(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT artifact_id, artifact_type, title, created_by, "
                    "created_at, current_version FROM artifacts "
                    "WHERE project_id = ? ORDER BY created_at DESC",
                    (project_id,),
                ).fetchall()
            ]

    def get(self, artifact_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise ArtifactError("artifact not found")
            versions = [
                dict(item)
                for item in connection.execute(
                    "SELECT version, operation, payload_sha256, created_by, "
                    "created_at, provenance FROM artifact_versions "
                    "WHERE artifact_id = ? ORDER BY version",
                    (artifact_id,),
                ).fetchall()
            ]
        document = dict(row)
        document["versions"] = versions
        return document

    def read_source(self, artifact_id: str, version: int | None = None) -> dict[str, Any]:
        meta = self.get(artifact_id)
        target = int(version) if version is not None else int(meta["current_version"])
        record = next(
            (item for item in meta["versions"] if item["version"] == target), None
        )
        if record is None:
            raise ArtifactError("unknown artifact version")
        try:
            ext = CANONICAL_SOURCE_EXT[str(meta["artifact_type"])]
        except KeyError:
            raise ArtifactError(
                f"artifact type {meta['artifact_type']!r} has no canonical source representation"
            ) from None
        path = self.payloads_root / artifact_id / f"v{target}{ext}"
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ArtifactError("artifact payload missing") from error
        return {"version": target, "content": content, "provenance": json.loads(record["provenance"])}

    def edit(
        self,
        *,
        artifact_id: str,
        content: str,
        created_by: str,
        base_version: int,
        reason: str = "",
    ) -> dict[str, Any]:
        meta = self.get(artifact_id)
        self._validate_source(str(meta["artifact_type"]), content)
        if base_version != int(meta["current_version"]):
            raise ArtifactError(
                f"artifact changed since version {base_version}; "
                f"current version is {meta['current_version']}"
            )
        with self._connect() as connection:
            version = self._write_version(
                connection=connection,
                artifact_id=artifact_id,
                project_id=str(meta["project_id"]),
                artifact_type=str(meta["artifact_type"]),
                title=str(meta["title"]),
                content=content,
                operation="edit",
                created_by=created_by,
                provenance={"base_version": base_version, "reason": reason},
                new_artifact=False,
            )
        return self.get(artifact_id)

    def restore(
        self, *, artifact_id: str, version: int, created_by: str
    ) -> dict[str, Any]:
        source = self.read_source(artifact_id, version)
        meta = self.get(artifact_id)
        if version == int(meta["current_version"]):
            raise ArtifactError("version already matches the current content")
        with self._connect() as connection:
            new_version = self._write_version(
                connection=connection,
                artifact_id=artifact_id,
                project_id=str(meta["project_id"]),
                artifact_type=str(meta["artifact_type"]),
                title=str(meta["title"]),
                content=source["content"],
                operation="restore",
                created_by=created_by,
                provenance={"restored_from_version": version},
                new_artifact=False,
            )
        return self.get(artifact_id)




#: Types a model may author through artifact blocks, mapped to canonical
#: source types.  Binary-first types (docx/pptx/pdf/xlsx) are reached via
#: their canonical sources or renditions instead.
AUTHORABLE_TYPES = {
    # A model may declare the format the user asked for.  docx is stored as the
    # markdown source that the docx export already renders, exactly as csv is
    # stored as the xlsx source.  pdf is deliberately absent: there is no PDF
    # rendering pipeline, and accepting it here would promise a file that
    # cannot be produced.
    "docx": "markdown",
    "markdown": "markdown",
    "md": "markdown",
    "text": "markdown",
    "csv": "xlsx",
    "xlsx": "xlsx",
    "html": "html",
    "tex": "tex",
    "bib": "bib",
    "pptx": "pptx",
}

MAX_BLOCKS_PER_RESPONSE = 6


def strip_artifact_blocks(text: str) -> str:
    """Remove artifact fences from a reply so the chat shows the answer.

    The fences are a machine instruction to AMCA, not prose.  Leaving them in
    rendered the raw ```` ```artifact docx title=... ```` header and its body as
    a code block in the transcript, so the reader saw the transport instead of
    the response.  Extraction still happens separately; this only removes what
    was extracted.
    """
    pattern = re.compile(
        r"^```(?:artifact[ \t]+[A-Za-z0-9]+[ \t]+title=[^\r\n]+|bib)[ \t]*\r?$",
        re.MULTILINE,
    )
    result: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        closing = text.find("\n```", match.end())
        if closing == -1:
            continue
        result.append(text[cursor:match.start()])
        cursor = closing + len("\n```")
        if cursor < len(text) and text[cursor] == "\n":
            cursor += 1
    result.append(text[cursor:])
    return "".join(result).strip()


def parse_artifact_blocks(text: str) -> list[dict[str, str]]:
    """Extract `````artifact <type> title=<title>```` ` fenced blocks.

    Deterministic and tolerant: malformed headers, unknown types, and
    oversized content are skipped (counted by the caller via len compare if
    needed) rather than failing the whole response.  Duplicate titles get
    numeric suffixes so nothing silently overwrites.
    """

    blocks: list[dict[str, str]] = []
    titles_seen: set[str] = set()
    pattern = re.compile(
        r"^```(?:artifact[ \t]+([A-Za-z0-9]+)[ \t]+title=([^\r\n]+)|(?P<bib>bib))[ \t]*\r?$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    for index, match in enumerate(matches):
        if len(blocks) >= MAX_BLOCKS_PER_RESPONSE:
            break
        declared_type = (match.group(1) or "bib").casefold()
        raw_title = (match.group(2) or "Bibliography").strip().strip("\"'")
        artifact_type = AUTHORABLE_TYPES.get(declared_type)
        if artifact_type is None or not raw_title or len(raw_title) > 120:
            continue
        start = match.end()
        closing = text.find("\n```", start)
        if closing == -1:
            continue
        content = text[start:closing].lstrip("\n")
        if not content.strip() or len(content.encode("utf-8")) > MAX_SOURCE_BYTES:
            continue
        title = raw_title
        suffix = 2
        while title in titles_seen:
            title = f"{raw_title}-{suffix}"
            suffix += 1
        titles_seen.add(title)
        blocks.append(
            {"artifact_type": artifact_type, "title": title, "content": content}
        )
    return blocks


__all__ = [
    "AUTHORABLE_TYPES",
    "ARTIFACT_TYPES",
    "ArtifactError",
    "ArtifactStore",
    "parse_artifact_blocks",
]
