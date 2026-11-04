"""B1 scheduler tests: fires without Run Now, missed policy, recovery."""

from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from amca.automations import AutomationStore
from amca.domain import ProjectState
from amca.scheduler import AutomationScheduler, next_run_at
from amca.storage import SQLiteStore


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class NextRunCalculatorTests(unittest.TestCase):
    def test_manual_never_fires(self) -> None:
        self.assertIsNone(next_run_at("manual", "recurring", None, _utc_now()))

    def test_interval_never_run_is_due_immediately(self) -> None:
        now = _utc_now()
        self.assertEqual(
            next_run_at("every 5m", "recurring", None, now), now
        )

    def test_interval_catches_up_to_now(self) -> None:
        now = _utc_now()
        stale = now - timedelta(hours=3)
        self.assertEqual(next_run_at("every 1h", "recurring", stale.isoformat(), now), now)

    def test_daily_before_target_returns_today(self) -> None:
        now = _utc_now().replace(hour=8, minute=0, second=0, microsecond=0)
        self.assertEqual(
            next_run_at("daily 14:30", "recurring", None, now),
            now.replace(hour=14, minute=30),
        )

    def test_daily_after_target_never_run_returns_tomorrow(self) -> None:
        now = _utc_now().replace(hour=20, minute=0, second=0, microsecond=0)
        expected = (now + timedelta(days=1)).replace(hour=14, minute=30)
        self.assertEqual(
            next_run_at("daily 14:30", "recurring", None, now), expected
        )

    def test_daily_prior_fire_before_slot_catches_up_today(self) -> None:
        now = _utc_now().replace(hour=20, minute=0, second=0, microsecond=0)
        prior = now.replace(hour=8, minute=0)  # fired earlier today, slot 14:30 passed unfired? No: 14:30 > 08:00 means slot already passed without a fire AFTER it
        # Prior fire at 08:00 is before today's 14:30 target: the slot was missed.
        self.assertEqual(
            next_run_at("daily 14:30", "recurring", prior.isoformat(), now),
            now.replace(hour=14, minute=30),
        )

    def test_once_parses_exact_datetime(self) -> None:
        moment = _utc_now() - timedelta(minutes=5)
        result = next_run_at(moment.isoformat(), "once", None, _utc_now())
        self.assertEqual(result, moment)

    def test_unknown_grammar_raises(self) -> None:
        with self.assertRaises(ValueError):
            next_run_at("whenever", "recurring", None, _utc_now())


class SchedulerTickTests(unittest.TestCase):
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
        return AutomationScheduler(
            self.store,
            self._dispatcher(**{"fail": kwargs.pop("fail", False)}),
            clock=lambda: self.now,
            **kwargs,
        )

    def test_enabled_automation_fires_without_run_now(self) -> None:
        self.now = _utc_now() - timedelta(minutes=10)
        self.store.save(
            {
                "name": "Nightly",
                "project_id": "sched-p",
                "goal": "Summarize project state",
                "automation_type": "recurring",
                "schedule": "every 5m",
                "enabled": True,
            }
        )
        scheduler = self._scheduler()
        dispatched = scheduler.tick(now=_utc_now())
        self.assertEqual(len(dispatched), 1)
        self.assertFalse(dispatched[0]["missed"])
        self.assertEqual(len(self.dispatched), 1)
        stored = self.store.list()[0]
        self.assertEqual(stored["last_run_id"], "run-1")

    def test_once_type_disables_after_firing(self)-> None:
        self.now = _utc_now()
        past = (_utc_now() - timedelta(minutes=1)).isoformat()
        self.store.save(
            {
                "name": "One-shot",
                "project_id": "sched-p",
                "goal": "one time",
                "automation_type": "once",
                "schedule": past,
                "enabled": True,
            }
        )
        scheduler = self._scheduler()
        scheduler.tick(now=_utc_now())
        stored = self.store.list()[0]
        self.assertFalse(stored["enabled"])
        self.assertEqual(stored["last_run_id"], "run-1")
        # A second tick must not fire it again.
        scheduler.tick(now=_utc_now())
        self.assertEqual(len(self.dispatched), 1)

    def test_missed_run_flagged_in_metadata(self) -> None:
        self.now = _utc_now()
        stale = (_utc_now() - timedelta(hours=2)).isoformat()
        self.store.save(
            {
                "name": "Lagging",
                "project_id": "sched-p",
                "goal": "catch up",
                "automation_type": "recurring",
                "schedule": "every 30m",
                "enabled": True,
                "last_run_at": stale,
            }
        )
        scheduler = self._scheduler()
        dispatched = scheduler.tick(now=_utc_now())
        self.assertTrue(dispatched[0]["missed"])
        stored = self.store.list()[0]
        self.assertTrue(stored["metadata"].get("missed"))

    def test_disabled_and_manual_never_dispatch(self) -> None:
        self.now = _utc_now()
        self.store.save(
            {
                "name": "Off",
                "project_id": "sched-p",
                "goal": "nope",
                "automation_type": "recurring",
                "schedule": "every 5m",
                "enabled": False,
            }
        )
        self.store.save(
            {
                "name": "Manual",
                "project_id": "sched-p",
                "goal": "also nope",
                "automation_type": "recurring",
                "schedule": "manual",
                "enabled": True,
            }
        )
        scheduler = self._scheduler()
        self.assertEqual(scheduler.tick(now=_utc_now()), [])
        self.assertEqual(self.dispatched, [])

    def test_restart_recovery_redispatches_due_work(self) -> None:
        # First "process" instance schedules and dies before firing.
        self.now = _utc_now()
        self.store.save(
            {
                "name": "Survivor",
                "project_id": "sched-p",
                "goal": "survive restart",
                "automation_type": "recurring",
                "schedule": "every 1m",
                "enabled": True,
            }
        )
        dead_scheduler = self._scheduler(fail=True)
        dead_scheduler.tick(now=_utc_now())  # dispatch fails; stays due
        self.assertEqual(len(self.dispatched), 0)

        # Restart with a healthy dispatcher: the same durable definition fires.
        revived = AutomationScheduler(
            self.store,
            self._dispatcher(),
            clock=lambda: _utc_now(),
        )
        dispatched = revived.tick(now=_utc_now())
        self.assertEqual(len(dispatched), 1)
        self.assertIn("survive restart", self.dispatched[0][0])

    def test_dispatch_failure_records_skip_not_crash(self) -> None:
        self.now = _utc_now()
        self.store.save(
            {
                "name": "Failing",
                "project_id": "sched-p",
                "goal": "will fail",
                "automation_type": "recurring",
                "schedule": "every 1m",
                "enabled": True,
            }
        )
        failing = AutomationScheduler(
            self.store,
            self._dispatcher(fail=True),
            clock=lambda: _utc_now(),
        )
        failing.tick(now=_utc_now())
        stored = self.store.list()[0]
        self.assertTrue(stored["metadata"].get("recent_skips"))


class SchedulerLifecycleTests(unittest.TestCase):
    """B1.3A: deterministic stop/join lifecycle for the scheduler worker."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id="life-p", goal="g"))
        self.store = AutomationStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _scheduler(self, **kwargs) -> AutomationScheduler:
        return AutomationScheduler(self.store, lambda goal, project_id: "run-l", **kwargs)

    def _automation(self) -> dict:
        return self.store.save(
            {
                "name": "Lifecycle",
                "project_id": "life-p",
                "goal": "tick",
                "automation_type": "recurring",
                "schedule": "every 5m",
                "enabled": True,
            }
        )

    def test_stop_before_start_is_safe(self) -> None:
        scheduler = self._scheduler()
        self.assertTrue(scheduler.stop())
        self.assertIsNone(scheduler._thread)

    def test_start_then_stop_joins_worker(self) -> None:
        scheduler = self._scheduler()
        scheduler.start()
        worker = scheduler._thread
        self.assertIsNotNone(worker)
        self.assertTrue(scheduler.stop(timeout=5.0))
        self.assertFalse(worker.is_alive())

    def test_repeated_start_creates_single_worker(self) -> None:
        scheduler = self._scheduler()
        try:
            scheduler.start()
            scheduler.start()
            workers = [t for t in threading.enumerate() if t.name == "amca-scheduler"]
            self.assertEqual(len(workers), 1)
        finally:
            scheduler.stop(timeout=5.0)

    def test_stop_timeout_reports_truthfully_while_tick_in_progress(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def slow_dispatch(goal: str, project_id: str) -> str:
            entered.set()
            release.wait(timeout=10)
            return "run-blocked"

        # Fast poll so the real worker enters its tick promptly.
        scheduler = AutomationScheduler(self.store, slow_dispatch, poll_seconds=0.05)
        self._automation()
        scheduler.start()
        self.assertTrue(entered.wait(timeout=5))
        worker = scheduler._thread
        self.assertIsNotNone(worker)
        # The worker is mid-tick: a short graceful timeout must report False
        # instead of waiting forever...
        self.assertFalse(scheduler.stop(timeout=0.2))
        # ...and once the bounded tick finishes, the worker stops cleanly.
        release.set()
        self.assertTrue(scheduler.stop(timeout=5.0))
        self.assertFalse(worker.is_alive())

    def test_restart_after_stop_starts_fresh_worker(self) -> None:
        scheduler = self._scheduler()
        scheduler.start()
        first = scheduler._thread
        self.assertTrue(scheduler.stop(timeout=5.0))
        scheduler.start()
        second = scheduler._thread
        self.assertIsNotNone(second)
        self.assertIsNot(second, first)
        self.assertTrue(second.is_alive())
        scheduler.stop(timeout=5.0)


if __name__ == "__main__":

    unittest.main()
