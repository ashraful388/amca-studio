"""Tests for G8 Acceptance Test Harness.

Work Order: ANTIGRAVITY-AMCA-V111-G8-ACCEPTANCE-HARNESS-20N
Validates:
- Parameterization (source_sha, build_root, data_root, evidence_root, expected_version, require_reingest)
- Version gate parameter evaluation
- Storage location gate evaluation
- Evidence generation (JSON and Markdown summary)
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

software_root = Path(__file__).resolve().parents[1]
packaging_dir = str(software_root / "packaging")
if packaging_dir not in sys.path:
    sys.path.insert(0, packaging_dir)

from g8_acceptance_harness import (
    G8Config,
    evaluate_storage_location_gate,
    evaluate_version_gate,
    resolve_source_sha,
    write_evidence_and_summary,
)


class G8AcceptanceHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.software_root = Path(__file__).resolve().parents[1]
        self.repo_root = self.software_root.parent

    def test_resolve_source_sha_explicit(self) -> None:
        """Explicit SHA overrides git inspection."""
        sha = "93fdc66a1a8c57c10feb3400609db2d49aecb935"
        resolved = resolve_source_sha(sha, self.repo_root)
        self.assertEqual(resolved, sha)

    def test_resolve_source_sha_dynamic(self) -> None:
        """Dynamic SHA resolution returns a 40-character hex string."""
        resolved = resolve_source_sha(None, self.repo_root)
        self.assertRegex(resolved, r"^[0-9a-fA-F]{40}$")

    def test_version_gate_parameter_matching(self) -> None:
        """Version gate passes when the parameter matches the live canonical version."""
        import re

        init_text = (self.software_root / "src" / "amca" / "__init__.py").read_text(encoding="utf-8")
        live_version = re.search(r'__version__\s*=\s*"([^"]+)"', init_text).group(1)
        res = evaluate_version_gate(self.repo_root, live_version)
        self.assertEqual(res.get("VERSION_PARAMETER_GATE_IMPLEMENTED"), "YES")
        self.assertEqual(res.get("EXPECTED_VERSION"), live_version)
        # The gate must confirm every manifest agrees with the live version.
        self.assertEqual(res.get("PYPROJECT_VERSION"), live_version)
        self.assertEqual(res.get("UI_PACKAGE_VERSION"), live_version)
        self.assertEqual(res.get("TAURI_VERSION"), live_version)
        self.assertEqual(res.get("VERSION_ALIGNMENT"), "PASS")

    def test_version_gate_parameter_mismatch(self) -> None:
        """Version gate fails if expected version does not match manifests."""
        res = evaluate_version_gate(self.repo_root, "2.9.9-nonexistent")
        self.assertEqual(res.get("VERSION_ALIGNMENT"), "FAIL")

    def test_storage_location_gate_compliant(self) -> None:
        """Storage location gate passes when paths are under D: drive."""
        data_root = Path(r"D:\ChatGPT\AMCA\.agent-tmp	est-data")
        evidence_root = Path(r"D:\ChatGPT\AMCA\.agent-tmp	est-evidence")
        res = evaluate_storage_location_gate(data_root, evidence_root)
        self.assertEqual(res.get("D_DRIVE_STORAGE_GATE_IMPLEMENTED"), "YES")
        self.assertEqual(res.get("AMCA_STATE_ON_C_DRIVE"), "NO")
        self.assertEqual(res.get("AMCA_EVIDENCE_ON_C_DRIVE"), "NO")
        self.assertEqual(res.get("STORAGE_GATE_RESULT"), "PASS")

    def test_evidence_generation_creates_deterministic_files(self) -> None:
        """Harness writes g8_evidence.json, g8_summary.md, and sub-gate JSONs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ev_root = Path(tmpdir)
            sample_summary = {
                "g8_result": "PASS",
                "source_sha": "93fdc66a1a8c57c10feb3400609db2d49aecb935",
                "expected_version": "1.1.0",
                "require_reingest": False,
                "gates": {
                    "dependency": {"DEPENDENCY_GATE_RESULT": "PASS", "OPENAI_PACKAGED": "PASS"},
                    "version": {"VERSION_ALIGNMENT": "PASS"},
                    "storage": {"STORAGE_GATE_RESULT": "PASS", "AMCA_STATE_ON_C_DRIVE": "NO"},
                    "lifecycle": {
                        "PACKAGED_DESKTOP_LAUNCH": "PASS",
                        "NORMAL_SHUTDOWN": "PASS",
                        "DOCX_EXPORT": "PASS",
                        "PPTX_EXPORT": "PASS",
                        "XLSX_EXPORT": "PASS",
                    },
                    "security": {"SECURITY_GATE_RESULT": "PASS"},
                },
            }
            res = write_evidence_and_summary(ev_root, sample_summary)
            self.assertEqual(res.get("MACHINE_READABLE_EVIDENCE"), "YES")
            self.assertEqual(res.get("HUMAN_READABLE_SUMMARY"), "YES")

            # Check files exist
            json_file = ev_root / "g8_evidence.json"
            md_file = ev_root / "g8_summary.md"
            self.assertTrue(json_file.exists())
            self.assertTrue(md_file.exists())

            loaded = json.loads(json_file.read_text(encoding="utf-8"))
            self.assertEqual(loaded.get("g8_result"), "PASS")
            self.assertEqual(loaded.get("source_sha"), "93fdc66a1a8c57c10feb3400609db2d49aecb935")

            md_text = md_file.read_text(encoding="utf-8")
            self.assertIn("G8_RESULT=PASS", md_text)
            self.assertIn("OPENAI_PACKAGED=PASS", md_text)


if __name__ == "__main__":
    unittest.main()
