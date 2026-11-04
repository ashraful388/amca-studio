"""
AMCA Studio Beta B13 Intelligent Project Lifecycle Management Foundation Test Suite
Validates:
1. ProjectMilestoneRecord creation and evidence link validation
2. ProjectLifecycleProgress derivation from verified completion counters
3. Local SQLite persistence with strict project isolation
4. Zero subjective AI scores; strictly empirical verification
"""

import tempfile
import unittest
from pathlib import Path

from amca.project_lifecycle import (
    ProjectLifecycleProgress,
    ProjectLifecycleStore,
    ProjectMilestoneRecord,
    calculate_project_lifecycle_progress,
)


class StudioBetaProjectLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_lifecycle.sqlite3"
        self.store = ProjectLifecycleStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_milestone_persistence_and_project_isolation(self) -> None:
        """Verify milestone records persist in SQLite with project boundaries."""
        m1 = ProjectMilestoneRecord(
            milestone_id="m-01-core",
            project_id="proj-beta-amca",
            title="Core Architecture Readiness",
            completion_criteria=("test_suite_passes", "docs_published"),
            evidence_links=("/docs/arch.md", "/tests/test_arch.py"),
            is_completed=True,
            completed_at="2026-09-02T08:00:00Z",
        )
        self.store.save_milestone(m1)

        # Retrieve in Project Beta AMCA
        p_milestones = self.store.list_milestones("proj-beta-amca")
        self.assertEqual(len(p_milestones), 1)
        self.assertEqual(p_milestones[0]["milestone_id"], "m-01-core")
        self.assertTrue(p_milestones[0]["is_completed"])

        # Project Other must be empty
        other_milestones = self.store.list_milestones("proj-other")
        self.assertEqual(len(other_milestones), 0)

    def test_progress_calculation_strictly_measurable(self) -> None:
        """Verify progress calculation derives directly from verified completion counts without subjective scoring."""
        milestones = [
            ProjectMilestoneRecord("m-1", "p1", "M1", ("done",), (), True),
            ProjectMilestoneRecord("m-2", "p1", "M2", ("done",), (), True),
            ProjectMilestoneRecord("m-3", "p1", "M3", ("done",), (), False),
            ProjectMilestoneRecord("m-4", "p1", "M4", ("done",), (), False),
        ]

        progress = calculate_project_lifecycle_progress(
            "p1",
            milestones,
            total_tasks=10,
            completed_tasks=5,
            artifact_count=8,
            workflow_run_count=4,
        )

        self.assertEqual(progress.total_milestones, 4)
        self.assertEqual(progress.completed_milestones, 2)
        self.assertEqual(progress.total_tasks, 10)
        self.assertEqual(progress.completed_tasks, 5)
        self.assertEqual(progress.artifact_count, 8)
        self.assertEqual(progress.workflow_run_count, 4)
        # 0.7 * (2/4) + 0.3 * (5/10) = 0.35 + 0.15 = 0.50
        self.assertEqual(progress.progress_ratio, 0.50)


if __name__ == "__main__":
    unittest.main()

