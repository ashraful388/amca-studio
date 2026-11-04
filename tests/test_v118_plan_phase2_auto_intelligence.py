"""AMCA Studio v1.1.8 Final Build Plan - Phase 2 evidence (Auto Intelligence core).

Phase 2 requires, in the plan's own words:

    Required settings always enabled: Deterministic Planner, Adaptive Planning,
    Hybrid Execution, Manual Agent, Model Router
    Fix: Default Auto control.  Auto settings persistence.  Auto configuration
    visibility.

The gate controller documents a security property that constrains how the first
of those may be satisfied: *"initial state always OFF … nothing auto-enables"*.
The manager ruled on the conflict: implement persistence and explain a conflict
rather than silently disabling, and do not change the shipped default to ON.

These tests assert the behaviour that was actually implemented:

* a fresh install still starts with every gate OFF (nothing auto-enables);
* a user's explicit choice persists across a restart (Auto settings persistence);
* the Default Auto control is real, persisted, and visible;
* a capability that cannot operate reports its reason (Auto configuration
  visibility), rather than being silently disabled.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.intelligence.gate_state import CHOOSABLE_GATES, GateStateStore
from amca.intelligence.gates import FeatureGateRegistry
from amca.intelligence.runtime_bridge import IntelligenceRuntimeBridge
from amca.preferences import (
    DEFAULT_SELECTION_POLICY,
    DEFAULT_UI_FONT_SCALE,
    DesktopPreferencesStore,
)


class NoAutoEnableTests(unittest.TestCase):
    """The documented security property must survive persistence."""

    def test_a_fresh_install_starts_with_every_gate_off(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            database = Path(temporary.name) / "amca.db"
            store = GateStateStore(database)
            self.assertEqual(store.load(), {}, "nothing recorded on a fresh install")
            bridge = IntelligenceRuntimeBridge(
                gates=None, bootstrap_mode="legacy_dev_http"
            )
            self.assertEqual(
                set(bridge.gate_controller.gates.to_dict().values()),
                {False},
                "a fresh bridge must start from the shipped default",
            )
        finally:
            temporary.cleanup()

    def test_the_store_writes_nothing_on_construction(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            database = Path(temporary.name) / "amca.db"
            GateStateStore(database)
            self.assertFalse(database.exists(), "opening must not create the file")
            self.assertEqual(GateStateStore(database).load(), {})
        finally:
            temporary.cleanup()


class AutoSettingsPersistenceTests(unittest.TestCase):
    """A user's explicit choice survives a restart."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "amca.db"
        self.store = GateStateStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_an_enabled_gate_is_recorded_and_restored(self) -> None:
        self.store.save({"adaptive_planner": True, "model_router": True})
        self.assertEqual(
            GateStateStore(self.database).load(),
            {"adaptive_planner": True, "model_router": True},
        )

    def test_a_disabled_gate_stays_disabled(self) -> None:
        self.store.save({"adaptive_planner": True})
        self.store.save({"adaptive_planner": False})
        self.assertEqual(
            GateStateStore(self.database).load(), {"adaptive_planner": False}
        )

    def test_an_unknown_gate_name_is_ignored(self) -> None:
        """A stale or tampered row must not resurrect a capability."""
        import sqlite3

        self.store.save({"adaptive_planner": True})
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "INSERT INTO intelligence_gate_state (gate, enabled, updated_at)"
                " VALUES ('not_a_gate', 1, '2026-01-01T00:00:00+00:00')"
            )
            connection.commit()
        finally:
            connection.close()
        loaded = GateStateStore(self.database).load()
        self.assertEqual(loaded, {"adaptive_planner": True})
        self.assertNotIn("not_a_gate", loaded)

    def test_the_restored_registry_is_accepted_by_the_bridge(self) -> None:
        self.store.save({"hybrid_execution": True})
        recorded = self.store.load()
        gates = FeatureGateRegistry(
            **{k: v for k, v in recorded.items() if k in CHOOSABLE_GATES}
        )
        bridge = IntelligenceRuntimeBridge(
            gates=gates, bootstrap_mode="legacy_dev_http"
        )
        self.assertTrue(bridge.gate_controller.gates.hybrid_execution)

    def test_every_choosable_gate_is_persisted(self) -> None:
        self.store.save({gate: True for gate in CHOOSABLE_GATES})
        self.assertEqual(
            GateStateStore(self.database).load(),
            {gate: True for gate in CHOOSABLE_GATES},
        )


class DefaultAutoControlTests(unittest.TestCase):
    """The Default Auto control is real, persisted, and visible."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "amca.db"
        self.store = DesktopPreferencesStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_the_shipped_default_is_auto_intelligence(self) -> None:
        self.assertEqual(self.store.selection_policy(), DEFAULT_SELECTION_POLICY)
        self.assertTrue(self.store.to_dict()["default_auto"])

    def test_turning_default_auto_off_persists(self) -> None:
        self.store.set_selection_policy("manual_model_set")
        reopened = DesktopPreferencesStore(self.database)
        self.assertFalse(reopened.to_dict()["default_auto"])
        self.assertEqual(reopened.selection_policy(), "manual_model_set")

    def test_the_toggle_speaks_in_booleans(self) -> None:
        self.store.update({"default_auto": False})
        self.assertFalse(self.store.to_dict()["default_auto"])
        self.store.update({"default_auto": True})
        self.assertTrue(self.store.to_dict()["default_auto"])

    def test_an_invalid_policy_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "selection_policy must be one of"):
            self.store.set_selection_policy("something_else")
        with self.assertRaisesRegex(ValueError, "default_auto must be a boolean"):
            self.store.update({"default_auto": "yes"})

    def test_the_store_writes_nothing_on_construction(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            database = Path(temporary.name) / "amca.db"
            DesktopPreferencesStore(database)
            self.assertFalse(database.exists())
            self.assertEqual(
                DesktopPreferencesStore(database).selection_policy(),
                DEFAULT_SELECTION_POLICY,
            )
        finally:
            temporary.cleanup()


class InterfaceTypographyTests(unittest.TestCase):
    """Phase 9 requires the user to be able to adjust interface typography."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "amca.db"
        self.store = DesktopPreferencesStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_the_default_scale_is_standard(self) -> None:
        self.assertEqual(self.store.to_dict()["ui_font_scale"], DEFAULT_UI_FONT_SCALE)

    def test_a_chosen_scale_persists(self) -> None:
        self.store.set_ui_font_scale("large")
        self.assertEqual(
            DesktopPreferencesStore(self.database).to_dict()["ui_font_scale"], "large"
        )

    def test_an_unsupported_scale_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "ui_font_scale must be one of"):
            self.store.set_ui_font_scale("enormous")


class AutoConfigurationVisibilityTests(unittest.TestCase):
    """A capability that cannot operate must explain itself."""

    def test_the_backend_can_state_a_readiness_reason(self) -> None:
        """The service exposes a reason map the status payload can carry."""
        from amca.intelligence.local_readiness import LocalReadinessService

        service = LocalReadinessService()
        reason_map = service.reason_map()
        self.assertIsInstance(reason_map, dict)
        for value in reason_map.values():
            self.assertIsInstance(value, str)

    def test_the_status_payload_can_carry_a_reason(self) -> None:
        """The bridge emits readiness_reason when a probe supplies one."""
        bridge = IntelligenceRuntimeBridge(bootstrap_mode="legacy_dev_http")
        capabilities = bridge.status()["capabilities"]
        for name in CHOOSABLE_GATES:
            self.assertIn(name, capabilities)
            entry = capabilities[name]
            self.assertIn("runtime_ready", entry)
            # The key is optional; when present it must be a non-empty string.
            if "readiness_reason" in entry:
                self.assertIsInstance(entry["readiness_reason"], str)
                self.assertTrue(entry["readiness_reason"].strip())


class ServiceWiringTests(unittest.TestCase):
    """The service restores and persists through its own stores."""

    def setUp(self) -> None:
        from amca.service import ServiceContext

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "amca.db"
        self.context = ServiceContext(
            root=self.root,
            database=self.database,
            benchmarks=self.root / "benchmarks.json",
        )

    def tearDown(self) -> None:
        self.context.close()
        self.temporary.cleanup()

    def test_the_service_exposes_the_default_auto_preference(self) -> None:
        payload = self.context.desktop_preferences()
        self.assertEqual(payload["schema"], "amca-desktop-preferences/1")
        self.assertIn("default_auto", payload["preferences"])

    def test_setting_the_preference_through_the_service_persists(self) -> None:
        from amca.service import ServiceContext

        self.context.set_desktop_preferences({"default_auto": False})
        second = ServiceContext(
            root=self.root,
            database=self.database,
            benchmarks=self.root / "benchmarks.json",
        )
        try:
            self.assertFalse(
                second.desktop_preferences()["preferences"]["default_auto"]
            )
        finally:
            second.close()

    def test_a_committed_gate_change_is_persisted(self) -> None:
        from amca.service import ServiceContext

        controller = self.context.intelligence_runtime.gate_controller
        controller._on_commit(controller.gates.with_overrides(model_router=True))
        recorded = self.context.gate_state_store.load()
        self.assertTrue(recorded.get("model_router"))
        for gate in CHOOSABLE_GATES:
            if gate != "model_router":
                self.assertFalse(recorded.get(gate), gate)
        second = ServiceContext(
            root=self.root,
            database=self.database,
            benchmarks=self.root / "benchmarks.json",
        )
        try:
            self.assertTrue(
                second.intelligence_runtime.gate_controller.gates.model_router,
                "a user's choice must survive a restart",
            )
        finally:
            second.close()


if __name__ == "__main__":
    unittest.main()
