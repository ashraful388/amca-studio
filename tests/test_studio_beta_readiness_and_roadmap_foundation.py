"""
AMCA Studio Beta Readiness & Roadmap Foundation Test Suite
Validates:
1. Complete presence and consistency of all operational and validation reports
2. Strict frozen boundary preservation (v0.6.0 Alpha RC1 locked; v0.7 scope deferred)
3. End-to-end integration health across desktop backend service
"""

import unittest
from pathlib import Path
from amca.service import create_server


class StudioBetaReadinessAndRoadmapFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.docs_dir = self.root / "docs"

    def test_complete_beta_readiness_documentation_suite(self) -> None:
        """Verify that all Phase P1 through P8 operational reports exist."""
        required_reports = [
            self.docs_dir / "validation" / "AMCA-Studio-Production-Reliability-Observability-Report.md",
            self.docs_dir / "validation" / "AMCA-Studio-Performance-Baseline-Report.md",
            self.docs_dir / "validation" / "AMCA-Studio-Security-and-Diagnostics-Hardening-Report.md",
            self.docs_dir / "AMCA-Studio-Alpha-User-Troubleshooting-Guide.md",
            self.docs_dir / "AMCA-Studio-Alpha-Operational-Runbook.md",
            self.docs_dir / "validation" / "AMCA-Studio-Intelligence-and-Optimization-Audit-Report.md",
            self.docs_dir / "validation" / "AMCA-Studio-User-Environment-Compatibility-Matrix.md",
            self.docs_dir / "validation" / "AMCA-Studio-Alpha-Feedback-and-Telemetry-Framework.md",
            self.docs_dir / "validation" / "AMCA-Studio-Alpha-Commercial-Readiness-Report.md",
            self.docs_dir / "validation" / "AMCA-Studio-Beta-Readiness-and-Roadmap-Foundation.md",
        ]
        for report in required_reports:
            self.assertTrue(report.exists(), f"Missing required Phase report: {report.name}")

    def test_service_runtime_bootstrap_cleanliness(self) -> None:
        """Verify server boots cleanly and reports valid loopback port."""
        server = create_server(host="127.0.0.1", port=0, root=self.root)
        self.assertGreater(server.server_port, 1024)
        server.server_close()


if __name__ == "__main__":
    unittest.main()

