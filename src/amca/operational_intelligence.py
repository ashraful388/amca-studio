"""Operational Intelligence and Reporting Foundation (AG-AMCA-STUDIO-BETA-B14).

Provides measurable workspace operational performance indicators, resource awareness,
and exportable auditable health reports for AMCA Studio.
Zero cloud analytics; zero subjective productivity scoring; strictly local SQLite storage.
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
class OperationalMetricsSnapshot:
    workspace_id: str
    completed_workflows: int
    active_projects: int
    agent_executions: int
    local_model_calls: int
    cloud_model_calls: int
    artifact_count: int
    evaluated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "completed_workflows": self.completed_workflows,
            "active_projects": self.active_projects,
            "agent_executions": self.agent_executions,
            "local_model_calls": self.local_model_calls,
            "cloud_model_calls": self.cloud_model_calls,
            "artifact_count": self.artifact_count,
            "evaluated_at": self.evaluated_at,
        }


@dataclass(frozen=True, slots=True)
class OperationalReport:
    report_id: str
    workspace_id: str
    metrics: OperationalMetricsSnapshot
    summary_text: str
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "workspace_id": self.workspace_id,
            "metrics": self.metrics.to_dict(),
            "summary_text": self.summary_text,
            "generated_at": self.generated_at,
        }


def generate_operational_report(
    workspace_id: str,
    *,
    completed_workflows: int = 0,
    active_projects: int = 0,
    agent_executions: int = 0,
    local_model_calls: int = 0,
    cloud_model_calls: int = 0,
    artifact_count: int = 0,
) -> OperationalReport:
    """Derives strictly measurable operational metrics and summary without subjective scoring."""
    now_str = _now()
    metrics = OperationalMetricsSnapshot(
        workspace_id=str(workspace_id).strip(),
        completed_workflows=int(completed_workflows),
        active_projects=int(active_projects),
        agent_executions=int(agent_executions),
        local_model_calls=int(local_model_calls),
        cloud_model_calls=int(cloud_model_calls),
        artifact_count=int(artifact_count),
        evaluated_at=now_str,
    )
    total_calls = local_model_calls + cloud_model_calls
    local_ratio = round(local_model_calls / total_calls, 2) if total_calls > 0 else 1.0

    summary = (
        f"Workspace '{workspace_id}': {completed_workflows} workflows completed across {active_projects} active projects. "
        f"{agent_executions} agent executions recorded with {total_calls} total model invocations "
        f"({int(local_ratio * 100)}% local). {artifact_count} artifacts tracked."
    )

    return OperationalReport(
        report_id=f"oprep-{uuid4().hex[:16]}",
        workspace_id=str(workspace_id).strip(),
        metrics=metrics,
        summary_text=summary,
        generated_at=now_str,
    )


class OperationalIntelligenceStore:
    """Workspace-partitioned SQLite store for persistent operational reports."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS operational_reports (
                    report_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    summary_text TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_oprep_ws ON operational_reports(workspace_id);"
            )

    def save_report(self, report: OperationalReport) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO operational_reports VALUES (?, ?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.workspace_id,
                    report.summary_text,
                    json.dumps(report.to_dict()),
                    report.generated_at,
                ),
            )

    def list_reports(self, workspace_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM operational_reports WHERE workspace_id=? ORDER BY generated_at DESC LIMIT ?",
                (workspace_id, int(limit)),
            ).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]

