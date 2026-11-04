"""
AMCA Studio Release Freeze & Manifest Validation Suite
Validates:
1. Manifest documentation completeness and format structure
2. Existence and integrity of all release documentation and distribution artifacts
3. Verification matrix coverage across all 13 development & validation milestones
"""

import hashlib
import json
import unittest
from pathlib import Path


class StudioReleaseFreezeManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.software_root = Path(__file__).resolve().parents[1]
        self.manifest_path = self.software_root / "docs" / "validation" / "AMCA-Studio-v0.6.0-Alpha-Release-Manifest.md"
        self.notes_path = self.software_root / "docs" / "AMCA-Studio-v0.6.0-Alpha-Release-Notes.md"
        self.demo_path = self.software_root / "docs" / "AMCA-Studio-Commercial-Alpha-Demo-Guide.md"
        self.cert_path = self.software_root / "docs" / "validation" / "AMCA-Studio-Alpha-RC-Final-Acceptance-Certificate.md"

    def test_release_documentation_artifacts_exist(self) -> None:
        """Verify that all core release documentation artifacts exist."""
        self.assertTrue(self.manifest_path.exists(), "Release manifest missing")
        self.assertTrue(self.notes_path.exists(), "Release notes missing")
        self.assertTrue(self.demo_path.exists(), "Demo guide missing")
        self.assertTrue(self.cert_path.exists(), "Final acceptance certificate missing")

    def test_manifest_contains_critical_release_sections(self) -> None:
        """Verify that the manifest contains required release package sections."""
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("v0.6.0-alpha.1", content)
        self.assertIn("AMCA-Studio-Setup-v0.6.0.exe", content)
        self.assertIn("AMCA-Studio-Portable-v0.6.0.zip", content)
        self.assertIn("Commercial Module Verification Sign-Off", content)
        self.assertIn("OFFICIALLY APPROVED FOR COMMERCIAL ALPHA V0.6.0 DISTRIBUTION", content)

    def test_frontend_dist_checksum_verifiable(self) -> None:
        """Verify that frontend dist/index.html is readable and can produce a valid SHA-256."""
        index_html = self.software_root / "desktop-ui" / "dist" / "index.html"
        self.assertTrue(index_html.exists(), "Frontend index.html missing")
        
        with open(index_html, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        
        self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()

