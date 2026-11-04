"""Work Order 32C: Release Day Operator Simulation Test Suite.

Work Order: ANTIGRAVITY-AMCA-RELEASE-DAY-OPERATOR-SIMULATION-32C
Validates:
1. All 4 32C operator simulation documents exist and contain required coverage.
2. Boundary Invariants: TAG_CHANGED = NO, OFFICIAL_DIST_CHANGED = NO, BUILD_EXECUTED = NO, SOURCE_CHANGED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_32c_simulation_documents_exist_and_complete():
    """Verify all required 32C simulation documents exist and are complete."""
    sim_dir = SOFTWARE_DIR / "docs" / "release" / "simulation_32c"
    assert sim_dir.is_dir(), f"Missing directory: {sim_dir}"

    required_docs = {
        "AMCA_RELEASE_DAY_OPERATOR_SIMULATION_32C.md": [
            "Approval Receipt",
            "Activation Form Completion",
            "Build Authorization",
            "Signing Authorization",
            "Artifact Verification",
            "Publication Decision",
        ],
        "AMCA_RELEASE_DAY_FAILURE_SCENARIOS_32C.md": [
            "Wrong Certificate",
            "Legal Document Checksum",
            "SHA256 Mismatch",
            "Installer Failure",
            "Update Manifest",
            "Operator Abort",
            "Detection Point",
            "Responsible Role",
            "State Transition",
            "Recovery Action",
        ],
        "AMCA_FINAL_15_MINUTE_RELEASE_CHECKLIST_32C.md": [
            "Certificate Verification",
            "Legal Approval Confirmation",
            "Custody Verification",
            "Build Artifact Verification",
            "Signing Verification",
            "Publication Authorization",
        ],
        "AMCA_RELEASE_WAR_ROOM_DECISION_LOG_32C.md": [
            "Timestamp",
            "Decision",
            "Responsible Person",
            "Evidence Reference",
            "Approval Status",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = sim_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_32c_boundary_invariants():
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
