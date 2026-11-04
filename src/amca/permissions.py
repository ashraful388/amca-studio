"""Durable, exact-scope user approvals for consequential AMCA actions."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .security import assert_no_sensitive_keys


class PermissionAction(str, Enum):
    READ_ASSET = "read_asset"
    WRITE_ARTIFACT = "write_artifact"
    RUN_CODE = "run_code"
    CALL_CLOUD_MODEL = "call_cloud_model"
    TRANSMIT_EXCERPT = "transmit_excerpt"
    NETWORK_REQUEST = "network_request"
    DELETE = "delete"
    IRREVERSIBLE = "irreversible"
    LOCAL_FILE_MODIFY = "local_file_modify"
    LOCAL_FILE_MOVE = "local_file_move"
    LOCAL_FILE_TRASH = "local_file_trash"
    RUN_COMMAND = "run_command"
    CONTROL_APPLICATION = "control_application"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _scope_digest(action: PermissionAction, scope: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _canonical({"action": action.value, "scope": dict(scope)}).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    id: str
    project_id: str
    action: PermissionAction
    scope: Mapping[str, Any]
    scope_sha256: str
    risk: str
    reason: str
    requested_by: str
    status: ApprovalStatus
    created_at: str
    expires_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    max_uses: int = 1
    uses: int = 0
    consumed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "action": self.action.value,
            "scope": dict(self.scope),
            "scope_sha256": self.scope_sha256,
            "risk": self.risk,
            "reason": self.reason,
            "requested_by": self.requested_by,
            "status": self.status.value,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "decision_reason": self.decision_reason,
            "max_uses": self.max_uses,
            "uses": self.uses,
            "consumed_at": self.consumed_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ApprovalRequest":
        scope = json.loads(row["scope_json"])
        if not isinstance(scope, dict):
            raise ValueError("persisted approval scope is invalid")
        return cls(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            action=PermissionAction(row["action"]),
            scope=scope,
            scope_sha256=str(row["scope_sha256"]),
            risk=str(row["risk"]),
            reason=str(row["reason"]),
            requested_by=str(row["requested_by"]),
            status=ApprovalStatus(row["status"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            decided_at=row["decided_at"],
            decided_by=row["decided_by"],
            decision_reason=row["decision_reason"],
            max_uses=int(row["max_uses"]),
            uses=int(row["uses"]),
            consumed_at=row["consumed_at"],
        )


class PermissionStore:
    """SQLite-backed one-time approvals, safe to share with the state database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=5,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_requests (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    scope_sha256 TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    decision_reason TEXT,
                    max_uses INTEGER NOT NULL,
                    uses INTEGER NOT NULL DEFAULT 0,
                    consumed_at TEXT
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_approval_project_status "
                "ON approval_requests(project_id, status, created_at)"
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "PermissionStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def request(
        self,
        *,
        project_id: str,
        action: PermissionAction | str,
        scope: Mapping[str, Any],
        reason: str,
        risk: str = "medium",
        requested_by: str = "amca",
        ttl_seconds: float = 900,
        max_uses: int = 1,
        request_id: str | None = None,
    ) -> ApprovalRequest:
        resolved_action = PermissionAction(action)
        if not project_id.strip() or len(project_id) > 128:
            raise ValueError("approval project_id is invalid")
        if not reason.strip() or len(reason) > 2_000:
            raise ValueError("approval reason is required and bounded")
        if risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("approval risk must be low, medium, high, or critical")
        if not math.isfinite(float(ttl_seconds)) or not 1 <= ttl_seconds <= 86_400:
            raise ValueError("approval ttl must be between 1 second and 24 hours")
        if not 1 <= max_uses <= 100:
            raise ValueError("approval max_uses must be between 1 and 100")
        scope_value = dict(scope)
        assert_no_sensitive_keys(scope_value, path="approval.scope")
        created = _now()
        request = ApprovalRequest(
            id=request_id or uuid4().hex,
            project_id=project_id,
            action=resolved_action,
            scope=scope_value,
            scope_sha256=_scope_digest(resolved_action, scope_value),
            risk=risk,
            reason=reason.strip(),
            requested_by=requested_by,
            status=ApprovalStatus.PENDING,
            created_at=_timestamp(created),
            expires_at=_timestamp(created + timedelta(seconds=float(ttl_seconds))),
            max_uses=max_uses,
        )
        document = request.to_dict()
        assert_no_sensitive_keys(document, path="approval")
        with self._lock, self._connection:
            project_exists = False
            # A standalone chat is addressed as ``conv-<conversation_id>``, the
            # same identity LocalPermissionStore resolves. It was missing here,
            # so an approval raised from a chat with no Studio Project failed
            # with "approval project does not exist" - which is only reachable
            # now that such a chat can hold a machine-wide grant. As in the
            # local store, a prefixed id only counts when the underlying row
            # really exists, and an exact row always wins.
            candidates: list[tuple[str, str, str]] = [
                ("projects", "project_id", project_id),
                ("studio_projects", "project_id", project_id),
                ("desktop_conversations", "conversation_id", project_id),
            ]
            for prefix in ("conv-", "desktop-"):
                if project_id.startswith(prefix) and len(project_id) > len(prefix):
                    candidates.append(
                        ("desktop_conversations", "conversation_id", project_id[len(prefix):])
                    )
            for table, column, value in candidates:
                table_exists = self._connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if table_exists and self._connection.execute(
                    f"SELECT 1 FROM {table} WHERE {column} = ?", (value,)
                ).fetchone():
                    project_exists = True
                    break
            if not project_exists:
                raise ValueError("approval project does not exist")
            self._connection.execute(
                """
                INSERT INTO approval_requests (
                    id, project_id, action, scope_json, scope_sha256, risk,
                    reason, requested_by, status, created_at, expires_at,
                    max_uses, uses
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    request.id,
                    request.project_id,
                    request.action.value,
                    _canonical(request.scope),
                    request.scope_sha256,
                    request.risk,
                    request.reason,
                    request.requested_by,
                    request.status.value,
                    request.created_at,
                    request.expires_at,
                    request.max_uses,
                ),
            )
        return request

    def get(self, request_id: str) -> ApprovalRequest | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
        return ApprovalRequest.from_row(row) if row is not None else None

    def list(
        self,
        *,
        project_id: str | None = None,
        status: ApprovalStatus | str | None = None,
        limit: int = 200,
    ) -> tuple[ApprovalRequest, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("approval list limit must be between 1 and 1000")
        clauses: list[str] = []
        parameters: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = ?")
            parameters.append(project_id)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(ApprovalStatus(status).value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM approval_requests"
                + where
                + " ORDER BY created_at DESC LIMIT ?",
                (*parameters, limit),
            ).fetchall()
        return tuple(ApprovalRequest.from_row(row) for row in rows)

    def decide(
        self,
        request_id: str,
        *,
        approve: bool,
        decided_by: str = "user",
        reason: str | None = None,
    ) -> ApprovalRequest:
        now = _now()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise ValueError("approval request not found")
            request = ApprovalRequest.from_row(row)
            if request.status is not ApprovalStatus.PENDING:
                raise ValueError("approval request is no longer pending")
            if now >= _parse(request.expires_at):
                self._connection.execute(
                    "UPDATE approval_requests SET status = ? WHERE id = ?",
                    (ApprovalStatus.EXPIRED.value, request_id),
                )
                raise ValueError("approval request expired")
            status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
            self._connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, decided_at = ?, decided_by = ?, decision_reason = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    _timestamp(now),
                    decided_by,
                    reason[:1000] if reason else None,
                    request_id,
                    ApprovalStatus.PENDING.value,
                ),
            )
        resolved = self.get(request_id)
        assert resolved is not None
        return resolved

    def authorize(
        self,
        request_id: str,
        *,
        project_id: str,
        action: PermissionAction | str,
        scope: Mapping[str, Any],
    ) -> ApprovalRequest:
        resolved_action = PermissionAction(action)
        scope_value = dict(scope)
        assert_no_sensitive_keys(scope_value, path="authorization.scope")
        digest = _scope_digest(resolved_action, scope_value)
        now = _now()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise PermissionError("approval request not found")
            request = ApprovalRequest.from_row(row)
            if (
                request.project_id != project_id
                or request.action is not resolved_action
                or request.scope_sha256 != digest
            ):
                raise PermissionError("approval scope does not exactly match the action")
            if request.status is not ApprovalStatus.APPROVED:
                raise PermissionError("approval is not active")
            if now >= _parse(request.expires_at):
                self._connection.execute(
                    "UPDATE approval_requests SET status = ? WHERE id = ?",
                    (ApprovalStatus.EXPIRED.value, request_id),
                )
                raise PermissionError("approval expired")
            uses = request.uses + 1
            status = (
                ApprovalStatus.CONSUMED
                if uses >= request.max_uses
                else ApprovalStatus.APPROVED
            )
            consumed_at = _timestamp(now) if status is ApprovalStatus.CONSUMED else None
            updated = self._connection.execute(
                """
                UPDATE approval_requests
                SET uses = ?, status = ?, consumed_at = ?
                WHERE id = ? AND status = ? AND uses = ?
                """,
                (
                    uses,
                    status.value,
                    consumed_at,
                    request_id,
                    ApprovalStatus.APPROVED.value,
                    request.uses,
                ),
            )
            if updated.rowcount != 1:
                raise PermissionError("approval was consumed concurrently")
        resolved = self.get(request_id)
        assert resolved is not None
        return resolved


__all__ = [
    "ApprovalRequest",
    "ApprovalStatus",
    "PermissionAction",
    "PermissionStore",
]
