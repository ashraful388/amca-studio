"""Tests for Work Order 20L: Packaged Dependency Hardening.

Validates:
- OPENAI_HIDDENIMPORT_EXPLICIT=YES
- COLLECT_ALL_OPENAI_USED=NO
- DOCX_DATA_COLLECTION_ADDED=NO
- DOCX_DATA_COLLECTION_PROVEN_NECESSARY=NOT_ADDED
- Mandatory dependency importability: openai, anthropic, pypdf, docx, lxml, pptx, openpyxl
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import unittest
from pathlib import Path


def _resolve_packaging_python() -> Path:
    """Reuse the harness's packaging-interpreter resolution.

    The G8 harness already decides which Python the packaged build comes
    from; mirror that logic so the test evaluates the build interpreter,
    not whichever interpreter happened to launch pytest.
    """
    # Same candidate order as g8_acceptance_harness.resolve_python_exe:
    # repo-local venv first, then common absolute fallback, then the
    # currently running interpreter as the documented last resort.
    repo_root = Path(__file__).resolve().parents[3]
    candidates = [
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "bin" / "python",
        Path(r"D:\ChatGPT\AMCA\software\.venv\Scripts\python.exe"),
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return Path(sys.executable)


class PackagedDependencyHardening20LTests(unittest.TestCase):
    def setUp(self) -> None:
        self.software_root = Path(__file__).resolve().parents[1]
        self.spec_path = self.software_root / "packaging" / "amca-backend.spec"

    def test_spec_hiddenimports_includes_openai_explicitly(self) -> None:
        """OPENAI_HIDDENIMPORT_EXPLICIT=YES, COLLECT_ALL_OPENAI_USED=NO."""
        self.assertTrue(self.spec_path.exists(), "amca-backend.spec must exist")
        content = self.spec_path.read_text(encoding="utf-8")

        # Verify explicit hiddenimport
        self.assertIn('"openai"', content)
        self.assertIn('"anthropic"', content)
        self.assertIn('"pypdf"', content)

        # Verify collect_all was NOT used
        self.assertNotIn('collect_all("openai")', content)
        self.assertNotIn("collect_all('openai')", content)

    def test_spec_docx_data_collection_not_speculative(self) -> None:
        """DOCX_DATA_COLLECTION_ADDED=NO, DOCX_DATA_COLLECTION_PROVEN_NECESSARY=NOT_ADDED."""
        content = self.spec_path.read_text(encoding="utf-8")
        self.assertNotIn('collect_data_files("docx")', content)
        self.assertNotIn("collect_data_files('docx')", content)

    def test_mandatory_runtime_dependencies_importable(self) -> None:
        """Verify mandatory deps are importable in the *packaging* interpreter.

        The packaging stage uses a specific Python (per the G8 harness
        probe order). The test must evaluate that interpreter, not the
        pytest-runner Python, which may be a different environment.
        """
        mandatory = [
            "openai",
            "anthropic",
            "pypdf",
            "docx",
            "lxml",
            "pptx",
            "openpyxl",
        ]
        packaging_py = _resolve_packaging_python()

        proc = subprocess.run(
            [
                str(packaging_py),
                "-c",
                "import importlib.util,sys\n"
                + "mandatory=['openai','anthropic','pypdf','docx','lxml','pptx','openpyxl']\n"
                + "missing=[m for m in mandatory if not importlib.util.find_spec(m)]\n"
                + "print('MISSING=' + ','.join(missing)); sys.exit(len(missing))",
            ],
            capture_output=True,
            text=True,
        )
        # Surface the packaging interpreter + its report on any mismatch so
        # the failure message makes a pytest-run-on-the-wrong-python case
        # immediately diagnosable.
        self.assertEqual(
            proc.returncode,
            0,
            f"Mandatory dependencies missing in packaging interpreter {packaging_py}: {proc.stdout.strip()}",
        )
        self.assertIn("MISSING=", proc.stdout, "missing marker not emitted")


if __name__ == "__main__":
    unittest.main()
