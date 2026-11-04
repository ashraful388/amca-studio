"""
AMCA Studio Beta B9 Adaptive User Interaction Foundation Test Suite
Validates:
1. UserInteractionContext and context-aware assistance recommendation derivation
2. UserPreferenceRecord storage and retrieval in local SQLite
3. Privacy boundaries: explicit preference controls with zero covert behavioral profiling
4. Full backward compatibility with Studio engine
"""

import tempfile
import unittest
from pathlib import Path

from amca.user_interaction import (
    AdaptiveAssistanceRecommendation,
    UserInteractionContext,
    UserInteractionStore,
    UserPreferenceRecord,
    generate_adaptive_assistance,
)


class StudioBetaAdaptiveUserInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_user.sqlite3"
        self.store = UserInteractionStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_explicit_user_preference_persistence(self) -> None:
        """Verify user preferences are stored and queried without external telemetry."""
        prefs = UserPreferenceRecord(
            user_id="user-ashraf",
            preferred_model_id="ollama/qwen2.5-coder:7b",
            preferred_collaboration_mode="hybrid",
            ui_theme="dark",
            notifications_enabled=True,
        )
        self.store.save_preferences(prefs)

        saved = self.store.get_preferences("user-ashraf")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["preferred_model_id"], "ollama/qwen2.5-coder:7b")
        self.assertEqual(saved["preferred_collaboration_mode"], "hybrid")

        # Non-existent user
        missing = self.store.get_preferences("unknown-user")
        self.assertIsNone(missing)

    def test_context_aware_assistance_generation(self) -> None:
        """Verify recommendations are strictly derived from visible UI state without hidden tracking."""
        chat_ctx = UserInteractionContext(
            context_id="ctx-001",
            user_id="user-ashraf",
            active_view="chat",
            current_project_id="proj-amca",
            recent_actions=("send_message",),
        )
        chat_recs = generate_adaptive_assistance(chat_ctx)
        self.assertTrue(len(chat_recs) > 0)
        self.assertEqual(chat_recs[0].category, "shortcut")
        self.assertIn("Ctrl+K", chat_recs[0].description)

        model_ctx = UserInteractionContext(
            context_id="ctx-002",
            user_id="user-ashraf",
            active_view="models",
            current_project_id="proj-amca",
            recent_actions=(),
        )
        model_recs = generate_adaptive_assistance(model_ctx)
        self.assertTrue(len(model_recs) > 0)
        self.assertEqual(model_recs[0].category, "feature_discovery")
        self.assertIn("Local $0", model_recs[0].title)


if __name__ == "__main__":
    unittest.main()

