"""Real-World Usage Validation Framework Foundation (AG-AMCA-STUDIO-BETA-B18).

Provides systematic tracking for real-world Beta usage scenarios, known limitations,
workarounds, and empirical health summaries for AMCA Studio v0.7.0-beta.1.
Zero cloud telemetry; zero user profiling; strictly local SQLite persistence.
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
class BetaUsageScenario:
    scenario_id: str
    workspace_id: str
    name: str
    workflow_type: str
    required_modules: tuple[str, ...]
    status: str
    evidence_ref: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "workflow_type": self.workflow_type,
            "required_modules": list(self.required_modules),
            "status": self.status,
            "evidence_ref": self.evidence_ref,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class KnownLimitationRecord:
    limitation_id: str
    module_name: str
    description: str
    workaround: str
    planned_improvement: str
    is_resolved: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "limitation_id": self.limitation_id,
            "module_name": self.module_name,
            "description": self.description,
            "workaround": self.workaround,
            "planned_improvement": self.planned_improvement,
            "is_resolved": self.is_resolved,
        }


@dataclass(frozen=True, slots=True)
class BetaHealthSummary:
    workspace_id: str
    total_scenarios: int
    passed_scenarios: int
    unresolved_limitations: int
    feedback_count: int
    documentation_coverage: float
    health_ratio: float
    evaluated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "total_scenarios": self.total_scenarios,
            "passed_scenarios": self.passed_scenarios,
            "unresolved_limitations": self.unresolved_limitations,
            "feedback_count": self.feedback_count,
            "documentation_coverage": self.documentation_coverage,
            "health_ratio": self.health_ratio,
            "evaluated_at": self.evaluated_at,
        }


def calculate_beta_health_summary(
    workspace_id: str,
    scenarios: Sequence[BetaUsageScenario],
    limitations: Sequence[KnownLimitationRecord],
    *,
    feedback_count: int = 0,
    documentation_coverage: float = 1.0,
) -> BetaHealthSummary:
    total_scen = len(scenarios)
    passed_scen = sum(1 for s in scenarios if s.status.lower() == "passed")
    unresolved_lim = sum(1 for l in limitations if not l.is_resolved)

    pass_ratio = (passed_scen / total_scen) if total_scen > 0 else 1.0
    lim_penalty = min(0.2, unresolved_lim * 0.05)
    health = max(0.0, round(pass_ratio * 0.8 + documentation_coverage * 0.2 - lim_penalty, 2))

    return BetaHealthSummary(
        workspace_id=str(workspace_id).strip(),
        total_scenarios=total_scen,
        passed_scenarios=passed_scen,
        unresolved_limitations=unresolved_lim,
        feedback_count=int(feedback_count),
        documentation_coverage=float(documentation_coverage),
        health_ratio=health,
        evaluated_at=_now(),
    )


class RealWorldValidationStore:
    """Project/workspace-partitioned SQLite store for real-world Beta validation records."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS beta_scenarios (
                    scenario_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    workflow_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS known_limitations (
                    limitation_id TEXT PRIMARY KEY,
                    module_name TEXT NOT NULL,
                    is_resolved INTEGER NOT NULL,
                    record_json TEXT NOT NULL
                );
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_bscenario_ws ON beta_scenarios(workspace_id);"
            )

    def save_scenario(self, scenario: BetaUsageScenario) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO beta_scenarios VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scenario.scenario_id,
                    scenario.workspace_id,
                    scenario.name,
                    scenario.workflow_type,
                    scenario.status,
                    json.dumps(scenario.to_dict()),
                    scenario.updated_at or _now(),
                ),
            )

    def list_scenarios(self, workspace_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM beta_scenarios WHERE workspace_id=? ORDER BY scenario_id ASC",
                (workspace_id,),
            ).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]

    def save_limitation(self, limitation: KnownLimitationRecord) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO known_limitations VALUES (?, ?, ?, ?)
                """,
                (
                    limitation.limitation_id,
                    limitation.module_name,
                    1 if limitation.is_resolved else 0,
                    json.dumps(limitation.to_dict()),
                ),
            )

    def list_limitations(self, *, unresolved_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT record_json FROM known_limitations"
        if unresolved_only:
            query += " WHERE is_resolved=0"
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(query).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]

