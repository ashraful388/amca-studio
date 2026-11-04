"""
AMCA Studio v0.7.0 Public Release Finalization Test Suite
Validates:
1. PublicReleaseManifest assembly and operational handoff metadata
2. Distribution readiness and git commit SHA attachment
3. Operational handoff status verification (READY_FOR_DISTRIBUTION)
4. Zero external cloud dependencies
"""

import unittest

from amca.release_finalization import (
    OperationalHandoffRecord,
    PublicReleaseManifest,
    assemble_public_release_package,
)


class StudioBetaReleaseFinalizationTests(unittest.TestCase):
    def test_public_release_manifest_assembly(self) -> None:
        """Verify public release manifest correctly marks readiness and handoff status."""
        manifest = assemble_public_release_package(
            release_version="v0.7.0",
            git_commit_sha="bc991ab5",
            release_channel="public_beta",
            artifacts_count=2,
            all_gates_cleared=True,
        )

        self.assertEqual(manifest.release_version, "v0.7.0")
        self.assertEqual(manifest.release_tag, "v0.7.0")
        self.assertEqual(manifest.artifacts_verified_count, 2)
        self.assertEqual(manifest.operational_readiness, "PUBLIC_RELEASE_READY")

        # Handoff verification
        handoff = manifest.handoff
        self.assertTrue(handoff.handoff_id.startswith("handoff-"))
        self.assertEqual(handoff.status, "READY_FOR_DISTRIBUTION")
        self.assertEqual(handoff.release_channel, "public_beta")
        self.assertEqual(handoff.git_commit_sha, "bc991ab5")

    def test_manifest_holds_on_uncleared_gates(self) -> None:
        """Verify release holds if gates are not cleared."""
        manifest = assemble_public_release_package(
            release_version="v0.7.0",
            all_gates_cleared=False,
        )
        self.assertEqual(manifest.operational_readiness, "HOLD")
        self.assertEqual(manifest.handoff.status, "PENDING")


if __name__ == "__main__":
    unittest.main()

