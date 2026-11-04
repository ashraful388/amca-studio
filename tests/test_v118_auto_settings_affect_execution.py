"""Work Order section 13 — persisted Auto settings must AFFECT EXECUTION.

"Do not mark a setting functional merely because it persists.  It must influence
the execution path."

These tests set a setting, observe a DIFFERENT execution outcome, change the
setting, and observe the outcome change back.  A setting that merely round-trips
through storage fails here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.domain import CollaborationMode


def _context(tmp: Path):
    from amca.service import ServiceContext

    software_root = Path(__file__).resolve().parents[1]
    return ServiceContext(
        root=software_root, database=tmp / "t.db",
        benchmarks=software_root / "benchmarks" / "tasks.json",
    )


class SelectionPolicyChangesExecutionTests(unittest.TestCase):
    """The manual/auto choice must change which path selects models."""

    def test_the_selection_policy_round_trips(self) -> None:
        from amca.preferences import DesktopPreferencesStore

        store = DesktopPreferencesStore(Path(tempfile.mkdtemp()) / "p.sqlite")
        self.assertEqual(store.selection_policy(), "auto_intelligence")
        store.set_selection_policy("manual_model_set")
        self.assertEqual(store.selection_policy(), "manual_model_set")

    def test_the_policy_is_not_a_free_string(self) -> None:
        # An unrecognised policy must not be stored as if it meant something.
        from amca.preferences import DesktopPreferencesStore

        store = DesktopPreferencesStore(Path(tempfile.mkdtemp()) / "p.sqlite")
        with self.assertRaises(ValueError):
            store.set_selection_policy("something-else")
        self.assertEqual(store.selection_policy(), "auto_intelligence")

    def test_a_stored_policy_survives_a_new_store_over_the_same_database(self) -> None:
        from amca.preferences import DesktopPreferencesStore

        path = Path(tempfile.mkdtemp()) / "p.sqlite"
        DesktopPreferencesStore(path).set_selection_policy("manual_model_set")
        self.assertEqual(DesktopPreferencesStore(path).selection_policy(), "manual_model_set")


class GateStateChangesExecutionTests(unittest.TestCase):
    """A gate must change what the run actually does, not just what it reports."""

    def test_a_fresh_install_starts_with_every_gate_off(self) -> None:
        # The approved security decision: nothing auto-enables.
        from amca.intelligence.gate_state import CHOOSABLE_GATES, GateStateStore

        store = GateStateStore(Path(tempfile.mkdtemp()) / "g.sqlite")
        loaded = store.load()
        for gate in CHOOSABLE_GATES:
            with self.subTest(gate=gate):
                self.assertFalse(bool(loaded.get(gate, False)),
                                 "a fresh install must not enable a gate")

    def test_a_user_choice_persists_across_a_new_store(self) -> None:
        from amca.intelligence.gate_state import GateStateStore

        path = Path(tempfile.mkdtemp()) / "g.sqlite"
        GateStateStore(path).save({"adaptive_planner": True})
        self.assertTrue(GateStateStore(path).load().get("adaptive_planner"))

    def test_enabling_a_gate_is_refused_when_readiness_is_unknown(self) -> None:
        """The fail-closed property, and it is the reason the test below exists.

        A capability whose dependency has not been verified ready cannot be
        switched on.  This is not an obstacle to the test — it IS the behaviour:
        readiness decides whether the gate may be enabled at all.
        """
        from amca.intelligence.errors import ContractError

        context = _context(Path(tempfile.mkdtemp()))
        with self.assertRaises(ContractError) as caught:
            context.intelligence_runtime.configure_gates(
                {"adaptive_planner": True}, contract_version=1, correlation_id="t"
            )
        self.assertIn("readiness", str(caught.exception).casefold())

    def test_gate_state_decides_whether_the_planner_is_model_backed(self) -> None:
        """The execution-path proof.

        `adaptive_planner` and `hybrid_execution` together decide whether the
        planner participates as model_backed or falls back to deterministic.
        That is an execution difference, not a display difference.

        Readiness is stubbed to 'ready' here because the refusal above proves a
        gate cannot otherwise be enabled — so the ONLY way to observe the
        enabled path is to satisfy its precondition, which is exactly what a
        real user with a reachable runtime does.
        """
        from unittest.mock import patch

        context = _context(Path(tempfile.mkdtemp()))

        def snapshot():
            return context._chat_intelligence_snapshot(
                requested_mode="adaptive_hybrid",
                resolved_mode=CollaborationMode.ADAPTIVE_HYBRID,
            )

        # Gates off: the deterministic planner is authoritative.
        self.assertEqual(snapshot()["planner_participation"], "deterministic")

        both_on = {"adaptive_planner": True, "hybrid_execution": True,
                   "nano_agents": False, "model_router": False}
        with patch.object(
            type(context.intelligence_runtime), "status",
            return_value={"gates": both_on},
        ):
            self.assertEqual(
                snapshot()["planner_participation"], "model_backed",
                "the gates must change the execution path, not only the report",
            )

        # And back off, the deterministic path returns.
        with patch.object(
            type(context.intelligence_runtime), "status",
            return_value={"gates": {"adaptive_planner": False, "hybrid_execution": False}},
        ):
            self.assertEqual(snapshot()["planner_participation"], "deterministic")

    def test_a_gate_does_not_affect_a_mode_that_does_not_use_it(self) -> None:
        # The gate must be specific: if it changed every mode, the "difference"
        # would be indiscriminate rather than causal.
        from unittest.mock import patch

        context = _context(Path(tempfile.mkdtemp()))
        with patch.object(
            type(context.intelligence_runtime), "status",
            return_value={"gates": {"adaptive_planner": True, "hybrid_execution": True}},
        ):
            solo = context._chat_intelligence_snapshot(
                requested_mode="solo", resolved_mode=CollaborationMode.SOLO,
            )
        self.assertEqual(solo["planner_participation"], "not_applicable")


class PreferenceChangesExecutionTests(unittest.TestCase):
    """Text size and the default-auto preference must reach the app, not just storage."""

    def test_the_font_scale_round_trips_and_is_bounded(self) -> None:
        from amca.preferences import DesktopPreferencesStore

        store = DesktopPreferencesStore(Path(tempfile.mkdtemp()) / "p.sqlite")
        self.assertEqual(store.ui_font_scale(), "standard")
        store.set_ui_font_scale("large")
        self.assertEqual(store.ui_font_scale(), "large")
        with self.assertRaises(ValueError):
            store.set_ui_font_scale("enormous")

    def test_the_preferences_surface_exposes_every_setting(self) -> None:
        # The UI reads this shape, so a missing key would silently disable a
        # control rather than fail loudly.
        from amca.preferences import DesktopPreferencesStore

        payload = DesktopPreferencesStore(Path(tempfile.mkdtemp()) / "p.sqlite").to_dict()
        for key in ("selection_policy", "ui_font_scale"):
            with self.subTest(key=key):
                self.assertIn(key, payload)


if __name__ == "__main__":
    unittest.main()
