"""B1.2B tests: deterministic conditional automation execution."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from amca.conditions import (
    ConditionEvaluator,
    ConditionResult,
    validate_condition,
)
from amca.automations import AutomationStore
from amca.domain import ProjectState
from amca.scheduler import AutomationScheduler, occurrence_slot
from amca.service import reconcile_automation_occurrences
from amca.storage import SQLiteStore


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class StubConditionEvaluator:
    """Deterministic evaluator returning a scripted outcome per call."""

    def __init__(self, outcomes: list | None = None, default=True) -> None:
        self.script = list(outcomes or [])
        self.default = default
        self.calls: list[dict] = []

    def evaluate(self, condition) -> ConditionResult:
        self.calls.append(dict(condition))
        outcome = self.script.pop(0) if self.script else self.default
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "error":
            return ConditionResult(
                kind=str(condition.get("kind")), ok=False, error="workspace unavailable"
            )
        return ConditionResult(
            kind=str(condition.get("kind")),
            ok=True,
            value=bool(outcome),
            evidence={"kind": str(condition.get("kind")), "exists": bool(outcome)},
        )


class ConditionalValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = AutomationStore(Path(self.temporary.name) / "state.sqlite3")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _save_conditional(self, condition, **overrides):
        record = {
            "name": "Cond",
            "project_id": "p1",
            "goal": "conditional work",
            "automation_type": "conditional",
            "schedule": "every 5m",
            "enabled": True,
        }
        if condition is not ...:
            record["condition"] = condition
        record.update(overrides)
        return self.store.save(record)

    def test_always_condition_validates_and_saves(self) -> None:
        saved = self._save_conditional({"version": 1, "kind": "always"})
        self.assertEqual(saved["condition"], {"version": 1, "kind": "always"})

    def test_file_exists_condition_validates_and_normalizes(self) -> None:
        saved = self._save_conditional(
            {"version": 1, "kind": "file_exists", "path": "docs/report.txt"}
        )
        self.assertEqual(saved["condition"]["path"], "docs/report.txt")

    def test_missing_condition_rejected_for_new_conditional(self) -> None:
        with self.assertRaisesRegex(ValueError, "condition"):
            self._save_conditional(...)

    def test_unknown_kind_and_version_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_condition({"version": 1, "kind": "gmail_unread"})
        with self.assertRaises(ValueError):
            validate_condition({"version": 2, "kind": "always"})
        with self.assertRaises(ValueError):
            self._save_conditional({"version": 1, "kind": "webhook"})

    def test_malformed_condition_rejected(self) -> None:
        for bad in ("not-an-object", [], {"version": "one", "kind": "always"}):
            with self.assertRaises(ValueError):
                validate_condition(bad)

    def test_absolute_path_drive_and_unc_rejected(self) -> None:
        for bad_path in (
            "C:\\evil\\file.txt",
            "C:/evil/file.txt",
            "\\\\server\\share\\file.txt",
            "/etc/passwd",
        ):
            with self.assertRaises(ValueError):
                validate_condition(
                    {"version": 1, "kind": "file_exists", "path": bad_path}
                )

    def test_traversal_rejected(self) -> None:
        for bad_path in ("../secret.txt", "docs/../../secret.txt"):
            with self.assertRaises(ValueError):
                validate_condition(
                    {"version": 1, "kind": "file_exists", "path": bad_path}
                )

    def test_non_string_and_empty_paths_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_condition({"version": 1, "kind": "file_exists", "path": 5})
        with self.assertRaises(ValueError):
            validate_condition({"version": 1, "kind": "file_exists", "path": "   "})

    def test_oversized_condition_payload_rejected(self) -> None:
        huge = {"version": 1, "kind": "always", "pad": "x" * 5000}
        with self.assertRaisesRegex(ValueError, "exceeds"):
            validate_condition(huge)

    def test_legacy_rows_without_condition_stay_loadable(self) -> None:
        connection = sqlite3.connect(self.store.database)
        # Hand-insert a pre-B1.2B style row lacking any condition.
        connection.execute(
            "INSERT INTO desktop_automations (id,name,project_id,goal,automation_type,"
            "schedule,enabled,created_at,updated_at,last_run_at,last_run_id,metadata_json,revision)"
            " VALUES ('legacy-c','Old','p','g','conditional','every 5m',1,'c','u',NULL,NULL,'{}',0)"
        )
        connection.commit()
        connection.close()
        reopened = AutomationStore(self.store.database)
        legacy = reopened.get("legacy-c")
        assert legacy is not None
        self.assertIsNone(legacy["condition"])
        self.assertEqual(legacy["automation_type"], "conditional")


class FileExistsEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "proj").mkdir()
        (self.root / "proj" / "report.txt").write_text("CONFIDENTIAL-CONTENT")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_true_when_contained_file_exists(self) -> None:
        result = ConditionEvaluator(self.root / "proj").evaluate(
            {"version": 1, "kind": "file_exists", "path": "report.txt"}
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.value)
        self.assertEqual(result.evidence["exists"], True)

    def test_false_when_file_absent(self) -> None:
        result = ConditionEvaluator(self.root / "proj").evaluate(
            {"version": 1, "kind": "file_exists", "path": "missing.txt"}
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.value)

    def test_evidence_never_contains_file_content(self) -> None:
        result = ConditionEvaluator(self.root / "proj").evaluate(
            {"version": 1, "kind": "file_exists", "path": "report.txt"}
        )
        encoded = json.dumps(result.evidence)
        self.assertNotIn("CONFIDENTIAL-CONTENT", encoded)
        self.assertEqual(set(result.evidence.keys()), {"kind", "path", "exists"})

    def test_root_escape_via_symlink_rejected_or_skipped_with_reason(self) -> None:
        outside = self.root / "outside-secret.txt"
        outside.write_text("secret")
        link = self.root / "proj" / "leak.txt"
        try:
            link.symlink_to(outside)
        except OSError as error:
            # Windows may deny symlink creation without privilege.
            self.skipTest(f"symlink creation unavailable: {error}")
        result = ConditionEvaluator(self.root / "proj").evaluate(
            {"version": 1, "kind": "file_exists", "path": "leak.txt"}
        )
        self.assertFalse(result.ok)
        self.assertIn("escapes the authorized project workspace", result.error)

    def test_evaluator_is_model_free_by_construction(self) -> None:
        evaluator = ConditionEvaluator(self.root / "proj")
        for _ in range(25):
            result = evaluator.evaluate({"version": 1, "kind": "file_exists", "path": "x"})
        self.assertIsInstance(result.value, bool)


class ConditionalSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id="cond-p", goal="g"))
        self.store = AutomationStore(self.database)
        self.dispatched: list[tuple[str, str]] = []
        self.now = _utc_now()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _dispatcher(self):
        def dispatch(goal: str, project_id: str) -> str:
            run_id = f"run-{len(self.dispatched) + 1}"
            self.dispatched.append((goal, project_id))
            return run_id

        return dispatch

    def _scheduler(self, evaluator=None) -> AutomationScheduler:
        factory = (lambda project_id: evaluator) if evaluator is not None else None
        clock_now = self.now

        def clock():
            return clock_now

        scheduler = AutomationScheduler(
            self.store,
            self._dispatcher(),
            clock=clock,
            condition_evaluator_factory=factory,
        )
        # Keep tick times aligned with the mutable logical clock.
        scheduler.clock = lambda: self.now
        return scheduler

    def _conditional(self, schedule="every 5m", condition=None, kind="conditional", **overrides):
        record = {
            "name": f"C-{kind}",
            "project_id": "cond-p",
            "goal": "conditional work",
            "automation_type": kind,
            "schedule": schedule,
            "enabled": True,
            "condition": condition
            if condition is not None
            else {"version": 1, "kind": "always"},
        }
        record.update(overrides)
        return self.store.save(record)

    def test_always_condition_dispatches_exactly_once_per_occurrence(self) -> None:
        self._conditional()
        evaluator = StubConditionEvaluator(default=True)
        scheduler = self._scheduler(evaluator)
        dispatched = scheduler.tick(now=self.now)
        again = scheduler.tick(now=self.now)
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(again, [])
        self.assertEqual(dispatched[0]["condition_result"], "true")
        self.assertEqual(len(self.dispatched), 1)
        self.assertEqual(len(evaluator.calls), 1)

    def test_false_condition_creates_no_runjob(self) -> None:
        automation = self._conditional(
            condition={"version": 1, "kind": "file_exists", "path": "gate.txt"}
        )
        evaluator = StubConditionEvaluator(default=False)
        scheduler = self._scheduler(evaluator)
        dispatched = scheduler.tick(now=self.now)
        self.assertEqual(len(dispatched), 1)
        self.assertIsNone(dispatched[0]["run_id"])
        self.assertEqual(dispatched[0]["condition_result"], "false")
        self.assertEqual(self.dispatched, [])
        occurrence = self.store.find_occurrence(automation["id"], dispatched[0]["scheduled_for"])
        assert occurrence is not None
        self.assertEqual(occurrence["status"], "skipped")
        self.assertEqual(occurrence["check"]["result"], "false")
        self.assertIn("no action executed", occurrence["error"])

    def test_error_condition_fails_closed_without_action(self) -> None:
        automation = self._conditional()
        scheduler = self._scheduler(StubConditionEvaluator(default="error"))
        dispatched = scheduler.tick(now=self.now)
        self.assertEqual(self.dispatched, [])
        occurrence = self.store.find_occurrence(automation["id"], dispatched[0]["scheduled_for"])
        assert occurrence is not None
        self.assertEqual(occurrence["status"], "failed")
        self.assertIn("condition evaluation failed", occurrence["error"])

    def test_evaluator_crash_also_fails_closed(self) -> None:
        automation = self._conditional()
        scheduler = self._scheduler(StubConditionEvaluator(default=RuntimeError("kaboom")))
        dispatched = scheduler.tick(now=self.now)
        occurrence = self.store.find_occurrence(automation["id"], dispatched[0]["scheduled_for"])
        assert occurrence is not None
        self.assertEqual(occurrence["status"], "failed")
        self.assertEqual(self.dispatched, [])

    def test_legacy_conditional_without_condition_fails_closed(self) -> None:
        automation = self._conditional(condition={"version": 1, "kind": "always"})
        # Force the row into a legacy shape with no persisted condition.
        connection = sqlite3.connect(self.database)
        connection.execute(
            "UPDATE desktop_automations SET condition_json = NULL WHERE id = ?",
            (automation["id"],),
        )
        connection.commit()
        connection.close()
        scheduler = self._scheduler(StubConditionEvaluator(default=True))
        dispatched = scheduler.tick(now=self.now)
        self.assertEqual(self.dispatched, [])
        occurrence = self.store.find_occurrence(automation["id"], dispatched[0]["scheduled_for"])
        assert occurrence is not None
        self.assertEqual(occurrence["status"], "failed")
        self.assertIn("no valid condition", occurrence["error"])

    def test_no_evaluator_configured_fails_closed(self) -> None:
        automation = self._conditional()
        scheduler = self._scheduler(None)
        dispatched = scheduler.tick(now=self.now)
        occurrence = self.store.find_occurrence(automation["id"], dispatched[0]["scheduled_for"])
        assert occurrence is not None
        self.assertEqual(occurrence["status"], "failed")
        self.assertEqual(self.dispatched, [])

    def test_monitor_and_event_triggered_fail_closed(self) -> None:
        for unsupported in ("monitor", "event_triggered"):
            with self.subTest(kind=unsupported):
                automation = self._conditional(kind=unsupported)
                scheduler = self._scheduler(StubConditionEvaluator(default=True))
                dispatched = scheduler.tick(now=self.now)
                self.assertEqual(self.dispatched, [])
                self.assertEqual(dispatched[0]["condition_result"], "unsupported")
                occurrence = self.store.find_occurrence(
                    automation["id"], dispatched[0]["scheduled_for"]
                )
                assert occurrence is not None
                self.assertEqual(occurrence["status"], "skipped")
                self.assertIn("fails closed", occurrence["error"])

    def test_disabled_stale_snapshot_prevents_evaluation_and_dispatch(self) -> None:
        automation = self._conditional()
        snapshot = [automation]
        calls: list[int] = []

        class RacingStore(AutomationStore):
            def list(self):  # stale scheduler view
                return snapshot

            def set_condition_check(self, *args, **kwargs):  # spy
                calls.append(1)
                return super().set_condition_check(*args, **kwargs)

        racing = RacingStore(self.database)
        racing.__init__(self.database)
        self.store.save({**(self.store.get(automation["id"]) or {}), "enabled": False})
        self.now = _utc_now()
        scheduler = AutomationScheduler(
            racing,
            self._dispatcher(),
            clock=lambda: self.now,
            condition_evaluator_factory=lambda pid: StubConditionEvaluator(default=True),
        )
        self.assertEqual(scheduler.tick(now=self.now), [])
        self.assertEqual(self.dispatched, [])
        self.assertEqual(calls, [])  # evaluator never invoked
        self.assertFalse(self.store.get(automation["id"])["enabled"])

    def test_false_check_advances_cadence_without_touching_last_run(self) -> None:
        automation = self._conditional(schedule="every 10m")
        evaluator = StubConditionEvaluator(default=False)
        scheduler = self._scheduler(evaluator)
        first = scheduler.tick(now=self.now)
        stored_after_first = self.store.get(automation["id"])
        assert stored_after_first is not None and stored_after_first["last_run_at"] is None
        self.assertIsNotNone(stored_after_first["last_check_at"])
        check_at_first = evaluator.calls and True
        # Next interval: O2 becomes due even though nothing ever ran.
        self.now = self.now + timedelta(minutes=11)
        second = scheduler.tick(now=self.now)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0]["scheduled_for"], second[0]["scheduled_for"])
        final = self.store.get(automation["id"])
        assert final is not None
        self.assertIsNone(final["last_run_at"])  # FALSE never means an action ran
        del check_at_first

    def test_true_check_advances_cadence_and_run_bookkeeping(self) -> None:
        automation = self._conditional(schedule="every 10m")
        evaluator = StubConditionEvaluator(default=True)
        scheduler = self._scheduler(evaluator)
        first = scheduler.tick(now=self.now)
        self.assertEqual(len(self.dispatched), 1)
        after_first = self.store.get(automation["id"])
        assert after_first is not None
        first_run_at = after_first["last_run_at"]
        self.assertIsNotNone(first_run_at)
        self.assertIsNotNone(after_first["last_check_at"])
        self.now = self.now + timedelta(minutes=11)
        second = scheduler.tick(now=self.now)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        final = self.store.get(automation["id"])
        assert final is not None
        self.assertNotEqual(final["last_run_at"], first_run_at)

    def test_once_conditional_false_checks_exactly_once_forever(self) -> None:
        automation = self._conditional(schedule="manual-once", condition={
            "version": 1, "kind": "file_exists", "path": "trigger.txt"
        })
        # Give the once automation an exact past datetime slot instead.
        once_slot = (_utc_now() - timedelta(minutes=2)).isoformat()
        self.store.save({**automation, "schedule": once_slot})
        evaluator = StubConditionEvaluator(default=False)
        scheduler = self._scheduler(evaluator)
        first = scheduler.tick(now=_utc_now())
        second = scheduler.tick(now=_utc_now())
        third_time = _utc_now() + timedelta(hours=1)
        third = AutomationScheduler(
            self.store,
            self._dispatcher(),
            clock=lambda: third_time,
            condition_evaluator_factory=lambda pid: evaluator,
        ).tick(now=third_time)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(third, [])
        self.assertEqual(len(evaluator.calls), 1)
        stored = self.store.get(automation["id"])
        assert stored is not None
        self.assertFalse(stored["enabled"])

    def test_once_conditional_true_dispatches_exactly_once(self) -> None:
        automation = self._conditional()
        once_slot = (_utc_now() - timedelta(minutes=2)).isoformat()
        self.store.save({**automation, "schedule": once_slot})
        evaluator = StubConditionEvaluator(default=True)
        scheduler = self._scheduler(evaluator)
        now = _utc_now()
        first = scheduler.tick(now=now)
        second = scheduler.tick(now=now)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["condition_result"], "true")
        self.assertEqual(second, [])
        self.assertEqual(len(self.dispatched), 1)

    def test_restart_after_false_does_not_replay_same_check(self) -> None:
        automation = self._conditional(schedule="every 30m")
        evaluator = StubConditionEvaluator(default=False)
        scheduler = self._scheduler(evaluator)
        start = self.now
        first = scheduler.tick(now=start)
        self.assertEqual(len(first), 1)
        restarted_store = AutomationStore(self.database)

        def later_clock():
            return start + timedelta(seconds=30)

        revived = AutomationScheduler(
            restarted_store,
            self._dispatcher(),
            clock=later_clock,
            condition_evaluator_factory=lambda pid: evaluator,
        )
        replay = revived.tick(now=start + timedelta(seconds=30))
        self.assertEqual(replay, [])
        self.assertEqual(len(evaluator.calls), 1)  # O1 never re-evaluated

    def test_restart_after_true_keeps_canonical_reconciliation(self) -> None:
        automation = self._conditional(schedule="every 30m")
        evaluator = StubConditionEvaluator(default=True)
        scheduler = self._scheduler(evaluator)
        start = self.now
        first = scheduler.tick(now=start)
        run_id = first[0]["run_id"]
        assert run_id is not None
        restarted_store = AutomationStore(self.database)
        # Canonical registry says the linked run succeeded.
        changed = reconcile_automation_occurrences(
            restarted_store,
            lambda rid: {"status": "succeeded", "error": None} if rid == run_id else None,
        )
        self.assertEqual(changed, 1)
        occurrence = restarted_store.find_occurrence(
            automation["id"], first[0]["scheduled_for"]
        )
        assert occurrence is not None
        self.assertEqual(occurrence["status"], "succeeded")
        # Repeated poll/restart cannot dispatch O1 again; O2 stays schedulable.
        def later_clock():
            return start + timedelta(seconds=45)

        revived = AutomationScheduler(
            restarted_store,
            self._dispatcher(),
            clock=later_clock,
            condition_evaluator_factory=lambda pid: evaluator,
        )
        self.assertEqual(revived.tick(now=start + timedelta(seconds=45)), [])
        o2_time = start + timedelta(minutes=31)
        o2 = AutomationScheduler(
            restarted_store,
            self._dispatcher(),
            clock=lambda: o2_time,
            condition_evaluator_factory=lambda pid: evaluator,
        ).tick(now=o2_time)
        self.assertEqual(len(o2), 1)

    def test_concurrent_claim_yields_single_evaluation_owner(self) -> None:
        automation = self._conditional()
        evaluator = StubConditionEvaluator(default=True)
        # Two independent store handles + schedulers race for the same due
        # occurrence; only the durable claim winner may evaluate and dispatch.
        rival_store = AutomationStore(self.database)
        owner_scheduler = self._scheduler(evaluator)
        rival_scheduler = AutomationScheduler(
            rival_store,
            self._dispatcher(),
            clock=lambda: self.now,
            condition_evaluator_factory=lambda pid: evaluator,
        )
        first = owner_scheduler.tick(now=self.now)
        second = rival_scheduler.tick(now=self.now)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(evaluator.calls), 1)  # evaluated by ONE owner only
        self.assertEqual(len(self.dispatched), 1)


class ConditionalStopGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id="gate-p", goal="g"))
        self.store = AutomationStore(self.database)
        self.dispatched: list[tuple[str, str]] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _dispatcher(self):
        def dispatch(goal: str, project_id: str) -> str:
            run_id = f"run-{len(self.dispatched) + 1}"
            self.dispatched.append((goal, project_id))
            return run_id

        return dispatch

    def _evaluator(self, value):
        return StubConditionEvaluator(default=value)

    def test_stop_gate_scenario_1_false(self) -> None:
        automation = self.store.save(
            {
                "name": "GateFalse",
                "project_id": "gate-p",
                "goal": "only when gate file exists",
                "automation_type": "conditional",
                "schedule": "every 20m",
                "enabled": True,
                "condition": {"version": 1, "kind": "file_exists", "path": "gate.txt"},
            }
        )
        evaluator = self._evaluator(False)
        start = _utc_now()
        scheduler = AutomationScheduler(
            self.store,
            self._dispatcher(),
            clock=lambda: start,
            condition_evaluator_factory=lambda pid: evaluator,
        )
        o1 = scheduler.tick(now=start)
        self.assertEqual(len(o1), 1)
        self.assertEqual(self.dispatched, [])  # zero RunJob dispatch
        occurrence = self.store.find_occurrence(automation["id"], o1[0]["scheduled_for"])
        assert occurrence is not None
        self.assertEqual(occurrence["status"], "skipped")  # terminally skipped
        self.assertEqual(occurrence["check"]["evidence"]["exists"], False)
        # Restart: O1 is not repeated...
        restarted = AutomationStore(self.database)

        def soon():
            return start + timedelta(seconds=40)

        revived = AutomationScheduler(
            restarted,
            self._dispatcher(),
            clock=soon,
            condition_evaluator_factory=lambda pid: evaluator,
        )
        self.assertEqual(revived.tick(now=start + timedelta(seconds=40)), [])
        # ...while the next due O2 can be checked...
        o2_time = start + timedelta(minutes=21)
        o2 = AutomationScheduler(
            restarted,
            self._dispatcher(),
            clock=lambda: o2_time,
            condition_evaluator_factory=lambda pid: self._evaluator(True),
        ).tick(now=o2_time)
        self.assertEqual(len(o2), 1)
        self.assertEqual(len(self.dispatched), 1)
        # ...and last_run_at still means last ACTUAL action (only O2's run).
        final = restarted.get(automation["id"])
        assert final is not None
        self.assertEqual(final["last_run_id"], "run-1")

    def test_stop_gate_scenario_2_true(self) -> None:
        automation = self.store.save(
            {
                "name": "GateTrue",
                "project_id": "gate-p",
                "goal": "run when gate file exists",
                "automation_type": "conditional",
                "schedule": "every 20m",
                "enabled": True,
                "condition": {"version": 1, "kind": "file_exists", "path": "gate.txt"},
            }
        )
        evaluator = self._evaluator(True)
        start = _utc_now()
        scheduler = AutomationScheduler(
            self.store,
            self._dispatcher(),
            clock=lambda: start,
            condition_evaluator_factory=lambda pid: evaluator,
        )
        o1 = scheduler.tick(now=start)
        self.assertEqual(len(o1), 1)
        self.assertEqual(len(self.dispatched), 1)  # exactly one canonical dispatch
        run_id = o1[0]["run_id"]
        assert run_id is not None
        # Terminal RunJob outcome reconciles to O1 across restart.
        restarted = AutomationStore(self.database)
        changed = reconcile_automation_occurrences(
            restarted,
            lambda rid: {"status": "succeeded", "error": None} if rid == run_id else None,
        )
        self.assertEqual(changed, 1)
        occurrence = restarted.find_occurrence(automation["id"], o1[0]["scheduled_for"])
        assert occurrence is not None
        self.assertEqual(occurrence["status"], "succeeded")
        # Repeated poll cannot re-dispatch O1...
        def soon():
            return start + timedelta(seconds=50)

        revived = AutomationScheduler(
            restarted,
            self._dispatcher(),
            clock=soon,
            condition_evaluator_factory=lambda pid: evaluator,
        )
        self.assertEqual(revived.tick(now=start + timedelta(seconds=50)), [])
        self.assertEqual(len(self.dispatched), 1)
        # ...and O2 remains independently schedulable.
        o2_time = start + timedelta(minutes=21)
        o2 = AutomationScheduler(
            restarted,
            self._dispatcher(),
            clock=lambda: o2_time,
            condition_evaluator_factory=lambda pid: evaluator,
        ).tick(now=o2_time)
        self.assertEqual(len(o2), 1)
        self.assertEqual(len(self.dispatched), 2)


class ConditionalMigrationTests(unittest.TestCase):
    def test_legacy_database_gains_condition_columns_idempotently(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            database = Path(temporary.name) / "state.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                """CREATE TABLE desktop_automations (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, project_id TEXT NOT NULL,
                    goal TEXT NOT NULL, automation_type TEXT NOT NULL, schedule TEXT NOT NULL,
                    enabled INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    last_run_at TEXT, last_run_id TEXT, metadata_json TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0
                )"""
            )
            connection.execute(
                "INSERT INTO desktop_automations VALUES ('old','Old','p','g','recurring','manual',0,'c','u',NULL,NULL,'{}',3)"
            )
            connection.commit()
            connection.close()

            first = AutomationStore(database).get("old")
            assert first is not None
            self.assertIsNone(first["condition"])
            self.assertIsNone(first["last_check_at"])
            # Reopen twice: idempotent, history preserved.
            second = AutomationStore(database)
            again = second.get("old")
            assert again is not None
            self.assertEqual(again["revision"], 3)
            self.assertIsNone(again["last_run_at"])
            occurrences = second.list_occurrences()
            self.assertEqual(occurrences, [])
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
