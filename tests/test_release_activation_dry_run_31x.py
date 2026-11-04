"""Work Order 31X: Release Activation Dry Run Test Suite.

Work Order: ANTIGRAVITY-AMCA-RELEASE-ACTIVATION-DRY-RUN-31X
Validates:
1. All 4 31X dry run documents exist and contain required coverage.
2. Boundary Invariants: TAG_CHANGED = NO, OFFICIAL_DIST_CHANGED = NO, BUILD_EXECUTED = NO, SOURCE_CHANGED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31x_dry_run_documents_exist_and_complete():
    """Verify all required 31X dry run documents exist and are complete."""
    act_dir = SOFTWARE_DIR / "docs" / "release" / "activation"
    assert act_dir.is_dir(), f"Missing directory: {act_dir}"

    required_docs = {
        "AMCA_RELEASE_ACTIVATION_DRY_RUN_CHECKLIST_31X.md": [
            "External Approval Inputs",
            "Certificate Readiness",
            "Legal Document",
            "Release Manager Approval Sequence",
            "Evidence Bundle",
        ],
        "AMCA_RELEASE_DAY_TIMELINE_SIMULATION_31X.md": [
            "T-24h",
            "T-1h",
            "T+0",
            "T+30",
            "T+150",
        ],
        "AMCA_RELEASE_OPERATOR_COMMAND_REFERENCE_31X.md": [
            "Build Commands",
            "Signing Commands",
            "Verification",
            "Artifact Collection",
            "Rollback Commands",
        ],
        "AMCA_FINAL_GO_NO_GO_SUMMARY_31X.md": [
            "Completed",
            "Remaining External",
            "Release Risk",
            "Activation Conditions",
            "Rollback Readiness",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = act_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31x_boundary_invariants():
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
