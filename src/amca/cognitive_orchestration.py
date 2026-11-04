"""Unified Cognitive Workflow Orchestration Foundation (AG-AMCA-STUDIO-BETA-B5).

Coordinates cross-system intelligence between Agent Fabric, Collaboration Sessions,
Persistent Memory, and Adaptive Model Routing without replacing the underlying execution engines.
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
class CognitiveWorkflowMetrics:
    planning_completeness: float
    capability_coverage_ratio: float
    model_suitability_ratio: float
    context_availability: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "planning_completeness": self.planning_completeness,
            "capability_coverage_ratio": self.capability_coverage_ratio,
            "model_suitability_ratio": self.model_suitability_ratio,
            "context_availability": self.context_availability,
        }


@dataclass(frozen=True, slots=True)
class CognitiveTaskUnderstandingRecord:
    understanding_id: str
    project_id: str
    task_summary: str
    required_capabilities: tuple[str, ...]
    matched_agent_ids: tuple[str, ...]
    matched_model_ids: tuple[str, ...]
    relevant_memory_ids: tuple[str, ...]
    recommended_mode: str
    metrics: CognitiveWorkflowMetrics
    orchestration_decision_ids: tuple[str, ...] = ()
    collaboration_session_ids: tuple[str, ...] = ()
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "understanding_id": self.understanding_id,
            "project_id": self.project_id,
            "task_summary": self.task_summary,
            "required_capabilities": list(self.required_capabilities),
            "matched_agent_ids": list(self.matched_agent_ids),
            "matched_model_ids": list(self.matched_model_ids),
            "relevant_memory_ids": list(self.relevant_memory_ids),
            "recommended_mode": self.recommended_mode,
            "metrics": self.metrics.to_dict(),
            "orchestration_decision_ids": list(self.orchestration_decision_ids),
            "collaboration_session_ids": list(self.collaboration_session_ids),
            "created_at": self.created_at,
        }


def synthesize_cognitive_workflow(
    task_summary: str,
    *,
    project_id: str,
    required_capabilities: Sequence[str],
    matched_agent_ids: Sequence[str],
    matched_model_ids: Sequence[str],
    relevant_memory_ids: Sequence[str],
    recommended_mode: str = "solo",
    orchestration_decision_ids: Sequence[str] = (),
    collaboration_session_ids: Sequence[str] = (),
) -> CognitiveTaskUnderstandingRecord:
    caps = tuple(dict.fromkeys(str(c).strip() for c in required_capabilities if str(c).strip()))
    agents = tuple(dict.fromkeys(str(a).strip() for a in matched_agent_ids if str(a).strip()))
    models = tuple(dict.fromkeys(str(m).strip() for m in matched_model_ids if str(m).strip()))
    mems = tuple(dict.fromkeys(str(m).strip() for m in relevant_memory_ids if str(m).strip()))

    cap_coverage = 1.0 if not caps else min(1.0, len(agents) / len(caps))
    model_cov = 1.0 if models else 0.0
    context_avail = bool(mems)
    completeness = round(0.4 * cap_coverage + 0.3 * model_cov + 0.3 * (1.0 if context_avail else 0.5), 2)

    metrics = CognitiveWorkflowMetrics(
        planning_completeness=completeness,
        capability_coverage_ratio=round(cap_coverage, 2),
        model_suitability_ratio=round(model_cov, 2),
        context_availability=context_avail,
    )

    return CognitiveTaskUnderstandingRecord(
        understanding_id=f"cog-{uuid4().hex[:16]}",
        project_id=str(project_id).strip(),
        task_summary=str(task_summary).strip()[:1000],
        required_capabilities=caps,
        matched_agent_ids=agents,
        matched_model_ids=models,
        relevant_memory_ids=mems,
        recommended_mode=str(recommended_mode).strip(),
        metrics=metrics,
        orchestration_decision_ids=tuple(str(d).strip() for d in orchestration_decision_ids if str(d).strip()),
        collaboration_session_ids=tuple(str(s).strip() for s in collaboration_session_ids if str(s).strip()),
        created_at=_now(),
    )


class CognitiveWorkflowStore:
    """Project-partitioned SQLite store for cognitive workflow understandings and cross-system traces."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS cognitive_workflow_understandings (
                    understanding_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    task_summary TEXT NOT NULL,
                    recommended_mode TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_cog_proj ON cognitive_workflow_understandings(project_id);"
            )

    def save_understanding(self, record: CognitiveTaskUnderstandingRecord) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO cognitive_workflow_understandings VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.understanding_id,
                    record.project_id,
                    record.task_summary,
                    record.recommended_mode,
                    json.dumps(record.to_dict()),
                    record.created_at or _now(),
                ),
            )

    def list_understandings(self, project_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM cognitive_workflow_understandings WHERE project_id=? ORDER BY created_at ASC",
                (project_id,),
            ).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]

