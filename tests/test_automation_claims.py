"""B1.2A tests: durable occurrence claiming, crash truthfulness, update races."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from amca.automations import (
    AUTOMATION_TYPES,
    INTERRUPTED_CLAIM_ERROR,
    AutomationStore,
)
from amca.domain import ProjectState
from amca.scheduler import AutomationScheduler, next_run_at, occurrence_slot
from amca.service import RunJob, RunRegistry, reconcile_automation_occurrences
from amca.storage import SQLiteStore


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class OccurrenceSlotTests(unittest.TestCase):
    """The claim key must be a deterministic function of durable state."""

    def test_interval_slot_is_unclamped_next_cycle(self) -> None:
        now = _utc_now()
        reference = now - timedelta(hours=3)
        slot = occurrence_slot("every 1h", "recurring", reference.isoformat(), None, now)
        self.assertEqual(slot, reference + timedelta(hours=1))
        # Stable across a later restart at a different wall-clock.
        later = occurrence_slot(
            "every 1h", "recurring", reference.isoformat(), None, now + timedelta(minutes=10)
        )
        self.assertEqual(slot, later)

    def test_interval_never_run_anchors_on_created_at(self) -> None:
        now = _utc_now()
        created = now - timedelta(hours=5)
        first = occurrence_slot("every 30m", "recurring", None, created.isoformat(), now)
        self.assertIsNotNone(first)
        # Same slot recomputed minutes later by a restarted process.
        again = occurrence_slot(
            "every 30m", "recurring", None, created.isoformat(), now + timedelta(minutes=7)
        )
        self.assertEqual(first, again)

    def test_daily_and_once_slots_are_calendar_deterministic(self) -> None:
        now = _utc_now().replace(hour=8, minute=0, second=0, microsecond=0)
        self.assertEqual(
            occurrence_slot("daily 14:30", "recurring", None, None, now),
            now.replace(hour=14, minute=30),
        )
        moment = _utc_now() - timedelta(minutes=5)
        self.assertEqual(
            occurrence_slot(moment.isoformat(), "once", None, None, _utc_now()), moment
        )

    def test_manual_has_no_slot(self) -> None:
        self.assertIsNone(occurrence_slot("manual", "recurring", None, None, _utc_now()))

    def test_unknown_grammar_raises(self) -> None:
        with self.assertRaises(ValueError):
            occurrence_slot("whenever", "recurring", None, None, _utc_now())


class OccurrenceClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id="claim-p", goal="g"))
        self.store = AutomationStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _automation(self, **overrides) -> dict:
        record = {
            "name": "Claimed",
            "project_id": "claim-p",
            "goal": "once and only once",
            "automation_type": "recurring",
            "schedule": "every 5m",
            "enabled": True,
        }
        record.update(overrides)
        return self.store.save(record)

    def test_same_occurrence_claims_exactly_once(self) -> None:
        automation = self._automation()
        first = self.store.claim_occurrence(automation["id"], "2026-01-01T00:00:00")
        second = self.store.claim_occurrence(automation["id"], "2026-01-01T00:00:00")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(self.store.list_occurrences(automation["id"])), 1)

    def test_claim_rejected_when_disabled_or_missing(self) -> None:
        automation = self._automation(enabled=False)
        self.assertIsNone(
            self.store.claim_occurrence(automation["id"], "2026-01-01T00:00:00")
        )
        self.assertIsNone(
            self.store.claim_occurrence("does-not-exist", "2026-01-01T00:00:00")
        )

    def test_interrupted_claim_blocks_replay_and_surfaces_failure(self) -> None:
        automation = self._automation()
        claim = self.store.claim_occurrence(automation["id"], "2026-01-01T00:00:00")
        assert claim is not None
        self.store.mark_claim_dispatched(claim["id"], "run-crash")
        # Simulate process death: a fresh store leaves the run-linked claim for
        # canonical reconciliation, which finds no run record and freezes it.
        reopened = AutomationStore(self.database)
        frozen = reopened.find_occurrence(automation["id"], "2026-01-01T00:00:00")
        assert frozen is not None
        self.assertEqual(frozen["status"], "running")
        changed = reconcile_automation_occurrences(reopened, lambda run_id: None)
        self.assertEqual(changed, 1)
        frozen = reopened.find_occurrence(automation["id"], "2026-01-01T00:00:00")
        assert frozen is not None
        self.assertEqual(frozen["status"], "failed")
        self.assertIn("interrupted", frozen["error"])
        self.assertEqual(frozen["run_id"], "run-crash")
        self.assertIsNone(
            reopened.claim_occurrence(automation["id"], "2026-01-01T00:00:00")
        )


class SchedulerClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id="sched-p", goal="g"))
        self.store = AutomationStore(self.database)
        self.dispatched: list[tuple[str, str]] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _dispatcher(self, fail: bool = False):
        def dispatch(goal: str, project_id: str) -> str:
            if fail:
                raise RuntimeError("dispatch path unavailable")
            run_id = f"run-{len(self.dispatched) + 1}"
            self.dispatched.append((goal, project_id))
            return run_id

        return dispatch

    def _scheduler(self, **kwargs) -> AutomationScheduler:
        fail = kwargs.pop("fail", False)
        return AutomationScheduler(
            self.store, self._dispatcher(fail=fail), clock=lambda: self.now, **kwargs
        )

    def _recurring(self, schedule: str = "every 5m", **overrides) -> dict:
        record = {
            "name": "Recurring",
            "project_id": "sched-p",
            "goal": "tick work",
            "automation_type": "recurring",
            "schedule": schedule,
            "enabled": True,
        }
        record.update(overrides)
        return self.store.save(record)

    def test_repeated_tick_never_executes_same_occurrence_twice(self) -> None:
        self.now = _utc_now() - timedelta(minutes=10)
        self._recurring()
        scheduler = self._scheduler()
        first = scheduler.tick(now=_utc_now())
        second = scheduler.tick(now=_utc_now())
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(self.dispatched), 1)
        self.assertEqual(len(self.store.list_occurrences()), 1)

    def test_restart_after_terminal_occurrence_does_not_replay(self) -> None:
        self._recurring("every 60m")
        fired_at = _utc_now() - timedelta(hours=2)
        scheduler = AutomationScheduler(self.store, self._dispatcher(), clock=lambda: fired_at)
        scheduler.tick(now=fired_at)
        self.assertEqual(len(self.dispatched), 1)
        # Restart well after the interval: the next legitimate occurrence fires;
        # the completed one does not.
        self.now = fired_at + timedelta(hours=3)
        revived = AutomationScheduler(self.store, self._dispatcher(), clock=lambda: self.now)
        dispatched = revived.tick(now=self.now)
        self.assertEqual(len(dispatched), 1)
        self.assertNotEqual(dispatched[0]["scheduled_for"], self.store.list()[0]["last_run_at"])

    def test_crash_between_claim_and_dispatch_does_not_duplicate(self) -> None:
        self.now = _utc_now()
        automation = self._recurring()
        scheduler = self._scheduler()
        # Claim commits, then the process dies before dispatch completes.
        due = next_run_at(automation["schedule"], "recurring", None, self.now)
        assert due is not None
        slot = occurrence_slot(
            automation["schedule"],
            "recurring",
            automation["last_run_at"],
            automation["created_at"],
            self.now,
        )
        claim = self.store.claim_occurrence(automation["id"], slot.isoformat())
        self.assertIsNotNone(claim)
        del scheduler, due
        # Restarted process: reconciliation freezes the orphan, tick refuses.
        restarted_store = AutomationStore(self.database)
        restarted_scheduler = AutomationScheduler(
            restarted_store, self._dispatcher(), clock=lambda: self.now
        )
        self.assertEqual(restarted_scheduler.tick(now=self.now), [])
        self.assertEqual(self.dispatched, [])

    def test_concurrent_claim_attempts_yield_one_winner(self) -> None:
        automation = self._recurring()
        # Two independent store handles race for the same occurrence; SQLite's
        # BEGIN IMMEDIATE serializes them so exactly one claim survives.
        rival_store = AutomationStore(self.database)
        winner = self.store.claim_occurrence(automation["id"], "2026-03-04T00:00:00")
        loser = rival_store.claim_occurrence(automation["id"], "2026-03-04T00:00:00")
        self.assertIsNotNone(winner)
        self.assertIsNone(loser)

    def test_disable_after_snapshot_before_claim_prevents_dispatch(self) -> None:
        automation = self._recurring()
        snapshot = [automation]  # scheduler's stale view says enabled

        class RacingStore(AutomationStore):
            def list(self):  # noqa: D102 - deliberate stale snapshot
                return snapshot

        racing = RacingStore(self.database)
        racing.__init__(self.database)  # reconcile like a real constructor
        # The user disables after the snapshot was taken.
        self.store.save({**(self.store.get(automation["id"]) or {}), "enabled": False})
        scheduler = AutomationScheduler(racing, self._dispatcher(), clock=lambda: self.now)
        self.now = _utc_now()
        self.assertEqual(scheduler.tick(now=self.now), [])
        self.assertEqual(self.dispatched, [])
        self.assertFalse(self.store.get(automation["id"])["enabled"])

    def test_bookkeeping_cannot_overwrite_newer_user_changes(self) -> None:
        self.now = _utc_now() - timedelta(minutes=10)
        automation = self._recurring()
        scheduler = self._scheduler()
        scheduler.tick(now=_utc_now())
        self.assertEqual(len(self.dispatched), 1)
        # A user rename lands right before the scheduler's post-run writes.
        user_edit = self.store.save({**(self.store.get(automation["id"]) or {}), "name": "User Renamed"})
        scheduler.store.set_enabled(automation["id"], True)  # stale bookkeeping toggle
        final = self.store.get(automation["id"])
        assert final is not None
        self.assertEqual(final["name"], "User Renamed")
        self.assertGreaterEqual(final["revision"], user_edit["revision"])
        self.assertTrue(final["enabled"])

    def test_revision_increments_deterministically(self) -> None:
        automation = self._recurring()
        self.assertEqual(automation["revision"], 1)
        edited = self.store.save({**automation, "goal": "edited goal"})
        self.assertEqual(edited["revision"], 2)
        toggled = self.store.set_enabled(automation["id"], False)
        self.assertEqual(toggled["revision"], 3)
        marked = self.store.mark_run(automation["id"], "run-r1")
        self.assertEqual(marked["revision"], 4)

    def test_disabled_stays_disabled_after_scheduler_activity(self) -> None:
        self.now = _utc_now()
        automation = self._recurring(enabled=False)
        scheduler = self._scheduler()
        for _ in range(3):
            scheduler.tick(now=self.now + timedelta(minutes=5))
        final = self.store.get(automation["id"])
        assert final is not None
        self.assertFalse(final["enabled"])
        self.assertEqual(self.dispatched, [])
        self.assertEqual(self.store.list_occurrences(automation["id"]), [])

    def test_once_automation_still_disables_after_firing(self) -> None:
        past = (_utc_now() - timedelta(minutes=1)).isoformat()
        self.now = _utc_now()
        self.store.save(
            {
                "name": "One-shot",
                "project_id": "sched-p",
                "goal": "one time only",
                "automation_type": "once",
                "schedule": past,
                "enabled": True,
            }
        )
        scheduler = self._scheduler()
        dispatched = scheduler.tick(now=_utc_now())
        self.assertEqual(len(dispatched), 1)
        stored = self.store.list()[0]
        self.assertFalse(stored["enabled"])
        self.assertEqual(scheduler.tick(now=_utc_now()), [])
        self.assertEqual(len(self.dispatched), 1)

    def test_recurring_continues_to_next_distinct_occurrence(self) -> None:
        self.now = start = _utc_now() - timedelta(minutes=30)
        self._recurring("every 10m")
        scheduler = self._scheduler()
        first = scheduler.tick(now=start)
        second_tick_time = start + timedelta(minutes=12)
        second = AutomationScheduler(
            self.store, self._dispatcher(), clock=lambda: second_tick_time
        ).tick(now=second_tick_time)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0]["scheduled_for"], second[0]["scheduled_for"])
        self.assertEqual(len(self.dispatched), 2)

    def test_manual_schedule_never_auto_fires_or_claims(self) -> None:
        self.now = _utc_now()
        self._recurring(schedule="manual")
        scheduler = self._scheduler()
        self.assertEqual(scheduler.tick(now=self.now), [])
        self.assertEqual(self.store.list_occurrences(), [])

    def test_dispatch_refusal_retains_provenance_and_allows_retry(self) -> None:
        self.now = _utc_now() - timedelta(minutes=10)
        automation = self._recurring()
        failing = AutomationScheduler(
            self.store, self._dispatcher(fail=True), clock=lambda: self.now
        )
        self.assertEqual(failing.tick(now=_utc_now()), [])
        stored = self.store.get(automation["id"])
        assert stored is not None
        self.assertTrue(stored["metadata"].get("recent_skips"))
        # Refused dispatch keeps an explicit retained failure record...
        occurrences = self.store.list_occurrences(automation["id"])
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0]["status"], "failed")
        self.assertTrue(occurrences[0]["error"].startswith("dispatch refused:"))
        # ...which the very next healthy tick may revive IN PLACE (same row).
        healthy = AutomationScheduler(self.store, self._dispatcher(), clock=lambda: self.now)
        dispatched = healthy.tick(now=_utc_now())
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]["occurrence_id"], occurrences[0]["id"])
        self.assertEqual(len(self.store.list_occurrences(automation["id"])), 1)

    def test_stop_gate_full_scenario(self) -> None:
        """O1 claims/runs exactly once across ticks and restarts; O2 runs after."""

        self.now = start = _utc_now() - timedelta(hours=1)
        self._recurring("every 30m")
        scheduler = self._scheduler()

        o1 = scheduler.tick(now=start)
        self.assertEqual(len(o1), 1)
        # Same instant re-polled (same tick/poll race): cannot claim O1 again.
        self.assertEqual(scheduler.tick(now=start), [])
        # Restart: O1 still cannot dispatch again...
        restarted_store = AutomationStore(self.database)
        restarted = AutomationScheduler(
            restarted_store, self._dispatcher(), clock=lambda: start + timedelta(minutes=1)
        )
        self.assertEqual(restarted.tick(now=start + timedelta(minutes=1)), [])
        # ...while the next legitimate occurrence O2 can claim and run.
        o2_time = start + timedelta(minutes=31)
        final = AutomationScheduler(
            restarted_store, self._dispatcher(), clock=lambda: o2_time
        ).tick(now=o2_time)
        self.assertEqual(len(final), 1)
        self.assertNotEqual(final[0]["scheduled_for"], o1[0]["scheduled_for"])
        self.assertEqual(len(self.dispatched), 2)


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_legacy_database_migrates_idempotently_and_preserves_rows(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.execute(
            """CREATE TABLE desktop_automations (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, project_id TEXT NOT NULL,
                goal TEXT NOT NULL, automation_type TEXT NOT NULL, schedule TEXT NOT NULL,
                enabled INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                last_run_at TEXT, last_run_id TEXT, metadata_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO desktop_automations VALUES ('legacy-1','Legacy','p','g','recurring','manual',0,'c','u',NULL,NULL,'{}')"
        )
        connection.commit()
        connection.close()

        store = AutomationStore(self.database)
        legacy = store.get("legacy-1")
        assert legacy is not None
        self.assertEqual(legacy["revision"], 0)
        self.assertIn("conditional", AUTOMATION_TYPES)
        # Reopen twice: migration must be idempotent, history intact.
        reopened = AutomationStore(self.database)
        again = reopened.get("legacy-1")
        assert again is not None
        self.assertEqual(again["created_at"], legacy["created_at"])
        self.assertEqual(again["last_run_at"], legacy["last_run_at"])
        self.assertEqual(again["revision"], 0)


class TerminalOutcomeTests(unittest.TestCase):
    """ZC-B1.2A.1: canonical RunJob outcomes drive occurrence provenance."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id="term-p", goal="g"))
        self.store = AutomationStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _running_claim(self, slot: str, run_id: str) -> dict:
        automation = self.store.save(
            {
                "name": f"A-{slot}",
                "project_id": "term-p",
                "goal": "terminal truth",
                "automation_type": "recurring",
                "schedule": "every 5m",
                "enabled": True,
            }
        )
        claim = self.store.claim_occurrence(automation["id"], slot)
        assert claim is not None
        self.store.mark_claim_dispatched(claim["id"], run_id)
        return claim

    def test_run_success_maps_occurrence_to_succeeded(self) -> None:
        claim = self._running_claim("2026-06-01T00:00:00", "run-ok")
        self.assertEqual(
            self.store.complete_claim_by_run("run-ok", "succeeded", None), 1
        )
        final = self.store.get_occurrence(claim["id"])
        assert final is not None
        self.assertEqual(final["status"], "succeeded")
        self.assertIsNone(final["error"])
        self.assertIsNotNone(final["completed_at"])

    def test_failed_partial_cancelled_map_truthfully(self) -> None:
        failed = self._running_claim("2026-06-01T01:00:00", "run-f")
        partial = self._running_claim("2026-06-01T02:00:00", "run-p")
        cancelled = self._running_claim("2026-06-01T03:00:00", "run-c")
        self.assertEqual(
            self.store.complete_claim_by_run("run-f", "failed", "boom"), 1
        )
        self.assertEqual(
            self.store.complete_claim_by_run("run-p", "partial", None), 1
        )
        self.assertEqual(
            self.store.complete_claim_by_run("run-c", "cancelled", None), 1
        )
        failed_row = self.store.get_occurrence(failed["id"])
        assert failed_row is not None
        self.assertEqual(failed_row["status"], "failed")
        self.assertIn("boom", failed_row["error"])
        for row_id, run in ((partial["id"], "partial"), (cancelled["id"], "cancelled")):
            row = self.store.get_occurrence(row_id)
            assert row is not None
            self.assertEqual(row["status"], "failed")
            self.assertIn(f"run {run}", row["error"])

    def test_nonterminal_job_leaves_claim_running(self) -> None:
        claim = self._running_claim("2026-06-01T04:00:00", "run-live")
        self.assertEqual(self.store.complete_claim_by_run("run-live", "queued", None), 0)
        self.assertEqual(self.store.complete_claim_by_run("run-live", "running", None), 0)
        row = self.store.get_occurrence(claim["id"])
        assert row is not None
        self.assertEqual(row["status"], "running")

    def test_succeeded_job_survives_restart_reconciliation(self) -> None:
        self._running_claim("2026-06-01T05:00:00", "run-ok")
        self.store.complete_claim_by_run("run-ok", "succeeded", None)
        reopened = AutomationStore(self.database)
        changed = reconcile_automation_occurrences(reopened, lambda rid: None)
        self.assertEqual(changed, 0)
        occurrences = reopened.list_occurrences()
        self.assertEqual(occurrences[0]["status"], "succeeded")

    def test_reconciliation_follows_canonical_registry_truth(self) -> None:
        succeeded = self._running_claim("2026-06-01T06:00:00", "r-ok")
        stale = self._running_claim("2026-06-01T07:00:00", "r-stale")
        missing = self._running_claim("2026-06-01T08:00:00", "r-missing")

        def registry(run_id):
            if run_id == "r-ok":
                return {"status": "succeeded", "error": None}
            if run_id == "r-stale":
                # Mirrors RunRegistry._load_jobs restart recovery output.
                return {
                    "status": "failed",
                    "error": "The desktop application stopped before this run completed.",
                }
            return None

        store = AutomationStore(self.database)
        changed = reconcile_automation_occurrences(store, registry)
        self.assertEqual(changed, 3)
        ok_row = store.get_occurrence(succeeded["id"])
        assert ok_row is not None
        self.assertEqual(ok_row["status"], "succeeded")
        stale_row = store.get_occurrence(stale["id"])
        assert stale_row is not None
        self.assertEqual(stale_row["status"], "failed")
        self.assertIn("stopped before this run completed", stale_row["error"])
        missing_row = store.get_occurrence(missing["id"])
        assert missing_row is not None
        self.assertEqual(missing_row["status"], "failed")
        self.assertIn("interrupted", missing_row["error"])

    def test_crash_before_dispatch_freezes_on_reopen(self) -> None:
        automation = self.store.save(
            {
                "name": "PreDispatch",
                "project_id": "term-p",
                "goal": "died early",
                "automation_type": "recurring",
                "schedule": "every 5m",
                "enabled": True,
            }
        )
        claim = self.store.claim_occurrence(automation["id"], "2026-06-01T09:00:00")
        assert claim is not None
        reopened = AutomationStore(self.database)
        frozen = reopened.find_occurrence(automation["id"], "2026-06-01T09:00:00")
        assert frozen is not None
        self.assertEqual(frozen["status"], "failed")
        self.assertEqual(frozen["error"], INTERRUPTED_CLAIM_ERROR)
        self.assertIsNone(frozen["run_id"])

    def test_reconciliation_is_idempotent_and_completed_at_stable(self) -> None:
        self._running_claim("2026-06-01T10:00:00", "r-idem")
        store = AutomationStore(self.database)
        registry = {"r-idem": {"status": "failed", "error": "boom"}}
        reconcile_automation_occurrences(store, lambda rid: registry.get(rid))
        first = store.list_occurrences()[0]
        self.assertEqual(first["status"], "failed")
        for _ in range(3):
            self.assertEqual(
                reconcile_automation_occurrences(store, lambda rid: registry.get(rid)), 0
            )
        again = store.list_occurrences()[0]
        self.assertEqual(again["completed_at"], first["completed_at"])
        self.assertEqual(again["error"], first["error"])

    def test_terminal_occurrence_never_reclaimed(self) -> None:
        automation = self.store.save(
            {
                "name": "Done",
                "project_id": "term-p",
                "goal": "finished",
                "automation_type": "recurring",
                "schedule": "every 5m",
                "enabled": True,
            }
        )
        claim = self.store.claim_occurrence(automation["id"], "2026-06-01T11:00:00")
        assert claim is not None
        self.store.complete_claim(claim["id"], "succeeded", None)
        self.assertIsNone(
            self.store.claim_occurrence(automation["id"], "2026-06-01T11:00:00")
        )

    def test_registry_terminal_event_updates_linked_claim(self) -> None:
        claim = self._running_claim("2026-06-01T12:00:00", "r-cb")
        events: list[tuple[str, str]] = []
        registry = RunRegistry(
            root=self.temporary.name,
            database=self.database,
            run_dir=Path(self.temporary.name) / "runs",
            on_run_terminal=lambda rid, status, error: (
                events.append((rid, status)),
                self.store.complete_claim_by_run(rid, status, error),
            ),
        )
        registry._jobs["r-cb"] = RunJob(run_id="r-cb", project_id="term-p", goal="g")
        registry._update("r-cb", status="succeeded")
        self.assertEqual(events, [("r-cb", "succeeded")])
        row = self.store.get_occurrence(claim["id"])
        assert row is not None
        self.assertEqual(row["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
