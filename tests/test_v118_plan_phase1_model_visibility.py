"""AMCA Studio v1.1.8 Final Build Plan - Phase 1 evidence (model visibility).

Phase 1 requires provider discovery to keep working while adding per-model
visibility: a connection test must validate credentials and discovery without
publishing every discovered model into chat, and only explicitly enabled
models may appear in model selectors.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from amca.model_hub import ModelHubStore


class ModelVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "amca.sqlite3"
        self.store = ModelHubStore(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _provider(self, model_ids: list[str] | None = None):
        provider, _ = self.store.create_provider(
            {
                "provider_kind": "openai",
                "name": "OpenAI",
                "endpoint": "",
                "model_ids": model_ids or [],
            }
        )
        return provider

    def test_discovery_publishes_nothing_by_default(self) -> None:
        provider = self._provider()
        discovered = self.store.replace_provider_catalog(
            provider["id"], ["gpt-a", "gpt-b", "gpt-c"]
        )
        self.assertEqual(
            sorted(discovered["model_ids"]), ["gpt-a", "gpt-b", "gpt-c"]
        )
        self.assertEqual(discovered["enabled_model_ids"], [])
        self.assertEqual(self.store.enabled_model_ids(provider["id"]), [])

    def test_enabling_one_model_publishes_only_that_model(self) -> None:
        provider = self._provider()
        self.store.replace_provider_catalog(provider["id"], ["gpt-a", "gpt-b"])
        updated = self.store.set_model_visibility(provider["id"], "gpt-a", True)
        self.assertEqual(updated["enabled_model_ids"], ["gpt-a"])
        self.assertEqual(self.store.enabled_model_ids(provider["id"]), ["gpt-a"])

    def test_hiding_an_enabled_model_removes_it_again(self) -> None:
        provider = self._provider()
        self.store.replace_provider_catalog(provider["id"], ["gpt-a"])
        self.store.set_model_visibility(provider["id"], "gpt-a", True)
        updated = self.store.set_model_visibility(provider["id"], "gpt-a", False)
        self.assertEqual(updated["enabled_model_ids"], [])

    def test_rediscovery_preserves_the_users_choices(self) -> None:
        provider = self._provider()
        self.store.replace_provider_catalog(provider["id"], ["gpt-a", "gpt-b"])
        self.store.set_model_visibility(provider["id"], "gpt-b", True)
        # The provider later reports a different catalog that still contains
        # gpt-b and adds a new model.
        updated = self.store.replace_provider_catalog(
            provider["id"], ["gpt-b", "gpt-new"]
        )
        self.assertEqual(updated["enabled_model_ids"], ["gpt-b"])
        self.assertIn("gpt-new", updated["model_ids"])
        self.assertNotIn("gpt-new", updated["enabled_model_ids"])

    def test_a_model_outside_the_catalog_cannot_be_enabled(self) -> None:
        provider = self._provider()
        self.store.replace_provider_catalog(provider["id"], ["gpt-a"])
        with self.assertRaisesRegex(ValueError, "not part of this provider"):
            self.store.set_model_visibility(provider["id"], "gpt-unknown", True)

    def test_visibility_on_an_unknown_provider_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider connection not found"):
            self.store.set_model_visibility("provider.missing", "gpt-a", True)

    def test_an_existing_catalog_is_adopted_as_visible_on_upgrade(self) -> None:
        """An install that already had a working catalog must not go dark."""
        provider = self._provider(model_ids=["gpt-a", "gpt-b"])
        self.assertEqual(
            sorted(provider["enabled_model_ids"]), ["gpt-a", "gpt-b"]
        )
        # A fresh store over the same database keeps the adopted state.
        reopened = ModelHubStore(self.database)
        self.assertEqual(
            sorted(reopened.provider(provider["id"])["enabled_model_ids"]),
            ["gpt-a", "gpt-b"],
        )

    def test_adoption_happens_once_and_later_discovery_stays_hidden(self) -> None:
        provider = self._provider(model_ids=["gpt-a"])
        self.store.replace_provider_catalog(provider["id"], ["gpt-a", "gpt-b"])
        reopened = ModelHubStore(self.database)
        self.assertEqual(reopened.provider(provider["id"])["enabled_model_ids"], ["gpt-a"])


if __name__ == "__main__":
    unittest.main()


class VisibilityFallbackTransitionTests(unittest.TestCase):
    """The first explicit toggle must not hide the rest of a legacy catalog."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "amca.sqlite3"
        self.store = ModelHubStore(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _fallback_provider(self, model_ids: list[str]):
        """A provider whose catalog predates the visibility table (no rows)."""
        provider, _ = self.store.create_provider(
            {
                "provider_kind": "openai",
                "name": "Pasted catalog",
                "endpoint": "",
                "model_ids": model_ids,
            }
        )
        self.assertEqual(
            sorted(provider["enabled_model_ids"]), sorted(model_ids)
        )
        return provider

    def test_hiding_one_model_keeps_the_others_visible(self) -> None:
        provider = self._fallback_provider(["alpha", "beta", "gamma"])
        updated = self.store.set_model_visibility(provider["id"], "beta", False)
        self.assertEqual(sorted(updated["enabled_model_ids"]), ["alpha", "gamma"])

    def test_hiding_one_model_survives_a_restart(self) -> None:
        provider = self._fallback_provider(["alpha", "beta"])
        self.store.set_model_visibility(provider["id"], "beta", False)
        reopened = ModelHubStore(self.database)
        self.assertEqual(
            reopened.provider(provider["id"])["enabled_model_ids"], ["alpha"]
        )

    def test_enabling_one_model_on_a_fallback_provider_keeps_the_rest(self) -> None:
        provider = self._fallback_provider(["alpha", "beta"])
        updated = self.store.set_model_visibility(provider["id"], "alpha", True)
        self.assertEqual(sorted(updated["enabled_model_ids"]), ["alpha", "beta"])


class VisibilityUpgradeSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "amca.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _sql(self, statement: str, parameters: tuple = ()) -> list:
        connection = sqlite3.connect(self.database)
        try:
            rows = connection.execute(statement, parameters).fetchall()
            connection.commit()
            return rows
        finally:
            connection.close()

    def test_an_unreadable_legacy_catalog_does_not_block_store_construction(self) -> None:
        """A row written by an older build must not make the store unopenable."""
        ModelHubStore(self.database)
        self._sql(
            """INSERT INTO model_hub_providers
               (id, provider_kind, name, endpoint, credential_ref, status,
                pricing_mode, model_ids_json, created_at, updated_at)
               VALUES ('p1', 'openai', 'Old', 'https://x/v1', 'ref',
                       'catalog_ready', 'auto', ?, '2026-01-01', '2026-01-01')""",
            ('["gpt 4o", "gpt-a"]',),
        )
        # Must not raise: adoption is a convenience, not a startup gate.
        reopened = ModelHubStore(self.database)
        provider = reopened.provider("p1")
        self.assertIsNotNone(provider)
        # The unreadable catalog is skipped, not adopted as visible.
        self.assertEqual(
            self._sql(
                "SELECT COUNT(*) FROM model_hub_model_visibility WHERE provider_id='p1'"
            )[0][0],
            0,
        )

    def test_deleting_a_provider_removes_its_visibility_rows(self) -> None:
        store = ModelHubStore(self.database)
        provider, _ = store.create_provider(
            {"provider_kind": "openai", "name": "Temp", "endpoint": ""}
        )
        store.replace_provider_catalog(provider["id"], ["m1"])
        store.set_model_visibility(provider["id"], "m1", True)
        store.delete_provider(provider["id"])
        rows = self._sql(
            "SELECT COUNT(*) FROM model_hub_model_visibility WHERE provider_id=?",
            (provider["id"],),
        )
        self.assertEqual(int(rows[0][0]), 0)


class HiddenModelIsNotRunnableTests(unittest.TestCase):
    """Hiding a model has to bind on every path, not just the composer menu."""

    def setUp(self) -> None:
        from amca.service import ServiceContext

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.context = ServiceContext(
            root=self.root,
            database=self.root / "amca.db",
            benchmarks=self.root / "benchmarks.json",
        )
        created = self.context.create_hub_provider(
            {
                "provider_kind": "custom",
                "name": "Visibility Provider",
                "endpoint": "https://visibility.example/v1",
                "secret": "test-key",
                "model_ids": ["alpha", "beta"],
            }
        )
        self.provider_id = created["provider"]["id"]

    def tearDown(self) -> None:
        self.context.close()
        self.temp.cleanup()

    def _publish(self, model: str) -> None:
        self.context.set_hub_model_visibility(
            self.provider_id, {"model_id": model, "enabled": True}
        )

    def test_a_hidden_model_is_refused_as_a_manual_participant(self) -> None:
        self._publish("alpha")
        # A pasted catalog is published as-is, so hiding beta is explicit here.
        self.context.set_hub_model_visibility(
            self.provider_id, {"model_id": "beta", "enabled": False}
        )
        with self.assertRaisesRegex(ValueError, "not published"):
            self.context.start_chat_run(
                goal="Summarize the note.",
                participant_ids=[f"{self.provider_id}:beta"],
                mode="solo",
                selection_policy="manual_model_set",
            )

    def test_a_newly_discovered_model_is_refused_until_it_is_published(self) -> None:
        """Discovery alone must not make a model runnable."""
        self.context.model_hub_store.replace_provider_catalog(
            self.provider_id, ["alpha", "beta", "gamma"]
        )
        self._publish("alpha")
        with self.assertRaisesRegex(ValueError, "not published"):
            self.context.start_chat_run(
                goal="Summarize the note.",
                participant_ids=[f"{self.provider_id}:gamma"],
                mode="solo",
                selection_policy="manual_model_set",
            )

    def test_a_published_model_is_accepted_as_a_manual_participant(self) -> None:
        self._publish("alpha")
        # Reaching the run registry (rather than a participant refusal) is the
        # proof the published model passed validation.
        result = self.context.start_chat_run(
            goal="Summarize the note.",
            participant_ids=[f"{self.provider_id}:alpha"],
            mode="solo",
            selection_policy="manual_model_set",
        )
        self.assertNotEqual(result.get("status"), "failed")

    def test_a_disabled_provider_refuses_its_still_published_model(self) -> None:
        self._publish("alpha")
        self.context.update_hub_provider(self.provider_id, {"enabled": False})
        provider = self.context.model_hub_store.provider(self.provider_id)
        self.assertIn("alpha", provider["enabled_model_ids"])
        with self.assertRaisesRegex(ValueError, "provider .* is disabled"):
            self.context.start_chat_run(
                goal="Summarize the note.",
                participant_ids=[f"{self.provider_id}:alpha"],
                mode="solo",
                selection_policy="manual_model_set",
            )

    def test_reenabling_provider_preserves_published_model_selection(self) -> None:
        self._publish("alpha")
        self.context.update_hub_provider(self.provider_id, {"enabled": False})
        self.context.update_hub_provider(self.provider_id, {"enabled": True})
        provider = self.context.model_hub_store.provider(self.provider_id)
        self.assertIn("alpha", provider["enabled_model_ids"])
        result = self.context.start_chat_run(
            goal="Summarize the note.",
            participant_ids=[f"{self.provider_id}:alpha"],
            mode="solo",
            selection_policy="manual_model_set",
        )
        self.assertNotEqual(result.get("status"), "failed")

    def test_the_visibility_endpoint_rejects_a_non_boolean_flag(self) -> None:
        with self.assertRaisesRegex(ValueError, "enabled must be a boolean"):
            self.context.set_hub_model_visibility(
                self.provider_id, {"model_id": "alpha", "enabled": "yes"}
            )

    def test_the_visibility_endpoint_requires_a_model_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "model_id is required"):
            self.context.set_hub_model_visibility(
                self.provider_id, {"enabled": True}
            )
