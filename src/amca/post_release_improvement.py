"""AMCA Studio v0.7.0 Post-Release Observability & Improvement Loop (AG-AMCA-STUDIO-V0.7.0).

Provides local-first quality dashboards, evidence-based improvement reports,
release health snapshots, and patch/release governance rules for AMCA Studio v0.7.0.
Strictly zero cloud telemetry, 100% offline, local SQLite persistence.
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
class QualityIssueSummary:
    module_name: str
    total_issues: int
    resolved_issues: int
    open_issues: int
    max_severity: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_name": self.module_name,
            "total_issues": self.total_issues,
            "resolved_issues": self.resolved_issues,
            "open_issues": self.open_issues,
            "max_severity": self.max_severity,
        }


@dataclass(frozen=True, slots=True)
class ReleaseHealthSnapshot:
    snapshot_id: str
    workspace_id: str
    stability_status: str
    total_open_issues: int
    total_resolved_issues: int
    regression_status: str
    known_limitations_count: int
    captured_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "workspace_id": self.workspace_id,
            "stability_status": self.stability_status,
            "total_open_issues": self.total_open_issues,
            "total_resolved_issues": self.total_resolved_issues,
            "regression_status": self.regression_status,
            "known_limitations_count": self.known_limitations_count,
            "captured_at": self.captured_at,
        }


@dataclass(frozen=True, slots=True)
class EvidenceBasedImprovementReport:
    report_id: str
    workspace_id: str
    summaries: tuple[QualityIssueSummary, ...]
    release_snapshot: ReleaseHealthSnapshot
    patch_release_justified: bool
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "workspace_id": self.workspace_id,
            "summaries": [s.to_dict() for s in self.summaries],
            "release_snapshot": self.release_snapshot.to_dict(),
            "patch_release_justified": self.patch_release_justified,
            "generated_at": self.generated_at,
        }


def generate_improvement_report(
    workspace_id: str,
    issues: Sequence[Mapping[str, Any]],
    *,
    stability_status: str = "STABLE",
    regression_status: str = "ALL_PASS",
    known_limitations_count: int = 8,
) -> EvidenceBasedImprovementReport:
    """Aggregates recorded issues into module summaries and derives patch justification."""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for iss in issues:
        mod = iss.get("affected_module", "unknown")
        grouped.setdefault(mod, []).append(iss)

    summaries: list[QualityIssueSummary] = []
    total_open = 0
    total_resolved = 0
    has_critical_or_high_open = False

    for mod, mod_issues in grouped.items():
        t_count = len(mod_issues)
        r_count = sum(1 for i in mod_issues if i.get("resolution_status") == "resolved")
        o_count = t_count - r_count
        total_open += o_count
        total_resolved += r_count

        severities = [i.get("severity", "L0") for i in mod_issues]
        max_sev = "L0"
        for s in ("L3", "L2", "L1", "L0"):
            if s in severities:
                max_sev = s
                break

        for i in mod_issues:
            if i.get("resolution_status") != "resolved" and i.get("severity") in ("L2", "L3"):
                has_critical_or_high_open = True

        summaries.append(
            QualityIssueSummary(
                module_name=mod,
                total_issues=t_count,
                resolved_issues=r_count,
                open_issues=o_count,
                max_severity=max_sev,
            )
        )

    now_str = _now()
    snapshot = ReleaseHealthSnapshot(
        snapshot_id=f"snap-{uuid4().hex[:16]}",
        workspace_id=str(workspace_id).strip(),
        stability_status=stability_status,
        total_open_issues=total_open,
        total_resolved_issues=total_resolved,
        regression_status=regression_status,
        known_limitations_count=known_limitations_count,
        captured_at=now_str,
    )

    patch_justified = has_critical_or_high_open or regression_status != "ALL_PASS"

    return EvidenceBasedImprovementReport(
        report_id=f"imprep-{uuid4().hex[:16]}",
        workspace_id=str(workspace_id).strip(),
        summaries=tuple(summaries),
        release_snapshot=snapshot,
        patch_release_justified=patch_justified,
        generated_at=now_str,
    )


class QualityImprovementStore:
    """Project/workspace-partitioned SQLite store for quality snapshots and improvement reports."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS improvement_reports (
                    report_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    patch_justified INTEGER NOT NULL,
                    record_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_improv_rep_ws ON improvement_reports(workspace_id);"
            )

    def save_report(self, report: EvidenceBasedImprovementReport) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO improvement_reports VALUES (?, ?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.workspace_id,
                    1 if report.patch_release_justified else 0,
                    json.dumps(report.to_dict()),
                    report.generated_at,
                ),
            )

    def list_reports(self, workspace_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM improvement_reports WHERE workspace_id=? ORDER BY generated_at DESC",
                (workspace_id,),
            ).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]

