"""Knowledge Intelligence and Asset Management Foundation (AG-AMCA-STUDIO-BETA-B12).

Provides structured knowledge asset cataloging, collections/tags organization,
rigorous provenance tracking, and safety sharing boundaries for AMCA Studio.
Zero external cloud marketplace dependencies; strictly local SQLite storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class KnowledgeProvenance:
    originating_task_id: str
    generating_agent_id: str
    model_decision_id: str
    workflow_id: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "originating_task_id": self.originating_task_id,
            "generating_agent_id": self.generating_agent_id,
            "model_decision_id": self.model_decision_id,
            "workflow_id": self.workflow_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeAssetRecord:
    asset_id: str
    workspace_id: str
    title: str
    asset_type: str
    source_ref: str
    collections: tuple[str, ...]
    tags: tuple[str, ...]
    is_shared: bool
    provenance: KnowledgeProvenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "workspace_id": self.workspace_id,
            "title": self.title,
            "asset_type": self.asset_type,
            "source_ref": self.source_ref,
            "collections": list(self.collections),
            "tags": list(self.tags),
            "is_shared": self.is_shared,
            "provenance": self.provenance.to_dict(),
        }


def validate_knowledge_sharing_safety(
    asset_type: str,
    title: str,
    source_ref: str,
    is_shared: bool,
) -> tuple[bool, str]:
    """Validates that sensitive content (credentials, private memories) cannot be shared across workspaces."""
    if not is_shared:
        return True, "Private asset; sharing restrictions do not apply."

    t = asset_type.casefold().strip()
    s = (title + " " + source_ref).casefold()

    if t in ("credential", "secret", "private_key") or any(k in s for k in ("secret", "key", "token", "password", "sk-", "ghp-")):
        return False, "Assets containing or referencing secrets/credentials cannot be shared across workspaces."

    if t == "memory" and "private" in s:
        return False, "Private episodic/preference memories cannot be shared."

    return True, "Asset passed sharing safety verification."


class KnowledgeAssetStore:
    """Project/workspace-partitioned SQLite store for knowledge asset metadata and provenance."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_assets (
                    asset_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    is_shared INTEGER NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_k_asset_ws ON knowledge_assets(workspace_id);"
            )

    def save_asset(self, record: KnowledgeAssetRecord) -> None:
        safe, reason = validate_knowledge_sharing_safety(
            record.asset_type,
            record.title,
            record.source_ref,
            record.is_shared,
        )
        if not safe:
            raise ValueError(f"Knowledge sharing safety violation: {reason}")

        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO knowledge_assets VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.asset_id,
                    record.workspace_id,
                    record.title,
                    record.asset_type,
                    1 if record.is_shared else 0,
                    json.dumps(record.to_dict()),
                    record.provenance.created_at or _now(),
                ),
            )

    def list_assets(
        self,
        workspace_id: str,
        *,
        tag: str | None = None,
        collection: str | None = None,
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM knowledge_assets WHERE workspace_id=? ORDER BY created_at DESC",
                (workspace_id,),
            ).fetchall()

        results: list[dict[str, Any]] = []
        for r in rows:
            data = json.loads(str(r["record_json"]))
            if tag and tag not in data.get("tags", []):
                continue
            if collection and collection not in data.get("collections", []):
                continue
            results.append(data)
        return results

