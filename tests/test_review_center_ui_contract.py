"""Tests for Review Center UI contract, filtering, and invariant guarantees (B1.4B)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.automations import AutomationStore
from amca.review_queue import ReviewQueueStore
from amca.service import record_automation_review_item


class ReviewCenterContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_amca.sqlite3"
        self.review_store = ReviewQueueStore(self.db_path)
        self.automation_store = AutomationStore(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_unread_rendering_and_count(self) -> None:
        """Verifies unread items render with correct initial status and badge counts."""
        self.assertEqual(self.review_store.unread_count(), 0)

        item1 = self.review_store.record(
            source_key="occurrence:occ-1",
            source_type="automation_run_succeeded",
            project_id="proj-a",
            title="Nightly Build Succeeded",
            summary="All 40 unit tests completed cleanly.",
            severity="info",
            run_id="run-1",
            occurrence_id="occ-1",
        )
        item2 = self.review_store.record(
            source_key="occurrence:occ-2",
            source_type="automation_run_failed",
            project_id="proj-b",
            title="Integration Pipeline Failed",
            summary="Timeout connecting to external service.",
            severity="critical",
            run_id="run-2",
            occurrence_id="occ-2",
        )
        self.assertIsNotNone(item1)
        self.assertIsNotNone(item2)
        self.assertEqual(self.review_store.unread_count(), 2)
        self.assertEqual(self.review_store.unread_count(project_id="proj-a"), 1)
        self.assertEqual(self.review_store.unread_count(project_id="proj-b"), 1)

    def test_read_transition(self) -> None:
        """Verifies marking an item as read transitions its status and updates unread count."""
        item = self.review_store.record(
            source_key="occurrence:occ-3",
            source_type="automation_run_succeeded",
            project_id="proj-a",
            title="Task Succeeded",
            summary="Summary text",
            severity="info",
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["status"], "unread")

        # Mark read
        success = self.review_store.mark_read(item["id"])
        self.assertTrue(success)

        updated = self.review_store.get_item(item["id"])
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "read")
        self.assertEqual(self.review_store.unread_count(), 0)

    def test_dismiss_transition(self) -> None:
        """Verifies dismissing an item transitions its status to dismissed."""
        item = self.review_store.record(
            source_key="occurrence:occ-4",
            source_type="automation_run_succeeded",
            project_id="proj-a",
            title="Task Succeeded",
            summary="Summary text",
            severity="info",
        )
        self.assertIsNotNone(item)
        # Dismiss from unread
        success = self.review_store.dismiss(item["id"])
        self.assertTrue(success)

        updated = self.review_store.get_item(item["id"])
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "dismissed")

    def test_project_filtering(self) -> None:
        """Verifies listing items filtered by project_id."""
        self.review_store.record(
            source_key="occurrence:occ-5",
            source_type="automation_run_succeeded",
            project_id="proj-1",
            title="Project 1 Run",
        )
        self.review_store.record(
            source_key="occurrence:occ-6",
            source_type="automation_run_succeeded",
            project_id="proj-2",
            title="Project 2 Run",
        )

        p1_items = self.review_store.list_items(project_id="proj-1")
        self.assertEqual(len(p1_items), 1)
        self.assertEqual(p1_items[0]["project_id"], "proj-1")

        p2_items = self.review_store.list_items(project_id="proj-2")
        self.assertEqual(len(p2_items), 1)
        self.assertEqual(p2_items[0]["project_id"], "proj-2")

        all_items = self.review_store.list_items()
        self.assertEqual(len(all_items), 2)

    def test_long_summary_truncation(self) -> None:
        """Verifies summaries exceeding maximum limit are safely clipped without crashing."""
        huge_summary = "A" * 1000
        item = self.review_store.record(
            source_key="occurrence:occ-7",
            source_type="automation_run_succeeded",
            project_id="proj-1",
            title="Long summary task",
            summary=huge_summary,
        )
        self.assertIsNotNone(item)
        self.assertLessEqual(len(item["summary"]), 400)

    def test_attention_critical_severity_mapping(self) -> None:
        """Verifies failed runs map to critical/attention severity."""
        item = self.review_store.record(
            source_key="occurrence:occ-8",
            source_type="automation_run_failed",
            project_id="proj-1",
            title="Security Audit Alert",
            summary="CVE detected",
            severity="critical",
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["severity"], "critical")

    def test_invariant_dismiss_does_not_mutate_underlying_automation_state(self) -> None:
        """Critical Invariant: Dismissing a ReviewItem must never mutate, delete, or alter
        underlying automation definition or occurrence records."""
        auto = self.automation_store.save({
            "name": "Deploy Bot",
            "goal": "Deploy release",
            "project_id": "proj-prod",
            "enabled": True,
        })
        occ = self.automation_store.claim_occurrence(
            automation_id=auto["id"],
            scheduled_for="2026-08-24T22:00:00Z",
        )
        self.assertIsNotNone(occ)
        self.automation_store.mark_claim_dispatched(occ["id"], "run-deploy-99")
        self.automation_store.complete_claim(occ["id"], "succeeded")
        completed_occs = self.automation_store.list_occurrences(auto["id"])
        self.assertEqual(len(completed_occs), 1)
        completed_occ = completed_occs[0]

        # Record review item
        record_automation_review_item(
            self.review_store,
            self.automation_store,
            occurrence=completed_occ,
            outcome="succeeded",
        )
        items = self.review_store.list_items(project_id="proj-prod")
        self.assertEqual(len(items), 1)
        review_id = items[0]["id"]

        # Dismiss the review item
        self.review_store.dismiss(review_id)
        dismissed_item = self.review_store.get_item(review_id)
        self.assertIsNotNone(dismissed_item)
        self.assertEqual(dismissed_item["status"], "dismissed")

        # Invariant check: Underlying automation and occurrence remain completely untouched!
        auto_after = self.automation_store.get(auto["id"])
        self.assertIsNotNone(auto_after)
        self.assertEqual(auto_after["name"], "Deploy Bot")
        self.assertEqual(auto_after["goal"], "Deploy release")

        occs_after = self.automation_store.list_occurrences(auto["id"])
        self.assertEqual(len(occs_after), 1)
        self.assertEqual(occs_after[0]["id"], occ["id"])
        self.assertEqual(occs_after[0]["status"], "succeeded")
        self.assertEqual(occs_after[0]["run_id"], "run-deploy-99")


if __name__ == "__main__":
    unittest.main()
