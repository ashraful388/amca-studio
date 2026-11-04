"""
AMCA Studio Beta B2 Collaborative Intelligence Foundation Test Suite
Validates:
1. Collaboration session record creation and persistence
2. Strict project isolation across sessions
3. Measurable quality metrics computation (zero hallucinated scores)
4. Full backward compatibility with existing DesktopCollaborationRouter and planners
"""

import tempfile
import unittest
from pathlib import Path

from amca.collaboration import (
    CollaborationQualityMetrics,
    CollaborationSessionRecord,
    CollaborationSessionStore,
    calculate_collaboration_metrics,
    DesktopCollaborationRouter,
)
from amca.domain import CollaborationMode


class StudioBetaCollaborativeIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "collab_sessions.sqlite3"
        self.store = CollaborationSessionStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_metrics_calculation_truthfulness(self) -> None:
        """Verify metric calculation is strictly derived from measurable execution counters."""
        metrics = calculate_collaboration_metrics(
            participants=["qwen2.5-coder:7b", "llama3.2:3b", "qwen2.5-coder:7b"],
            decomposition_steps=3,
            local_count=4,
            total_executions=5,
            synthesis_complete=True,
            unresolved_conflicts=0,
        )
        self.assertEqual(metrics.participant_count, 2)  # Unique participants
        self.assertEqual(metrics.decomposition_depth, 3)
        self.assertEqual(metrics.model_diversity, 2)
        self.assertEqual(metrics.local_execution_ratio, 0.8)
        self.assertTrue(metrics.synthesis_complete)
        self.assertEqual(metrics.unresolved_conflicts, 0)

    def test_session_persistence_and_project_isolation(self) -> None:
        """Verify session records in Project Alpha do not bleed into Project Beta."""
        metrics = calculate_collaboration_metrics(
            participants=["qwen2.5-coder:7b", "claude-3-5-sonnet"],
            decomposition_steps=2,
            local_count=1,
            total_executions=2,
            synthesis_complete=True,
        )
        record = CollaborationSessionRecord(
            session_id="sess-alpha-001",
            project_id="proj-alpha",
            mode=CollaborationMode.JOINT,
            goal="Analyze security boundaries and verify contracts",
            participants=("qwen2.5-coder:7b", "claude-3-5-sonnet"),
            agent_roles={"qwen2.5-coder:7b": "code-inspector", "claude-3-5-sonnet": "lead-verifier"},
            metrics=metrics,
            decision_triggers=("high_complexity",),
            decision_reasons=("Task requires joint focus deliberation.",),
        )
        self.store.save_session(record)

        # Query Project Alpha
        sessions_a = self.store.list_sessions("proj-alpha")
        self.assertEqual(len(sessions_a), 1)
        self.assertEqual(sessions_a[0]["session_id"], "sess-alpha-001")
        self.assertEqual(sessions_a[0]["mode"], "joint")
        self.assertEqual(sessions_a[0]["metrics"]["local_execution_ratio"], 0.5)

        # Query Project Beta
        sessions_b = self.store.list_sessions("proj-beta")
        self.assertEqual(len(sessions_b), 0)

    def test_backward_compatibility_with_router(self) -> None:
        """Verify existing DesktopCollaborationRouter functions completely unmodified."""
        from amca.collaboration import TaskShape, CollaborationSignals
        from amca.domain import ComplexityLevel
        router = DesktopCollaborationRouter()
        shape = TaskShape(
            complexity=ComplexityLevel.HIGH,
            coupled=True,
            decomposable=False,
            verification_heavy=False,
            novelty_required=False,
        )
        signals = CollaborationSignals()
        decision = router.decide(
            shape=shape,
            signals=signals,
            eligible_model_count=2,
        )
        self.assertIsNotNone(decision.mode)
        self.assertGreater(decision.expected_rounds, 0)


if __name__ == "__main__":
    unittest.main()

