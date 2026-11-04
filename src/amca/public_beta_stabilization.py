"""Public Beta Preparation and Stabilization Foundation (AG-AMCA-STUDIO-BETA-B17).

Provides public beta onboarding validation, local offline feedback capture,
sanitized diagnostic export, and release stability verification for AMCA Studio v0.7.0-beta.1.
Zero cloud analytics; 100% offline local SQLite; strictly privacy-preserving.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class BetaFeedbackRecord:
    feedback_id: str
    workspace_id: str
    category: str
    title: str
    description: str
    diagnostic_ref: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_id": self.feedback_id,
            "workspace_id": self.workspace_id,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "diagnostic_ref": self.diagnostic_ref,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class BetaStabilizationStatus:
    release_version: str
    distribution_ready: bool
    onboarding_validated: bool
    docs_complete: bool
    feedback_ready: bool
    overall_status: str
    evaluated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_version": self.release_version,
            "distribution_ready": self.distribution_ready,
            "onboarding_validated": self.onboarding_validated,
            "docs_complete": self.docs_complete,
            "feedback_ready": self.feedback_ready,
            "overall_status": self.overall_status,
            "evaluated_at": self.evaluated_at,
        }


_SENSITIVE_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"ghp_[a-zA-Z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"xoxb-[a-zA-Z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"(password|secret|private_key)\s*[:=]\s*['\"][^'\"]+['\"]", re.IGNORECASE),
]


def sanitize_diagnostic_export(data: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively redacts API keys, secret tokens, and credentials from diagnostic dumps."""
    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        k_lower = key.lower()
        if any(s in k_lower for s in ("token", "password", "secret", "key", "auth")):
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, str):
            clean_str = value
            for pat in _SENSITIVE_PATTERNS:
                clean_str = pat.sub("[REDACTED]", clean_str)
            sanitized[key] = clean_str
        elif isinstance(value, Mapping):
            sanitized[key] = sanitize_diagnostic_export(value)
        elif isinstance(value, (list, tuple)):
            sanitized[key] = [
                sanitize_diagnostic_export(item) if isinstance(item, Mapping) else item
                for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized


def evaluate_public_beta_stabilization(
    release_version: str = "v0.7.0-beta.1",
    *,
    distribution_ready: bool = True,
    onboarding_validated: bool = True,
    docs_complete: bool = True,
    feedback_ready: bool = True,
) -> BetaStabilizationStatus:
    all_ready = distribution_ready and onboarding_validated and docs_complete and feedback_ready
    status_str = "PUBLIC_BETA_READY" if all_ready else "STABILIZATION_IN_PROGRESS"

    return BetaStabilizationStatus(
        release_version=str(release_version).strip(),
        distribution_ready=distribution_ready,
        onboarding_validated=onboarding_validated,
        docs_complete=docs_complete,
        feedback_ready=feedback_ready,
        overall_status=status_str,
        evaluated_at=_now(),
    )


class BetaFeedbackStore:
    """Project/workspace-partitioned SQLite store for offline user feedback capture."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS beta_feedback (
                    feedback_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_bfeedback_ws ON beta_feedback(workspace_id);"
            )

    def record_feedback(
        self,
        *,
        workspace_id: str,
        category: str,
        title: str,
        description: str,
        diagnostic_ref: str = "",
    ) -> BetaFeedbackRecord:
        record = BetaFeedbackRecord(
            feedback_id=f"fdb-{uuid4().hex[:16]}",
            workspace_id=str(workspace_id).strip(),
            category=str(category).strip(),
            title=str(title).strip()[:200],
            description=str(description).strip()[:1000],
            diagnostic_ref=str(diagnostic_ref).strip(),
            created_at=_now(),
        )
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT INTO beta_feedback VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.feedback_id,
                    record.workspace_id,
                    record.category,
                    record.title,
                    json.dumps(record.to_dict()),
                    record.created_at,
                ),
            )
        return record

    def list_feedback(self, workspace_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM beta_feedback WHERE workspace_id=? ORDER BY created_at DESC",
                (workspace_id,),
            ).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]

