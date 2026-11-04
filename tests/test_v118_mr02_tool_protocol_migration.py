"""AMCA-MR-02: registry authority for tool capability, and the marked fallback.

The manager's required semantics:

    registry says tools = true   -> enabled
    registry says tools = false  -> blocked
    registry unknown             -> blocked
    legacy fallback used         -> logged

plus the required cases: capability match, missing capability, conflicting
capability.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from amca.model_registry import (
    ModelRegistryStore,
    ToolCapabilityResolution,
    build_record,
)
from amca.models.tool_protocol import (
    LEGACY_FALLBACK_MARKER,
    effective_tool_support,
    install_registry_tool_resolver,
    known_model_tool_support,
    legacy_fallback_count,
    legacy_fallback_events,
    release_registry_tool_resolver,
    reset_legacy_fallback_events,
)


class _ResolverBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "amca.db"
        self.store = ModelRegistryStore(self.database)
        reset_legacy_fallback_events()
        install_registry_tool_resolver(self._resolve)

    def tearDown(self) -> None:
        install_registry_tool_resolver(None)
        reset_legacy_fallback_events()
        self.temporary.cleanup()

    def _resolve(self, model_name: str):
        resolution = self.store.resolve_tool_capability(model_name)
        return resolution.to_dict() if resolution is not None else None

    def _record(self, ref: str, model_id: str, tools: bool | None) -> None:
        self.store.upsert(
            build_record(
                model_ref=ref,
                provider_kind="openai",
                model_id=model_id,
                capabilities={"tools": tools},
            )
        )


class RegistryAuthorityTests(_ResolverBase):
    def test_registry_true_enables(self) -> None:
        self._record("p1:m", "m", True)
        self.assertTrue(effective_tool_support(True, "m"))

    def test_registry_false_blocks(self) -> None:
        self._record("p1:m", "m", False)
        self.assertFalse(effective_tool_support(True, "m"))

    def test_registry_unknown_blocks(self) -> None:
        """A record exists but does not state the fact: block, never guess."""
        self._record("p1:m", "m", None)
        self.assertFalse(effective_tool_support(True, "m"))

    def test_a_recorded_false_beats_the_legacy_table(self) -> None:
        """The registry is the authority even when the old table said yes."""
        # The legacy table is a prefix table; pick a name it would accept.
        self.assertIs(known_model_tool_support("gpt-4o"), True)
        self._record("p1:gpt-4o", "gpt-4o", False)
        self.assertFalse(
            effective_tool_support(True, "gpt-4o"),
            "a recorded false must override the legacy name table",
        )


class ConflictingCapabilityTests(_ResolverBase):
    def test_two_records_that_disagree_block(self) -> None:
        """The required 'conflicting capability' case."""
        self._record("p1:m", "m", True)
        self._record("p2:m", "m", False)
        resolution = self.store.resolve_tool_capability("m")
        self.assertEqual(resolution.source, "registry_conflict")
        self.assertEqual(len(resolution.conflicting_refs), 2)
        self.assertFalse(
            effective_tool_support(True, "m"),
            "a conflicting capability must block rather than pick a winner",
        )

    def test_records_that_agree_do_not_conflict(self) -> None:
        self._record("p1:m", "m", True)
        self._record("p2:m", "m", True)
        self.assertEqual(self.store.resolve_tool_capability("m").source, "registry")
        self.assertTrue(effective_tool_support(True, "m"))

    def test_an_unknown_record_beside_a_stated_one_is_a_conflict(self) -> None:
        """A grant must not fill in another provider's unknown model.

        Two providers can serve the same model id.  If one record states a fact
        and the other is unknown, the unknown one must veto: otherwise a blanket
        grant from a provider whose catalog is attacker-controlled enables a
        different provider's model that the registry does not know.
        """
        self._record("p1:m", "m", True)
        self._record("p2:m", "m", None)
        resolution = self.store.resolve_tool_capability("m")
        self.assertEqual(resolution.source, "registry_conflict")
        self.assertFalse(effective_tool_support(True, "m"))

    def test_all_records_unknown_stays_unknown_and_blocks(self) -> None:
        self._record("p1:m", "m", None)
        self._record("p2:m", "m", None)
        resolution = self.store.resolve_tool_capability("m")
        self.assertEqual(resolution.source, "registry")
        self.assertIsNone(resolution.value)
        self.assertFalse(effective_tool_support(True, "m"))

    def test_an_unreadable_registry_blocks_instead_of_falling_back(self) -> None:
        """'Cannot read' is not 'no record' - a denial must not be bypassed."""
        self._record("p1:gpt-4o", "gpt-4o", False)
        # Damage the table so reads fail while it still exists.
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("ALTER TABLE model_registry RENAME COLUMN model_id TO mid")
            connection.commit()
        finally:
            connection.close()
        resolution = self.store.resolve_tool_capability("gpt-4o")
        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.source, "registry_unavailable")
        self.assertFalse(
            effective_tool_support(True, "gpt-4o"),
            "an unreadable registry must block, never fall back to the legacy table",
        )

    def test_a_dropped_registry_table_blocks_instead_of_granting(self) -> None:
        """A missing registry is 'cannot answer', not 'no record'."""
        self._record("p1:gpt-4o", "gpt-4o", False)
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("DROP TABLE model_registry")
            connection.commit()
        finally:
            connection.close()
        resolution = self.store.resolve_tool_capability("gpt-4o")
        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.source, "registry_unavailable")
        self.assertFalse(
            effective_tool_support(True, "gpt-4o"),
            "a dropped registry must block, never fall back to the legacy table",
        )

    def test_an_empty_registry_still_uses_the_marked_fallback(self) -> None:
        """A never-written registry is the intended fallback case."""
        self.store.ensure_schema()
        self.assertIsNone(self.store.resolve_tool_capability("qwen3:1.7b"))
        self.assertTrue(effective_tool_support(True, "qwen3:1.7b"))
        self.assertEqual(legacy_fallback_count(), 1)

    def test_a_doubly_prefixed_id_cannot_bypass_a_denial(self) -> None:
        self._record("p1:gemini-2.0-flash", "gemini-2.0-flash", False)
        for form in (
            "gemini-2.0-flash",
            "models/gemini-2.0-flash",
            "models/models/gemini-2.0-flash",
            "MODELS/Models/Gemini-2.0-Flash",
        ):
            resolution = self.store.resolve_tool_capability(form)
            self.assertIsNotNone(resolution, form)
            self.assertIs(resolution.value, False, form)
            self.assertFalse(effective_tool_support(True, form), form)

    def test_a_non_mapping_resolution_blocks_instead_of_raising(self) -> None:
        for bad in ("yes", ["registry"], 42, {}):
            install_registry_tool_resolver(None)
            install_registry_tool_resolver(lambda _m, _bad=bad: _bad)
            self.assertFalse(
                effective_tool_support(True, "m"), f"resolver returned {bad!r}"
            )

    def test_a_non_boolean_value_blocks(self) -> None:
        for bad in (0, "", "false", [], 1):
            install_registry_tool_resolver(None)
            install_registry_tool_resolver(
                lambda _m, _bad=bad: {"value": _bad, "source": "registry"}
            )
            self.assertFalse(
                effective_tool_support(True, "m"), f"registry said {bad!r}"
            )

    def test_a_non_string_model_name_does_not_raise(self) -> None:
        install_registry_tool_resolver(None)
        for bad in (None, 5, b"gpt-4o", ["gpt-4o"]):
            self.assertFalse(effective_tool_support(True, bad))


class MissingCapabilityTests(_ResolverBase):
    def test_no_record_falls_back_and_logs(self) -> None:
        """The required 'missing capability' case, and the logged fallback."""
        self.assertEqual(legacy_fallback_count(), 0)
        result = effective_tool_support(True, "qwen3:1.7b")
        self.assertTrue(result, "the legacy table still answers for now")
        self.assertEqual(legacy_fallback_count(), 1)
        event = legacy_fallback_events()[-1]
        self.assertEqual(event["model"], "qwen3:1.7b")
        self.assertIs(event["verdict"], True)

    def test_the_fallback_event_carries_the_deprecation_marker(self) -> None:
        effective_tool_support(True, "qwen3:1.7b")
        event = legacy_fallback_events()[-1]
        for key, value in LEGACY_FALLBACK_MARKER.items():
            self.assertEqual(event[key], value, f"marker field {key} missing")

    def test_a_model_unknown_everywhere_still_fails_closed(self) -> None:
        self.assertFalse(effective_tool_support(True, "totally-made-up-model"))
        self.assertEqual(legacy_fallback_count(), 1, "the fallback was consulted")

    def test_the_registry_path_is_not_logged_as_a_fallback(self) -> None:
        self._record("p1:m", "m", True)
        effective_tool_support(True, "m")
        self.assertEqual(legacy_fallback_count(), 0)

    def test_the_runtime_declaration_is_not_logged_as_a_fallback(self) -> None:
        effective_tool_support(True, "m", True, declared_model_capability=True)
        self.assertEqual(legacy_fallback_count(), 0)


class IntersectionTests(_ResolverBase):
    """Adapter and route support still gate the result."""

    def test_adapter_support_still_gates(self) -> None:
        self._record("p1:m", "m", True)
        self.assertFalse(effective_tool_support(False, "m"))

    def test_route_support_still_gates(self) -> None:
        self._record("p1:m", "m", True)
        self.assertFalse(effective_tool_support(True, "m", route_supports_tools=False))

    def test_nothing_overrides_a_registry_denial(self) -> None:
        """'registry says tools=false -> blocked' is final (MR-02 order)."""
        self._record("p1:m", "m", False)
        self.assertFalse(
            effective_tool_support(True, "m", declared_model_capability=True),
            "a registry denial must not be overturned by runtime evidence",
        )

    def test_runtime_evidence_vetoes_a_registry_grant(self) -> None:
        """Specific negative evidence may still veto, because that fails closed."""
        self._record("p1:m", "m", True)
        self.assertFalse(
            effective_tool_support(True, "m", declared_model_capability=False)
        )

    def test_runtime_evidence_decides_when_the_registry_has_no_record(self) -> None:
        self.assertTrue(
            effective_tool_support(True, "unregistered", declared_model_capability=True)
        )
        self.assertFalse(
            effective_tool_support(True, "unregistered", declared_model_capability=False)
        )
        self.assertEqual(legacy_fallback_count(), 0)

    def test_a_curated_veto_blocks_a_registry_grant_and_is_logged(self) -> None:
        """The 17E invariant: a curated 'no tools' model stays blocked."""
        self.assertIs(known_model_tool_support("dolphin-llama3:8b"), False)
        self._record("p1:dolphin-llama3:8b", "dolphin-llama3:8b", True)
        self.assertFalse(
            effective_tool_support(True, "dolphin-llama3:8b"),
            "a blanket registry grant must not re-enable a curated no-tools model",
        )
        self.assertEqual(legacy_fallback_count(), 1)
        self.assertIn("veto", legacy_fallback_events()[-1]["reason"])

    def test_a_registry_lookup_failure_blocks_instead_of_raising(self) -> None:
        """A lookup that raises blocks and never reaches the legacy table."""

        def _broken(_model_name):
            raise RuntimeError("database is locked")

        install_registry_tool_resolver(None)
        install_registry_tool_resolver(_broken)
        self.assertFalse(effective_tool_support(True, "m"))
        self.assertEqual(
            legacy_fallback_count(),
            0,
            "the registry answered 'unavailable', so the fallback was not used",
        )

    def test_two_contexts_do_not_disarm_each_other(self) -> None:
        """A second resolver must not silently replace the first one's denial."""
        self._record("p1:gpt-4o", "gpt-4o", False)
        self.assertFalse(effective_tool_support(True, "gpt-4o"))
        other = lambda _m: None  # noqa: E731 - a second context with no record
        install_registry_tool_resolver(other)
        self.assertFalse(
            effective_tool_support(True, "gpt-4o"),
            "the first resolver's denial must keep binding",
        )
        release_registry_tool_resolver(other)
        self.assertFalse(effective_tool_support(True, "gpt-4o"))
        release_registry_tool_resolver(self._resolve)
        self.assertTrue(effective_tool_support(True, "gpt-4o"), "no authority left")

    def test_an_explicit_registry_argument_is_honoured(self) -> None:
        """A caller that already resolved the fact can pass it directly.

        ``None`` means "no override supplied", so the installed resolver (or,
        absent one, the marked fallback) decides — an explicit *unknown* is
        expressed by the resolver's ``registry`` resolution with a null value,
        which blocks.
        """
        install_registry_tool_resolver(None)
        self.assertTrue(effective_tool_support(True, "m", registry_capability=True))
        self.assertFalse(effective_tool_support(True, "m", registry_capability=False))
        self.assertEqual(legacy_fallback_count(), 0, "no fallback was needed")


class ServiceIntegrationTests(unittest.TestCase):
    """The resolver is installed by the service, so the wiring is real."""

    def setUp(self) -> None:
        from amca.service import ServiceContext

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        reset_legacy_fallback_events()
        self.context = ServiceContext(
            root=self.root,
            database=self.root / "amca.db",
            benchmarks=self.root / "benchmarks.json",
        )

    def tearDown(self) -> None:
        install_registry_tool_resolver(None)
        reset_legacy_fallback_events()
        self.context.close()
        self.temporary.cleanup()

    def test_a_migrated_provider_model_is_authoritative(self) -> None:
        created = self.context.create_hub_provider(
            {
                "provider_kind": "openai",
                "name": "Authority Provider",
                "endpoint": "https://authority.example/v1",
                "secret": "test-key",
                "model_ids": ["gpt-4o"],
            }
        )
        self.context.adopt_model_hub_into_registry()
        provider_id = created["provider"]["id"]
        record = self.context.model_registry_store.get(f"{provider_id}:gpt-4o")
        self.assertIs(record.capabilities.tools, True)
        # The name resolves through the registry, not the legacy table.
        self.assertTrue(effective_tool_support(True, "gpt-4o"))
        self.assertEqual(legacy_fallback_count(), 0)

    def test_a_declared_false_binds_end_to_end(self) -> None:
        created = self.context.create_hub_provider(
            {
                "provider_kind": "openai",
                "name": "No Tools Provider",
                "endpoint": "https://notools.example/v1",
                "secret": "test-key",
                "model_ids": ["gpt-4o"],
            }
        )
        provider_id = created["provider"]["id"]
        self.context.declare_model_registry_record(
            {
                "model_ref": f"{provider_id}:gpt-4o",
                "provider_kind": "openai",
                "provider_id": provider_id,
                "model_id": "gpt-4o",
                "capabilities": {"tools": False},
                "capability_source": "measured",
            }
        )
        self.assertFalse(
            effective_tool_support(True, "gpt-4o"),
            "a declared false must bind through the installed resolver",
        )

    def test_the_legacy_path_still_answers_and_is_logged(self) -> None:
        self.assertTrue(effective_tool_support(True, "qwen3:1.7b"))
        self.assertEqual(legacy_fallback_count(), 1)
        self.assertTrue(legacy_fallback_events()[-1]["deprecated"])


if __name__ == "__main__":
    unittest.main()
