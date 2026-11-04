"""B1.4A tests: durable Review Queue for background automation outcomes."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from amca.automations import AutomationStore
from amca.background_audit import BackgroundAudit
from amca.domain import ProjectState
from amca.review_queue import ReviewQueueStore
from amca.scheduler import AutomationScheduler
from amca.service import (
    RunRegistry,
    record_automation_review_item,
    reconcile_automation_occurrences,
)
from amca.storage import SQLiteStore


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ReviewQueueStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ReviewQueueStore(Path(self.temporary.name) / "state.sqlite3")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_success_creates_one_info_item(self) -> None:
        item = self.store.record(
            source_key="occurrence:occ-1",
            source_type="automation_run_succeeded",
            project_id="p1",
            title="Automation 'Nightly' run succeeded",
            summary="Goal: summarize",
            severity="info",
            run_id="run-1",
            occurrence_id="occ-1",
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["status"], "unread")
        self.assertEqual(item["severity"], "info")
        # duplicate terminal event for the same occurrence -> no second item
        again = self.store.record(
            source_key="occurrence:occ-1",
            source_type="automation_run_succeeded",
            project_id="p1",
            title="Automation 'Nightly' run succeeded",
            severity="info",
            run_id="run-1",
            occurrence_id="occ-1",
        )
        self.assertIsNone(again)
        self.assertEqual(self.store.unread_count(), 1)

    def test_failure_creates_attention_item_with_bounded_detail(self) -> None:
        item = self.store.record(
            source_key="occurrence:occ-2",
            source_type="automation_run_failed",
            project_id="p1",
            title="Automation 'Nightly' run failed",
            summary="x" * 1000,
            severity="attention",
            run_id="run-2",
            occurrence_id="occ-2",
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["severity"], "attention")
        self.assertLessEqual(len(item["summary"]), 400)

    def test_read_and_dismiss_persist_across_reopen(self) -> None:
        item = self.store.record(
            source_key="occurrence:occ-3",
            source_type="automation_run_succeeded",
            project_id="p1",
            title="t",
        )
        assert item is not None
        self.assertTrue(self.store.mark_read(item["id"]))
        reopened = ReviewQueueStore(self.store.database)
        row = reopened.get_item(item["id"])
        assert row is not None
        self.assertEqual(row["status"], "read")
        self.assertTrue(reopened.dismiss(item["id"]))
        final = ReviewQueueStore(self.store.database).get_item(item["id"])
        assert final is not None
        self.assertEqual(final["status"], "dismissed")

    def test_dismiss_from_unread_is_allowed_and_terminal(self) -> None:
        item = self.store.record(
            source_key="occurrence:occ-4",
            source_type="automation_run_failed",
            project_id="p1",
            title="t",
            severity="attention",
        )
        assert item is not None
        self.assertTrue(self.store.dismiss(item["id"]))
        self.assertFalse(self.store.dismiss(item["id"]))  # dismissed is terminal
        self.assertFalse(self.store.mark_read(item["id"]))  # cannot resurrect

    def test_unread_count_scopes_by_project(self) -> None:
        for index, pid in enumerate(("p1", "p1", "p2")):
            self.store.record(
                source_key=f"occurrence:{index}:{pid}",
                source_type="automation_run_failed",
                project_id=pid,
                title="t",
                severity="attention",
            )
        self.assertEqual(self.store.unread_count("p1"), 2)
        self.assertEqual(self.store.unread_count("p2"), 1)
        self.assertEqual(self.store.unread_count(), 3)

    def test_concurrent_record_calls_create_single_item(self) -> None:
        results: list[str | None] = []
        lock = threading.Lock()

        def worker() -> None:
            item = self.store.record(
                source_key="occurrence:conc-1",
                source_type="automation_run_failed",
                project_id="p1",
                title="concurrent",
                severity="attention",
            )
            with lock:
                results.append(item["id"] if item else None)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        created = [r for r in results if r]
        self.assertEqual(len(created), 1)
        self.assertEqual(self.store.unread_count(), 1)


class SchedulerReviewSinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id="rv-p", goal="g"))
        self.store = AutomationStore(self.database)
        self.review_queue = ReviewQueueStore(self.database)
        self.dispatched: list[str] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _scheduler(self, condition_error: bool = False) -> AutomationScheduler:
        from amca.conditions import ConditionResult

        class Stub:
            def evaluate(self, condition):
                if condition_error:
                    return ConditionResult(
                        kind="file_exists", ok=False, error="workspace unavailable"
                    )
                return ConditionResult(
                    kind="file_exists", ok=True, value=False,
                    evidence={"kind": "file_exists", "path": "x", "exists": False},
                )

        return AutomationScheduler(
            self.store,
            lambda goal, project_id: self.dispatched.append("run") or "run-1",
            clock=lambda: _utc_now(),
            condition_evaluator_factory=lambda pid: Stub(),
            review_sink=lambda aid, pid, oid, kind, detail: self.review_queue.record(
                source_key=f"occurrence:{oid}:{kind}",
                source_type="automation_attention",
                project_id=pid,
                title="Automation needs attention",
                summary=detail,
                severity="attention",
                occurrence_id=oid,
            ),
        )

    def _conditional(self, schedule: str = "every 5m") -> dict:
        return self.store.save(
            {
                "name": "Gated",
                "project_id": "rv-p",
                "goal": "gated work",
                "automation_type": "conditional",
                "schedule": schedule,
                "enabled": True,
                "condition": {"version": 1, "kind": "file_exists", "path": "gate.txt"},
            }
        )

    def test_condition_error_creates_single_attention_item(self) -> None:
        automation = self._conditional()
        scheduler = self._scheduler(condition_error=True)
        scheduler.tick(now=_utc_now())
        items = self.review_queue.list_items(project_id="rv-p")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["severity"], "attention")
        self.assertIn("condition evaluation failed", items[0]["summary"])
        self.assertEqual(
            len(self.store.list_occurrences(automation["id"])), 1
        )

    def test_dispatch_refusal_creates_attention_item(self) -> None:
        self._conditional()
        failing = AutomationScheduler(
            self.store,
            lambda goal, project_id: (_ for _ in ()).throw(RuntimeError("backend gone")),
            clock=lambda: _utc_now(),
            condition_evaluator_factory=lambda pid: self._scheduler_ok_factory(),
            review_sink=lambda aid, pid, oid, kind, detail: self.review_queue.record(
                source_key=f"occurrence:{oid}:{kind}",
                source_type="automation_attention",
                project_id=pid,
                title="Automation needs attention",
                summary=detail,
                severity="attention",
                occurrence_id=oid,
            ),
        )
        failing.tick(now=_utc_now())
        items = self.review_queue.list_items(project_id="rv-p")
        self.assertEqual(len(items), 1)
        self.assertIn("dispatch refused", items[0]["summary"])

    def _scheduler_ok_factory(self):
        from amca.conditions import ConditionResult

        class Ok:
            def evaluate(self, condition):
                return ConditionResult(
                    kind="file_exists", ok=True, value=True,
                    evidence={"kind": "file_exists", "exists": True},
                )

        return Ok()


class TerminalRunReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id="term-p", goal="g"))
        self.store = AutomationStore(self.database)
        self.review_queue = ReviewQueueStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_succeeded_run_creates_one_info_item(self) -> None:
        automation = self.store.save(
            {
                "name": "Nightly",
                "project_id": "term-p",
                "goal": "summarize project",
                "automation_type": "recurring",
                "schedule": "every 5m",
                "enabled": True,
            }
        )
        claim = self.store.claim_occurrence(automation["id"], "2026-08-24T00:00:00")
        assert claim is not None
        self.store.mark_claim_dispatched(claim["id"], "run-ok")
        self.store.complete_claim_by_run("run-ok", "succeeded", None)
        # terminal review creation follows the canonical claim outcome
        claim_after = self.store.find_occurrence(automation["id"], "2026-08-24T00:00:00")
        assert claim_after is not None
        record_automation_review_item(
            self.review_queue,
            self.store,
            occurrence=claim_after,
            outcome="succeeded" if claim_after["status"] == "succeeded" else "failed",
        )
        items = self.review_queue.list_items(project_id="term-p")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["severity"], "info")
        self.assertIn("Nightly", items[0]["title"])
        # duplicate terminal event for the same run -> no second item
        record_automation_review_item(
            self.review_queue,
            self.store,
            occurrence=claim_after,
            outcome="succeeded",
        )
        self.assertEqual(len(self.review_queue.list_items(project_id="term-p")), 1)

    def test_reconciled_failure_creates_attention_item_without_duplicate(self) -> None:
        automation = self.store.save(
            {
                "name": "Reconciled",
                "project_id": "term-p",
                "goal": "restart survivor",
                "automation_type": "recurring",
                "schedule": "every 5m",
                "enabled": True,
            }
        )
        claim = self.store.claim_occurrence(automation["id"], "2026-07-01T00:00:00")
        assert claim is not None
        self.store.mark_claim_dispatched(claim["id"], "run-gone")
        # startup reconciliation with a missing run record -> interrupted failure
        changed = reconcile_automation_occurrences(
            self.store, lambda run_id: None, self.review_queue
        )
        self.assertEqual(changed, 1)
        items = self.review_queue.list_items(project_id="term-p")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["severity"], "attention")
        # repeated reconciliation must not duplicate the review item
        reconcile_automation_occurrences(
            self.store, lambda run_id: None, self.review_queue
        )
        self.assertEqual(len(self.review_queue.list_items(project_id="term-p")), 1)

    def test_sensitive_goal_text_is_not_amplified_but_goal_is_user_content(self) -> None:
        automation = self.store.save(
            {
                "name": "HasGoal",
                "project_id": "term-p",
                "goal": "process report for project alpha",
                "automation_type": "recurring",
                "schedule": "every 5m",
                "enabled": True,
            }
        )
        claim = self.store.claim_occurrence(automation["id"], "2026-07-02T00:00:00")
        assert claim is not None
        record_automation_review_item(
            self.review_queue,
            self.store,
            occurrence=claim,
            outcome="failed",
            detail="run failed: boom",
        )
        items = self.review_queue.list_items(project_id="term-p")
        self.assertEqual(len(items), 1)
        encoded = json.dumps(items[0])
        self.assertNotIn("password", encoded.lower())
        self.assertIn("boom", items[0]["error"] if "error" in items[0] else items[0]["summary"])


if __name__ == "__main__":
    unittest.main()
