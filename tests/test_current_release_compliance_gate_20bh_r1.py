"""20BH-R1 closure vectors for the current-release compliance gate.

Closes exactly the two unresolved certification gates:
1. CLI/callable entrypoint with deterministic exit semantics:
   0 = PASS, 1 = FAIL, 2 = unexpected error; failures are never swallowed
   into a zero exit.
2. UNCLASSIFIED_REQUIRED_ARTIFACT_FAIL_CLOSED: a required artifact type
   without a registered naming rule cannot silently disappear from the
   release contract — the gate fails closed and reports it explicitly.

Tests only; no product/packaging/canonical changes; no redesign of the gate.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = SOFTWARE_ROOT / "packaging" / "current_release_compliance_gate.py"

_spec = importlib.util.spec_from_file_location(
    "current_release_compliance_gate", GATE_PATH
)
gate = importlib.util.module_from_spec(_spec)
sys.modules["current_release_compliance_gate"] = gate
_spec.loader.exec_module(gate)

# Reuse the 20BH fixture builder.
_spec20 = importlib.util.spec_from_file_location(
    "test_current_release_compliance_gate_20bh",
    SOFTWARE_ROOT / "tests" / "test_current_release_compliance_gate_20bh.py",
)
_20bh = importlib.util.module_from_spec(_spec20)
sys.modules["test_current_release_compliance_gate_20bh"] = _20bh
_spec20.loader.exec_module(_20bh)
build_valid_dist = _20bh.build_valid_dist


def _cli(dist: Path, version: str = "1.1.1") -> "tuple[int, dict]":
    proc = subprocess.run(
        [
            sys.executable, str(GATE_PATH),
            "--version", version, "--dist-dir", str(dist),
        ],
        capture_output=True, text=True,
    )
    return proc.returncode, json.loads(proc.stdout)


class CliDeterministicExitSemantics(unittest.TestCase):
    def test_valid_dist_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            code, report = _cli(dist)
        self.assertEqual(0, code)
        self.assertEqual("PASS", report["verdict"])

    def test_failing_dist_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            (dist / "sbom.cdx.json").unlink()  # BLOCKING finding
            code, report = _cli(dist)
        self.assertEqual(1, code)
        self.assertEqual("FAIL", report["verdict"])

    def test_missing_dist_exits_one_never_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, report = _cli(Path(tmp) / "does-not-exist")
        self.assertEqual(1, code)
        self.assertEqual("FAIL", report["verdict"])
        self.assertTrue(report["blocking_count"] >= 1)

    def test_usage_error_exits_nonzero(self):
        proc = subprocess.run(
            [sys.executable, str(GATE_PATH)],  # missing required args
            capture_output=True, text=True,
        )
        self.assertNotEqual(0, proc.returncode)

    def test_unexpected_gate_error_exits_two_never_zero(self):
        # run_compliance_gate raising must surface as exit 2 (ERROR),
        # never as a swallowed zero.
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            original = gate.run_compliance_gate

            def boom(identity):
                raise RuntimeError("simulated gate crash")

            gate.run_compliance_gate = boom
            try:
                code = gate.main([
                    "--version", "1.1.1", "--dist-dir", str(dist),
                ])
            finally:
                gate.run_compliance_gate = original
        self.assertEqual(2, code)


class UnclassifiedRequiredArtifactFailClosed(unittest.TestCase):
    def test_unregistered_required_type_fails_closed_and_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            extended = gate.REQUIRED_ARTIFACT_TYPES + ("notarization_blob",)
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(
                    version="1.1.1",
                    dist_dir=dist,
                    required_artifact_types=extended,
                )
            )
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn(
            "UNCLASSIFIED_REQUIRED_ARTIFACT_TYPE",
            {f["code"] for f in report["findings"]},
        )
        # The unknown type cannot silently disappear from the contract:
        # it stays explicitly listed as required AND unclassified.
        self.assertIn(
            "notarization_blob", report["required_artifact_types"]
        )
        self.assertEqual(
            ["notarization_blob"], report["unclassified_required_types"]
        )
        self.assertTrue(
            all(f["blocking"] for f in report["findings"]
                if f["code"] == "UNCLASSIFIED_REQUIRED_ARTIFACT_TYPE")
        )

    def test_default_contract_fully_classified(self):
        # No regression: the five registered types classify cleanly.
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual([], report["unclassified_required_types"])
        self.assertEqual("PASS", report["verdict"])


if __name__ == "__main__":
    unittest.main()
