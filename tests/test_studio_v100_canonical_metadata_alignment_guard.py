"""
AMCA Studio v1.0.0 Canonical Metadata Alignment Guard
Tests that all active release metadata files agree on the canonical version.
Distinguishes active release metadata from historical documentation.
Runs as part of the packaging/release regression suite.
"""

import json
import re
import unittest
from pathlib import Path


#: Canonical active version, read from the single source of truth at import
#: time so the guard tracks every future version instead of a frozen literal.
_SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_VERSION = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    (_SOFTWARE_ROOT / "src" / "amca" / "__init__.py").read_text(encoding="utf-8"),
).group(1)


class CanonicalVersionAlignmentGuardTests(unittest.TestCase):
    """Guard against stale active version declarations across all packaging metadata."""

    def setUp(self) -> None:
        self.software_root = Path(__file__).resolve().parent.parent
        self.desktop_root = self.software_root / "desktop-ui"

    # ------------------------------------------------------------------
    # Active release metadata files (must all agree on CANONICAL_VERSION)
    # ------------------------------------------------------------------

    def test_pyproject_toml_version(self) -> None:
        """pyproject.toml must declare the canonical active version."""
        path = self.software_root / "pyproject.toml"
        self.assertTrue(path.exists(), "pyproject.toml must exist")
        content = path.read_text(encoding="utf-8")
        self.assertIn(
            f'version = "{CANONICAL_VERSION}"',
            content,
            f"pyproject.toml must declare version {CANONICAL_VERSION}",
        )

    def test_package_json_version(self) -> None:
        """desktop-ui/package.json must declare the canonical active version."""
        path = self.desktop_root / "package.json"
        self.assertTrue(path.exists(), "desktop-ui/package.json must exist")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            data["version"],
            CANONICAL_VERSION,
            f"package.json must declare version {CANONICAL_VERSION}",
        )

    def test_tauri_conf_version(self) -> None:
        """tauri.conf.json must declare the canonical active version."""
        path = self.desktop_root / "src-tauri" / "tauri.conf.json"
        self.assertTrue(path.exists(), "tauri.conf.json must exist")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            data["version"],
            CANONICAL_VERSION,
            f"tauri.conf.json must declare version {CANONICAL_VERSION}",
        )

    def test_cargo_toml_version(self) -> None:
        """Cargo.toml must declare the canonical active version."""
        path = self.desktop_root / "src-tauri" / "Cargo.toml"
        self.assertTrue(path.exists(), "Cargo.toml must exist")
        content = path.read_text(encoding="utf-8")
        # Match only the [package] section version, not dependency versions
        lines = content.splitlines()
        in_package = False
        package_version = None
        for line in lines:
            stripped = line.strip()
            if stripped == "[package]":
                in_package = True
            elif stripped.startswith("[") and stripped != "[package]":
                in_package = False
            elif in_package and stripped.startswith("version"):
                package_version = stripped.split("=", 1)[1].strip().strip('"')
                break
        self.assertIsNotNone(package_version, "Cargo.toml must have a [package] version")
        self.assertEqual(
            package_version,
            CANONICAL_VERSION,
            f"Cargo.toml [package] version must be {CANONICAL_VERSION}",
        )

    def test_all_four_metadata_files_agree(self) -> None:
        """All four active metadata files must declare the same canonical version."""
        pyproject = self.software_root / "pyproject.toml"
        package_json = self.desktop_root / "package.json"
        tauri_conf = self.desktop_root / "src-tauri" / "tauri.conf.json"
        cargo_toml = self.desktop_root / "src-tauri" / "Cargo.toml"

        py_v = None
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("version ="):
                py_v = line.strip().split("=", 1)[1].strip().strip('"')
                break

        pkg_v = json.loads(package_json.read_text(encoding="utf-8"))["version"]
        tauri_v = json.loads(tauri_conf.read_text(encoding="utf-8"))["version"]

        lines = cargo_toml.read_text(encoding="utf-8").splitlines()
        in_package = False
        cargo_v = None
        for line in lines:
            s = line.strip()
            if s == "[package]":
                in_package = True
            elif s.startswith("[") and s != "[package]":
                in_package = False
            elif in_package and s.startswith("version"):
                cargo_v = s.split("=", 1)[1].strip().strip('"')
                break

        versions = {"pyproject.toml": py_v, "package.json": pkg_v,
                    "tauri.conf.json": tauri_v, "Cargo.toml": cargo_v}
        self.assertEqual(
            len(set(versions.values())),
            1,
            f"All active metadata files must agree on version. Got: {versions}",
        )
        self.assertEqual(
            list(versions.values())[0],
            CANONICAL_VERSION,
            f"All files must declare canonical version {CANONICAL_VERSION}. Got: {versions}",
        )

    # ------------------------------------------------------------------
    # Regression guard: no accidental reversion to stale versions
    # ------------------------------------------------------------------

    def test_no_stale_v07_in_active_metadata(self) -> None:
        """Active packaging metadata must not contain stale v0.7.x declarations."""
        stale = "0.7.0"
        for path in [
            self.software_root / "pyproject.toml",
            self.desktop_root / "package.json",
            self.desktop_root / "src-tauri" / "tauri.conf.json",
        ]:
            content = path.read_text(encoding="utf-8")
            # Only flag if stale version appears as the active declared version
            self.assertNotIn(
                f'"{stale}"',
                content if path.suffix == ".json" else content.split("[tool.")[0],
                f"{path.name} must not declare stale version {stale}",
            )

    def test_no_stale_v06_in_active_metadata(self) -> None:
        """Active packaging metadata must not contain stale v0.6.x declarations."""
        for stale in ("0.6.0", "0.6.1", "0.6.2"):
            py_content = (self.software_root / "pyproject.toml").read_text(encoding="utf-8")
            pkg_data = json.loads((self.desktop_root / "package.json").read_text(encoding="utf-8"))
            tauri_data = json.loads(
                (self.desktop_root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
            )
            self.assertNotIn(
                f'version = "{stale}"',
                py_content.split("[tool.")[0],
                f"pyproject.toml must not declare stale version {stale}",
            )
            self.assertNotEqual(
                pkg_data.get("version"), stale,
                f"package.json must not declare stale version {stale}",
            )
            self.assertNotEqual(
                tauri_data.get("version"), stale,
                f"tauri.conf.json must not declare stale version {stale}",
            )

    def test_historical_docs_preserved(self) -> None:
        """Historical v0.7.0 release notes must still exist (not deleted)."""
        # Historical documents legitimately reference older versions and must be preserved
        release_notes = (
            self.software_root / "docs" / "releases" / "AMCA-Studio-v0.7.0-Release-Notes.md"
        )
        self.assertTrue(
            release_notes.exists(),
            "Historical v0.7.0 release notes must be preserved (not deleted)",
        )

    def test_release_manifest_v090_present(self) -> None:
        """dist/release_manifest_v0.9.0.json must exist and declare v0.9.0."""
        manifest = self.software_root / "dist" / "release_manifest_v0.9.0.json"
        self.assertTrue(manifest.exists(), "v0.9.0 release manifest must exist")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        # Historical manifest: the declared version must match the version
        # encoded in the manifest filename (0.9.0), not the live canonical
        # version of the tree.
        filename_version = re.search(r"release_manifest_v([0-9.]+)\.json", manifest.name).group(1)
        self.assertEqual(
            data.get("version"),
            filename_version,
            "release_manifest_v0.9.0.json must declare version 0.9.0",
        )


if __name__ == "__main__":
    unittest.main()
