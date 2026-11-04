"""ZC-AMCA-STUDIO-BACKEND-SUPPORT-01: read-only research visibility APIs.

Backend read surfaces over data ALREADY stored by the R2.x stack, exposed
for AMCA Studio Alpha UI integration (AG-R2.6A). Strictly read-only:

- pure SELECT statements against canonical amca.db;
- no DDL, no migrations, no workflows, no writes of any kind;
- project isolation enforced by callers passing project scoping;
- summaries deliberately exclude raw argument payloads (no sensitive or
  oversized content leaves the backend).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class ResearchVisibilityStore:
    """Read-only SELECT facade over existing research execution tables."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    # -- runs ------------------------------------------------------------

    def list_runs(self, *, project_id: str | None = None,
                  limit: int = 200) -> tuple[list[dict[str, Any]], int]:
        """Returns (runs_page, total_matching) for dashboard pagination."""
        try:
            with self._connect() as connection:
                if project_id:
                    total = connection.execute(
                        "SELECT COUNT(*) AS c FROM research_runs "
                        "WHERE project_id = ?",
                        (project_id,)).fetchone()["c"]
                    rows = connection.execute(
                        """SELECT run_id, project_id, session_id, objective,
                                  status, stop_reason, mode, planner_mode,
                                  planner_source, started_at, completed_at
                           FROM research_runs WHERE project_id = ?
                           ORDER BY started_at DESC LIMIT ?""",
                        (project_id, int(limit))).fetchall()
                else:
                    total = connection.execute(
                        "SELECT COUNT(*) AS c FROM research_runs").fetchone()["c"]
                    rows = connection.execute(
                        """SELECT run_id, project_id, session_id, objective,
                                  status, stop_reason, mode, planner_mode,
                                  planner_source, started_at, completed_at
                           FROM research_runs
                           ORDER BY started_at DESC LIMIT ?""",
                        (int(limit),)).fetchall()
            return [dict(row) for row in rows], int(total)
        except sqlite3.OperationalError:
            return [], 0

    def run_details(self, run_id: str, *, project_id: str | None = None) \
            -> dict[str, Any] | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """SELECT * FROM research_runs WHERE run_id = ?""",
                    (run_id,)).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        record = dict(row)
        if project_id is not None and record.get("project_id") != project_id:
            return None  # no existence leak across projects
        limits_raw = record.pop("limits_json", None)
        try:
            record["limits"] = json.loads(limits_raw) if limits_raw else {}
        except json.JSONDecodeError:
            record["limits"] = {}
        return record

    # -- execution trail --------------------------------------------------

    def run_steps_summary(self, run_id: str,
                          *, limit: int = 500) -> list[dict[str, Any]]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT step_id, ordinal, action_type, reason_code,
                              decision, execution_ref, outcome, created_by,
                              started_at, completed_at
                       FROM research_run_steps WHERE run_id = ?
                       ORDER BY ordinal LIMIT ?""",
                    (run_id, int(limit))).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            return []

    def model_participation(self, run_id: str) -> dict[str, Any]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT created_by AS planner, action_type,
                              decision, outcome, COUNT(*) AS occurrences
                       FROM research_run_steps WHERE run_id = ?
                       GROUP BY created_by, action_type, decision, outcome
                       ORDER BY occurrences DESC""", (run_id,)).fetchall()
        except sqlite3.OperationalError:
            return {"planners": [], "totals": {}}
        planners: dict[str, dict[str, int]] = {}
        totals: dict[str, int] = {}
        for row in rows:
            planner = row["planner"] or "unknown"
            bucket = planners.setdefault(
                planner, {"steps": 0, "awaiting_approval": 0, "executed": 0})
            bucket["steps"] += row["occurrences"]
            if row["decision"] == "awaiting_approval":
                bucket["awaiting_approval"] += row["occurrences"]
            if row["outcome"] == "executed":
                bucket["executed"] += row["occurrences"]
            key = f"{row['action_type']}:{row['outcome']}"
            totals[key] = totals.get(key, 0) + row["occurrences"]
        return {"planners": planners, "action_outcomes": totals}

    # -- planning trail ----------------------------------------------------

    def planning_summary(self, run_id: str) -> dict[str, Any]:
        try:
            with self._connect() as connection:
                turns = connection.execute(
                    """SELECT turn_index, model_provider_id, model_model_id,
                              planning_source, mode, created_at
                       FROM research_planning_turns WHERE run_id = ?
                       ORDER BY turn_index""", (run_id,)).fetchall()
                events = connection.execute(
                    """SELECT turn_index, candidate_index,
                              validator_result, rejection_reason
                       FROM research_planning_events WHERE run_id = ?
                       ORDER BY turn_index, candidate_index""",
                    (run_id,)).fetchall()
        except sqlite3.OperationalError:
            return {"turns": [], "candidates": []}
        by_turn: dict[int, list[dict[str, Any]]] = {}
        for event in events:
            by_turn.setdefault(event["turn_index"], []).append({
                "candidate_index": event["candidate_index"],
                "validator_result": event["validator_result"],
                "rejection_reason": event["rejection_reason"],
            })
        return {
            "turns": [{
                "turn_index": turn["turn_index"],
                "planning_source": turn["planning_source"],
                "mode": turn["mode"],
                "model_provider_id": turn["model_provider_id"],
                "model_model_id": turn["model_model_id"],
                "candidate_count": len(by_turn.get(turn["turn_index"], [])),
                "candidates": by_turn.get(turn["turn_index"], []),
                "created_at": turn["created_at"],
            } for turn in turns],
        }

    # -- collaboration mode history ----------------------------------------

    def mode_history(self, run_id: str) -> dict[str, Any]:
        details = self.run_details(run_id)
        if details is None:
            return {}
        return {
            "run_id": run_id,
            "mode": details.get("mode"),
            "planner_mode": details.get("planner_mode"),
            "planner_source": details.get("planner_source"),
            "fallback_reason": details.get("fallback_reason"),
            "status": details.get("status"),
            "stop_reason": details.get("stop_reason"),
        }

    # -- Studio Alpha observability (ZC-AMCA-STUDIO-OBSERVABILITY-HARDENING-01)

    def studio_status(self, *, project_id: str) -> dict[str, Any]:
        """Compact cross-run status summary for one project (read-only).

        Exposes counts, recent run references and collaboration modes ONLY -
        no reasoning traces, prompts, credentials or state blobs."""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT run_id, objective, status, stop_reason, mode,
                              planner_source, started_at, completed_at
                       FROM research_runs WHERE project_id = ?
                       ORDER BY COALESCE(completed_at, started_at) DESC""",
                    (project_id,)).fetchall()
        except sqlite3.OperationalError:
            return {"runs_total": 0}
        runs = [dict(row) for row in rows]
        counts: dict[str, int] = {}
        last_activity = None
        for run in runs:
            status_value = run.get("status") or "unknown"
            counts[status_value] = counts.get(status_value, 0) + 1
            for stamp in (run.get("completed_at"), run.get("started_at")):
                if stamp and (last_activity is None or stamp > last_activity):
                    last_activity = stamp
        modes: dict[str, int] = {}
        for run in runs:
            mode = run.get("mode") or "deterministic"
            modes[mode] = modes.get(mode, 0) + 1
        return {
            "project_id": project_id,
            "runs_total": len(runs),
            "runs_by_status": counts,
            "active_runs": sum(
                v for k, v in counts.items()
                if k in ("planned", "running", "waiting_approval")),
            "collaboration_modes": modes,
            "recent_runs": [
                {"run_id": r["run_id"], "objective": r["objective"],
                 "status": r["status"], "mode": r["mode"],
                 "stop_reason": r["stop_reason"],
                 "started_at": r["started_at"],
                 "completed_at": r["completed_at"]}
                for r in runs[:5]],
            "last_activity": last_activity,
        }

    def studio_health(self) -> dict[str, Any]:
        """Safe readiness booleans for the Studio UI (no internals)."""
        health = {
            "database_available": False,
            "research_subsystem_available": False,
            "tool_subsystem_available": False,
            "migrations_complete": False,
        }
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1").fetchone()
                health["database_available"] = True
                tables = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")
                }
                required_research = {
                    "research_runs", "research_run_steps",
                    "research_sources", "research_evidence",
                }
                health["research_subsystem_available"] = (
                    required_research.issubset(tables))
                health["tool_subsystem_available"] = (
                    "tool_fabric_executions" in tables)
                # Migrations considered complete when the newest R2.5/R2.6
                # additive columns are present on the core tables.
                run_columns = {
                    row["name"] for row in connection.execute(
                        "PRAGMA table_info(research_runs)")}
                step_columns = {
                    row["name"] for row in connection.execute(
                        "PRAGMA table_info(research_run_steps)")}
                health["migrations_complete"] = (
                    "mode" in run_columns and "created_by" in step_columns)
        except sqlite3.OperationalError:
            pass
        return health

    def activity_summary(self, *, project_id: str, limit: int = 50) \
            -> dict[str, Any]:
        """Unified read-only recent-activity feed for the Studio Logs panel.

        Merges three authoritative event sources for one project - Tool
        Fabric executions, approval decisions, research run steps - into a
        bounded timestamp-descending list. Summaries only: never prompts,
        reasoning traces, credentials or argument payloads. Sources whose
        tables do not exist yet are skipped and reported truthfully in the
        ``sources`` availability map instead of failing the whole feed."""
        events: list[dict[str, Any]] = []
        sources = {
            "tool_executions": True,
            "approval_decisions": True,
            "research_steps": True,
        }
        feeds = (
            ("tool_executions",
             """SELECT 'tool_execution' AS kind,
                       e.execution_id AS ref, r.tool_id AS detail,
                       e.operation AS action, e.status AS status,
                       COALESCE(e.started_at, e.completed_at) AS occurred_at
                FROM tool_fabric_executions e
                JOIN tool_fabric_requests r
                  ON r.request_id = e.request_id
                WHERE r.project_id = ?
                ORDER BY e.started_at DESC LIMIT ?"""),
            ("approval_decisions",
             """SELECT 'approval_decision' AS kind, id AS ref,
                       action AS detail, status AS status,
                       COALESCE(decided_at, created_at) AS occurred_at
                FROM approval_requests WHERE project_id = ?
                ORDER BY COALESCE(decided_at, created_at) DESC LIMIT ?"""),
            ("research_steps",
             """SELECT 'research_step' AS kind, s.step_id AS ref,
                       s.action_type AS detail, s.outcome AS status,
                       COALESCE(s.completed_at, s.started_at) AS occurred_at
                FROM research_run_steps s
                JOIN research_runs r ON r.run_id = s.run_id
                WHERE r.project_id = ?
                ORDER BY COALESCE(s.completed_at, s.started_at)
                    DESC LIMIT ?"""),
        )
        with self._connect() as connection:
            for key, query in feeds:
                try:
                    rows = connection.execute(
                        query, (project_id, int(limit))).fetchall()
                except sqlite3.OperationalError:
                    sources[key] = False
                    continue
                events.extend(dict(row) for row in rows)
        events.sort(key=lambda e: e.get("occurred_at") or "", reverse=True)
        return {"events": events[: int(limit)], "limit": int(limit),
                "sources": sources}

    # -- provenance references ---------------------------------------------

    def provenance_references(self, run_id: str, *,
                              project_id: str | None = None) \
            -> dict[str, Any]:
        details = self.run_details(run_id, project_id=project_id)
        if details is None:
            return {}
        session_id = details.get("session_id")
        references: dict[str, Any] = {
            "sources": [], "snapshots": [], "evidence": [], "citations": [],
            # R2.6A UI support: Research Workspace also needs claims and
            # synthesis metadata already stored by the accepted R1.4 stack.
            "claims": [], "synthesis": [],
        }
        try:
            with self._connect() as connection:
                references["sources"] = [
                    dict(row) for row in connection.execute(
                        """SELECT source_id, source_kind, canonical_origin,
                                  created_at FROM research_sources
                           WHERE session_id = ? ORDER BY created_at LIMIT 100""",
                        (session_id,)).fetchall()]
                references["snapshots"] = [
                    dict(row) for row in connection.execute(
                        """SELECT snap.snapshot_id, snap.source_id,
                                  snap.content_digest, snap.created_at
                           FROM research_source_snapshots snap
                           JOIN research_sources src
                             ON src.source_id = snap.source_id
                          WHERE src.session_id = ?
                           ORDER BY snap.created_at LIMIT 100""",
                        (session_id,)).fetchall()]
                references["evidence"] = [
                    dict(row) for row in connection.execute(
                        """SELECT ev.evidence_id, ev.snapshot_id,
                                  ev.extraction_method, ev.created_at
                           FROM research_evidence ev
                           JOIN research_source_snapshots snap
                             ON snap.snapshot_id = ev.snapshot_id
                           JOIN research_sources src
                             ON src.source_id = snap.source_id
                          WHERE src.session_id = ?
                           ORDER BY ev.created_at LIMIT 100""",
                        (session_id,)).fetchall()]
                references["citations"] = [
                    dict(row) for row in connection.execute(
                        """SELECT c.citation_id, c.display_label,
                                  c.evidence_id, c.created_at
                           FROM research_citations c
                           JOIN research_evidence ev
                             ON ev.evidence_id = c.evidence_id
                           JOIN research_source_snapshots snap
                             ON snap.snapshot_id = ev.snapshot_id
                           JOIN research_sources src
                             ON src.source_id = snap.source_id
                          WHERE src.session_id = ?
                           ORDER BY c.created_at LIMIT 100""",
                        (session_id,)).fetchall()]
                references["claims"] = [
                    {**dict(row),
                     "claim_text": row["claim_text"][:200]}
                    for row in connection.execute(
                        """SELECT claim_id, claim_text, claim_type, status,
                                  question_id, created_at
                           FROM research_claims WHERE session_id = ?
                           ORDER BY created_at LIMIT 100""",
                        (session_id,)).fetchall()]
                references["synthesis"] = [
                    dict(row) for row in connection.execute(
                        """SELECT p.plan_id, p.output_form, p.created_at
                                  AS plan_created_at,
                                  v.version_id, v.seq,
                                  v.grounding_status, v.created_at
                                  AS version_created_at
                           FROM research_synthesis_plans p
                           LEFT JOIN research_synthesis_versions v
                             ON v.plan_id = p.plan_id
                            AND v.seq = (SELECT MAX(v2.seq)
                                         FROM research_synthesis_versions v2
                                         WHERE v2.plan_id = p.plan_id)
                           WHERE p.session_id = ?
                           ORDER BY p.created_at LIMIT 100""",
                        (session_id,)).fetchall()]
        except sqlite3.OperationalError:
            pass
        return references
