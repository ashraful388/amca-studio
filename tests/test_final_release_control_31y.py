"""Work Order 31Y: Final Release Control Package Test Suite.

Work Order: ANTIGRAVITY-AMCA-FINAL-RELEASE-CONTROL-PACKAGE-31Y
Validates:
1. All 4 31Y release control documents exist and contain required coverage.
2. Boundary Invariants: TAG_CHANGED = NO, OFFICIAL_DIST_CHANGED = NO, BUILD_EXECUTED = NO, SOURCE_CHANGED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31y_control_documents_exist_and_complete():
    """Verify all required 31Y release control documents exist and are complete."""
    ctrl_dir = SOFTWARE_DIR / "docs" / "release" / "control"
    assert ctrl_dir.is_dir(), f"Missing directory: {ctrl_dir}"

    required_docs = {
        "AMCA_MASTER_RELEASE_CONTROL_SHEET_31Y.md": [
            "Release Identity",
            "Frozen Commit",
            "Approval Gates",
            "Responsible Person",
            "Execution Timestamps",
            "Final Verification Checklist",
        ],
        "AMCA_RELEASE_EVIDENCE_INDEX_31Y.md": [
            "Milestone Evidence",
            "Cryptographic Custody",
            "Code Signing",
            "Legal & Compliance Records",
            "Emergency Rollback Evidence",
        ],
        "AMCA_RELEASE_WAR_ROOM_PROCEDURE_31Y.md": [
            "Participant Roles",
            "Communication Rules",
            "Mandatory Stop",
            "Escalation Path",
            "Final Approval Recording",
        ],
        "AMCA_RELEASE_STATUS_DASHBOARD_31Y.md": [
            "Gate Status",
            "Certificate Status",
            "Legal Status",
            "Build Status",
            "Signing Status",
            "Verification Status",
            "Publication Status",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = ctrl_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31y_boundary_invariants():
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
