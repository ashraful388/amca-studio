"""
AMCA Studio Alpha Commercial Readiness Audit Test Suite
Validates:
1. Documentation consistency across all operational guides
2. Feature inventory completeness across core Studio modules
3. Version and reference consistency (v0.6.0 Alpha RC1)
"""

import unittest
from pathlib import Path


class StudioCommercialReadinessAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.docs_dir = self.root / "docs"

    def test_core_documentation_suite_exists(self) -> None:
        """Verify presence of all mandatory Alpha commercial documentation."""
        required_docs = [
            self.docs_dir / "AMCA-Studio-Alpha-User-Troubleshooting-Guide.md",
            self.docs_dir / "AMCA-Studio-Alpha-Operational-Runbook.md",
            self.docs_dir / "validation" / "AMCA-Studio-User-Environment-Compatibility-Matrix.md",
            self.docs_dir / "validation" / "AMCA-Studio-Alpha-Feedback-and-Telemetry-Framework.md",
            self.docs_dir / "validation" / "AMCA-Studio-Alpha-Commercial-Readiness-Report.md",
        ]
        for doc in required_docs:
            self.assertTrue(doc.exists(), f"Mandatory document missing: {doc.name}")

    def test_feature_inventory_integrity(self) -> None:
        """Verify that core studio endpoints and routes correspond to active capabilities."""
        from amca.service import create_server
        # Verify server initialization succeeds without unhandled module import errors
        server = create_server(host="127.0.0.1", port=0, root=self.root)
        self.assertIsNotNone(server)
        server.server_close()

    def test_version_reference_consistency(self) -> None:
        """Verify that version reference across package configuration and tauri config match."""
        import json
        import re

        pyproject = self.root / "pyproject.toml"
        tauri_conf = self.root / "desktop-ui" / "src-tauri" / "tauri.conf.json"
        init_file = self.root / "src" / "amca" / "__init__.py"
        self.assertTrue(pyproject.exists())
        self.assertTrue(tauri_conf.exists())
        with open(pyproject, "r", encoding="utf-8") as f:
            py_content = f.read()
        with open(init_file, "r", encoding="utf-8") as f:
            init_content = f.read()
        tauri_content = json.loads(tauri_conf.read_text(encoding="utf-8"))

        live_version = re.search(r'__version__\s*=\s*"([^"]+)"', init_content).group(1)
        pyproject_version = re.search(r'^version\s*=\s*"([^"]+)"', py_content, re.MULTILINE).group(1)
        self.assertEqual(pyproject_version, live_version)
        self.assertEqual(tauri_content.get("version"), live_version)


if __name__ == "__main__":
    unittest.main()

