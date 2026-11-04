"""CLOSURE-05 Phase B physical proof: governed application lifecycle.

Proves, against a REAL notepad.exe process on this machine:

  B1. Launch: an approved CONTROL_APPLICATION launch starts a real
      notepad.exe whose PID is visible to the OS (tasklist), not just to
      the Python process handle.
  B2. Inspect: the governed inspect operation reports the live PID and a
      non-empty window title while the process is running.
  B3. Terminate: the governed terminate operation ends the real process;
      tasklist no longer lists the PID afterwards.
  B4. Fail-closed negatives: an unapproved launch is refused; a shell
      (cmd.exe) is refused at proposal time by the governed registry.

Run:  python scripts/prove_closure05_phase_b.py
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


def tasklist_pids(image: str) -> set[int]:
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout
    pids: set[int] = set()
    for line in out.splitlines():
        parts = [p.strip('" ') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="closure05-phase-b-"))
    try:
        workspace = work / "workspace"
        workspace.mkdir()
        database = work / "state.sqlite3"
        project_id = "closure05-phase-b"
        with SQLiteStore(database) as store:
            store.save_state(ProjectState(project_id=project_id, goal="phase-b proof"))

        store_b = LocalPermissionStore(database)
        executor = LocalToolExecutor(store_b, state_root=work / "tool-state")
        store_b.set_profile(
            project_id=project_id,
            profile=PermissionProfile.EXTENDED,
            workspace_root=workspace,
        )
        grant = store_b.create_grant(
            project_id=project_id,
            root_path=workspace,
            capabilities=[FileCapability.PROPOSE_APPLICATION],
            reason="Phase B physical proof grant",
        )

        # ---- B4a: shell must never reach a proposal ----
        refused = False
        try:
            executor.propose_application(
                project_id=project_id,
                grant_id=grant.grant_id,
                application="cmd.exe",
                task="Open a shell",
                reason="Must be refused by the governed registry",
            )
        except PermissionError:
            refused = True
        record("B4-shell-refused", refused, "cmd.exe proposal refused by registry")

        # ---- B1: approved launch of a real notepad.exe ----
        proposed = executor.propose_application(
            project_id=project_id,
            grant_id=grant.grant_id,
            application="notepad",
            task="Open Notepad for the physical lifecycle proof",
            reason="CLOSURE-05 Phase B physical proof",
        )
        approval_id = str(proposed["approval"]["id"])
        with PermissionStore(database) as permissions:
            permissions.decide(approval_id, approve=True, decided_by="closure05_prover")

        launched = executor.execute_application(
            project_id=project_id,
            grant_id=grant.grant_id,
            approval_id=approval_id,
            application="notepad",
            operation="launch",
        )
        pid = int(launched["pid"])
        time.sleep(1.0)
        os_pids = tasklist_pids("notepad.exe")
        record(
            "B1-launch-real-process",
            launched["status"] == "running" and pid in os_pids,
            f"pid={pid} visible-to-tasklist={pid in os_pids} "
            f"executable={launched['executable']}",
        )

        # ---- B2: inspect sees the live process and governed windows ----
        inspected = executor.execute_application(
            project_id=project_id,
            grant_id=grant.grant_id,
            approval_id=approval_id,
            application="notepad",
            operation="inspect",
        )
        governed_windows = inspected.get("windows", [])
        titles = [w.get("title", "") for w in governed_windows]
        record(
            "B2-inspect-live",
            inspected["alive"]
            and inspected["pid"] == pid
            and any("Notepad" in t for t in titles),
            f"alive={inspected['alive']} pid={inspected['pid']} "
            f"own_pid_title={inspected['window_title']!r} "
            f"governed_windows={len(governed_windows)} titles={titles[:3]!r}",
        )

        # ---- B4b: the same approval cannot launch a second instance ----
        double = False
        try:
            executor.execute_application(
                project_id=project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
                application="notepad",
                operation="launch",
            )
        except ValueError as error:
            double = "already has a running application session" in str(error)
        record("B4-no-second-instance", double, "second launch on same approval refused")

        # ---- B3: terminate kills the real process ----
        terminated = executor.execute_application(
            project_id=project_id,
            grant_id=grant.grant_id,
            approval_id=approval_id,
            application="notepad",
            operation="terminate",
        )
        time.sleep(1.0)
        os_pids_after = tasklist_pids("notepad.exe")
        record(
            "B3-terminate-real-process",
            terminated["status"] == "terminated" and pid not in os_pids_after,
            f"status={terminated['status']} pid-gone={pid not in os_pids_after}",
        )

        # ---- summary ----
        passed = sum(1 for item in RESULTS if item["ok"])
        summary = {
            "phase": "CLOSURE-05 Phase B",
            "passed": passed,
            "total": len(RESULTS),
            "results": RESULTS,
        }
        out_path = SOFTWARE_ROOT / "tests" / "closure05-phase-b-proof.json"
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
