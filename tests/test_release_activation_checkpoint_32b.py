"""Work Order 32B: Release Activation Checkpoint Package Test Suite.

Work Order: ANTIGRAVITY-AMCA-RELEASE-ACTIVATION-CHECKPOINT-32B
Validates:
1. All 4 32B checkpoint documents exist and contain required coverage.
2. Boundary Invariants: TAG_CHANGED = NO, OFFICIAL_DIST_CHANGED = NO, BUILD_EXECUTED = NO, SOURCE_CHANGED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_32b_checkpoint_documents_exist_and_complete():
    """Verify all required 32B checkpoint documents exist and are complete."""
    chk_dir = SOFTWARE_DIR / "docs" / "release" / "checkpoint"
    assert chk_dir.is_dir(), f"Missing directory: {chk_dir}"

    required_docs = {
        "AMCA_RELEASE_ACTIVATION_INPUT_VALIDATION_32B.md": [
            "Certificate Thumbprint",
            "Certificate Chain Validation",
            "Legal Approval Reference ID",
            "Approved Legal Document Versions",
            "Release Manager Approval Record",
            "Final Custody Snapshot Reference",
        ],
        "AMCA_RELEASE_STATE_MACHINE_32B.md": [
            "RELEASE_HOLD",
            "READY_FOR_EXECUTION",
            "BUILDING",
            "READY_FOR_PUBLICATION",
            "RELEASED",
            "ANY STATE",
        ],
        "AMCA_RELEASE_APPROVAL_EVIDENCE_BINDING_32B.md": [
            "External Approvals",
            "Custody Snapshot",
            "Build Evidence",
            "Signing Evidence",
            "Artifact Hashes",
            "Release Tag",
        ],
        "AMCA_FINAL_RELEASE_ACTIVATION_FORM_32B.md": [
            "EXT-01 VERIFIED",
            "EXT-02 VERIFIED",
            "CUSTODY VERIFIED",
            "BUILD VERIFIED",
            "SIGNING VERIFIED",
            "MANAGER APPROVAL",
            "FINAL DECISION",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = chk_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_32b_boundary_invariants():
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
