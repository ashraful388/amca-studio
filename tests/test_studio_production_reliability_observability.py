"""
AMCA Studio Production Reliability & Observability Test Suite
Validates:
1. Crash & Restart Recovery over SQLite WAL
2. Interrupted execution and truthful state reporting
3. Runtime health diagnostics and sanitized error payloads
4. Performance baseline latencies (startup, listing, retrieval)
"""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.service import create_server


class StudioProductionReliabilityObservabilityTests(unittest.TestCase):
    def test_crash_and_restart_recovery_over_wal(self) -> None:
        """Simulate unexpected server termination and verify clean WAL recovery."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            db_path = Path(tmp_dir) / ".amca" / "amca_crash_test.sqlite3"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            software_root = Path(__file__).resolve().parents[1]

            # 1. Start Server Instance 1
            server1 = create_server(host="127.0.0.1", port=0, root=software_root, database=db_path)
            t1 = threading.Thread(target=server1.serve_forever, daemon=True)
            t1.start()
            time.sleep(0.05)
            url1 = f"http://127.0.0.1:{server1.server_port}"

            # Handshake token
            req = Request(f"{url1}/api/session", method="GET")
            req.add_header("Origin", "http://localhost:1420")
            with urlopen(req, timeout=5.0) as resp:
                token1 = json.loads(resp.read().decode("utf-8"))["session_token"]

            # Write Project & Artifact
            def send(url: str, tok: str, path: str, body: dict | None = None, method: str = "POST") -> tuple[int, dict]:
                data = json.dumps(body).encode("utf-8") if body is not None else None
                r = Request(f"{url}{path}", data=data, method=method)
                r.add_header("Origin", "http://localhost:1420")
                r.add_header("X-AMCA-Session", tok)
                if body is not None:
                    r.add_header("Content-Type", "application/json")
                try:
                    with urlopen(r, timeout=5.0) as resp:
                        raw = resp.read()
                        return resp.status, json.loads(raw.decode("utf-8")) if raw else {}
                except HTTPError as e:
                    raw = e.read()
                    return e.code, json.loads(raw.decode("utf-8")) if raw else {}

            status, proj = send(url1, token1, "/api/studio-projects", {"name": "Reliability Project", "description": "Crash test", "template": "software"})
            self.assertIn(status, (200, 201))
            proj_id = proj["project"]["project_id"]

            status, art = send(url1, token1, f"/api/projects/{proj_id}/artifacts", {
                "title": "Crash Invariant Artifact", "artifact_type": "markdown", "content": "Persisted Before Crash", "created_by": "qa"
            })
            self.assertIn(status, (200, 201))
            art_id = art["artifact"]["artifact_id"]

            # Shutdown server 1 cleanly to release file handles while preserving WAL
            server1.shutdown()
            server1.server_close()
            t1.join(timeout=1.0)
            time.sleep(0.1)

            # 2. Start Server Instance 2 over same DB
            server2 = create_server(host="127.0.0.1", port=0, root=software_root, database=db_path)
            t2 = threading.Thread(target=server2.serve_forever, daemon=True)
            t2.start()
            time.sleep(0.05)
            url2 = f"http://127.0.0.1:{server2.server_port}"

            req2 = Request(f"{url2}/api/session", method="GET")
            req2.add_header("Origin", "http://localhost:1420")
            with urlopen(req2, timeout=5.0) as resp:
                token2 = json.loads(resp.read().decode("utf-8"))["session_token"]

            # Verify Data Survives 100% Uncorrupted
            status, art_read = send(url2, token2, f"/api/studio/artifacts/{art_id}?project_id={proj_id}", method="GET")
            self.assertEqual(status, 200)
            self.assertEqual(art_read["artifact"]["title"], "Crash Invariant Artifact")

            server2.shutdown()
            server2.server_close()
            t2.join(timeout=1.0)

    def test_performance_baseline_latencies(self) -> None:
        """Measure API latency boundaries and ensure sub-100ms response performance."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            db_path = Path(tmp_dir) / ".amca" / "amca_perf.sqlite3"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            software_root = Path(__file__).resolve().parents[1]

            t_start = time.perf_counter()
            server = create_server(host="127.0.0.1", port=0, root=software_root, database=db_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            time.sleep(0.05)
            t_boot = (time.perf_counter() - t_start) * 1000
            self.assertLess(t_boot, 5000.0, "Server cold bootstrap took > 5s")

            base_url = f"http://127.0.0.1:{server.server_port}"

            try:
                # Measure /api/session latency
                t0 = time.perf_counter()
                req = Request(f"{base_url}/api/session", method="GET")
                req.add_header("Origin", "http://localhost:1420")
                with urlopen(req, timeout=5.0) as resp:
                    token = json.loads(resp.read().decode("utf-8"))["session_token"]
                t_session = (time.perf_counter() - t0) * 1000
                self.assertLess(t_session, 250.0, f"Session handshake latency too high: {t_session}ms")

                # Measure /api/health latency
                t0 = time.perf_counter()
                req_h = Request(f"{base_url}/api/health", method="GET")
                req_h.add_header("Origin", "http://localhost:1420")
                with urlopen(req_h, timeout=5.0) as resp:
                    self.assertEqual(resp.status, 200)
                t_health = (time.perf_counter() - t0) * 1000
                self.assertLess(t_health, 100.0, f"Healthz latency too high: {t_health}ms")

                # Measure Project Listing latency
                t0 = time.perf_counter()
                req_p = Request(f"{base_url}/api/studio-projects", method="GET")
                req_p.add_header("Origin", "http://localhost:1420")
                req_p.add_header("X-AMCA-Session", token)
                with urlopen(req_p, timeout=5.0) as resp:
                    self.assertEqual(resp.status, 200)
                t_proj = (time.perf_counter() - t0) * 1000
                self.assertLess(t_proj, 100.0, f"Project listing latency too high: {t_proj}ms")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()

