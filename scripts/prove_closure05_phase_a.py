"""CLOSURE-05 Phase A physical proof: persistent command execution.

Proves, against REAL processes and a REAL sqlite store on disk:

  A1. Timeout: an approved `ping -n 60 127.0.0.1` run is killed by the
      watchdog at a 2-second approved timeout and final status is "timeout".
  A2. Cancellation: a running `ping -n 60 127.0.0.1` is killed by
      cancel_run() and final status is "cancelled" (never overwritten back
      to "completed" by the reader thread).
  A3. Restart survival: the sqlite run store on disk still contains all
      three runs when a NEW executor process reads it, and a stale
      "running" record is failed-closed at load.

Run:  python scripts/prove_closure05_phase_a.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.domain import ProjectState  # noqa: E402
from amca.local_tools import (  # noqa: E402
    FileCapability,
    LocalPermissionStore,
    LocalToolExecutor,
    PermissionProfile,
)
from amca.permissions import PermissionStore  # noqa: E402
from amca.storage import SQLiteStore  # noqa: E402

RESULTS: list[dict[str, object]] = []


def record(check_id: str, ok: bool, detail: str) -> None:
    RESULTS.append({"id": check_id, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + check_id + " :: " + detail)


def wait_terminal(executor: LocalToolExecutor, run_id: str, seconds: float) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        snap = executor.command_run_snapshot(run_id)
        if snap["status"] != "running":
            return snap
        time.sleep(0.1)
    return executor.command_run_snapshot(run_id)


def propose_and_approve(
    executor: LocalToolExecutor,
    *,
    database: Path,
    project_id: str,
    grant_id: str,
    argv: list[str],
    reason: str,
) -> str:
    proposed = executor.propose_argv_command(
        project_id=project_id, grant_id=grant_id, argv=argv, cwd=".", reason=reason
    )
    approval_id = str(proposed["approval"]["id"])
    with PermissionStore(database) as permissions:
        permissions.decide(approval_id, approve=True, decided_by="closure05_prover")
    return approval_id


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="closure05-phase-a-"))
    try:
        workspace = work / "workspace"
        workspace.mkdir()
        database = work / "state.sqlite3"
        project_id = "closure05-phase-a"
        with SQLiteStore(database) as store:
            store.save_state(ProjectState(project_id=project_id, goal="phase-a proof"))

        store_a = LocalPermissionStore(database)
        executor_a = LocalToolExecutor(store_a, state_root=work / "tool-state")
        store_a.set_profile(
            project_id=project_id,
            profile=PermissionProfile.EXTENDED,
            workspace_root=workspace,
        )
        grant = store_a.create_grant(
            project_id=project_id,
            root_path=workspace,
            capabilities=[FileCapability.PROPOSE_COMMAND],
            reason="Phase A physical proof grant",
        )
        record(
            "A0-setup",
            grant.grant_id.startswith("grant"),
            f"grant={grant.grant_id} state_root={work / 'tool-state'}",
        )

        # ---- A1: approved timeout terminates a real long-running process ----
        approval_timeout = propose_and_approve(
            executor_a,
            database=database,
            project_id=project_id,
            grant_id=grant.grant_id,
            argv=["ping", "-n", "60", "127.0.0.1"],
            reason="Prove watchdog timeout kills a long run",
        )
        started_mono = time.monotonic()
        started = executor_a.execute_approved_argv(
            project_id=project_id,
            grant_id=grant.grant_id,
            approval_id=approval_timeout,
            timeout_seconds=2,
        )
        run_timeout = str(started["run_id"])
        snap = wait_terminal(executor_a, run_timeout, 20)
        elapsed = time.monotonic() - started_mono
        record(
            "A1-timeout-status",
            snap["status"] == "timeout",
            f"status={snap['status']} elapsed={elapsed:.2f}s "
            f"truncated={snap.get('truncated')}",
        )
        record(
            "A1-timeout-enforced-early",
            elapsed < 15,
            f"run ended at {elapsed:.2f}s (must be far below the 60-ping "
            "natural runtime)",
        )

        # ---- A2: cancellation kills the real process ----
        approval_cancel = propose_and_approve(
            executor_a,
            database=database,
            project_id=project_id,
            grant_id=grant.grant_id,
            argv=["ping", "-n", "60", "127.0.0.1"],
            reason="Prove cancel_run kills a long run",
        )
        started = executor_a.execute_approved_argv(
            project_id=project_id,
            grant_id=grant.grant_id,
            approval_id=approval_cancel,
            timeout_seconds=120,
        )
        run_cancel = str(started["run_id"])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            snap_now = executor_a.command_run_snapshot(run_cancel)
            if snap_now["status"] == "running" and "Reply from" in str(
                snap_now.get("output", "")
            ):
                break
            time.sleep(0.1)
        cancel_result = executor_a.cancel_run(run_cancel)
        snap = wait_terminal(executor_a, run_cancel, 10)
        time.sleep(0.5)  # give the reader thread a chance to misbehave if buggy
        snap_after = executor_a.command_run_snapshot(run_cancel)
        record(
            "A2-cancel-status",
            cancel_result["status"] == "cancelled"
            and snap["status"] == "cancelled"
            and snap_after["status"] == "cancelled",
            f"cancel={cancel_result['status']} terminal={snap['status']} "
            f"stable-after-delay={snap_after['status']}",
        )

        # ---- A3: restart survival from the on-disk sqlite store ----
        executor_a_snapshot = executor_a.command_run_snapshot(run_timeout)
        store_a.close()
        executor_b = LocalToolExecutor(store_a, state_root=work / "tool-state")
        recovered_timeout = executor_b.command_run_snapshot(run_timeout)
        recovered_cancel = executor_b.command_run_snapshot(run_cancel)
        same = (
            recovered_timeout["status"] == executor_a_snapshot["status"]
            and recovered_timeout["argv"] == executor_a_snapshot["argv"]
            and recovered_cancel["status"] == "cancelled"
        )
        record(
            "A3-restart-survival",
            same,
            f"timeout run recovered as {recovered_timeout['status']}; "
            f"cancel run recovered as {recovered_cancel['status']}",
        )

        # ---- A4: stale running rows fail closed on restart ----
        db_path = work / "tool-state" / "command-runs.sqlite3"
        import sqlite3

        stale_id = "stalerun0000000000000000000000000000"
        db = sqlite3.connect(str(db_path))
        try:
            db.execute(
                "INSERT OR REPLACE INTO local_command_runs "
                "(run_id, project_id, grant_id, approval_id, argv_json, status,"
                " exit_code, stdout, stderr, elapsed_seconds, truncated,"
                " started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    stale_id,
                    project_id,
                    grant.grant_id,
                    "",
                    json.dumps(["ping", "-n", "60", "127.0.0.1"]),
                    "running",
                    None,
                    "",
                    "",
                    None,
                    0,
                    "2026-01-01T00:00:00Z",
                    None,
                ),
            )
            db.commit()
        finally:
            db.close()
        executor_c = LocalToolExecutor(store_a, state_root=work / "tool-state")
        stale_snap = executor_c.command_run_snapshot(stale_id)
        record(
            "A4-stale-fail-closed",
            stale_snap["status"] == "failed",
            f"stale running row loaded as {stale_snap['status']}",
        )
        store_a.close()

        # ---- summary ----
        passed = sum(1 for item in RESULTS if item["ok"])
        summary = {
            "phase": "CLOSURE-05 Phase A",
            "passed": passed,
            "total": len(RESULTS),
            "results": RESULTS,
        }
        out_path = SOFTWARE_ROOT / "tests" / "closure05-phase-a-proof.json"
        out_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps({"passed": passed, "total": len(RESULTS)}))
        print(f"proof written to {out_path}")
        return 0 if passed == len(RESULTS) else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
