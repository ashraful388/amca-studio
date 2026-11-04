"""AMCA Studio v0.8.0 Knowledge FTS5 Retrieval Foundation (AG-AMCA-STUDIO-V0.8.0).

Provides native SQLite FTS5 lexical indexation, synchronization, search,
and air-gapped security enforcement for AMCA Studio artifacts.
Strictly local-first, zero cloud dependencies, workspace-isolated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import sqlite3
from typing import Any, Sequence


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_-]{10,}", re.IGNORECASE),
    re.compile(r"ghp_[a-zA-Z0-9]{10,}", re.IGNORECASE),
    re.compile(r"xoxb-[a-zA-Z0-9_-]{10,}", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(password|secret|api_key|token)\s*[:=]\s*['\"]?[^\s'\",]{8,}['\"]?"),
]


def sanitize_knowledge_text(text: str) -> str:
    """Redacts known sensitive patterns and credentials before indexation."""
    sanitized = str(text)
    for pat in SECRET_PATTERNS:
        sanitized = pat.sub("[REDACTED_SECRET]", sanitized)
    return sanitized


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    artifact_id: str
    workspace_id: str
    title: str
    snippet: str
    ranking_score: float
    provenance_reference: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "workspace_id": self.workspace_id,
            "title": self.title,
            "snippet": self.snippet,
            "ranking_score": self.ranking_score,
            "provenance_reference": self.provenance_reference,
        }


class KnowledgeFtsStore:
    """Native SQLite FTS5 lexical search engine partitioned by workspace."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS artifacts_fts5_idx USING fts5(
                    artifact_id UNINDEXED,
                    workspace_id UNINDEXED,
                    title,
                    content,
                    provenance_source,
                    tokenize = 'porter unicode61'
                );
                """
            )

    def index_artifact(
        self,
        *,
        artifact_id: str,
        workspace_id: str,
        title: str,
        content: str,
        provenance_source: str = "user",
    ) -> None:
        """Indexes or updates an artifact with pre-index secret redaction."""
        safe_title = sanitize_knowledge_text(title)
        safe_content = sanitize_knowledge_text(content)
        safe_source = sanitize_knowledge_text(provenance_source)

        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                "DELETE FROM artifacts_fts5_idx WHERE artifact_id = ? AND workspace_id = ?",
                (str(artifact_id).strip(), str(workspace_id).strip()),
            )
            db.execute(
                """
                INSERT INTO artifacts_fts5_idx (artifact_id, workspace_id, title, content, provenance_source)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(artifact_id).strip(),
                    str(workspace_id).strip(),
                    safe_title,
                    safe_content,
                    safe_source,
                ),
            )

    def remove_artifact(self, *, artifact_id: str, workspace_id: str) -> bool:
        """Removes an indexed artifact from the FTS5 table."""
        with sqlite3.connect(self.path, timeout=10) as db:
            cursor = db.execute(
                "DELETE FROM artifacts_fts5_idx WHERE artifact_id = ? AND workspace_id = ?",
                (str(artifact_id).strip(), str(workspace_id).strip()),
            )
            return cursor.rowcount > 0

    def search(
        self,
        *,
        workspace_id: str,
        query: str,
        limit: int = 20,
    ) -> list[KnowledgeSearchResult]:
        """Performs workspace-isolated FTS5 BM25 lexical search."""
        clean_query = str(query).strip()
        if not clean_query:
            return []

        tokens = [re.sub(r"[^\w\-]", "", t) for t in clean_query.split() if re.sub(r"[^\w\-]", "", t)]
        if not tokens:
            return []
        fts_query = " ".join(f'"{t}"' for t in tokens)

        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT 
                    artifact_id,
                    workspace_id,
                    title,
                    snippet(artifacts_fts5_idx, 3, '<b>', '</b>', '...', 16) AS snip,
                    bm25(artifacts_fts5_idx) AS score,
                    provenance_source
                FROM artifacts_fts5_idx
                WHERE workspace_id = ? AND artifacts_fts5_idx MATCH ?
                ORDER BY score ASC
                LIMIT ?
                """,
                (str(workspace_id).strip(), fts_query, max(1, limit)),
            ).fetchall()

        results: list[KnowledgeSearchResult] = []
        for r in rows:
            results.append(
                KnowledgeSearchResult(
                    artifact_id=str(r["artifact_id"]),
                    workspace_id=str(r["workspace_id"]),
                    title=str(r["title"]),
                    snippet=str(r["snip"]),
                    ranking_score=float(r["score"]),
                    provenance_reference=str(r["provenance_source"]),
                )
            )
        return results

