"""GOOSE-AMCA-V0.6.1-P1-REVIEW-CENTER-CONTRACT-FIX-01 tests.

Verifies the Review Center HTTP contract after fixing
BoundAMCARequestHandler's missing review_queue attribute.

Covers: empty queue, populated queue, proposal schema, status retrieval,
error handling, and security regression.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.credentials import MemoryCredentialStore
from amca.review_queue import ReviewQueueStore
from amca.service import create_server


class ReviewCenterHttpContractTests(unittest.TestCase):
    """Integration tests for the Review Center HTTP endpoints.

    These tests start a real AMCA server and exercise the /api/review-queue*
    endpoints, confirming the contract fix (self.review_queue -> self.context.review_queue)
    is effective and the full contract is satisfied.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        database = Path(self.temporary.name) / "state.sqlite3"
        self.credential_store = MemoryCredentialStore()
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=root,
            database=database,
            credential_store=self.credential_store,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        session = self._json("/api/session", authorized=False)
        self.token = str(session["session_token"])
        # Direct store access to seed review items for populated-queue tests.
        self.queue_store = ReviewQueueStore(database)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def _json(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, object] | None = None,
        authorized: bool = True,
        expect_status: int | None = None,
    ) -> dict[str, object]:
        headers = {"Origin": "http://localhost:1420"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authorized:
            headers["X-AMCA-Session"] = self.token
        request = Request(
            self.base + path, method=method, data=data, headers=headers
        )
        try:
            with urlopen(request, timeout=10) as response:
                if expect_status is not None:
                    self.assertEqual(
                        response.status, expect_status,
                        f"expected status {expect_status} for {method} {path}"
                    )
                return json.load(response)
        except HTTPError as error:
            if expect_status is not None:
                self.assertEqual(
                    error.code, expect_status,
                    f"expected status {expect_status} for {method} {path}, got {error.code}"
                )
            error_body = json.load(error)
            return error_body

    def _seed_item(self, title: str, severity: str = "info",
                   project_id: str = "p1") -> dict:
        """Record a review item directly in the store for test data."""
        item = self.queue_store.record(
            source_key=f"test:{title}",
            source_type="test",
            project_id=project_id,
            title=title,
            summary=f"Summary of {title}",
            severity=severity,
            run_id="run-1",
            occurrence_id="occ-1",
        )
        assert item is not None, "seed item should not be a duplicate"
        return item

    # --- 1. Empty queue ---
    def test_empty_queue_returns_empty_list(self) -> None:
        """GET /api/review-queue on a fresh database returns []."""
        result = self._json("/api/review-queue")
        self.assertIn("review_items", result)
        self.assertEqual(result["review_items"], [])
        self.assertIn("unread_count", result)
        self.assertEqual(result["unread_count"], 0)

    # --- 2. Populated queue ---
    def test_populated_queue_returns_items(self) -> None:
        """Items recorded in the store are surfaced by the HTTP endpoint."""
        self._seed_item("Test Item 1")
        self._seed_item("Test Item 2", severity="attention")
        result = self._json("/api/review-queue")
        items = result["review_items"]
        self.assertEqual(len(items), 2)
        titles = {i["title"] for i in items}
        self.assertIn("Test Item 1", titles)
        self.assertIn("Test Item 2", titles)

    def test_populated_queue_respects_limit(self) -> None:
        """The limit query parameter caps the number of returned items."""
        for i in range(5):
            self._seed_item(f"Item {i}")
        result = self._json("/api/review-queue?limit=2")
        self.assertEqual(len(result["review_items"]), 2)

    def test_populated_queue_filters_by_project(self) -> None:
        """The project_id query parameter filters items."""
        self._seed_item("Project A item", project_id="p_a")
        self._seed_item("Project B item", project_id="p_b")
        result = self._json("/api/review-queue?project_id=p_a")
        items = result["review_items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["project_id"], "p_a")

    # --- 3. Proposal schema validation ---
    def test_review_item_schema_contains_all_required_fields(self) -> None:
        """Each returned review item has the full expected schema."""
        item = self._seed_item("Schema Test")
        result = self._json(f"/api/review-queue/{item['id']}")
        # Verify all required fields are present
        for field in (
            "id", "source_key", "source_type", "project_id", "title",
            "summary", "severity", "status", "run_id", "occurrence_id",
            "created_at", "updated_at",
        ):
            self.assertIn(field, result)
        # Verify field types/values
        self.assertEqual(result["title"], "Schema Test")
        self.assertEqual(result["status"], "unread")
        self.assertEqual(result["severity"], "info")

    def test_review_item_schema_enforces_bounds(self) -> None:
        """Long title/summary strings are clipped at storage."""
        item = self.queue_store.record(
            source_key="schema:bounds",
            source_type="test",
            project_id="p1",
            title="x" * 500,
            summary="y" * 1000,
            severity="attention",
            run_id="run-1",
            occurrence_id="occ-2",
        )
        assert item is not None
        self.assertLessEqual(len(item["title"]), 200)
        self.assertLessEqual(len(item["summary"]), 400)

    # --- 4. Status retrieval ---
    def test_get_item_by_id_returns_item(self) -> None:
        """GET /api/review-queue/<id> returns the matching item."""
        item = self._seed_item("Status Test")
        result = self._json(f"/api/review-queue/{item['id']}")
        self.assertEqual(result["id"], item["id"])
        self.assertEqual(result["title"], "Status Test")

    def test_unread_count_reflects_records(self) -> None:
        """GET /api/review-queue/unread-count returns the correct count."""
        self._seed_item("Unread 1")
        self._seed_item("Unread 2")
        self._seed_item("Unread 3")
        result = self._json("/api/review-queue/unread-count")
        self.assertEqual(result["unread"], 3)

    def test_mark_read_transitions_status(self) -> None:
        """POST /api/review-queue/<id>/read changes status to 'read'."""
        item = self._seed_item("Mark Read Test")
        result = self._json(
            f"/api/review-queue/{item['id']}/read",
            method="POST",
            body={},
            authorized=True,
        )
        self.assertEqual(result["status"], "read")

    def test_dismiss_transitions_status(self) -> None:
        """POST /api/review-queue/<id>/dismiss changes status to 'dismissed'."""
        item = self._seed_item("Dismiss Test")
        result = self._json(
            f"/api/review-queue/{item['id']}/dismiss",
            method="POST",
            body={},
            authorized=True,
        )
        self.assertEqual(result["status"], "dismissed")

    # --- 5. Error handling ---
    def test_get_nonexistent_item_returns_404(self) -> None:
        """GET /api/review-queue/<unknown> returns 404 with no raw traceback."""
        result = self._json(
            "/api/review-queue/nonexistent-id",
            expect_status=404,
        )
        self.assertIn("error", result)
        # Verify no raw traceback or Python internals are exposed
        body = json.dumps(result)
        self.assertNotIn("Traceback", body)
        self.assertNotIn("AttributeError", body)
        self.assertNotIn("review_queue", body)

    def test_mark_read_nonexistent_returns_404(self) -> None:
        """POST /api/review-queue/<unknown>/read returns 404."""
        result = self._json(
            "/api/review-queue/nonexistent-id/read",
            method="POST",
            body={},
            authorized=True,
            expect_status=404,
        )
        self.assertIn("error", result)

    def test_dismiss_nonexistent_returns_404(self) -> None:
        """POST /api/review-queue/<unknown>/dismiss returns 404."""
        result = self._json(
            "/api/review-queue/nonexistent-id/dismiss",
            method="POST",
            body={},
            authorized=True,
            expect_status=404,
        )
        self.assertIn("error", result)

    # --- 6. Security regression ---
    def test_unauthenticated_request_is_rejected(self) -> None:
        """GET /api/review-queue without session token returns 401."""
        result = self._json(
            "/api/review-queue",
            authorized=False,
            expect_status=401,
        )
        self.assertIn("error", result)
        self.assertIn("session", result["error"].lower())

    def test_unauthenticated_mark_read_is_rejected(self) -> None:
        """POST /api/review-queue/<id>/read without session returns 401."""
        item = self._seed_item("Security Test")
        result = self._json(
            f"/api/review-queue/{item['id']}/read",
            method="POST",
            body={},
            authorized=False,
            expect_status=401,
        )
        self.assertIn("error", result)

    def test_unauthenticated_dismiss_is_rejected(self) -> None:
        """POST /api/review-queue/<id>/dismiss without session returns 401."""
        item = self._seed_item("Security Test 2")
        result = self._json(
            f"/api/review-queue/{item['id']}/dismiss",
            method="POST",
            body={},
            authorized=False,
            expect_status=401,
        )
        self.assertIn("error", result)

    def test_review_items_do_not_expose_credentials(self) -> None:
        """Review items never contain credential or secret data."""
        # Seed an item whose payload contains no secret words at all, then
        # verify the HTTP-serialized body carries no credential-shaped fields.
        item = self.queue_store.record(
            source_key="privacy:safe",
            source_type="test",
            project_id="p1",
            title="Automation finished",
            summary="Completed within budget",
            severity="info",
            run_id="run-1",
            occurrence_id="occ-1",
        )
        assert item is not None
        # The record itself has a fixed schema with no secret fields.
        for secret in ("api_key", "secret", "password", "token", "authorization"):
            self.assertNotIn(secret, item)
        # The HTTP response must not add any secret-shaped fields either.
        result = self._json(f"/api/review-queue/{item['id']}")
        for secret in ("api_key", "secret", "password", "token", "authorization"):
            self.assertNotIn(secret, result)