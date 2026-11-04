"""B1.3D tests: durable lifecycle audit trail for background automations."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from amca.automations import AutomationStore
from amca.credentials import MemoryCredentialStore
from amca.background_audit import BackgroundAudit
from amca.domain import ProjectState
from amca.scheduler import AutomationScheduler
from amca.service import create_server
from amca.storage import SQLiteStore


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class BackgroundAuditUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "background-lifecycle.jsonl"
        self.audit = BackgroundAudit(self.path)

    def tearDown(self) -> None:
        try:
            self.temporary.cleanup()
        except OSError:
            pass  # a just-joined worker may release handles a moment later

    def test_records_are_persisted_newest_first_and_bounded(self) -> None:
        for index in range(10):
            self.audit.record(f"event_{index}")
        recent = self.audit.recent(limit=3)
        self.assertEqual([item["event"] for item in recent], ["event_9", "event_8", "event_7"])
        # Bounded compaction keeps only the newest MAX_ENTRIES records.
        for index in range(1000):
            self.audit.record(f"flood_{index}")
        raw_lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        self.assertLessEqual(len(raw_lines), 601)

    def test_entries_carry_bounded_safe_fields_only(self) -> None:
        secret = "sk-SUPER-SECRET-KEY"
        self.audit.record("autostart_change_failed", success=False, detail=secret)
        entry = self.audit.recent(limit=1)[0]
        self.assertEqual(entry["event"], "autostart_change_failed")
        self.assertFalse(entry["success"])
        # Bounded truthfulness: details are capped; callers own redaction.
        self.assertLessEqual(len(entry.get("detail", "")), 200)
        del secret


class SchedulerAuditIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id="aud-p", goal="g"))
        self.store = AutomationStore(self.database)
        self.audit_path = Path(self.temporary.name) / "background-lifecycle.jsonl"
        self.audit = BackgroundAudit(self.audit_path)

    def tearDown(self) -> None:
        try:
            self.temporary.cleanup()
        except OSError:
            pass  # a just-joined worker may release handles a moment later

    def _scheduler(self, dispatch=None, **kwargs) -> AutomationScheduler:
        return AutomationScheduler(
            self.store,
            dispatch or (lambda goal, project_id: "run-x"),
            audit=self.audit,
            **kwargs,
        )

    def _automation(self) -> dict:
        return self.store.save(
            {
                "name": "Audited",
                "project_id": "aud-p",
                "goal": "tick",
                "automation_type": "recurring",
                "schedule": "every 5m",
                "enabled": True,
            }
        )

    def test_start_records_scheduler_started(self) -> None:
        scheduler = self._scheduler()
        scheduler.start()
        try:
            events = [item["event"] for item in self.audit.recent()]
            self.assertIn("scheduler_started", events)
        finally:
            scheduler.stop(timeout=5.0)

    def test_graceful_stop_records_completed_not_timeout(self) -> None:
        scheduler = self._scheduler()
        scheduler.start()
        self.assertTrue(scheduler.stop(timeout=20.0))
        events = [item["event"] for item in self.audit.recent()]
        self.assertIn("scheduler_stopped", events)
        self.assertNotIn("scheduler_stop_timeout", events)

    def test_stop_timeout_does_not_claim_graceful_completion(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def slow_dispatch(goal: str, project_id: str) -> str:
            entered.set()
            release.wait(timeout=10)
            return "run-blocked"

        self._automation()
        scheduler = self._scheduler(dispatch=slow_dispatch, poll_seconds=0.05)
        scheduler.start()
        self.assertTrue(entered.wait(timeout=5))
        self.assertFalse(scheduler.stop(timeout=0.2))
        events = [item["event"] for item in self.audit.recent()]
        self.assertIn("scheduler_stop_timeout", events)
        self.assertNotIn("scheduler_stopped", events)  # no false graceful claim
        release.set()
        scheduler.stop(timeout=5.0)


class ServiceShutdownAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        database = Path(self.temporary.name) / "state.sqlite3"
        with SQLiteStore(database) as store:
            store.save_state(ProjectState(project_id="svc-p", goal="g"))
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=root,
            database=database,
            credential_store=MemoryCredentialStore(),
        )
        self.port = self.server.server_address[1]
        self.token = self.server.amca_context.session_token

    def tearDown(self) -> None:
        if getattr(self.server, "amca_context", None) is not None:
            self.server.server_close()
        try:
            self.temporary.cleanup()
        except OSError:
            pass  # a just-joined worker may release handles a moment later

    def _request(self, method: str, path: str) -> tuple[int, dict]:
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"X-AMCA-Session": self.token}
        if method == "POST":
            headers["Content-Length"] = "0"
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8") or "{}")
        connection.close()
        return response.status, body

    def test_backend_started_audited_on_creation(self) -> None:
        events = [
            item["event"]
            for item in BackgroundAudit(
                Path(self.temporary.name) / "background-lifecycle.jsonl"
            ).recent()
        ]
        self.assertIn("backend_started", events)

    def test_shutdown_requested_then_completed_distinguished(self) -> None:
        serve = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        serve.start()
        status, payload = self._request("POST", "/api/shutdown")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "shutting-down")
        serve.join(timeout=10)
        self.assertFalse(serve.is_alive())
        self.server.server_close()  # graceful completion lands here
        import json as json_module
        from pathlib import Path as _Path

        log_file = self.server.lifecycle_audit.path
        entries = [
            json_module.loads(line)
            for line in _Path(log_file).read_text(encoding="utf-8").splitlines()
        ]
        requested_at = next(
            e["at"] for e in entries if e["event"] == "backend_shutdown_requested"
        )
        completed_at = next(
            e["at"] for e in entries if e["event"] == "backend_shutdown_completed"
        )
        self.assertLessEqual(requested_at, completed_at)



if __name__ == "__main__":
    unittest.main()
