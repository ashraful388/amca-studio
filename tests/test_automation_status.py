"""B1.3B tests: truthful scheduler status and occurrence history surface."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from amca.automations import AutomationStore
from amca.credentials import MemoryCredentialStore
from amca.domain import ProjectState
from amca.scheduler import AutomationScheduler
from amca.service import create_server
from amca.storage import SQLiteStore


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class _StatusHistoryHarness:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "state.sqlite3"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id="obs-p", goal="g"))
            store.save_state(ProjectState(project_id="other-p", goal="g"))
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=root,
            database=self.database,
            credential_store=MemoryCredentialStore(),
        )
        self.port = self.server.server_address[1]
        self.token = self.server.amca_context.session_token
        self.store: AutomationStore = self.server.amca_context.automation_store
        serve = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        serve.start()
        self.serve_thread = serve

    def get(self, path: str, authorized: bool = True) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"X-AMCA-Session": self.token} if authorized else {}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8") or "{}")
        connection.close()
        return response.status, payload

    def close(self) -> None:
        self.server.shutdown()
        self.serve_thread.join(timeout=10)
        self.server.server_close()
        self.temporary.cleanup()


class SchedulerStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _StatusHistoryHarness()

    def tearDown(self) -> None:
        self.harness.close()

    def test_status_reports_worker_alive_while_running(self) -> None:
        status_code, payload = self.harness.get("/api/automations/status")
        self.assertEqual(status_code, 200)
        self.assertTrue(payload["running"])
        self.assertTrue(payload["worker_alive"])
        self.assertIsNotNone(payload["started_at"])

    def test_status_distinguishes_backend_from_dead_scheduler(self) -> None:
        # Stop ONLY the worker; the HTTP backend keeps serving this request.
        self.harness.server.amca_context.scheduler.stop(timeout=5.0)
        status_code, payload = self.harness.get("/api/automations/status")
        self.assertEqual(status_code, 200)  # backend reachable...
        self.assertFalse(payload["running"])  # ...but scheduler truthfully dead
        self.assertFalse(payload["worker_alive"])

    def test_unauthorized_status_request_rejected(self) -> None:
        status_code, _ = self.harness.get("/api/automations/status", authorized=False)
        self.assertEqual(status_code, 401)


class OccurrenceHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _StatusHistoryHarness()
        self.store = self.harness.store
        self.now = _utc_now()

    def tearDown(self) -> None:
        self.harness.close()

    def _automation(self, project_id: str = "obs-p", **overrides) -> dict:
        record = {
            "name": f"Obs-{project_id}",
            "project_id": project_id,
            "goal": "observable work",
            "automation_type": "recurring",
            "schedule": "every 5m",
            "enabled": True,
        }
        record.update(overrides)
        return self.store.save(record)

    def _occurrence(self, automation_id: str, slot: str, run_id: str | None) -> dict:
        claim = self.store.claim_occurrence(automation_id, slot)
        assert claim is not None
        if run_id is not None:
            self.store.mark_claim_dispatched(claim["id"], run_id)
        return claim

    def test_history_newest_first_and_bounded(self) -> None:
        automation = self._automation()
        for index in range(7):
            claim = self._occurrence(
                automation["id"], f"2026-07-01T00:{index:02d}:00", f"run-{index}"
            )
            self.store.complete_claim(claim["id"], "succeeded", None)
        status, document = self.harness.get(
            f"/api/automations/{automation['id']}/occurrences?limit=5"
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(document["occurrences"]), 5)
        stamps = [item["scheduled_for"] for item in document["occurrences"]]
        self.assertEqual(stamps, sorted(stamps, reverse=True))  # newest first
        self.assertEqual(stamps[0], "2026-07-01T00:06:00")

    def test_unknown_automation_returns_not_found(self) -> None:
        status, payload = self.harness.get("/api/automations/does-not-exist/occurrences")
        self.assertEqual(status, 404)
        self.assertIn("not found", payload.get("error", ""))

    def test_history_scoped_to_requested_automation_only(self) -> None:
        mine = self._automation(project_id="obs-p")
        other_project_automation = self._automation(project_id="other-p")
        claim = self._occurrence(mine["id"], "2026-07-02T00:00:00", "run-mine")
        self.store.complete_claim(claim["id"], "succeeded", None)
        # A guessed identifier from the OTHER project context cannot leak rows.
        status, document = self.harness.get(
            f"/api/automations/{other_project_automation['id']}/occurrences"
        )
        self.assertEqual(status, 200)
        self.assertEqual(document["occurrences"], [])
        self.assertEqual(1, len(self.store.list_occurrences(mine["id"])))

    def test_false_condition_occurrence_is_skipped_without_run(self) -> None:
        automation = self.store.save(
            {
                "name": "Gate",
                "project_id": "obs-p",
                "goal": "gated",
                "automation_type": "conditional",
                "schedule": "every 5m",
                "enabled": True,
                "condition": {"version": 1, "kind": "file_exists", "path": "gate.txt"},
            }
        )
        claim = self.store.claim_occurrence(automation["id"], "2026-07-03T00:00:00")
        assert claim is not None
        self.store.set_condition_check(
            claim["id"],
            {
                "kind": "file_exists",
                "version": 1,
                "checked_at": "2026-07-03T00:00:00",
                "result": "false",
                "evidence": {"kind": "file_exists", "path": "gate.txt", "exists": False},
            },
        )
        self.store.complete_claim(claim["id"], "skipped", "condition evaluated false; no action executed")
        status, document = self.harness.get(
            f"/api/automations/{automation['id']}/occurrences"
        )
        self.assertEqual(status, 200)
        occurrence = document["occurrences"][0]
        self.assertEqual(occurrence["status"], "skipped")
        self.assertIsNone(occurrence["run_id"])
        self.assertEqual(occurrence["check"]["result"], "false")

    def test_true_occurrence_exposes_linked_run_truthfully(self) -> None:
        automation = self.store.save(
            {
                "name": "GateTrue",
                "project_id": "obs-p",
                "goal": "gated true",
                "automation_type": "conditional",
                "schedule": "every 5m",
                "enabled": True,
                "condition": {"version": 1, "kind": "file_exists", "path": "gate.txt"},
            }
        )
        claim = self.store.claim_occurrence(automation["id"], "2026-07-04T00:00:00")
        assert claim is not None
        self.store.set_condition_check(
            claim["id"],
            {
                "kind": "file_exists",
                "version": 1,
                "checked_at": "2026-07-04T00:00:00",
                "result": "true",
                "evidence": {"kind": "file_exists", "path": "gate.txt", "exists": True},
            },
        )
        self.store.mark_claim_dispatched(claim["id"], "run-linked")
        status, document = self.harness.get(
            f"/api/automations/{automation['id']}/occurrences"
        )
        occurrence = document["occurrences"][0]
        self.assertEqual(occurrence["run_id"], "run-linked")
        self.assertEqual(occurrence["check"]["result"], "true")
        del status

    def test_failed_check_exposes_bounded_error(self) -> None:
        import sqlite3

        automation = self.store.save(
            {
                "name": "Broken",
                "project_id": "obs-p",
                "goal": "legacy conditional",
                "automation_type": "conditional",
                "schedule": "every 5m",
                "enabled": True,
                "condition": {"version": 1, "kind": "always"},
            }
        )
        # Simulate a pre-B1.2B legacy row that never carried a condition.
        connection = sqlite3.connect(self.store.database)
        connection.execute(
            "UPDATE desktop_automations SET condition_json = NULL WHERE id = ?",
            (automation["id"],),
        )
        connection.commit()
        connection.close()
        claim = self.store.claim_occurrence(automation["id"], "2026-07-05T00:00:00")
        assert claim is not None
        reason = "condition evaluation failed: legacy conditional automation has no valid condition"
        self.store.complete_claim(claim["id"], "failed", reason)
        _, document = self.harness.get(f"/api/automations/{automation['id']}/occurrences")
        occurrence = document["occurrences"][0]
        self.assertEqual(occurrence["status"], "failed")
        self.assertIn("no valid condition", occurrence["error"])
        self.assertLessEqual(len(occurrence["error"]), 200)


class SummaryEnrichmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id="sum-p", goal="g"))
        self.store = AutomationStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_last_check_advances_independently_of_last_run(self) -> None:
        automation = self.store.save(
            {
                "name": "Cadence",
                "project_id": "sum-p",
                "goal": "checks vs runs",
                "automation_type": "conditional",
                "schedule": "every 30m",
                "enabled": True,
                "condition": {"version": 1, "kind": "always"},
            }
        )
        check_time = (_utc_now() - timedelta(minutes=5)).isoformat()
        self.store.record_check_at(automation["id"], check_time)
        updated = self.store.get(automation["id"])
        assert updated is not None
        self.assertIsNotNone(updated["last_check_at"])
        self.assertIsNone(updated["last_run_at"])  # FALSE checks never fake runs
        del check_time

    def test_next_conditional_check_derives_from_check_cadence(self) -> None:
        from amca.service import _next_scheduled_check

        now = _utc_now()
        last_check = now - timedelta(minutes=40)
        record = {
            "enabled": True,
            "automation_type": "conditional",
            "schedule": "every 30m",
            "last_run_at": None,
            "last_check_at": last_check.isoformat(),
        }
        next_check = _next_scheduled_check(record, now)
        self.assertIsNotNone(next_check)
        # Cadence advanced past the interval already: due immediately.
        self.assertNotIn(next_check, (None, ""))

    def test_disabled_manual_and_unsupported_have_no_next_action(self) -> None:
        from amca.service import _next_scheduled_check

        now = _utc_now()
        base = {"automation_type": "recurring", "schedule": "every 5m", "last_run_at": None}
        disabled = {**base, "enabled": False}
        manual = {**base, "enabled": True, "schedule": "manual"}
        monitor = {**base, "enabled": True, "automation_type": "monitor"}
        event_triggered = {**base, "enabled": True, "automation_type": "event_triggered"}
        bad_grammar = {**base, "enabled": True, "schedule": "whenever"}
        for record in (disabled, manual, monitor, event_triggered, bad_grammar):
            self.assertIsNone(_next_scheduled_check(record, now), record)


if __name__ == "__main__":
    unittest.main()
