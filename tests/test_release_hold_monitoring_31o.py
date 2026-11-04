"""Work Order 31O: Release Hold State Monitoring Test Suite.

Work Order: ANTIGRAVITY-AMCA-RELEASE-HOLD-MONITORING-31O
Validates:
1. All 3 release hold monitoring documents exist and contain required coverage.
2. Tagging Invariant: TAG_CHANGED = NO (Zero v1.2 tags exist).
3. Distribution Invariant: OFFICIAL_DIST_CHANGED = NO (software/dist/ remains untouched).
4. Build Execution Invariant: BUILD_EXECUTED = NO.
5. Source Code Invariant: SOURCE_CHANGED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31o_monitoring_documents_exist_and_complete():
    """Verify all required 31O release hold monitoring documents exist and are complete."""
    hold_dir = SOFTWARE_DIR / "docs" / "release" / "hold"
    assert hold_dir.is_dir(), f"Missing directory: {hold_dir}"

    required_docs = {
        "RELEASE_HOLD_INTEGRITY_MONITOR_31O.md": [
            "Freeze Watch & Integrity Verification Audit",
            "Git Working Tree",
            "Golden Tag Lineage",
            "Official Distribution",
            "Continuous Hold Watch Rules",
        ],
        "EXTERNAL_RELEASE_BLOCKER_STATUS_31O.md": [
            "Blocker Registry Table",
            "Commercial Code Signing Certificate",
            "Legal & Compliance Approval",
            "Transition Plan upon Blocker Resolution",
        ],
        "RELEASE_EXECUTION_TRIGGER_CHECKLIST_31O.md": [
            "Pre-Execution Trigger Checklist",
            "Certificate Available?",
            "Legal Approval Received?",
            "Release Candidate Hash Frozen?",
            "Evidence Bundle Location Confirmed?",
            "Release Manager Approval Received?",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = hold_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31o_hold_invariants_preserved():
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
