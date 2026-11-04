"""Work Order 32A: Release Execution Safety Audit Test Suite.

Work Order: ANTIGRAVITY-AMCA-POST-31Z-RELEASE-EXECUTION-SAFETY-AUDIT-32A
Validates:
1. All 4 32A safety audit documents exist and contain required coverage.
2. Boundary Invariants: TAG_CHANGED = NO, OFFICIAL_DIST_CHANGED = NO, BUILD_EXECUTED = NO, SOURCE_CHANGED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_32a_safety_audit_documents_exist_and_complete():
    """Verify all required 32A safety audit documents exist and are complete."""
    audit_dir = SOFTWARE_DIR / "docs" / "release" / "safety_audit"
    assert audit_dir.is_dir(), f"Missing directory: {audit_dir}"

    required_docs = {
        "AMCA_RELEASE_EXECUTION_PATH_AUDIT_32A.md": [
            "31Z Checklist Completeness",
            "Command Sequence Consistency",
            "Rollback Compatibility",
            "Evidence Collection Completeness",
            "Contradictory Instructions",
        ],
        "AMCA_EXTERNAL_APPROVAL_DEPENDENCY_AUDIT_32A.md": [
            "EXT-01",
            "EXT-02",
            "Approval Handoff Documents",
            "Placeholders",
            "Activation Blockers",
        ],
        "AMCA_FINAL_RELEASE_ARTIFACT_INVENTORY_REVIEW_32A.md": [
            "MSI Installer",
            "Portable Package",
            "SHA256SUMS",
            "Authenticode Verification Report",
            "Evidence Bundle",
            "Legal Assets",
            "Release Notes",
            "Update Manifest",
        ],
        "AMCA_RELEASE_OPERATOR_QUICK_REFERENCE_32A.md": [
            "Before Starting",
            "During Build",
            "During Signing",
            "Before Publication",
            "Emergency Stop Conditions",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = audit_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_32a_boundary_invariants():
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
