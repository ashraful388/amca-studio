"""
AMCA Studio Release Custody & Handover Validation Suite
Validates:
1. Release custody handover documentation existence and structure
2. Archive register completeness
3. Post-Alpha roadmap boundaries definition
"""

import unittest
from pathlib import Path


class StudioReleaseCustodyHandoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.software_root = Path(__file__).resolve().parents[1]
        self.handover_path = self.software_root / "docs" / "validation" / "AMCA-Studio-Alpha-Release-Custody-Handover-Report.md"

    def test_handover_report_exists_and_complete(self) -> None:
        """Verify that the custody handover report exists and contains all required sections."""
        self.assertTrue(self.handover_path.exists(), "Custody handover report missing")

        with open(self.handover_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("v0.6.0-alpha.1", content)
        self.assertIn("Completed Milestone Verification Record", content)
        self.assertIn("Final Artifact Custody Register", content)
        self.assertIn("Post-Alpha Commercial Boundaries & Deferred v0.7 Scope", content)
        self.assertIn("HANDOVER STATUS", content)


if __name__ == "__main__":
    unittest.main()

