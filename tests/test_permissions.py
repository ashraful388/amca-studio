from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.domain import ProjectState
from amca.permissions import ApprovalStatus, PermissionAction, PermissionStore
from amca.security import SensitiveDataError
from amca.storage import SQLiteStore


class PermissionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id="permission-project", goal="g"))
        self.store = PermissionStore(self.database)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_approved_scope_is_exact_and_consumed_once(self) -> None:
        scope = {
            "provider": "openai",
            "model": "configured-model",
            "excerpt_sha256": "a" * 64,
            "max_cost_usd": 0.25,
        }
        request = self.store.request(
            project_id="permission-project",
            action=PermissionAction.TRANSMIT_EXCERPT,
            scope=scope,
            reason="Send only this reviewed excerpt for one bounded call.",
            risk="high",
        )
        approved = self.store.decide(request.id, approve=True)

        self.assertIs(approved.status, ApprovalStatus.APPROVED)
        with self.assertRaisesRegex(PermissionError, "exactly match"):
            self.store.authorize(
                request.id,
                project_id="permission-project",
                action=PermissionAction.TRANSMIT_EXCERPT,
                scope={**scope, "max_cost_usd": 0.26},
            )
        consumed = self.store.authorize(
            request.id,
            project_id="permission-project",
            action=PermissionAction.TRANSMIT_EXCERPT,
            scope=scope,
        )
        self.assertIs(consumed.status, ApprovalStatus.CONSUMED)
        self.assertEqual(consumed.uses, 1)
        with self.assertRaisesRegex(PermissionError, "not active"):
            self.store.authorize(
                request.id,
                project_id="permission-project",
                action=PermissionAction.TRANSMIT_EXCERPT,
                scope=scope,
            )

    def test_denial_cannot_authorize(self) -> None:
        request = self.store.request(
            project_id="permission-project",
            action="run_code",
            scope={"sandbox": "benchmark", "source_sha256": "b" * 64},
            reason="Run bounded candidate tests.",
        )
        denied = self.store.decide(request.id, approve=False, reason="Not now")
        self.assertIs(denied.status, ApprovalStatus.DENIED)
        with self.assertRaises(PermissionError):
            self.store.authorize(
                request.id,
                project_id="permission-project",
                action="run_code",
                scope={"sandbox": "benchmark", "source_sha256": "b" * 64},
            )

    def test_secret_fields_never_enter_approval_database(self) -> None:
        with self.assertRaises(SensitiveDataError):
            self.store.request(
                project_id="permission-project",
                action="call_cloud_model",
                scope={"api_key": "must-not-be-stored"},
                reason="Unsafe fixture",
            )
        raw = self.database.read_bytes()
        self.assertNotIn(b"must-not-be-stored", raw)


if __name__ == "__main__":
    unittest.main()
