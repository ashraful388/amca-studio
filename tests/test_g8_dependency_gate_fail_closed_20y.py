"""Work Order 20Y: G8 dependency-gate fail-closed truthfulness tests.

Required deterministic vectors:
1. healthy/readable packaged module inventory -> correct PASS
2. a mandatory module genuinely absent -> FAIL
3. PYZ/archive inventory unreadable -> INDETERMINATE + overall FAIL
4. corrupt/unparseable archive -> INDETERMINATE + overall FAIL
5. inventory mechanism returns no usable evidence -> INDETERMINATE + overall FAIL
6. build environment contains openai but packaged evidence unavailable -> not PASS

Plus: SILENT_DEPENDENCY_PASS_FALLBACK=REMOVED and
UNVERIFIED_MANDATORY_DEPENDENCY_CAN_PASS=NO.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))

from g8_acceptance_harness import evaluate_dependency_gate  # noqa: E402

MANDATORY = ["openai", "anthropic", "pypdf", "docx", "lxml", "pptx", "openpyxl"]


def _healthy_reader(exe_path, build_root):
    inventory = set()
    for mod in MANDATORY:
        inventory.add(mod)
        inventory.add(f"{mod}.submodule")
    return inventory, "pyz:PYZ-00.pyz (test fixture)"


def _absent_openai_reader(exe_path, build_root):
    inventory = set()
    for mod in MANDATORY:
        if mod == "openai":
            continue
        inventory.add(mod)
    return inventory, "pyz:PYZ-00.pyz (test fixture, openai missing)"


def _unreadable_reader(exe_path, build_root):
    return None, "packaged inventory inspection failed: archive EOF marker missing"


def _corrupt_reader(exe_path, build_root):
    return None, "packaged inventory inspection failed: bad PYZ cookie"


def _no_evidence_reader(exe_path, build_root):
    return None, "no PYZ member found in packaged archive"


class DependencyGateFailClosed20YTests(unittest.TestCase):
    def test_01_healthy_inventory_passes(self) -> None:
        """Vector 1: healthy/readable packaged inventory -> correct PASS."""
        res = evaluate_dependency_gate(Path("x.exe"), Path("b"), _healthy_reader)
        self.assertEqual(res["PACKAGED_INVENTORY_STATUS"], "OK")
        for mod in MANDATORY:
            self.assertEqual(res[f"{mod.upper()}_PACKAGED"], "PASS")
        self.assertEqual(res["DEPENDENCY_GATE_RESULT"], "PASS")

    def test_02_missing_mandatory_module_fails(self) -> None:
        """Vector 2: mandatory module genuinely absent -> FAIL."""
        res = evaluate_dependency_gate(Path("x.exe"), Path("b"), _absent_openai_reader)
        self.assertEqual(res["OPENAI_PACKAGED"], "FAIL")
        self.assertEqual(res["PACKAGED_INVENTORY_STATUS"], "OK")
        self.assertEqual(res["DEPENDENCY_GATE_RESULT"], "FAIL")

    def test_03_unreadable_inventory_indeterminate_overall_fail(self) -> None:
        """Vector 3: inventory unreadable -> INDETERMINATE + overall FAIL."""
        res = evaluate_dependency_gate(Path("x.exe"), Path("b"), _unreadable_reader)
        self.assertEqual(res["PACKAGED_INVENTORY_STATUS"], "INDETERMINATE")
        for mod in MANDATORY:
            self.assertEqual(res[f"{mod.upper()}_PACKAGED"], "INDETERMINATE")
            self.assertFalse(res["details"][mod]["passed"])
        self.assertEqual(res["DEPENDENCY_GATE_RESULT"], "FAIL")
        self.assertIn("FAIL", res["DEPENDENCY_GATE_NOTE"])

    def test_04_corrupt_archive_indeterminate_overall_fail(self) -> None:
        """Vector 4: corrupt/unparseable archive -> INDETERMINATE + overall FAIL."""
        res = evaluate_dependency_gate(Path("x.exe"), Path("b"), _corrupt_reader)
        self.assertEqual(res["PACKAGED_INVENTORY_STATUS"], "INDETERMINATE")
        self.assertEqual(res["DEPENDENCY_GATE_RESULT"], "FAIL")
        self.assertNotEqual(res["OPENAI_PACKAGED"], "PASS")

    def test_05_no_usable_evidence_indeterminate_overall_fail(self) -> None:
        """Vector 5: no usable evidence -> INDETERMINATE + overall FAIL."""
        res = evaluate_dependency_gate(Path("x.exe"), Path("b"), _no_evidence_reader)
        self.assertEqual(res["PACKAGED_INVENTORY_STATUS"], "INDETERMINATE")
        self.assertEqual(res["DEPENDENCY_GATE_RESULT"], "FAIL")

    def test_06_build_env_openai_never_substitutes_for_packaged_evidence(self) -> None:
        """Vector 6: build env has openai, packaged evidence unavailable -> not PASS.

        Also proves the gate never consults build-environment imports: the
        module IS importable here (it is imported above/available) while the
        packaged inventory is unavailable, and the result is still not PASS.
        """
        import importlib

        try:
            importlib.import_module("openai")
            build_env_has_openai = True
        except ImportError:
            build_env_has_openai = False

        res = evaluate_dependency_gate(Path("x.exe"), Path("b"), _no_evidence_reader)
        self.assertNotEqual(res["OPENAI_PACKAGED"], "PASS")
        self.assertEqual(res["DEPENDENCY_GATE_RESULT"], "FAIL")
        if build_env_has_openai:
            # even in the strongest form of the vector: SDK importable in the
            # running interpreter, packaged evidence still rules.
            self.assertEqual(res["OPENAI_PACKAGED"], "INDETERMINATE")

    def test_07_silent_pass_fallback_removed(self) -> None:
        """SILENT_DEPENDENCY_PASS_FALLBACK=REMOVED: no-evidence can never PASS."""
        for reader in (_unreadable_reader, _corrupt_reader, _no_evidence_reader):
            res = evaluate_dependency_gate(Path("x.exe"), Path("b"), reader)
            for mod in MANDATORY:
                self.assertNotEqual(
                    res[f"{mod.upper()}_PACKAGED"], "PASS",
                    f"silent PASS detected for {mod} with reader {reader.__name__}",
                )
        # with a readable inventory, PASS is allowed only for modules that the
        # inventory actually contains (evidence-based, never optimistic).
        res = evaluate_dependency_gate(Path("x.exe"), Path("b"), _absent_openai_reader)
        self.assertNotEqual(res["OPENAI_PACKAGED"], "PASS")
        self.assertEqual(res["ANTHROPIC_PACKAGED"], "PASS")

    def test_08_unverified_mandatory_dependency_can_pass_no(self) -> None:
        """UNVERIFIED_MANDATORY_DEPENDENCY_CAN_PASS=NO: INDETERMINATE blocks gate PASS."""
        res = evaluate_dependency_gate(Path("x.exe"), Path("b"), _no_evidence_reader)
        self.assertEqual(res["DEPENDENCY_GATE_RESULT"], "FAIL")
        self.assertIn("COULD NOT BE VERIFIED", res["DEPENDENCY_GATE_NOTE"].upper())
        self.assertIn("FAIL-CLOSED", res["DEPENDENCY_GATE_NOTE"].upper())


if __name__ == "__main__":
    unittest.main()
