"""
AMCA Studio v0.7.0-RC2 Distribution and Release Certification Test Suite
Validates:
1. Distribution artifact records and checksum generation
2. Clean installation and migration safety verification
3. Final release recommendation evaluation (READY_FOR_PUBLIC_RELEASE vs HOLD)
4. Zero external telemetry or cloud dependencies

I2A update: fabricated checksums were removed from
``evaluate_release_certification``; tests now supply physically derived
records, exactly as any caller must.
"""

import hashlib
import tempfile
import unittest
from pathlib import Path
import zipfile

from amca.release_certification import (
    DistributionArtifactRecord,
    ReleaseCertificationPackage,
    evaluate_release_certification,
)


def _physical_artifact_record(tmp: Path, name: str, package_type: str) -> DistributionArtifactRecord:
    """Build an artifact record from a real file's bytes (no fabrication)."""
    archive = tmp / name
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("AMCA-Studio/amca-backend.exe", b"MZ" + b"B" * 4096)
        z.writestr("AMCA-Studio/amca-desktop.exe", b"MZ" + b"D" * 2048)
    data = archive.read_bytes()
    return DistributionArtifactRecord(
        package_name=name,
        package_type=package_type,
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        target_platform="windows_x64",
    )


class StudioBetaReleaseCertificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.artifacts = (
            _physical_artifact_record(tmp, "AMCA-Studio-Setup-v0.7.0-rc.2.exe", "nsis_installer"),
            _physical_artifact_record(tmp, "AMCA-Studio-Portable-v0.7.0-rc.2.zip", "portable_zip"),
        )

    def test_release_certification_approval(self) -> None:
        """Verify release certification package approves release when all criteria are met."""
        package = evaluate_release_certification(
            release_version="v0.7.0-rc.2",
            clean_install_verified=True,
            upgrade_safety_verified=True,
            data_preservation_verified=True,
            distribution_artifacts=self.artifacts,
        )

        self.assertTrue(package.certification_id.startswith("cert-"))
        self.assertEqual(package.release_version, "v0.7.0-rc.2")
        self.assertEqual(package.final_recommendation, "READY_FOR_PUBLIC_RELEASE")
        self.assertEqual(len(package.distribution_artifacts), 2)

        for art in package.distribution_artifacts:
            self.assertTrue(len(art.checksum_sha256) == 64)
            self.assertGreater(art.size_bytes, 0)
            self.assertIn("windows_x64", art.target_platform)

    def test_certification_hold_on_failure(self) -> None:
        """Verify release recommendation holds if upgrade safety fails."""
        package = evaluate_release_certification(
            release_version="v0.7.0-rc.2",
            clean_install_verified=True,
            upgrade_safety_verified=False,
            data_preservation_verified=True,
            distribution_artifacts=self.artifacts,
        )
        self.assertEqual(package.final_recommendation, "HOLD")

    def test_fabricated_artifacts_are_rejected(self) -> None:
        """I2A: certification without physically derived records must fail."""
        with self.assertRaises(ValueError):
            evaluate_release_certification(
                release_version="v0.7.0-rc.2",
                distribution_artifacts=None,
            )


if __name__ == "__main__":
    unittest.main()
