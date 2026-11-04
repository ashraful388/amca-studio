"""Build-gate tests for the desktop UI.

The first v1.2.0 implementation was reported as "typecheck passes" because
`npx tsc --noEmit -p tsconfig.json` was the command used - and the root
tsconfig has `"files": []` with only project references, so that command
typechecked ZERO files and exited 0 no matter what was broken.  Two real
compile errors shipped through it.

These tests make the real gate explicit: `tsc -b` actually builds the
referenced projects, and the cheap static invariants that must hold
regardless of whether a TypeScript toolchain is present are checked here too.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_UI = ROOT / "desktop-ui"
APP_TSX = DESKTOP_UI / "src" / "App.tsx"


class TypeScriptBuildGateTests(unittest.TestCase):
    """The frontend must actually compile."""

    def test_typescript_project_references_build(self) -> None:
        npx = shutil.which("npx")
        if npx is None:
            self.fail(
                "npx is not available, so the TypeScript build gate cannot run. "
                "This must not be skipped: a gate that silently does nothing is "
                "how two compile errors reached a release candidate."
            )
        completed = subprocess.run(
            [npx, "tsc", "-b"],
            cwd=DESKTOP_UI,
            capture_output=True,
            text=True,
            timeout=600,
        )
        self.assertEqual(
            completed.returncode,
            0,
            "tsc -b failed:\n"
            f"{completed.stdout}\n{completed.stderr}",
        )

    def test_the_root_tsconfig_cannot_be_used_as_a_typecheck(self) -> None:
        """Document WHY the build command is `tsc -b` and not `--noEmit -p`.

        If someone "simplifies" the command back to the old form, this test
        fails and explains the trap instead of the check going green on an
        empty file set.
        """
        config = json.loads((DESKTOP_UI / "tsconfig.json").read_text(encoding="utf-8"))
        self.assertEqual(
            config.get("files"),
            [],
            "root tsconfig changed shape; re-check that the build command still "
            "compiles the referenced projects",
        )
        self.assertTrue(
            config.get("references"),
            "root tsconfig must reference the app/node projects, or `tsc -b` "
            "has nothing to build",
        )


class RemovedComposerStateTests(unittest.TestCase):
    """The per-chat folder chooser is gone; no dangling state may remain."""

    def test_no_reference_to_the_removed_composer_folder_state(self) -> None:
        source = APP_TSX.read_text(encoding="utf-8")
        for removed in (
            "workspaceSetupOpen",
            "setWorkspaceSetupOpen",
            "workspaceRootDraft",
            "setWorkspaceRootDraft",
            "chat-workspace-setup",
        ):
            with self.subTest(identifier=removed):
                self.assertNotRegex(
                    source,
                    rf"\b{re.escape(removed)}\b",
                    f"{removed} still exists: the composer folder chooser was "
                    "removed, so this is dead state or a compile error",
                )

    def test_the_composer_select_and_the_settings_panel_share_one_path(self) -> None:
        """Both profile controls must go through the same grant logic.

        They used to differ: the settings panel created the Project-folder
        grant and the composer did not, so the same choice produced a usable
        Project from one screen and a silently unusable one from the other.
        """
        source = APP_TSX.read_text(encoding="utf-8")
        self.assertIn("applyPermissionProfile", source)
        self.assertEqual(
            source.count("await applyPermissionProfile("),
            2,
            "both the composer selector and the Save profile button must call "
            "the shared path",
        )


class VersionConsistencyTests(unittest.TestCase):
    """Every live manifest must state the same version."""

    EXPECTED = "1.1.21"

    def test_every_manifest_agrees_on_the_version(self) -> None:
        manifest = ROOT / "pyproject.toml"
        self.assertIn(
            f'version = "{self.EXPECTED}"',
            manifest.read_text(encoding="utf-8"),
            f"{manifest.name} disagrees",
        )
        self.assertIn(
            f'__version__ = "{self.EXPECTED}"',
            (ROOT / "src" / "amca" / "__init__.py").read_text(encoding="utf-8"),
        )
        package = json.loads((DESKTOP_UI / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["version"], self.EXPECTED, "package.json disagrees")
        lock = json.loads(
            (DESKTOP_UI / "package-lock.json").read_text(encoding="utf-8")
        )
        self.assertEqual(lock["version"], self.EXPECTED, "package-lock top level disagrees")
        self.assertEqual(
            lock["packages"][""]["version"], self.EXPECTED, "package-lock root package disagrees"
        )
        for name in ("tauri.conf.json", "tauri.conf.dev.json"):
            config = json.loads(
                (DESKTOP_UI / "src-tauri" / name).read_text(encoding="utf-8")
            )
            self.assertEqual(config["version"], self.EXPECTED, f"{name} disagrees")
        cargo = (DESKTOP_UI / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
        self.assertRegex(
            cargo, rf'version\s*=\s*"{re.escape(self.EXPECTED)}"',
            "src-tauri/Cargo.toml disagrees",
        )
        cargo_lock = (DESKTOP_UI / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8")
        block = re.search(
            r'\[\[package\]\]\nname = "amca-desktop"\n(.*?)(?=\n\[\[package\]\]|\Z)',
            cargo_lock,
            re.S,
        )
        self.assertIsNotNone(block, "amca-desktop entry missing from Cargo.lock")
        self.assertIn(f'version = "{self.EXPECTED}"', block.group(1), "Cargo.lock disagrees")

    def test_the_source_dev_window_title_matches_the_dev_config(self) -> None:
        """These two drifted apart once, showing a stale version in the title."""
        lib = (DESKTOP_UI / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
        title = re.search(r'SOURCE_WINDOW_TITLE:\s*&str\s*=\s*"([^"]+)"', lib)
        self.assertIsNotNone(title, "SOURCE_WINDOW_TITLE not found")
        config = json.loads(
            (DESKTOP_UI / "src-tauri" / "tauri.conf.dev.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            title.group(1),
            config["app"]["windows"][0]["title"],
            "the Rust window title and tauri.conf.dev.json disagree",
        )


class ConsentCopyTests(unittest.TestCase):
    """The consent text must not promise more than the backend enforces."""

    def setUp(self) -> None:
        self.source = APP_TSX.read_text(encoding="utf-8")

    def test_full_access_copy_states_the_real_limits(self) -> None:
        self.assertIn(
            "Removable and network drives are never included",
            self.source,
            "Full Access only ever granted fixed drives; the consent text must say so",
        )
        self.assertIn(
            "stays OUTSIDE access until you press Refresh drives",
            self.source,
            "a drive attached later is not covered; the user must be told",
        )
        self.assertIn(
            "restoring an existing file still asks you first",
            self.source,
            "restore is approval-gated; the consent text must include it",
        )

    def test_extended_is_not_described_as_reaching_beyond_the_project_folder(self) -> None:
        self.assertNotRegex(
            self.source,
            r"Adds move, recoverable trash, and command inspection(?!\s*,?\s*still inside)",
            "Extended is bounded by the Project folder and must not read as wider",
        )


if __name__ == "__main__":
    unittest.main()
