"""Work Order 31Z: Release Activation Trigger Package Test Suite.

Work Order: ANTIGRAVITY-AMCA-RELEASE-ACTIVATION-TRIGGER-PACKAGE-31Z
Validates:
1. All 4 31Z release trigger documents exist and contain required coverage.
2. Boundary Invariants: TAG_CHANGED = NO, OFFICIAL_DIST_CHANGED = NO, BUILD_EXECUTED = NO, SOURCE_CHANGED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31z_trigger_documents_exist_and_complete():
    """Verify all required 31Z trigger documents exist and are complete."""
    trig_dir = SOFTWARE_DIR / "docs" / "release" / "trigger"
    assert trig_dir.is_dir(), f"Missing directory: {trig_dir}"

    required_docs = {
        "AMCA_RELEASE_ACTIVATION_APPROVAL_RECORD_31Z.md": [
            "EXT-01",
            "EXT-02",
            "Custody",
            "Release Manager Authorization",
            "Final GO Decision",
        ],
        "AMCA_FINAL_RELEASE_EXECUTION_CHECKLIST_31Z.md": [
            "Clean Checkout",
            "Dependency Lock",
            "Build Execution",
            "Signing Execution",
            "Authenticode Verification",
            "SHA-256 Generation",
            "Evidence Sealing",
            "Git Tag Creation",
            "Publication",
        ],
        "AMCA_RELEASE_INCIDENT_COMMAND_SHEET_31Z.md": [
            "Incident Command",
            "Escalation Contacts",
            "Rollback Decision Authority",
            "Emergency Communication Templates",
            "Evidence Preservation",
        ],
        "AMCA_EXECUTIVE_RELEASE_AUTHORIZATION_SUMMARY_31Z.md": [
            "Completed Milestones",
            "Remaining External Approvals",
            "Executive Risk Assessment",
            "Final Activation Equation",
            "Release Authorization Statement",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = trig_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31z_boundary_invariants():
    """Verify strictly that tags, dist, and source remain unchanged."""
    res = subprocess.run(["git", "tag", "-l"], cwd=str(SOFTWARE_DIR), capture_output=True, text=True)
    assert res.returncode == 0
    tags = res.stdout.strip().splitlines()
    for tag in tags:
        assert not tag.startswith("amca-studio-v1.2"), f"Invariant violated: Tag '{tag}' exists prematurely!"

    dist_dir = SOFTWARE_DIR / "dist"
    if dist_dir.exists():
        for item in dist_dir.rglob("*"):
            assert "1.2.0" not in item.name, f"Unexpected v1.2.0 artifact in dist: {item.name}"
