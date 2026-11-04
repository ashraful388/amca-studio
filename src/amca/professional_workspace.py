"""Professional Workspace Experience Foundation (AG-AMCA-STUDIO-BETA-B11).

Provides read-only command center aggregation, cross-module navigation trace generation,
and unified activity timeline logging for AMCA Studio.
Zero duplicate state; strictly local-first and privacy-respecting.
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
class WorkspaceOverviewModel:
    workspace_id: str
    project_count: int
    active_agent_count: int
    local_model_count: int
    pending_approval_count: int
    health_status: str
    last_active_timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "project_count": self.project_count,
            "active_agent_count": self.active_agent_count,
            "local_model_count": self.local_model_count,
            "pending_approval_count": self.pending_approval_count,
            "health_status": self.health_status,
            "last_active_timestamp": self.last_active_timestamp,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceActivityItem:
    event_id: str
    workspace_id: str
    event_type: str
    title: str
    description: str
    actor_id: str
    resource_ref: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "workspace_id": self.workspace_id,
            "event_type": self.event_type,
            "title": self.title,
            "description": self.description,
            "actor_id": self.actor_id,
            "resource_ref": self.resource_ref,
            "timestamp": self.timestamp,
        }


def build_cross_module_trace_links(
    *,
    task_id: str,
    agent_id: str = "",
    model_id: str = "",
    artifact_id: str = "",
    memory_id: str = "",
    optimization_id: str = "",
) -> dict[str, str]:
    """Builds lightweight deep-link pointers across Studio modules without duplicating data."""
    links: dict[str, str] = {"task": f"/tasks/{task_id}"}
    if agent_id:
        links["agent"] = f"/agents/{agent_id}"
    if model_id:
        links["model"] = f"/models/{model_id}"
    if artifact_id:
        links["artifact"] = f"/artifacts/{artifact_id}"
    if memory_id:
        links["memory"] = f"/memory/{memory_id}"
    if optimization_id:
        links["optimization"] = f"/optimization/{optimization_id}"
    return links


class WorkspaceActivityTimelineStore:
    """Project/workspace-partitioned SQLite store for unified activity timeline events."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_timeline (
                    event_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    resource_ref TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_timeline_ws ON workspace_timeline(workspace_id);"
            )

    def record_activity(
        self,
        *,
        workspace_id: str,
        event_type: str,
        title: str,
        description: str,
        actor_id: str,
        resource_ref: str,
    ) -> WorkspaceActivityItem:
        item = WorkspaceActivityItem(
            event_id=f"act-{uuid4().hex[:16]}",
            workspace_id=str(workspace_id).strip(),
            event_type=str(event_type).strip(),
            title=str(title).strip()[:200],
            description=str(description).strip()[:500],
            actor_id=str(actor_id).strip(),
            resource_ref=str(resource_ref).strip(),
            timestamp=_now(),
        )
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT INTO workspace_timeline VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.event_id,
                    item.workspace_id,
                    item.event_type,
                    item.title,
                    item.description,
                    item.actor_id,
                    item.resource_ref,
                    item.timestamp,
                ),
            )
        return item

    def list_activities(self, workspace_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM workspace_timeline WHERE workspace_id=? ORDER BY timestamp DESC LIMIT ?",
                (workspace_id, int(limit)),
            ).fetchall()
        return [
            {
                "event_id": str(r["event_id"]),
                "workspace_id": str(r["workspace_id"]),
                "event_type": str(r["event_type"]),
                "title": str(r["title"]),
                "description": str(r["description"]),
                "actor_id": str(r["actor_id"]),
                "resource_ref": str(r["resource_ref"]),
                "timestamp": str(r["timestamp"]),
            }
            for r in rows
        ]

