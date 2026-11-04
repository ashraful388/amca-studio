"""
AMCA Studio v0.7.0 Release Package Consistency Test Suite
Validates:
1. Version alignment across pyproject.toml, package.json, tauri.conf.json, Cargo.toml (0.7.0)
2. Presence and validity of diagnose_port_conflict.ps1 diagnostic tool
3. Authoritative v0.7.0 Release Notes structure and known limitation count
"""

import json
from pathlib import Path
import unittest


class StudioReleasePackageConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent.parent
        self.software_root = Path(__file__).resolve().parent.parent

    def test_version_alignment_across_configurations(self) -> None:
        """Verify all build and packaging configurations agree with the canonical live version."""
        import re

        init_path = self.software_root / "src" / "amca" / "__init__.py"
        self.assertTrue(init_path.exists())
        live_version = re.search(
            r'__version__\s*=\s*"([^"]+)"', init_path.read_text(encoding="utf-8")
        ).group(1)

        # 1. pyproject.toml
        pyproject_path = self.software_root / "pyproject.toml"
        self.assertTrue(pyproject_path.exists())
        pyproject_content = pyproject_path.read_text(encoding="utf-8")
        self.assertIn(f'version = "{live_version}"', pyproject_content)

        # 2. package.json
        pkg_path = self.software_root / "desktop-ui" / "package.json"
        self.assertTrue(pkg_path.exists())
        pkg_data = json.loads(pkg_path.read_text(encoding="utf-8"))
        self.assertEqual(pkg_data["version"], live_version)

        # 3. tauri.conf.json
        tauri_path = self.software_root / "desktop-ui" / "src-tauri" / "tauri.conf.json"
        self.assertTrue(tauri_path.exists())
        tauri_data = json.loads(tauri_path.read_text(encoding="utf-8"))
        self.assertEqual(tauri_data["version"], live_version)

        # 4. Cargo.toml
        cargo_path = self.software_root / "desktop-ui" / "src-tauri" / "Cargo.toml"
        self.assertTrue(cargo_path.exists())
        cargo_content = cargo_path.read_text(encoding="utf-8")
        self.assertIn(f'version = "{live_version}"', cargo_content)

    def test_help_diagnostic_script_presence(self) -> None:
        """Verify diagnose_port_conflict.ps1 diagnostic tool is present and tracked."""
        diag_script = self.software_root / "docs" / "validation" / "diagnose_port_conflict.ps1"
        self.assertTrue(diag_script.exists())
        content = diag_script.read_text(encoding="utf-8")
        self.assertIn("AMCA Safe Port 8765 Conflict Diagnostic Script", content)
        self.assertIn("CONFIRMED_AMCA_BACKEND", content)

    def test_release_notes_integrity(self) -> None:
        """Verify official release notes document highlights, privacy, and 8 limitations."""
        rel_notes = self.software_root / "docs" / "releases" / "AMCA-Studio-v0.7.0-Release-Notes.md"
        self.assertTrue(rel_notes.exists())
        content = rel_notes.read_text(encoding="utf-8")
        self.assertIn("AMCA Studio v0.7.0 Public Release Notes", content)
        self.assertIn("Authoritative Known Limitations (8-Record Inventory)", content)
        self.assertIn("Zero Cloud Telemetry", content)


if __name__ == "__main__":
    unittest.main()

