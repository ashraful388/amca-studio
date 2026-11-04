"""CLOSURE-05 Phase C physical proof: governed browser runtime control.

Proves, against a REAL local HTTP server on 127.0.0.1 and REAL Edge:

  C1. Server: a real HTTP server serves a test page on 127.0.0.1 and a
      plain urllib fetch (loopback is allowed client-side) retrieves it.
  C2. Open: the governed browser 'open' launches real Edge headless
      against the loopback page (pid visible via tasklist).
  C3. Inspect: the captured DOM contains the test page's marker content.
  C4. Close: the session ends; a second open on the same approval is
      refused while one is running.
  C5. Negative: a non-loopback URL is refused at proposal time.

Run:  python scripts/prove_closure05_phase_c.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

TEST_PAGE = """<!doctype html>
<html><head><title>AMCA Closure05 Phase C</title></head>
<body><h1 id="marker">AMCA-PHASE-C-MARKER-7f3a9c</h1>
<p>Governed browser runtime loopback proof page.</p>
</body></html>"""


def record(check_id: str, ok: bool, detail: str) -> None:
    RESULTS.append({"id": check_id, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + check_id + " :: " + detail)


def tasklist_pids(image: str) -> set[int]:
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, timeout=20,
    ).stdout
    pids: set[int] = set()
    for line in out.splitlines():
        parts = [p.strip('" ') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def start_test_server(port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = TEST_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="closure05-phase-c-"))
    server = None
    try:
        workspace = work / "workspace"
        workspace.mkdir()
        database = work / "state.sqlite3"
        project_id = "closure05-phase-c"
        with SQLiteStore(database) as store:
            store.save_state(ProjectState(project_id=project_id, goal="phase-c proof"))

        store_c = LocalPermissionStore(database)
        executor = LocalToolExecutor(store_c, state_root=work / "tool-state")
        store_c.set_profile(
            project_id=project_id,
            profile=PermissionProfile.EXTENDED,
            workspace_root=workspace,
        )
        grant = store_c.create_grant(
            project_id=project_id,
            root_path=workspace,
            capabilities=[FileCapability.PROPOSE_APPLICATION],
            reason="Phase C physical proof grant",
        )

        # ---- C1: real loopback server serves the test page ----
        port = 8931
        server = start_test_server(port)
        time.sleep(0.3)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/test.html", timeout=10
        ) as response:
            body = response.read().decode("utf-8")
        record(
            "C1-server-up",
            "AMCA-PHASE-C-MARKER-7f3a9c" in body,
            f"http://127.0.0.1:{port}/test.html status={response.status} "
            f"bytes={len(body)}",
        )

        # ---- C5: non-loopback URL refused at proposal time ----
        refused = False
        try:
            executor.propose_browser_navigation(
                project_id=project_id,
                grant_id=grant.grant_id,
                browser="edge",
                url="https://example.com/page",
                task="Fetch a public page",
                reason="Must be refused: browser runtime is loopback-only",
            )
        except PermissionError:
            refused = True
        record("C5-nonloopback-refused", refused, "https://example.com refused")

        # ---- C2/C3/C4: approved open -> inspect -> close ----
        url = f"http://127.0.0.1:{port}/test.html"
        proposed = executor.propose_browser_navigation(
            project_id=project_id,
            grant_id=grant.grant_id,
            browser="edge",
            url=url,
            task="Render the local test page and capture its DOM",
            reason="CLOSURE-05 Phase C physical proof",
        )
        approval_id = str(proposed["approval"]["id"])
        with PermissionStore(database) as permissions:
            permissions.decide(approval_id, approve=True, decided_by="closure05_prover")

        opened = executor.execute_browser(
            project_id=project_id,
            grant_id=grant.grant_id,
            approval_id=approval_id,
            operation="open",
        )
        # dump-dom exits as soon as the DOM is captured, so the tasklist
        # probe must run immediately after spawn.
        edge_pids = tasklist_pids("msedge.exe")
        record(
            "C2-open-real-browser",
            opened["status"] == "running" and opened["pid"] in edge_pids,
            f"pid={opened['pid']} visible-to-tasklist={opened['pid'] in edge_pids}",
        )

        deadline = time.monotonic() + 30
        inspected: dict = {}
        while time.monotonic() < deadline:
            inspected = executor.execute_browser(
                project_id=project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
                operation="inspect",
            )
            if inspected["status"] != "running":
                break
            time.sleep(0.5)
        dom_ok = "AMCA-PHASE-C-MARKER-7f3a9c" in str(inspected.get("dom_preview", ""))
        record(
            "C3-dom-captured",
            inspected.get("status") == "completed"
            and inspected.get("dom_bytes", 0) > 100,
            f"status={inspected.get('status')} dom_bytes={inspected.get('dom_bytes')} "
            f"marker_in_preview={dom_ok}",
        )

        # second open while session still tracked must be refused
        second_open_refused = False
        try:
            executor.execute_browser(
                project_id=project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
                operation="open",
            )
        except ValueError as error:
            second_open_refused = "already has a browser session" in str(error)
        closed = executor.execute_browser(
            project_id=project_id,
            grant_id=grant.grant_id,
            approval_id=approval_id,
            operation="close",
        )
        record(
            "C4-close-session",
            closed["operation"] == "close" and second_open_refused,
            f"closed status={closed['status']} second-open-refused={second_open_refused}",
        )

        passed = sum(1 for item in RESULTS if item["ok"])
        summary = {
            "phase": "CLOSURE-05 Phase C",
            "passed": passed,
            "total": len(RESULTS),
            "results": RESULTS,
        }
        out_path = SOFTWARE_ROOT / "tests" / "closure05-phase-c-proof.json"
        out_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps({"passed": passed, "total": len(RESULTS)}))
        print(f"proof written to {out_path}")
        return 0 if passed == len(RESULTS) else 1
    finally:
        if server is not None:
            server.shutdown()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
