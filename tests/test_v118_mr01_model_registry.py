"""AMCA-MR-01: canonical Model Registry evidence.

Covers the work order's minimum test matrix:
  CRUD · capability (known / unknown / no name-based guessing) · coverage
  (multiple providers, duplicate model ids, local vs cloud) · migration (Model Hub
  data preserved, visibility preserved, no unwanted writes) · safety (hidden model
  cannot become selectable, invalid provider cannot inject models, malformed
  capability data rejected).
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from amca.model_registry import (
    ModelLocality,
    ModelRegistryError,
    ModelRegistryStore,
    PricingStatus,
    SpeedClass,
    build_record,
)


class _RegistryBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "amca.db"
        self.store = ModelRegistryStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _record(self, **overrides):
        values = {
            "model_ref": "provider.p1:gpt-4o",
            "provider_kind": "openai",
            "provider_id": "p1",
            "model_id": "gpt-4o",
            "display_name": "GPT 4o",
            "capabilities": {"tools": True, "vision": True, "streaming": True},
            "capability_source": "declared",
            "context_window": 128000,
            "record_source": "adapter_declaration",
        }
        values.update(overrides)
        return build_record(**values)


class NoWritesOnOpenTests(_RegistryBase):
    """Opening a database must not create unwanted writes (work order §3)."""

    def test_constructing_the_store_writes_nothing(self) -> None:
        self.assertFalse(self.database.exists())
        self.assertIsNone(self.store.get("provider.p1:gpt-4o"))
        self.assertEqual(self.store.list_records(), [])
        self.assertFalse(self.database.exists(), "a read must not create the file")

    def test_reads_against_an_existing_database_add_no_table(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("CREATE TABLE unrelated (id TEXT PRIMARY KEY)")
            connection.commit()
        finally:
            connection.close()
        before = self._schema()
        reopened = ModelRegistryStore(self.database)
        self.assertEqual(reopened.list_records(), [])
        self.assertEqual(reopened.get("x:y"), None)
        self.assertEqual(self._schema(), before, "opening added a table")

    def _schema(self) -> set[str]:
        connection = sqlite3.connect(self.database)
        try:
            return {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            connection.close()


class CrudTests(_RegistryBase):
    def test_create_and_retrieve(self) -> None:
        stored = self.store.upsert(self._record())
        self.assertEqual(stored.model_ref, "provider.p1:gpt-4o")
        fetched = self.store.get("provider.p1:gpt-4o")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.identity.display_name, "GPT 4o")
        self.assertEqual(fetched.execution.context_window, 128000)
        self.assertTrue(fetched.capabilities.tools)

    def test_update_replaces_the_record(self) -> None:
        self.store.upsert(self._record())
        self.store.upsert(self._record(context_window=200000, capabilities={"vision": False}))
        fetched = self.store.get("provider.p1:gpt-4o")
        self.assertEqual(fetched.execution.context_window, 200000)
        self.assertIs(fetched.capabilities.vision, False)
        self.assertIsNone(fetched.capabilities.tools)
        self.assertEqual(len(self.store.list_records()), 1)

    def test_delete_is_idempotent(self) -> None:
        self.store.upsert(self._record())
        self.assertTrue(self.store.delete("provider.p1:gpt-4o"))
        self.assertFalse(self.store.delete("provider.p1:gpt-4o"))
        self.assertIsNone(self.store.get("provider.p1:gpt-4o"))

    def test_an_unregistered_model_answers_with_an_unknown_record(self) -> None:
        record, registered = self.store.resolve("ollama:never-seen:latest")
        self.assertFalse(registered)
        self.assertEqual(record.identity.provider_kind, "ollama")
        self.assertEqual(record.identity.model_id, "never-seen:latest")
        self.assertIsNone(record.capabilities.tools)
        self.assertIsNone(record.execution.context_window)


class CapabilityTests(_RegistryBase):
    def test_known_capabilities_are_returned(self) -> None:
        self.store.upsert(self._record())
        tags = self.store.capabilities_for("provider.p1:gpt-4o")
        self.assertEqual(tags, frozenset({"tools", "vision"}))

    def test_unknown_capability_stays_unknown(self) -> None:
        self.store.upsert(self._record(capabilities={"tools": True}))
        record = self.store.get("provider.p1:gpt-4o")
        for field in ("vision", "audio", "json_mode", "coding", "system_messages"):
            self.assertIsNone(
                getattr(record.capabilities, field), f"{field} must stay unknown"
            )

    def test_no_name_based_guessing(self) -> None:
        """A name that looks like a vision/audio model declares nothing."""
        for index, name in enumerate(
            ("vision-pro-4o", "gemini-2-audio-realtime", "llama-4-coder-70b")
        ):
            ref = f"provider.p{index}:{name}"
            self.store.upsert(
                build_record(
                    model_ref=ref,
                    provider_kind="openai_compatible",
                    model_id=name,
                )
            )
            self.assertEqual(
                self.store.capabilities_for(ref),
                frozenset(),
                f"{name} must not acquire capabilities from its name",
            )

    def test_an_unknown_context_window_is_never_defaulted(self) -> None:
        self.store.upsert(self._record(context_window=None))
        self.assertIsNone(self.store.context_window_for("provider.p1:gpt-4o"))
        self.assertIsNone(self.store.context_window_for("provider.unknown:model"))


class CoverageTests(_RegistryBase):
    def test_multiple_providers_are_recorded_independently(self) -> None:
        self.store.upsert(self._record())
        self.store.upsert(
            self._record(
                model_ref="provider.p2:claude-3-5-sonnet",
                provider_kind="anthropic",
                provider_id="p2",
                model_id="claude-3-5-sonnet",
                display_name="Claude 3.5 Sonnet",
            )
        )
        self.store.upsert(
            self._record(
                model_ref="provider.p3:meta-llama/Llama-3.3-70B-Instruct-Turbo",
                provider_kind="openrouter",
                provider_id="p3",
                model_id="meta-llama/Llama-3.3-70B-Instruct-Turbo",
            )
        )
        kinds = {record.identity.provider_kind for record in self.store.list_records()}
        self.assertEqual(kinds, {"openai", "anthropic", "openrouter"})

    def test_duplicate_model_ids_across_providers_stay_distinct(self) -> None:
        self.store.upsert(self._record())
        self.store.upsert(
            self._record(
                model_ref="provider.p2:gpt-4o",
                provider_id="p2",
                display_name="GPT 4o (other connection)",
            )
        )
        self.assertEqual(len(self.store.list_records()), 2)
        self.assertEqual(self.store.get("provider.p1:gpt-4o").identity.provider_id, "p1")
        self.assertEqual(self.store.get("provider.p2:gpt-4o").identity.provider_id, "p2")

    def test_local_and_cloud_are_distinguished(self) -> None:
        local = build_record(
            model_ref="ollama:qwen3:1.7b", provider_kind="ollama", model_id="qwen3:1.7b"
        )
        cloud = self._record()
        self.assertIs(local.identity.locality, ModelLocality.LOCAL)
        self.assertIs(cloud.identity.locality, ModelLocality.CLOUD)

    def test_local_models_are_not_priced(self) -> None:
        local = build_record(
            model_ref="ollama:qwen3:1.7b", provider_kind="ollama", model_id="qwen3:1.7b"
        )
        self.assertIs(local.cost.pricing_status, PricingStatus.NOT_APPLICABLE)
        self.assertIsNone(local.cost.input_per_1m_usd)
        cloud = self._record()
        self.assertIs(cloud.cost.pricing_status, PricingStatus.UNKNOWN)
        self.assertIsNone(cloud.cost.output_per_1m_usd)


class PricingIsNeverInventedTests(_RegistryBase):
    def test_rates_without_a_known_status_are_refused(self) -> None:
        with self.assertRaisesRegex(ModelRegistryError, "pricing rates require"):
            self._record(input_per_1m_usd=3.0)

    def test_a_known_price_requires_a_recorded_source(self) -> None:
        with self.assertRaisesRegex(ModelRegistryError, "requires a pricing_source"):
            self._record(
                pricing_status="known", input_per_1m_usd=3.0, output_per_1m_usd=15.0
            )

    def test_a_known_price_with_a_source_is_accepted(self) -> None:
        record = self._record(
            pricing_status="known",
            input_per_1m_usd=3.0,
            output_per_1m_usd=15.0,
            currency="USD",
            pricing_source="provider price page 2026-09-18",
        )
        self.assertEqual(record.cost.input_per_1m_usd, 3.0)
        self.assertEqual(record.cost.currency, "USD")

    def test_speed_uses_categories_not_precision(self) -> None:
        self.assertIs(self._record().performance.speed_class, SpeedClass.UNKNOWN)
        fast = self._record(speed_class="fast")
        self.assertIs(fast.performance.speed_class, SpeedClass.FAST)
        with self.assertRaisesRegex(ModelRegistryError, "speed_class"):
            self._record(speed_class="3.2")


class MalformedDataTests(_RegistryBase):
    def test_a_non_boolean_capability_is_rejected(self) -> None:
        for bad in ("yes", 1, "true", []):
            with self.assertRaisesRegex(ModelRegistryError, "capabilities.vision"):
                self._record(capabilities={"vision": bad})

    def test_a_non_positive_context_window_is_rejected(self) -> None:
        for bad in (0, -1, "big", 1.5, True):
            with self.assertRaisesRegex(ModelRegistryError, "context_window"):
                self._record(context_window=bad)

    def test_an_invalid_provider_identifier_is_rejected(self) -> None:
        with self.assertRaisesRegex(ModelRegistryError, "provider_kind"):
            self._record(provider_kind="not a provider")
        with self.assertRaisesRegex(ModelRegistryError, "provider_id"):
            self._record(provider_id="has spaces")

    def test_an_unknown_capability_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(ModelRegistryError, "capability_source"):
            self._record(capability_source="guessed")

    def test_an_unknown_locality_is_rejected(self) -> None:
        with self.assertRaisesRegex(ModelRegistryError, "locality"):
            self._record(locality="somewhere")

    def test_a_control_character_in_the_reference_is_rejected(self) -> None:
        with self.assertRaisesRegex(ModelRegistryError, "control characters"):
            self._record(model_ref="provider.p1:bad\x00ref")


class ServiceIntegrationTests(unittest.TestCase):
    """The service APIs, Model Hub migration, and the visibility boundary."""

    def setUp(self) -> None:
        from amca.service import ServiceContext

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.context = ServiceContext(
            root=self.root,
            database=self.root / "amca.db",
            benchmarks=self.root / "benchmarks.json",
        )

    def tearDown(self) -> None:
        self.context.close()
        self.temporary.cleanup()

    def _provider(self, model_ids: list[str]):
        created = self.context.create_hub_provider(
            {
                "provider_kind": "openai",
                "name": "Registry Provider",
                "endpoint": "https://registry.example/v1",
                "secret": "test-key",
                "model_ids": model_ids,
            }
        )
        return created["provider"]["id"]

    def test_startup_adopts_the_installs_existing_models(self) -> None:
        """Migration has a real entry point: the service runs it at startup.

        The *store* stays inert (see NoWritesOnOpenTests); the migration is a
        deliberate service step, which is what gives it a caller at all.
        """
        provider_id = self._provider(["gpt-4o"])
        # A fresh ServiceContext over the same database adopts what exists.
        from amca.service import ServiceContext

        second = ServiceContext(
            root=self.root,
            database=self.context.database,
            benchmarks=self.root / "benchmarks.json",
        )
        try:
            record = second.model_registry_store.get(f"{provider_id}:gpt-4o")
            self.assertIsNotNone(record, "startup must adopt existing providers")
        finally:
            second.close()

    def test_migration_adopts_providers_without_overwriting(self) -> None:
        provider_id = self._provider(["gpt-4o", "gpt-4o-mini"])
        # A user-declared fact must survive migration.
        self.context.declare_model_registry_record(
            {
                "model_ref": f"{provider_id}:gpt-4o",
                "provider_kind": "openai",
                "provider_id": provider_id,
                "model_id": "gpt-4o",
                "capabilities": {"vision": True},
                "capability_source": "declared",
                "record_source": "user_declaration",
            }
        )
        outcome = self.context.adopt_model_hub_into_registry()
        self.assertIn(f"{provider_id}:gpt-4o-mini", outcome["adopted"])
        self.assertIn(f"{provider_id}:gpt-4o", outcome["kept"])
        record = self.context.model_registry_store.get(f"{provider_id}:gpt-4o")
        self.assertIs(record.capabilities.vision, True, "migration overwrote a record")
        self.assertEqual(record.record_source, "user_declaration")

    def test_migration_records_declared_facts_and_leaves_the_rest_unknown(self) -> None:
        """The adapter's declaration is a fact; what it omits stays unknown."""
        provider_id = self._provider(["gpt-4o"])
        self.context.adopt_model_hub_into_registry()
        record = self.context.model_registry_store.get(f"{provider_id}:gpt-4o")
        # Declared by the OpenAI adapter.
        self.assertIs(record.capabilities.tools, True)
        self.assertIs(record.capabilities.vision, True)
        # Not declared anywhere, so still unknown - never inferred.
        self.assertIsNone(record.capabilities.audio)
        self.assertIsNone(record.execution.context_window)
        self.assertIsNone(record.execution.max_output_tokens)
        self.assertEqual(record.cost.pricing_status.value, "unknown")
        self.assertIsNone(record.cost.input_per_1m_usd)

    def test_migration_does_not_change_model_hub_or_visibility(self) -> None:
        provider_id = self._provider(["gpt-4o", "gpt-4o-mini"])
        before = self.context.model_hub_store.provider(provider_id)
        self.context.adopt_model_hub_into_registry()
        after = self.context.model_hub_store.provider(provider_id)
        self.assertEqual(before["model_ids"], after["model_ids"])
        self.assertEqual(before["enabled_model_ids"], after["enabled_model_ids"])

    def test_registering_a_model_does_not_change_visibility(self) -> None:
        """The registry records facts; visibility stays owned by Model Hub."""
        provider_id = self._provider(["gpt-4o", "gpt-4o-mini"])
        self.context.model_hub_store.set_model_visibility(
            provider_id, "gpt-4o-mini", False
        )
        before = self.context.model_hub_store.enabled_model_ids(provider_id)
        self.assertEqual(before, ["gpt-4o"])
        self.context.adopt_model_hub_into_registry()
        after = self.context.model_hub_store.enabled_model_ids(provider_id)
        self.assertEqual(after, before, "the registry must not publish a model")
        # And a model the user hid stays hidden even though it is registered.
        self.assertIn(
            f"{provider_id}:gpt-4o-mini",
            [record.model_ref for record in self.context.model_registry_store.list_records()],
        )
        self.assertNotIn("gpt-4o-mini", after)

    def test_the_service_read_api_reports_registered_state(self) -> None:
        provider_id = self._provider(["gpt-4o"])
        self.context.adopt_model_hub_into_registry()
        present = self.context.model_registry_record(f"{provider_id}:gpt-4o")
        self.assertTrue(present["registered"])
        absent = self.context.model_registry_record("ollama:not-there")
        self.assertFalse(absent["registered"])
        self.assertIsNone(absent["record"]["capabilities"]["tools"])
        listing = self.context.model_registry()
        self.assertEqual(listing["schema"], "amca-model-registry/1")
        self.assertGreaterEqual(listing["count"], 1)

    def test_the_declare_api_rejects_malformed_capability_data(self) -> None:
        with self.assertRaises(ModelRegistryError):
            self.context.declare_model_registry_record(
                {
                    "model_ref": "provider.p9:x",
                    "provider_kind": "openai",
                    "model_id": "x",
                    "capabilities": {"tools": "maybe"},
                }
            )
        self.assertIsNone(self.context.model_registry_store.get("provider.p9:x"))


class CandidateCapabilitySourceTests(unittest.TestCase):
    """The router now reads the registry instead of guessing from the name."""

    def setUp(self) -> None:
        from amca.service import ServiceContext

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.context = ServiceContext(
            root=self.root,
            database=self.root / "amca.db",
            benchmarks=self.root / "benchmarks.json",
        )

    def tearDown(self) -> None:
        self.context.close()
        self.temporary.cleanup()

    def test_a_model_name_never_produces_a_capability(self) -> None:
        """The name is not evidence, even when it reads like one."""
        # openai_compatible has no adapter declaration, so nothing is known.
        tags = self.context._candidate_capabilities("p1:vision-pro-4o", "openai_compatible")
        self.assertLessEqual(self.context.BASELINE_TASK_TAGS, tags)
        # ``coding`` names a kind of work, not a model property, so it is a
        # policy baseline.  A model property must never come from the name.
        for guessed in ("vision", "audio", "tools", "json"):
            self.assertNotIn(guessed, tags, f"{guessed} must not be guessed")

    def test_an_adapter_declaration_is_a_fact_not_a_guess(self) -> None:
        """The provider's adapter declares this for every model it serves."""
        tags = self.context._candidate_capabilities("p1:anything-at-all", "openai")
        self.assertIn("vision", tags, "the OpenAI adapter declares vision")
        self.assertIn("tools", tags)
        kimi = self.context._candidate_capabilities("p2:anything", "kimi")
        self.assertNotIn("vision", kimi, "the Kimi adapter declares no vision")

    def test_registry_facts_reach_the_router(self) -> None:
        self.context.declare_model_registry_record(
            {
                "model_ref": "p1:declared-vision",
                "provider_kind": "openai",
                "model_id": "declared-vision",
                "capabilities": {"vision": True, "tools": True},
                "capability_source": "declared",
            }
        )
        tags = self.context._candidate_capabilities("p1:declared-vision", "openai")
        self.assertIn("vision", tags)
        self.assertIn("tools", tags)

    def test_an_unknown_context_window_never_satisfies_a_requirement(self) -> None:
        self.assertEqual(self.context._candidate_context_window("p1:unknown"), 0)
        self.context.declare_model_registry_record(
            {
                "model_ref": "p1:known",
                "provider_kind": "openai",
                "model_id": "known",
                "context_window": 131072,
            }
        )
        self.assertEqual(self.context._candidate_context_window("p1:known"), 131072)



class ContextGateTests(unittest.TestCase):
    """A known-too-small window is refused; an unknown window is not asserted."""

    def _candidate(self, context_length: int):
        from amca.model_hub import AccessSource, ModelCandidate

        return ModelCandidate(
            model_ref="p1:m",
            source=AccessSource.BYOK,
            provider="openai:p1",
            expected_cost_usd=None,
            quality_score=0.9,
            capabilities=frozenset({"text", "reasoning"}),
            context_length=context_length,
        )

    def test_an_unknown_window_does_not_disqualify_a_model(self) -> None:
        from amca.model_hub import CostQualityGate

        gate = CostQualityGate()
        self.assertTrue(
            gate.accepts(
                self._candidate(0),
                minimum_quality=0.8,
                required_context=8192,
                required_capabilities=("text",),
            ),
            "excluding every unregistered model would disable routing",
        )

    def test_a_known_too_small_window_is_refused(self) -> None:
        from amca.model_hub import CostQualityGate

        gate = CostQualityGate()
        self.assertFalse(
            gate.accepts(
                self._candidate(4096),
                minimum_quality=0.8,
                required_context=8192,
                required_capabilities=("text",),
            ),
            "a recorded window that is too small must be refused",
        )

    def test_a_known_sufficient_window_is_accepted(self) -> None:
        from amca.model_hub import CostQualityGate

        gate = CostQualityGate()
        self.assertTrue(
            gate.accepts(
                self._candidate(131072),
                minimum_quality=0.8,
                required_context=8192,
                required_capabilities=("text",),
            )
        )

    def test_a_recorded_window_changes_the_routing_outcome(self) -> None:
        """Recording a real window is what makes the gate meaningful."""
        from amca.service import ServiceContext

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = ServiceContext(
                root=root,
                database=root / "amca.db",
                benchmarks=root / "benchmarks.json",
            )
            try:
                self.assertEqual(context._candidate_context_window("p1:small"), 0)
                context.declare_model_registry_record(
                    {
                        "model_ref": "p1:small",
                        "provider_kind": "openai",
                        "model_id": "small",
                        "context_window": 4096,
                    }
                )
                self.assertEqual(context._candidate_context_window("p1:small"), 4096)
            finally:
                context.close()

class ReviewFindingRegressionTests(unittest.TestCase):
    """Regressions for the defects the review and bug-hunt roles found."""

    def setUp(self) -> None:
        from amca.service import ServiceContext

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.context = ServiceContext(
            root=self.root,
            database=self.root / "amca.db",
            benchmarks=self.root / "benchmarks.json",
        )

    def tearDown(self) -> None:
        self.context.close()
        self.temporary.cleanup()

    def _publish(self, model_ids: list[str]) -> str:
        created = self.context.create_hub_provider(
            {
                "provider_kind": "openai",
                "name": "Finding Provider",
                "endpoint": "https://finding.example/v1",
                "secret": "test-key",
                "model_ids": model_ids,
            }
        )
        provider_id = created["provider"]["id"]
        for model in model_ids:
            self.context.model_hub_store.set_model_visibility(provider_id, model, True)
        return provider_id

    def _candidates(self, goal: str, attachments: tuple[str, ...] = ()):
        return self.context._auto_model_candidates(
            goal=goal,
            attachment_names=list(attachments),
            inventory={"runtime_reachable": False, "models": []},
            profiles=[],
        )

    def test_a_coding_goal_still_has_a_candidate(self) -> None:
        """P1: 'coding' was a required tag nothing could supply."""
        self._publish(["gpt-4o"])
        candidates, required, _, _ = self._candidates(
            "write code to fix the bug in the repository"
        )
        self.assertIn("coding", required)
        self.assertTrue(candidates, "a coding goal must still find a model")
        self.assertTrue(required.issubset(candidates[0].capabilities))

    def test_an_image_attachment_finds_a_declared_vision_model(self) -> None:
        """P1: 'vision' is required for an image and is now a declared fact."""
        provider_id = self._publish(["gpt-4o"])
        candidates, required, _, _ = self._candidates("look at this", ("a.png",))
        self.assertIn("vision", required)
        declared = next(
            item for item in candidates if item.model_ref == f"{provider_id}:gpt-4o"
        )
        self.assertIn("vision", declared.capabilities)
        from amca.model_hub import CostQualityGate

        self.assertTrue(
            CostQualityGate().accepts(
                declared,
                minimum_quality=0.65,
                required_capabilities=required,
                required_context=8192,
            )
        )

    def test_a_model_declared_without_vision_is_refused_for_an_image(self) -> None:
        """The declared fact binds in both directions: kimi declares no vision."""
        provider = self.context.create_hub_provider(
            {
                "provider_kind": "kimi",
                "name": "Kimi",
                "endpoint": "https://kimi.example/v1",
                "secret": "test-key",
                "model_ids": ["moonshot-v1"],
            }
        )
        provider_id = provider["provider"]["id"]
        self.context.model_hub_store.set_model_visibility(
            provider_id, "moonshot-v1", True
        )
        candidates, required, _, _ = self._candidates("look at this", ("a.png",))
        self.assertIn("vision", required)
        declared = next(
            item for item in candidates if item.model_ref == f"{provider_id}:moonshot-v1"
        )
        self.assertNotIn("vision", declared.capabilities)
        from amca.model_hub import CostQualityGate

        self.assertFalse(
            CostQualityGate().accepts(
                declared,
                minimum_quality=0.65,
                required_capabilities=required,
                required_context=8192,
            ),
            "a model declared without vision must not serve an image request",
        )

    def test_a_non_mapping_capability_container_is_rejected(self) -> None:
        """P3: this used to raise TypeError and drop the HTTP connection."""
        with self.assertRaisesRegex(ModelRegistryError, "object or null"):
            self.context.declare_model_registry_record(
                {
                    "model_ref": "p1:x",
                    "provider_kind": "openai",
                    "model_id": "x",
                    "capabilities": 123,
                }
            )
        self.assertIsNone(self.context.model_registry_store.get("p1:x"))

    def test_a_corrupt_row_does_not_break_reads_or_routing(self) -> None:
        """Defect 4/9a: one poisoned row aborted list_records and routing."""
        store = self.context.model_registry_store
        before = len(store.list_records())
        store.upsert(
            build_record(
                model_ref="p1:good",
                provider_kind="openai",
                model_id="good",
                context_window=8192,
            )
        )
        connection = sqlite3.connect(self.context.database)
        try:
            connection.execute(
                "INSERT INTO model_registry (model_ref, provider_kind, provider_id,"
                " model_id, display_name, locality, capabilities_json, execution_json,"
                " cost_json, performance_json, record_source, verified_at, updated_at)"
                " VALUES ('p1:bad','openai',NULL,'bad','bad','cloud','{not json',"
                " '{\"context_window\": \"many\"}','{}','{}','user_declaration',"
                " NULL,'x')"
            )
            connection.commit()
        finally:
            connection.close()
        # The service adopts the install's own profiles at startup, so compare
        # against the count before the poisoned row was inserted.
        self.assertEqual(
            len(store.list_records()), before + 2, "a bad row hid the good ones"
        )
        self.assertEqual(store.context_window_for("p1:good"), 8192)
        # The corrupt window degrades to unknown rather than reaching the gate
        # as a string and raising TypeError there.
        self.assertEqual(self.context._candidate_context_window("p1:bad"), 0)

    def test_resolve_never_raises_for_an_odd_reference(self) -> None:
        """Defect 8b: a blank ref used to 500 the read API."""
        for ref in ("", "   ", ":", "ollama:", "a:b:c", "x" * 400):
            record, registered = self.context.model_registry_store.resolve(ref)
            self.assertFalse(registered)
            self.assertTrue(record.model_ref)

    def test_known_provider_prefixes_are_not_misreported(self) -> None:
        """Defect 8a: every ref used to be claimed as openai_compatible."""
        store = self.context.model_registry_store
        self.assertEqual(store.resolve("llama_cpp:m")[0].identity.provider_kind, "llama_cpp")
        self.assertEqual(store.resolve("anthropic:c")[0].identity.provider_kind, "anthropic")
        self.assertEqual(store.resolve("google:g")[0].identity.provider_kind, "google")
        self.assertIs(store.resolve("llama_cpp:m")[0].identity.locality, ModelLocality.LOCAL)
        self.assertIs(store.resolve("anthropic:c")[0].identity.locality, ModelLocality.CLOUD)

    def test_an_empty_provenance_is_rejected_not_defaulted(self) -> None:
        """Defect 6a: an explicit empty source silently became 'declared'."""
        with self.assertRaisesRegex(ModelRegistryError, "capability_source"):
            build_record(
                model_ref="p1:x",
                provider_kind="openai",
                model_id="x",
                capability_source="  ",
            )

    def test_the_declare_route_cannot_claim_an_adapters_provenance(self) -> None:
        """P12: the caller no longer chooses its own provenance."""
        stored = self.context.declare_model_registry_record(
            {
                "model_ref": "p1:x",
                "provider_kind": "openai",
                "model_id": "x",
                "record_source": "measured",
            }
        )
        self.assertEqual(stored["record"]["record_source"], "user_declaration")

class SecondReviewRegressionTests(unittest.TestCase):
    """Regressions for the security-audit and validation findings."""

    def setUp(self) -> None:
        from amca.service import ServiceContext

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.context = ServiceContext(
            root=self.root,
            database=self.root / "amca.db",
            benchmarks=self.root / "benchmarks.json",
        )

    def tearDown(self) -> None:
        self.context.close()
        self.temporary.cleanup()

    def _publish(self, provider_kind: str, model: str) -> str:
        created = self.context.create_hub_provider(
            {
                "provider_kind": provider_kind,
                "name": f"{provider_kind} provider",
                "endpoint": "https://x.example/v1",
                "secret": "test-key",
                "model_ids": [model],
            }
        )
        provider_id = created["provider"]["id"]
        self.context.model_hub_store.set_model_visibility(provider_id, model, True)
        return provider_id

    def test_a_recorded_negative_fact_binds_over_the_adapter_declaration(self) -> None:
        """A record saying 'no vision' must beat the adapter's blanket 'vision'."""
        provider_id = self._publish("openai", "no-vision-model")
        ref = f"{provider_id}:no-vision-model"
        self.assertIn("vision", self.context._candidate_capabilities(ref, "openai"))
        self.context.declare_model_registry_record(
            {
                "model_ref": ref,
                "provider_kind": "openai",
                "provider_id": provider_id,
                "model_id": "no-vision-model",
                "capabilities": {"vision": False},
                "capability_source": "measured",
            }
        )
        self.assertNotIn(
            "vision",
            self.context._candidate_capabilities(ref, "openai"),
            "a recorded negative must not be overridden by the adapter default",
        )

    def test_a_ref_that_names_a_different_model_is_rejected(self) -> None:
        """Security: a record must not be keyed to one model while claiming another."""
        with self.assertRaisesRegex(ModelRegistryError, "names a different model"):
            build_record(
                model_ref="ollama:qwen3:1.7b",
                provider_kind="openai",
                model_id="gpt-4o",
            )
        self.assertIsNone(self.context.model_registry_store.get("ollama:qwen3:1.7b"))

    def test_an_endpoint_class_never_carries_a_query_string(self) -> None:
        """Security: a credential in a URL must not be persisted."""
        from amca.service import _endpoint_class

        self.assertEqual(_endpoint_class("https://api.example.com?key=sk-live"), "api.example.com")
        self.assertEqual(_endpoint_class("api.example.com?key=sk-live"), "api.example.com")
        self.assertEqual(_endpoint_class("https://api.example.com:8443/v1"), "api.example.com:8443")

    def test_migration_skips_an_unusable_row_and_adopts_the_rest(self) -> None:
        """Security: one bad Model Hub row must not empty the whole registry."""
        # A model id at the Model Hub length cap produces a ref the registry
        # rejects, which used to abort the entire migration.
        long_id = "m" * 200
        self.context.model_hub_store.create_provider(
            {
                "provider_kind": "openai",
                "name": "Bad",
                "endpoint": "",
                "model_ids": [long_id],
            }
        )
        good_id = self._publish("openai", "good-model")
        outcome = self.context.adopt_model_hub_into_registry()
        self.assertIn(f"{good_id}:good-model", outcome["adopted"])
        self.assertTrue(outcome["skipped"], "the unusable row must be reported")
        self.assertIn("model_ref", outcome["skipped"][0])

    def test_startup_records_the_migration_outcome(self) -> None:
        """A swallowed migration failure left no trace at all."""
        from amca.service import ServiceContext

        second = ServiceContext(
            root=self.root,
            database=self.context.database,
            benchmarks=self.root / "benchmarks.json",
        )
        try:
            outcome = second.model_registry_migration
            self.assertIn("adopted", outcome)
            self.assertIn("skipped", outcome)
        finally:
            second.close()

    def test_a_busy_database_raises_the_error_type_the_route_maps(self) -> None:
        """Security: a locked database must surface as sqlite3.Error.

        The declare route maps ``sqlite3.Error`` to a 503; anything else escapes
        the handler and closes the connection with no status at all.
        """
        import sqlite3 as _sqlite3

        store = self.context.model_registry_store
        holder = _sqlite3.connect(self.context.database, isolation_level=None)
        real_connect = _sqlite3.connect
        try:
            holder.execute("BEGIN IMMEDIATE")
            # Force a short busy timeout so the test does not wait 5 seconds.
            _sqlite3.connect = lambda *a, **k: real_connect(*a, **{**k, "timeout": 0.05})
            with self.assertRaises(_sqlite3.Error):
                store.upsert(
                    build_record(
                        model_ref="p1:busy",
                        provider_kind="openai",
                        model_id="busy",
                    )
                )
        finally:
            _sqlite3.connect = real_connect
            holder.execute("ROLLBACK")
            holder.close()


if __name__ == "__main__":
    unittest.main()
