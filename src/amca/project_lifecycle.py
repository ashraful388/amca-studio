"""Intelligent Project Lifecycle Management Foundation (AG-AMCA-STUDIO-BETA-B13).

Provides structured project milestone tracking, task decomposition references,
and objective progress metrics without external SaaS servers or duplicate project storage.
Zero subjective scores; strictly measurable completion evidence.
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
class ProjectMilestoneRecord:
    milestone_id: str
    project_id: str
    title: str
    completion_criteria: tuple[str, ...]
    evidence_links: tuple[str, ...]
    is_completed: bool
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "milestone_id": self.milestone_id,
            "project_id": self.project_id,
            "title": self.title,
            "completion_criteria": list(self.completion_criteria),
            "evidence_links": list(self.evidence_links),
            "is_completed": self.is_completed,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True, slots=True)
class ProjectLifecycleProgress:
    project_id: str
    total_milestones: int
    completed_milestones: int
    total_tasks: int
    completed_tasks: int
    artifact_count: int
    workflow_run_count: int
    progress_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "total_milestones": self.total_milestones,
            "completed_milestones": self.completed_milestones,
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "artifact_count": self.artifact_count,
            "workflow_run_count": self.workflow_run_count,
            "progress_ratio": self.progress_ratio,
        }


def calculate_project_lifecycle_progress(
    project_id: str,
    milestones: Sequence[ProjectMilestoneRecord],
    *,
    total_tasks: int = 0,
    completed_tasks: int = 0,
    artifact_count: int = 0,
    workflow_run_count: int = 0,
) -> ProjectLifecycleProgress:
    """Derives strictly measurable project progress counters from verified evidence."""
    total_m = len(milestones)
    comp_m = sum(1 for m in milestones if m.is_completed)

    milestone_factor = (comp_m / total_m) if total_m > 0 else 0.0
    task_factor = (completed_tasks / total_tasks) if total_tasks > 0 else 1.0

    ratio = round(0.7 * milestone_factor + 0.3 * task_factor, 2) if total_m > 0 else (1.0 if completed_tasks == total_tasks else 0.0)

    return ProjectLifecycleProgress(
        project_id=str(project_id).strip(),
        total_milestones=total_m,
        completed_milestones=comp_m,
        total_tasks=int(total_tasks),
        completed_tasks=int(completed_tasks),
        artifact_count=int(artifact_count),
        workflow_run_count=int(workflow_run_count),
        progress_ratio=ratio,
    )


class ProjectLifecycleStore:
    """Project-isolated SQLite store for milestones and lifecycle tracking."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS project_milestones (
                    milestone_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    is_completed INTEGER NOT NULL,
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_milestone_proj ON project_milestones(project_id);"
            )

    def save_milestone(self, record: ProjectMilestoneRecord) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO project_milestones VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.milestone_id,
                    record.project_id,
                    record.title,
                    1 if record.is_completed else 0,
                    json.dumps(record.to_dict()),
                    record.completed_at or _now(),
                ),
            )

    def list_milestones(self, project_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM project_milestones WHERE project_id=? ORDER BY milestone_id ASC",
                (project_id,),
            ).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]

