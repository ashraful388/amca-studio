from __future__ import annotations

import unittest
from pathlib import Path


class LocalToolsUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1] / "desktop-ui" / "src"
        cls.app = (cls.root / "App.tsx").read_text(encoding="utf-8")
        cls.api = (cls.root / "api.ts").read_text(encoding="utf-8")
        cls.types = (cls.root / "types.ts").read_text(encoding="utf-8")

    def test_computer_access_surface_contains_the_three_access_modes(self) -> None:
        # Round 27 (owner order): three ZCode-style modes replace the four
        # profile cards. They map onto the SAME backend profiles, so nothing
        # about enforcement changes; "restricted" is no longer offered.
        self.assertIn("Ask before changes", self.app)
        self.assertIn("Autonomous mode", self.app)
        self.assertIn("Full access", self.app)
        self.assertIn('id: "standard"', self.app)
        self.assertIn('id: "extended"', self.app)
        self.assertIn('id: "full_computer"', self.app)
        self.assertNotIn('"restricted"', self.app)

    def test_composer_has_no_per_chat_folder_chooser(self) -> None:
        # v1.2.0: the folder belongs to the Project and is chosen once at
        # Project creation, so the message composer must not offer one.
        self.assertNotIn('aria-label="Workspace folder"', self.app)
        self.assertNotIn("chat-workspace-setup", self.app)
        self.assertNotIn("workspaceRootDraft", self.app)
        self.assertNotIn("workspaceSetupOpen", self.app)
        # The folder is still *shown* in the composer, read-only.
        self.assertIn('aria-label="Project folder"', self.app)

    def test_project_creation_offers_a_mandatory_folder(self) -> None:
        self.assertIn('aria-label="Project folder"', self.app)
        self.assertIn("Choose folder", self.app)
        self.assertIn("pickProjectFolder", self.app)
        self.assertIn("AMCA creates this folder if it does not exist", self.app)
        # The default comes from the backend, so there is one naming rule.
        self.assertIn("studioProjectDefaultFolder", self.api)
        self.assertIn("root_path", self.types)

    def test_full_access_scope_is_visible_and_refreshable(self) -> None:
        # (Round 27 heading: "Full access coverage")
        self.assertIn("localFullAccessScope", self.api)
        self.assertIn("refreshLocalFullAccess", self.api)
        self.assertIn("FullAccessScope", self.types)
        self.assertIn("Full access coverage", self.app)
        self.assertIn("Refresh drives", self.app)
        # Fail-closed: a drive attached later is stated as not yet covered.
        self.assertIn("is not covered until you refresh", self.app)

    def test_ui_exposes_grants_revocation_approvals_and_activity(self) -> None:
        # Round 27: the folder-grant BUILDER is gone (the three modes cover
        # access); existing grants stay listed for revocation, and the human
        # decisions + audit surfaces remain.
        self.assertIn("revokePermissionGrant", self.app)
        self.assertIn("Local approvals", self.app)
        self.assertIn("Tool activity", self.app)
        self.assertNotIn("Add a folder grant", self.app)
        self.assertNotIn("Create exact grant", self.app)

    def test_protocol_15_is_required_by_ui(self) -> None:
        self.assertIn("DESKTOP_PROTOCOL_VERSION = 15", self.api)


if __name__ == "__main__":
    unittest.main()
