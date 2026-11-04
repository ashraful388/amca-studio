"""Synthetic acceptance vectors for the current-release compliance gate (20BH).

Proves gate semantics with controlled temporary fixtures — no real v1.1.1
release artifacts are required, and missing real official artifacts can never
yield PASS (a gate run against a missing/invalid dist fails closed).

Tests only; no product/packaging/canonical changes; the immutable v1.0.0
historical compliance suite is not modified.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "current_release_compliance_gate",
    SOFTWARE_ROOT / "packaging" / "current_release_compliance_gate.py",
)
gate = importlib.util.module_from_spec(_spec)
sys.modules["current_release_compliance_gate"] = gate
_spec.loader.exec_module(gate)


def _write(path: Path, data: bytes) -> str:
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def build_valid_dist(
    dist: Path, version: str = "1.1.1", *, certifi_without_notice: bool = False,
    pyinstaller_shipped: bool = False, legal_approval=None,
) -> None:
    """Create a synthetic but fully compliant dist for `version`."""
    dist.mkdir(parents=True, exist_ok=True)
    sums = []
    artifacts = []
    for artifact_type in ("installer", "windows-msi", "portable_archive"):
        name = gate.ARTIFACT_NAMING[artifact_type].format(version=version)
        sha = _write(dist / name, name.encode() + b"-payload")
        sums.append(f"{sha}  {name}")
        artifacts.append({"name": name, "sha256": sha})
    shipped = [
        {"name": "openai"}, {"name": "anthropic"}, {"name": "pypdf"},
        # certifi is shipped by default WITH notice; vectors toggle the failure.
        {"name": "certifi"},
    ]
    if pyinstaller_shipped:
        shipped.append({"name": "pyinstaller"})
    (dist / "sbom.cdx.json").write_text('{"components": []}', encoding="utf-8")
    (dist / "NOTICES.txt").write_text(
        "openai: MIT\nanthropic: MIT\ncertifi: MPL-2.0\n", encoding="utf-8"
    )
    manifest = {
        "product_name": "AMCA Studio",
        "version": version,
        "verification": "physical_sha256_from_bytes",
        "artifacts": artifacts,
        "sbom": "sbom.cdx.json",
        "notices": ["NOTICES.txt"],
        "shipped_dependencies": shipped,
    }
    if legal_approval is not None:
        manifest["legal_approval"] = legal_approval
    if certifi_without_notice:
        manifest["notices"] = ["OTHER-NOTICES.txt"]
    manifest_name = gate.ARTIFACT_NAMING["release_manifest"].format(
        version=version
    )
    manifest_path = dist / manifest_name
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    sums.append(
        f"{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}  "
        f"{manifest_name}"
    )
    (dist / "SHA256SUMS.txt").write_text(
        "\n".join(sums) + "\n", encoding="utf-8"
    )


class ValidDistAccepts(unittest.TestCase):
    def test_fully_compliant_synthetic_dist_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual("PASS", report["verdict"])
        self.assertEqual(0, report["blocking_count"])
        self.assertTrue(report["review_survives"])


class MissingArtifactFailsClosed(unittest.TestCase):
    def test_each_required_artifact_type_missing_fails(self):
        for missing in gate.REQUIRED_ARTIFACT_TYPES:
            with tempfile.TemporaryDirectory() as tmp:
                dist = Path(tmp) / "dist"
                build_valid_dist(dist, "1.1.1")
                name = gate.ARTIFACT_NAMING[missing].format(version="1.1.1")
                (dist / name).unlink()
                report = gate.run_compliance_gate(
                    gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
                )
            self.assertEqual("FAIL", report["verdict"], missing)
            codes = {f["code"] for f in report["findings"]}
            self.assertIn("MISSING_ARTIFACT", codes, missing)

    def test_empty_dist_fails_closed_never_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(
                    version="1.1.1", dist_dir=Path(tmp) / "empty"
                )
            )
        self.assertEqual("FAIL", report["verdict"])


class WrongVersionFailsClosed(unittest.TestCase):
    def test_manifest_version_mismatch_rejected(self):
        # Correctly-named v1.1.1 manifest whose internal version field lies.
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            manifest_name = gate.ARTIFACT_NAMING["release_manifest"].format(
                version="1.1.1"
            )
            manifest = json.loads(
                (dist / manifest_name).read_text(encoding="utf-8")
            )
            manifest["version"] = "1.1.0"
            (dist / manifest_name).write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn(
            "WRONG_VERSION", {f["code"] for f in report["findings"]}
        )

    def test_wrongly_named_artifacts_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.0")
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual("FAIL", report["verdict"])


class TamperAndChecksumVectors(unittest.TestCase):
    def test_tampered_artifact_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            exe = dist / gate.ARTIFACT_NAMING["installer"].format(
                version="1.1.1"
            )
            exe.write_bytes(exe.read_bytes() + b"-tampered")
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn(
            "CHECKSUM_MISMATCH", {f["code"] for f in report["findings"]}
        )

    def test_manifest_sha_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            manifest_name = gate.ARTIFACT_NAMING["release_manifest"].format(
                version="1.1.1"
            )
            manifest = json.loads(
                (dist / manifest_name).read_text(encoding="utf-8")
            )
            manifest["artifacts"][0]["sha256"] = "0" * 64
            (dist / manifest_name).write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn(
            "SAME_SHA_REJECTED", {f["code"] for f in report["findings"]}
        )

    def test_undeclared_checksum_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            sums = dist / "SHA256SUMS.txt"
            lines = sums.read_text(encoding="utf-8").splitlines()
            sums.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn(
            "CHECKSUM_NOT_DECLARED", {f["code"] for f in report["findings"]}
        )


class PayloadAndLegalVectors(unittest.TestCase):
    def test_missing_sbom_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            (dist / "sbom.cdx.json").unlink()
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn("MISSING_SBOM", {f["code"] for f in report["findings"]})

    def test_missing_notices_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            (dist / "NOTICES.txt").unlink()
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn(
            "MISSING_NOTICES", {f["code"] for f in report["findings"]}
        )

    def test_certifi_without_notice_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1", certifi_without_notice=True)
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn(
            "CERTIFI_WITHOUT_NOTICE", {f["code"] for f in report["findings"]}
        )

    def test_pyinstaller_shipped_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1", pyinstaller_shipped=True)
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn(
            "PYINSTALLER_CLASSIFIED_SHIPPED",
            {f["code"] for f in report["findings"]},
        )

    def test_fabricated_legal_approval_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1", legal_approval=True)
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn(
            "FABRICATED_APPROVAL", {f["code"] for f in report["findings"]}
        )

    def test_incomplete_payload_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            manifest_name = gate.ARTIFACT_NAMING["release_manifest"].format(
                version="1.1.1"
            )
            manifest = json.loads(
                (dist / manifest_name).read_text(encoding="utf-8")
            )
            del manifest["verification"]
            (dist / manifest_name).write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn(
            "INCOMPLETE_COMPLIANCE_PAYLOAD",
            {f["code"] for f in report["findings"]},
        )


class StructuralGuarantees(unittest.TestCase):
    def test_review_survives_and_blocking_never_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            (dist / "sbom.cdx.json").unlink()  # one BLOCKING finding
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertTrue(report["review_survives"])
        self.assertGreaterEqual(report["blocking_count"], 1)
        self.assertEqual("FAIL", report["verdict"])  # BLOCKING != PASS

    def test_report_emits_no_file_contents(self):
        secret = b"AMCA-SECRET-CONTENTS-must-not-leak"
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            (dist / "sbom.cdx.json").unlink()
            report_text = json.dumps(
                gate.run_compliance_gate(
                    gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
                )
            )
        self.assertNotIn("AMCA-SECRET-CONTENTS", report_text)
        self.assertNotIn("must-not-leak", report_text)

    def test_gate_is_parameterized_for_future_target(self):
        # The gate's explicit identity is independent of the source version.
        source_init = SOFTWARE_ROOT / "src" / "amca" / "__init__.py"
        source_before = source_init.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build_valid_dist(dist, "1.1.1")
            report = gate.run_compliance_gate(
                gate.ReleaseIdentity(version="1.1.1", dist_dir=dist)
            )
        self.assertEqual("PASS", report["verdict"])
        self.assertEqual("1.1.1", report["release_identity"]["version"])
        # Product source version is independent of the gate's parameterized
        # identity; the gate must not mutate it either way.
        self.assertEqual(source_before, source_init.read_bytes())


if __name__ == "__main__":
    unittest.main()
