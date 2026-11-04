"""Beta Feedback Analysis and Refinement Framework Foundation (AG-AMCA-STUDIO-BETA-B19).

Provides structured feedback analysis, deterministic refinement prioritization,
and improvement reporting for AMCA Studio v0.7.0-beta.1.
Zero cloud analytics; zero subjective AI ranking; strictly local SQLite storage.
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


SEVERITY_WEIGHTS = {
    "critical": 1.0,
    "high": 0.75,
    "medium": 0.5,
    "low": 0.25,
}


@dataclass(frozen=True, slots=True)
class FeedbackInsightRecord:
    insight_id: str
    workspace_id: str
    category: str
    title: str
    affected_modules: tuple[str, ...]
    severity: str
    frequency: int
    priority_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "insight_id": self.insight_id,
            "workspace_id": self.workspace_id,
            "category": self.category,
            "title": self.title,
            "affected_modules": list(self.affected_modules),
            "severity": self.severity,
            "frequency": self.frequency,
            "priority_score": self.priority_score,
        }


@dataclass(frozen=True, slots=True)
class BetaImprovementReport:
    report_id: str
    workspace_id: str
    total_feedback_items: int
    category_breakdown: dict[str, int]
    top_refinements: tuple[FeedbackInsightRecord, ...]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "workspace_id": self.workspace_id,
            "total_feedback_items": self.total_feedback_items,
            "category_breakdown": self.category_breakdown,
            "top_refinements": [r.to_dict() for r in self.top_refinements],
            "generated_at": self.generated_at,
        }


def calculate_refinement_priority(
    severity: str,
    frequency: int,
    affected_module_count: int,
) -> float:
    """Calculates objective prioritization score without subjective AI ranking."""
    sev_w = SEVERITY_WEIGHTS.get(severity.lower().strip(), 0.5)
    freq_factor = min(1.0, frequency * 0.2)
    mod_factor = min(1.0, affected_module_count * 0.25)
    return round(0.5 * sev_w + 0.3 * freq_factor + 0.2 * mod_factor, 2)


def generate_beta_improvement_report(
    workspace_id: str,
    insights: Sequence[FeedbackInsightRecord],
) -> BetaImprovementReport:
    """Generates auditable improvement report synthesizing feedback categories and priorities."""
    breakdown: dict[str, int] = {}
    for item in insights:
        cat = item.category.lower().strip()
        breakdown[cat] = breakdown.get(cat, 0) + item.frequency

    sorted_insights = tuple(sorted(insights, key=lambda x: x.priority_score, reverse=True))

    return BetaImprovementReport(
        report_id=f"bimprep-{uuid4().hex[:16]}",
        workspace_id=str(workspace_id).strip(),
        total_feedback_items=sum(item.frequency for item in insights),
        category_breakdown=breakdown,
        top_refinements=sorted_insights[:10],
        generated_at=_now(),
    )


class BetaRefinementStore:
    """Project/workspace-partitioned SQLite store for feedback insights and improvement reports."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_insights (
                    insight_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    priority_score REAL NOT NULL,
                    record_json TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS improvement_reports (
                    report_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_fb_insight_ws ON feedback_insights(workspace_id);"
            )

    def save_insight(self, insight: FeedbackInsightRecord) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO feedback_insights VALUES (?, ?, ?, ?, ?)
                """,
                (
                    insight.insight_id,
                    insight.workspace_id,
                    insight.category,
                    insight.priority_score,
                    json.dumps(insight.to_dict()),
                ),
            )

    def list_insights(self, workspace_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM feedback_insights WHERE workspace_id=? ORDER BY priority_score DESC",
                (workspace_id,),
            ).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]

    def save_report(self, report: BetaImprovementReport) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO improvement_reports VALUES (?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.workspace_id,
                    report.generated_at,
                    json.dumps(report.to_dict()),
                ),
            )

    def list_reports(self, workspace_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM improvement_reports WHERE workspace_id=? ORDER BY generated_at DESC LIMIT 50",
                (workspace_id,),
            ).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]

