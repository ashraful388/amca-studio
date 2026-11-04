"""Research Execution Gateway (ZC-AMCA-RESEARCH-EXECUTION-GATEWAY-01).

Isolated integration facade between the Studio service layer and the
frozen R1/R2 research stack:

    Studio -> service.py -> research_gateway.py -> ResearchOrchestrator
           -> R1/R2 stores

The gateway publishes the minimum research-run lifecycle API (create /
status / cancel / resume) as a PROJECTION over stored RunStatus values -
existing values are never renamed or removed. It owns no execution logic:
starting, planning and stepping runs remain the orchestrator's job.

Concurrency guarantees implemented here:
- duplicate cancel is idempotent (CANCELLED -> CANCELLED, no error);
- duplicate resume is guarded (INTERRUPTED -> PLANNING, then
  PLANNING -> PLANNING as a safe no-op; never duplicates execution);
- status reads are pure SELECTs over persisted truth (no cache).

Ownership: every run-level operation demands session authentication
(enforced by the service layer) plus an explicit ``project_id``; a
missing project is 400 and a wrong-or-unknown project is an
indistinguishable 404.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .research_orchestrator import (
    RUN_MODES,
    DeterministicResearchPlanner,
    ResearchLimits,
    ResearchOrchestrator,
    ResearchRunStore,
    RunMode,
    RunStatus,
    StopReason,
)
from .research_source import ResearchStore
from .research_synthesis import ResearchSynthesisStore
from .research_ingest import ResearchIngestor
from .tool_fabric import ToolFabric, ToolFabricStore
from .web_acquisition import WebAcquisitionStore

__all__ = ["GatewayError", "ResearchGateway", "STATUS_PROJECTION",
           "DefaultRunExecutor"]

_MAX_CONTENT_CHARS = 20_000
_MAX_PROJECT_ID_CHARS = 128
_MAX_VERSIONS_PER_PLAN = 50


class GatewayError(Exception):
    """Gateway rejection carrying the HTTP status it maps to."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


#: Approved projection: stored RunStatus value -> public gateway token.
STATUS_PROJECTION = {
    "planned": "planning",
    "waiting_approval": "waiting_approval",
    "partial": "partial_progress",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "interrupted": "interrupted",
}

#: Stored states from which cancellation is rejected (cancel stays
#: idempotent on ``cancelled`` itself).
_CANCEL_REJECTED = frozenset({"completed", "failed", "partial"})

#: Stored states from which resume is safely rejected. Resume exists to
#: recover INTERRUPTED runs (INTERRUPTED -> PLANNING) and as a guarded
#: no-op on PLANNING; nothing else may re-enter planning.
_RESUME_REJECTED = frozenset({
    "waiting_approval", "running",
    "partial", "completed", "failed", "cancelled",
})

_MAX_OBJECTIVE_CHARS = 2_000


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ResearchGateway:
    """Lifecycle facade over the frozen research run store."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self.runs = ResearchRunStore(self.database)
        self._execution_engine: Callable[..., None] | None = None
        payloads_root = self.database.parent / "content"
        self.research_store = ResearchStore(
            self.database, storage_root=payloads_root)
        self.synthesis_store = ResearchSynthesisStore(
            self.database, storage_root=payloads_root)

    # -- create ------------------------------------------------------------

    def create_run(self, *, project_id: Any, objective: Any,
                   mode: Any, session_id: str) -> dict[str, Any]:
        """Persist a new planned run; returns the public created payload."""
        pid = self._require_project(project_id)
        if not isinstance(objective, str) or not objective.strip():
            raise GatewayError(400, "objective is required")
        if len(objective) > _MAX_OBJECTIVE_CHARS:
            raise GatewayError(
                400, f"objective must be at most {_MAX_OBJECTIVE_CHARS} chars")
        resolved_mode = "deterministic" if mode is None else mode
        if resolved_mode not in RUN_MODES:
            raise GatewayError(
                400,
                "mode must be one of "
                + ", ".join(sorted(RUN_MODES)),
            )
        if not isinstance(session_id, str) or not session_id.strip():
            raise GatewayError(400, "authenticated session is required")
        try:
            run = self.runs.create_run(
                project_id=pid,
                session_id=session_id[:64],
                objective=objective.strip(),
                limits=ResearchLimits(),
                mode=resolved_mode,
            )
        except ValueError as error:
            raise GatewayError(400, str(error)) from error
        return {
            "run_id": run["run_id"],
            "status": "created",
            "created_at": run["started_at"],
        }

    # -- status --------------------------------------------------------------

    def get_status(self, run_id: str, *, project_id: Any) -> dict[str, Any]:
        """Latest persisted projection of one owned run (read-only)."""
        pid = self._require_project(project_id)
        run = self._owned_run(run_id, pid)
        return {
            "run_id": run["run_id"],
            "status": STATUS_PROJECTION[run["status"]],
            "objective": run["objective"],
            "created_at": run["started_at"],
            "updated_at": run.get("completed_at") or run["started_at"],
            "stop_reason": run.get("stop_reason"),
        }

    # -- cancel ----------------------------------------------------------------

    def cancel_run(self, run_id: str, *, project_id: Any) -> dict[str, Any]:
        """Cancel an owned run; idempotent on already-cancelled runs."""
        pid = self._require_project(project_id)
        run = self._owned_run(run_id, pid)
        if run["status"] in _CANCEL_REJECTED:
            raise GatewayError(
                409,
                "cannot cancel a "
                + STATUS_PROJECTION[run["status"]]
                + " run")
        self.runs.cancel_run(run_id)
        persisted = self.runs.get_run(run_id)
        return {
            "run_id": run_id,
            "status": "cancelled",
            "updated_at": persisted["completed_at"],
        }

    # -- resume ---------------------------------------------------------------

    def resume_run(self, run_id: str, *, project_id: Any) -> dict[str, Any]:
        """Resume an interrupted run back into planning (guarded)."""
        pid = self._require_project(project_id)
        run = self._owned_run(run_id, pid)
        status = run["status"]
        if status == "planned":
            # Guarded duplicate resume: PLANNING -> PLANNING, no mutation,
            # no duplicate execution.
            return {
                "run_id": run_id,
                "status": "planning",
                "updated_at": run["started_at"],
            }
        if status in _RESUME_REJECTED:
            raise GatewayError(
                409,
                "cannot resume a "
                + STATUS_PROJECTION[status]
                + " run")
        assert status == "interrupted"
        self._resume_interrupted(run_id=run_id, project_id=pid)
        persisted = self.runs.get_run(run_id)
        return {
            "run_id": run_id,
            "status": "planning",
            "updated_at": persisted["started_at"],
        }

    def _resume_interrupted(self, *, run_id: str, project_id: str) -> None:
        """INTERRUPTED -> PLANNING as one guarded single-row write.

        Issued here because the frozen store API cannot clear
        ``completed_at`` on a non-terminal transition; no schema change,
        canonical table only, ownership + state enforced in the WHERE.
        """
        connection = sqlite3.connect(self.database, timeout=5)
        try:
            with connection:
                cursor = connection.execute(
                    """UPDATE research_runs
                       SET status='planned', stop_reason=NULL,
                           completed_at=NULL, started_at=?
                       WHERE run_id=? AND project_id=?
                         AND status='interrupted'""",
                    (_now(), run_id, project_id),
                )
                if cursor.rowcount != 1:
                    raise GatewayError(409, "run is not resumable")
        finally:
            connection.close()

    # -- execute -----------------------------------------------------------

    def set_execution_engine(self, engine: Callable[..., None]) -> None:
        """Replace the default executor (tests / alternate runtimes)."""
        self._execution_engine = engine

    def execute_run(self, run_id: str, *, project_id: Any) -> dict[str, Any]:
        """Start one bounded orchestrated pass for an owned planning run.

        The duplicate-execution guard runs synchronously (planned ->
        RUNNING before any worker exists), so two concurrent executes can
        never both start; the orchestrator then drives plan/step/stop
        transitions and completion is observed via the status endpoint.
        """
        pid = self._require_project(project_id)
        run = self._owned_run(run_id, pid)
        if run["status"] != "planned":
            raise GatewayError(
                409,
                "cannot execute a " + STATUS_PROJECTION[run["status"]]
                + " run")
        engine = self._execution_engine
        if engine is None:
            if run.get("mode") in (RunMode.ADAPTIVE.value,
                                   RunMode.HYBRID.value):
                raise GatewayError(
                    409,
                    "mode requires a model-backed planner, which is not "
                    "configured on this deployment")
            engine = DefaultRunExecutor(self.database)
        self.runs.set_run_status(run_id, RunStatus.RUNNING)
        try:
            threading.Thread(
                target=engine, args=(run_id, self.runs), daemon=True,
                name=f"research-run-{run_id[:12]}").start()
        except Exception:
            # Never strand a run in RUNNING because the worker could not
            # be started; hand it back to planning for a clean retry.
            self.runs.set_run_status(run_id, RunStatus.PLANNED)
            raise GatewayError(500, "execution could not be started")
        return {"run_id": run_id, "status": "running"}

    # -- startup reconciliation ---------------------------------------------

    def reconcile_orphaned_runs(self) -> int:
        """Crash recovery (ZC-AMCA-RUNTIME-RECOVERY-RECONCILIATION-01).

        Execution is in-process only, so any row still RUNNING at process
        start is an orphan from a crashed previous run: no worker exists
        and none will ever finish it. Mark each one INTERRUPTED with the
        orchestrator's external-action stop reason so the existing guarded
        resume path (INTERRUPTED -> PLANNING -> execute) becomes the
        truthful restart story. waiting_approval rows are legitimate
        cross-restart state and are left untouched.
        """
        connection = sqlite3.connect(self.database, timeout=5)
        try:
            orphans = [
                row["run_id"] if isinstance(row, sqlite3.Row) else row[0]
                for row in connection.execute(
                    "SELECT run_id FROM research_runs "
                    "WHERE status = 'running'").fetchall()
            ]
        finally:
            connection.close()
        for run_id in orphans:
            self.runs.set_run_status(
                run_id, RunStatus.INTERRUPTED,
                StopReason.INTERRUPTED_EXTERNAL_ACTION)
        return len(orphans)

    # -- workflow reads (ZC-AMCA-STUDIO-ALPHA-WORKFLOW-01) ------------------

    def snapshot_content(self, run_id: str, snapshot_id: str, *,
                         project_id: Any) -> dict[str, Any]:
        """Bounded extracted source text for one of the run's snapshots.

        Ownership is threefold: session auth (service layer), project_id,
        and the snapshot's source must belong to the run's own research
        session; any miss is an indistinguishable 404.
        """
        pid = self._require_project(project_id)
        run = self._owned_run(run_id, pid)
        snapshot = self.research_store.get_snapshot(
            snapshot_id, project_id=pid)
        source = (None if snapshot is None else self.research_store.get_source(
            snapshot["source_id"], project_id=pid))
        if source is None or source.get("session_id") != run["session_id"]:
            raise GatewayError(404, "snapshot not found")
        content = self.research_store.snapshot_content(snapshot)
        truncated = len(content) > _MAX_CONTENT_CHARS
        return {
            "run_id": run_id,
            "snapshot_id": snapshot_id,
            "source_id": snapshot["source_id"],
            "content_digest": snapshot["content_digest"],
            "content_type": snapshot.get("content_type"),
            "created_at": snapshot["created_at"],
            "truncated": truncated,
            "content": content[:_MAX_CONTENT_CHARS],
        }

    def run_synthesis(self, run_id: str, *,
                      project_id: Any) -> dict[str, Any]:
        """Latest synthesis version per plan for the run's session.

        Read-only projection over immutable synthesis versions; section
        text is bounded with a truthful truncation flag.
        """
        pid = self._require_project(project_id)
        run = self._owned_run(run_id, pid)
        connection = sqlite3.connect(self.database, timeout=5)
        try:
            plan_rows = connection.execute(
                """SELECT plan_id, output_form, created_at
                   FROM research_synthesis_plans
                   WHERE session_id = ? AND project_id = ?
                   ORDER BY created_at""",
                (run["session_id"], pid)).fetchall()
        finally:
            connection.close()
        plans: list[dict[str, Any]] = []
        for row in plan_rows:
            plan_id = row["plan_id"] if isinstance(row, sqlite3.Row) \
                else row[0]
            output_form = row["output_form"] if isinstance(row, sqlite3.Row) \
                else row[1]
            created_at = row["created_at"] if isinstance(row, sqlite3.Row) \
                else row[2]
            entry: dict[str, Any] = {
                "plan_id": plan_id,
                "output_form": str(output_form),
                "created_at": created_at,
                "version": None,
            }
            version = self.synthesis_store.get_latest_version(plan_id)
            if version is not None:
                sections = version["draft"].get("sections")
                kept, truncated = self._bound_sections(sections)
                entry["version"] = {
                    "version_id": version["version_id"],
                    "seq": version["seq"],
                    "grounding_status": version["grounding_status"],
                    "content_digest": version["content_digest"],
                    "created_at": version["created_at"],
                    "truncated": truncated,
                    "sections": kept,
                }
            plans.append(entry)
        return {"run_id": run_id, "plans": plans, "count": len(plans)}

    @staticmethod
    def _bound_sections(sections: Any) -> tuple[Any, bool]:
        """Bound section text at section granularity so the UI always
        receives well-formed entries; dropped sections flip the flag."""
        if not isinstance(sections, list):
            return sections, False
        kept: list[Any] = []
        budget = _MAX_CONTENT_CHARS
        for section in sections:
            rendered = json.dumps(section, sort_keys=True)
            if len(rendered) > budget:
                return kept, True
            kept.append(section)
            budget -= len(rendered)
        return kept, False

    def synthesis_history(self, run_id: str, *, project_id: Any,
                          limit_per_plan: int = 50) -> dict[str, Any]:
        """Metadata-only timeline of every synthesis version per plan.

        Ordering is deterministic and chronological: ``seq ASCENDING``
        within each plan, plans by creation time - the order a user reads
        to watch a result evolve. Versions are bounded per plan (oldest
        kept first); a capped plan sets ``versions_truncated`` truthfully.
        No section text is exposed here - use the synthesis view for the
        latest rendered result.
        """
        pid = self._require_project(project_id)
        run = self._owned_run(run_id, pid)
        cap = max(1, min(int(limit_per_plan), _MAX_VERSIONS_PER_PLAN))
        connection = sqlite3.connect(self.database, timeout=5)
        try:
            plan_rows = connection.execute(
                """SELECT plan_id, output_form, created_at
                   FROM research_synthesis_plans
                   WHERE session_id = ? AND project_id = ?
                   ORDER BY created_at""",
                (run["session_id"], pid)).fetchall()
            plans: list[dict[str, Any]] = []
            for row in plan_rows:
                plan_id = row["plan_id"] if isinstance(row, sqlite3.Row) \
                    else row[0]
                output_form = row["output_form"] \
                    if isinstance(row, sqlite3.Row) else row[1]
                created_at = row["created_at"] \
                    if isinstance(row, sqlite3.Row) else row[2]
                version_rows = connection.execute(
                    """SELECT version_id, seq, grounding_status,
                              content_digest, created_at
                       FROM research_synthesis_versions
                       WHERE plan_id = ?
                       ORDER BY seq ASC LIMIT ?""",
                    (plan_id, cap + 1)).fetchall()
                # One extra row fetched solely to detect a dropped tail.
                versions_truncated = len(version_rows) > cap
                version_rows = version_rows[:cap]
                versions = []
                for vrow in version_rows:
                    versions.append({
                        "version_id": vrow["version_id"]
                        if isinstance(vrow, sqlite3.Row) else vrow[0],
                        "seq": vrow["seq"]
                        if isinstance(vrow, sqlite3.Row) else vrow[1],
                        "grounding_status": vrow["grounding_status"]
                        if isinstance(vrow, sqlite3.Row) else vrow[2],
                        "output_form": str(output_form),
                        "content_digest": vrow["content_digest"]
                        if isinstance(vrow, sqlite3.Row) else vrow[3],
                        "created_at": vrow["created_at"]
                        if isinstance(vrow, sqlite3.Row) else vrow[4],
                    })
                plans.append({
                    "plan_id": plan_id,
                    "output_form": str(output_form),
                    "created_at": created_at,
                    "versions": versions,
                    "versions_truncated": versions_truncated,
                })
        finally:
            connection.close()
        return {"run_id": run_id, "plans": plans, "count": len(plans)}

    # -- validation helpers -------------------------------------------------

    @staticmethod
    def _require_project(project_id: Any) -> str:
        if not isinstance(project_id, str) or not project_id.strip():
            raise GatewayError(400, "project_id required")
        pid = project_id.strip()
        if len(pid) > _MAX_PROJECT_ID_CHARS:
            raise GatewayError(400, "project_id too long")
        return pid

    def _owned_run(self, run_id: str, project_id: str) -> dict[str, Any]:
        run = self.runs.get_run(run_id)
        # Unknown AND cross-project reads are indistinguishable.
        if run is None or run["project_id"] != project_id:
            raise GatewayError(404, "run not found")
        return run


class DefaultRunExecutor:
    """Builds a per-run orchestrator over canonical stores and executes
    one bounded orchestration pass with the planner the stored mode
    requires. No network provider ships configured: network actions gate
    through Tool Fabric approval before any backend is touched."""

    def __init__(self, database: Path,
                 payloads_root: Path | None = None) -> None:
        self.database = Path(database)
        self.payloads_root = payloads_root or self.database.parent / "content"

    def _build_orchestrator(self, project_id: str,
                            runs_store: ResearchRunStore) \
            -> ResearchOrchestrator:
        fabric = ToolFabric(ToolFabricStore(self.database),
                            project_root=project_id)
        from .web_acquisition import NetworkToolPolicy
        NetworkToolPolicy.register(fabric)
        research_store = ResearchStore(
            self.database, storage_root=self.payloads_root)
        return ResearchOrchestrator(
            run_store=runs_store,
            research_store=research_store,
            synthesis_store=ResearchSynthesisStore(
                self.database, storage_root=self.payloads_root),
            fabric=fabric,
            acq_store=WebAcquisitionStore(self.database),
            ingestor=ResearchIngestor(research_store),
        )

    def __call__(self, run_id: str, runs_store: ResearchRunStore) -> None:
        run = runs_store.get_run(run_id)
        if run is None:
            return
        mode = run.get("mode") or RunMode.DETERMINISTIC.value
        try:
            if mode == RunMode.DETERMINISTIC.value:
                planner = DeterministicResearchPlanner()
            else:
                # Model-backed planning is not configured on this
                # deployment; fail the run truthfully instead of hanging.
                runs_store.set_run_status(run_id, RunStatus.FAILED,
                                          StopReason.ERROR)
                return
            self._build_orchestrator(
                run["project_id"], runs_store).execute_run(run_id, planner)
        except Exception:
            # Persisted truth beats a stuck RUNNING row; never mask an
            # already-terminal transition.
            fresh = runs_store.get_run(run_id)
            if fresh is not None and fresh["status"] \
                    not in ("completed", "partial", "failed",
                            "cancelled", "interrupted"):
                try:
                    runs_store.set_run_status(run_id, RunStatus.FAILED,
                                              StopReason.ERROR)
                except Exception:
                    pass
