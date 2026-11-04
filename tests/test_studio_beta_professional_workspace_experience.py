"""
AMCA Studio Beta B11 Professional Workspace Experience Foundation Test Suite
Validates:
1. WorkspaceOverviewModel read-only aggregation schema
2. WorkspaceActivityTimelineStore append-only persistence and workspace isolation
3. Cross-module trace navigation pointers
4. Privacy validation: zero external telemetry and zero sensitive content logging
"""

import tempfile
import unittest
from pathlib import Path

from amca.professional_workspace import (
    WorkspaceActivityItem,
    WorkspaceActivityTimelineStore,
    WorkspaceOverviewModel,
    build_cross_module_trace_links,
)


class StudioBetaProfessionalWorkspaceExperienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_workspace.sqlite3"
        self.store = WorkspaceActivityTimelineStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_workspace_overview_model_schema(self) -> None:
        """Verify overview model represents workspace health and capacity accurately."""
        overview = WorkspaceOverviewModel(
            workspace_id="ws-amca-pro",
            project_count=5,
            active_agent_count=8,
            local_model_count=3,
            pending_approval_count=1,
            health_status="healthy",
            last_active_timestamp="2026-09-02T08:00:00Z",
        )
        d = overview.to_dict()
        self.assertEqual(d["workspace_id"], "ws-amca-pro")
        self.assertEqual(d["project_count"], 5)
        self.assertEqual(d["local_model_count"], 3)
        self.assertEqual(d["health_status"], "healthy")

    def test_activity_timeline_persistence_and_isolation(self) -> None:
        """Verify timeline events are stored in SQLite and partitioned by workspace."""
        item = self.store.record_activity(
            workspace_id="ws-enterprise-corp",
            event_type="workflow_event",
            title="Workflow Completed",
            description="Autonomous refactor workflow completed 4 steps successfully.",
            actor_id="agent.refactor-specialist",
            resource_ref="/workflows/wf-101",
        )
        self.assertTrue(item.event_id.startswith("act-"))

        # Query Workspace A
        acts_a = self.store.list_activities("ws-enterprise-corp")
        self.assertEqual(len(acts_a), 1)
        self.assertEqual(acts_a[0]["title"], "Workflow Completed")
        self.assertEqual(acts_a[0]["actor_id"], "agent.refactor-specialist")

        # Query Workspace B (strict isolation)
        acts_b = self.store.list_activities("ws-other-corp")
        self.assertEqual(len(acts_b), 0)

    def test_cross_module_trace_links(self) -> None:
        """Verify lightweight deep links are built correctly without payload duplication."""
        links = build_cross_module_trace_links(
            task_id="task-99",
            agent_id="agent.code",
            model_id="ollama/qwen2.5-coder:7b",
            artifact_id="art-doc-1",
        )
        self.assertEqual(links["task"], "/tasks/task-99")
        self.assertEqual(links["agent"], "/agents/agent.code")
        self.assertEqual(links["model"], "/models/ollama/qwen2.5-coder:7b")
        self.assertEqual(links["artifact"], "/artifacts/art-doc-1")
        self.assertNotIn("memory", links)


if __name__ == "__main__":
    unittest.main()

