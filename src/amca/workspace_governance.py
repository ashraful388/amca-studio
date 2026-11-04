"""Enterprise Workspace Governance Foundation (AG-AMCA-STUDIO-BETA-B7).

Provides local-first, role-based workspace governance, auditable activity trails,
and cross-project resource safety boundaries for AMCA Studio.
Zero cloud dependencies; strictly offline SQLite-compatible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class WorkspaceRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


ROLE_PERMISSIONS: Mapping[WorkspaceRole, frozenset[str]] = {
    WorkspaceRole.OWNER: frozenset({
        "project:read", "project:write", "project:delete",
        "artifact:read", "artifact:write", "artifact:delete",
        "memory:read", "memory:write", "memory:purge",
        "automation:read", "automation:execute", "automation:manage",
        "models:read", "models:configure", "workspace:manage",
    }),
    WorkspaceRole.ADMIN: frozenset({
        "project:read", "project:write",
        "artifact:read", "artifact:write", "artifact:delete",
        "memory:read", "memory:write", "memory:purge",
        "automation:read", "automation:execute", "automation:manage",
        "models:read", "models:configure",
    }),
    WorkspaceRole.OPERATOR: frozenset({
        "project:read", "project:write",
        "artifact:read", "artifact:write",
        "memory:read", "memory:write",
        "automation:read", "automation:execute",
        "models:read",
    }),
    WorkspaceRole.VIEWER: frozenset({
        "project:read",
        "artifact:read",
        "memory:read",
        "automation:read",
        "models:read",
    }),
}


def evaluate_workspace_permission(role: WorkspaceRole | str, permission: str) -> bool:
    """Checks if a given workspace role holds the specified permission string."""
    if isinstance(role, WorkspaceRole):
        r = role
    else:
        try:
            r = WorkspaceRole(str(role).strip().casefold())
        except ValueError:
            return False
    return permission.strip().casefold() in ROLE_PERMISSIONS.get(r, frozenset())


@dataclass(frozen=True, slots=True)
class WorkspaceAuditRecord:
    audit_id: str
    workspace_id: str
    actor_id: str
    role: str
    action: str
    resource_type: str
    resource_id: str
    timestamp: str
    source: str = "local_studio"

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "role": self.role,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "timestamp": self.timestamp,
            "source": self.source,
        }


class WorkspaceGovernanceStore:
    """Local SQLite store for workspace configurations and append-only audit trails."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_audit_log (
                    audit_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    source TEXT NOT NULL
                );
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_ws ON workspace_audit_log(workspace_id);"
            )

    def log_action(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        role: str,
        action: str,
        resource_type: str,
        resource_id: str,
        source: str = "local_studio",
    ) -> WorkspaceAuditRecord:
        record = WorkspaceAuditRecord(
            audit_id=f"aud-{uuid4().hex[:16]}",
            workspace_id=str(workspace_id).strip(),
            actor_id=str(actor_id).strip(),
            role=str(role).strip(),
            action=str(action).strip(),
            resource_type=str(resource_type).strip(),
            resource_id=str(resource_id).strip(),
            timestamp=_now(),
            source=str(source).strip(),
        )
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT INTO workspace_audit_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.audit_id,
                    record.workspace_id,
                    record.actor_id,
                    record.role,
                    record.action,
                    record.resource_type,
                    record.resource_id,
                    record.timestamp,
                    record.source,
                ),
            )
        return record

    def list_audit_records(self, workspace_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM workspace_audit_log WHERE workspace_id=? ORDER BY timestamp DESC LIMIT ?",
                (workspace_id, int(limit)),
            ).fetchall()
        return [
            {
                "audit_id": str(r["audit_id"]),
                "workspace_id": str(r["workspace_id"]),
                "actor_id": str(r["actor_id"]),
                "role": str(r["role"]),
                "action": str(r["action"]),
                "resource_type": str(r["resource_type"]),
                "resource_id": str(r["resource_id"]),
                "timestamp": str(r["timestamp"]),
                "source": str(r["source"]),
            }
            for r in rows
        ]

