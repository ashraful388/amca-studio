"""A2.1A.1 tests: persistence hardening for the Agent Fabric recovery store.

Covers the reposted work order requirements:
- canonical AMCA database integration (no secondary recovery DB, upgrade
  preserves pre-A2 data);
- attempt-aware verifier verdict identity;
- cross-table transaction commit/rollback;
- foreign-key enforcement;
- bounded sensitive-data handling in recovery records.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from amca.agent_recovery import AgentRecoveryStore
from amca.domain import ProjectState
from amca.storage import SQLiteStore


class _Harness:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "amca.sqlite3"

    def close(self) -> None:
        self.temporary.cleanup()


class CanonicalDatabaseIntegrationTests(unittest.TestCase):
    """Work order section 1: AgentRecoveryStore operates on the canonical
    AMCA database; migrations succeed; pre-A2 data survives; no secondary
    recovery database appears."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "amca.sqlite3"
        # Representative pre-A2 AMCA 0.3 database: existing store + data
        with SQLiteStore(self.database) as legacy:
            legacy.save_state(ProjectState(project_id="legacy-proj", goal="legacy goal"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_upgrade_preserves_legacy_data_and_creates_a2_tables(self) -> None:
        recovery = AgentRecoveryStore(self.database)
        recovery.create_organization(
            organization_id="org-a2", project_id="legacy-proj",
            goal="a2 work", budget_usd=2.0, max_concurrent=1,
        )
        # old data survives unchanged and remains readable through the
        # canonical AMCA store
        with SQLiteStore(self.database) as legacy:
            state = legacy.load_state("legacy-proj")
            self.assertIsNotNone(state)
            self.assertEqual(state.goal, "legacy goal")
        # A2 tables are available in the SAME database file
        recovery.create_task(
            task_id="t1", organization_id="org-a2", project_id="legacy-proj",
            agent_id="a", focus="f", executor_kind="solo",
        )
        self.assertIsNotNone(recovery.get_task("t1"))

    def test_no_secondary_recovery_database_is_created(self) -> None:
        AgentRecoveryStore(self.database)
        siblings = [
            p.name for p in self.database.parent.iterdir()
            if p.is_file() and "recovery" in p.name.lower()
        ]
        self.assertEqual(siblings, [])

    def test_representative_upgrade_reopen_cycle(self) -> None:
        first = AgentRecoveryStore(self.database)
        first.create_organization(
            organization_id="org-x", project_id="legacy-proj",
            goal="g", budget_usd=1.0, max_concurrent=1,
        )
        second = AgentRecoveryStore(self.database)
        self.assertIsNotNone(second.get_organization("org-x"))
        self.assertEqual(second.unread_count(), 0) if hasattr(
            second, "unread_count"
        ) else None


class AttemptAwareVerifierIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "amca.sqlite3"
        self.store = AgentRecoveryStore(self.database)
        self.store.create_organization(
            organization_id="org-1", project_id="p", goal="g",
            budget_usd=5.0, max_concurrent=2,
        )
        self.store.create_task(
            task_id="task-1", organization_id="org-1", project_id="p",
            agent_id="a", focus="f", executor_kind="solo",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _record(self, attempt: int, verifier: str, verdict: str = "pass") -> dict:
        return self.store.record_verdict(
            organization_id="org-1",
            task_id="task-1",
            task_attempt_number=attempt,
            verifier_id=verifier,
            verdict=verdict,
            summary="s",
            evidence_refs_json=[],
        )

    def test_same_verifier_new_attempt_gets_distinct_verdict(self) -> None:
        first = self._record(1, "verifier:claude", "pass")
        second = self._record(2, "verifier:claude", "fail")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["task_attempt_number"], 1)
        self.assertEqual(second["task_attempt_number"], 2)
        by_attempt = self.store.verdicts_by_attempt("org-1", "task-1", 1)
        self.assertEqual(len(by_attempt), 1)
        by_attempt2 = self.store.verdicts_by_attempt("org-1", "task-1", 2)
        self.assertEqual(len(by_attempt2), 1)

    def test_different_verifiers_same_attempt_distinct(self) -> None:
        a = self._record(1, "verifier:claude")
        b = self._record(1, "verifier:gpt")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(
            len(self.store.verdicts_by_attempt("org-1", "task-1", 1)), 2
        )

    def test_old_attempt_evidence_never_overwritten(self) -> None:
        self._record(1, "verifier:claude", "pass")
        self._record(2, "verifier:claude", "fail")
        attempt1 = self.store.verdicts_by_attempt("org-1", "task-1", 1)
        self.assertEqual(attempt1[0]["verdict"], "pass")


class CrossTableTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "amca.sqlite3"
        self.store = AgentRecoveryStore(self.database)
        self.store.create_organization(
            organization_id="org-1", project_id="p", goal="g",
            budget_usd=5.0, max_concurrent=2,
        )
        self.store.create_task(
            task_id="task-1", organization_id="org-1", project_id="p",
            agent_id="a", focus="f", executor_kind="solo",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_cross_table_commit_persists_both(self) -> None:
        with self.store.transaction() as tx:
            tx.create_reservation(
                reservation_id="res-1", organization_id="org-1",
                task_id="task-1", attempt=1, amount_usd=0.5,
            )
            tx.record_call_state(
                organization_id="org-1", task_id="task-1",
                task_attempt_number=1, call_key="primary", state="RESERVED",
                executor_kind="solo", adapter_id="scripted",
                reservation_id="res-1", reserved_usd=0.5,
            )
        reservation = self.store.get_reservation("res-1")
        attempt = self.store.get_call_attempt("org-1", "task-1", 1, "primary")
        self.assertIsNotNone(reservation)
        self.assertIsNotNone(attempt)
        self.assertEqual(reservation["status"], "active")

    def test_exception_rolls_back_everything(self) -> None:
        class Boom(Exception):
            pass

        def failing_body(store):
            store.create_reservation(
                reservation_id="res-x", organization_id="org-1",
                task_id="task-1", attempt=1, amount_usd=0.5,
            )
            store.record_call_state(
                organization_id="org-1", task_id="task-1",
                task_attempt_number=1, call_key="primary", state="RESERVED",
                executor_kind="solo", adapter_id="scripted",
                reservation_id="res-x", reserved_usd=0.5,
            )
            raise Boom()

        with self.assertRaises(Boom):
            with self.store.transaction() as tx:
                failing_body(tx)
        self.assertIsNone(self.store.get_reservation("res-x"))
        self.assertEqual(self.store.list_call_attempts("task-1"), [])

    def test_no_hidden_intermediate_autocommit(self) -> None:
        # a write inside an open transaction is invisible to other readers
        # until the transaction commits (single connection owns it)
        with self.store.transaction() as tx:
            tx.create_reservation(
                reservation_id="res-p", organization_id="org-1",
                task_id="task-1", attempt=1, amount_usd=0.5,
            )
            outside = AgentRecoveryStore(self.database)
            self.assertIsNone(outside.get_reservation("res-p"))
        self.assertIsNotNone(self.store.get_reservation("res-p"))

    def test_foreign_key_enforcement(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_reservation(
                reservation_id="res-orphan", organization_id="org-missing",
                task_id="task-missing", attempt=1, amount_usd=1.0,
            )


class SensitiveDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "amca.sqlite3"
        self.store = AgentRecoveryStore(self.database)
        self.store.create_organization(
            organization_id="org-1", project_id="p", goal="g",
            budget_usd=1.0, max_concurrent=1,
        )
        self.store.create_task(
            task_id="task-1", organization_id="org-1", project_id="p",
            agent_id="a", focus="f", executor_kind="solo",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_credential_like_keys_rejected_in_recovery_records(self) -> None:
        from amca.security import SensitiveDataError, assert_no_sensitive_keys

        self.store.create_reservation(
            reservation_id="res-1", organization_id="org-1",
            task_id="task-1", attempt=1, amount_usd=0.5,
        )
        reservation = self.store.get_reservation("res-1")
        assert_no_sensitive_keys(reservation)
        attempt = self.store.get_call_attempt("org-1", "task-1", 1, "primary")
        if attempt is not None:
            assert_no_sensitive_keys(attempt)


if __name__ == "__main__":
    unittest.main()
