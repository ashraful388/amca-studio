"""Workflow Optimization Intelligence Foundation (AG-AMCA-STUDIO-BETA-B6).

Provides read-only, empirical performance analysis and evidence-based optimization
recommendations for completed AMCA Studio workflows.
Strictly non-autonomous: analyzes past evidence, suggests improvements, never self-modifies.
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
class OptimizationRecommendation:
    recommendation_id: str
    category: str
    summary: str
    evidence: str
    suggested_action: str
    potential_savings_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "category": self.category,
            "summary": self.summary,
            "evidence": self.evidence,
            "suggested_action": self.suggested_action,
            "potential_savings_usd": self.potential_savings_usd,
        }


@dataclass(frozen=True, slots=True)
class WorkflowAnalysisRecord:
    analysis_id: str
    project_id: str
    workflow_id: str
    total_duration_seconds: float
    step_count: int
    failure_count: int
    local_execution_ratio: float
    bottleneck_step: str
    recommendations: tuple[OptimizationRecommendation, ...]
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "project_id": self.project_id,
            "workflow_id": self.workflow_id,
            "total_duration_seconds": self.total_duration_seconds,
            "step_count": self.step_count,
            "failure_count": self.failure_count,
            "local_execution_ratio": self.local_execution_ratio,
            "bottleneck_step": self.bottleneck_step,
            "recommendations": [r.to_dict() for r in self.recommendations],
            "created_at": self.created_at,
        }


def analyze_workflow_performance(
    project_id: str,
    workflow_id: str,
    steps: Sequence[Mapping[str, Any]],
) -> WorkflowAnalysisRecord:
    """Performs empirical analysis over completed workflow steps and derives recommendations."""
    if not steps:
        return WorkflowAnalysisRecord(
            analysis_id=f"opt-{uuid4().hex[:16]}",
            project_id=str(project_id).strip(),
            workflow_id=str(workflow_id).strip(),
            total_duration_seconds=0.0,
            step_count=0,
            failure_count=0,
            local_execution_ratio=1.0,
            bottleneck_step="none",
            recommendations=(),
            created_at=_now(),
        )

    total_duration = 0.0
    failures = 0
    local_count = 0
    longest_step = "none"
    max_step_duration = -1.0
    cloud_cost_estimated = 0.0

    for s in steps:
        dur = float(s.get("duration_seconds", 0.0))
        total_duration += dur
        if s.get("failed") or s.get("status") == "failed":
            failures += 1
        if s.get("is_local"):
            local_count += 1
        else:
            cloud_cost_estimated += float(s.get("cost_usd", 0.0))

        if dur > max_step_duration:
            max_step_duration = dur
            longest_step = str(s.get("step_id") or s.get("name") or "step")

    step_cnt = len(steps)
    local_ratio = round(local_count / step_cnt, 2)
    recs: list[OptimizationRecommendation] = []

    # Evidence-based recommendation 1: Local execution opportunity
    if local_ratio < 0.5 and cloud_cost_estimated > 0.0:
        recs.append(
            OptimizationRecommendation(
                recommendation_id=f"rec-{uuid4().hex[:12]}",
                category="cost",
                summary="Consider offloading routine bounded tasks to local models",
                evidence=f"Current local execution ratio is only {int(local_ratio * 100)}%, incurring ${cloud_cost_estimated:.3f} cloud cost.",
                suggested_action="Assign Ollama local models to deterministic inspection and formatting steps.",
                potential_savings_usd=round(cloud_cost_estimated * 0.7, 3),
            )
        )

    # Evidence-based recommendation 2: Step latency bottleneck
    if max_step_duration > 15.0 and total_duration > 0.0 and (max_step_duration / total_duration) > 0.4:
        recs.append(
            OptimizationRecommendation(
                recommendation_id=f"rec-{uuid4().hex[:12]}",
                category="latency",
                summary=f"Decompose or stream step '{longest_step}'",
                evidence=f"Step '{longest_step}' consumed {max_step_duration:.1f}s ({int(max_step_duration / total_duration * 100)}% of total execution time).",
                suggested_action="Break down into parallel work units or enable progressive token streaming.",
            )
        )

    # Evidence-based recommendation 3: Step failure retry
    if failures > 0:
        recs.append(
            OptimizationRecommendation(
                recommendation_id=f"rec-{uuid4().hex[:12]}",
                category="reliability",
                summary="Add automatic deterministic retry to fragile steps",
                evidence=f"Observed {failures} failed execution attempts during the workflow.",
                suggested_action="Configure exponential backoff and verify adapter health before execution.",
            )
        )

    return WorkflowAnalysisRecord(
        analysis_id=f"opt-{uuid4().hex[:16]}",
        project_id=str(project_id).strip(),
        workflow_id=str(workflow_id).strip(),
        total_duration_seconds=round(total_duration, 2),
        step_count=step_cnt,
        failure_count=failures,
        local_execution_ratio=local_ratio,
        bottleneck_step=longest_step,
        recommendations=tuple(recs),
        created_at=_now(),
    )


class WorkflowOptimizationStore:
    """Project-partitioned SQLite store for read-only workflow performance analyses."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_analyses (
                    analysis_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    bottleneck_step TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_opt_proj ON workflow_analyses(project_id);"
            )

    def save_analysis(self, record: WorkflowAnalysisRecord) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO workflow_analyses VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.analysis_id,
                    record.project_id,
                    record.workflow_id,
                    record.bottleneck_step,
                    json.dumps(record.to_dict()),
                    record.created_at or _now(),
                ),
            )

    def list_analyses(self, project_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM workflow_analyses WHERE project_id=? ORDER BY created_at ASC",
                (project_id,),
            ).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]

