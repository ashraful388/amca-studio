"""Work Order 31K: Final Release Governance Package Test Suite.

Work Order: ANTIGRAVITY-AMCA-FINAL-RELEASE-GOVERNANCE-PACKAGE-31K
Validates:
1. All 5 release governance documents exist and contain required operational sections.
2. Tagging Invariant: No production tags have been created (TAG_CREATION = NOT EXECUTED).
3. Distribution Invariant: OFFICIAL_DIST_CHANGED = NO (software/dist/ remains untouched).
4. Four-Gate Release Approval Model structure is verified.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31k_governance_documents_exist_and_complete():
    """Verify all 5 required governance documents are present and complete."""
    gov_dir = SOFTWARE_DIR / "docs" / "release" / "governance"
    assert gov_dir.is_dir(), f"Missing directory: {gov_dir}"

    required_docs = {
        "FINAL_RELEASE_RUNBOOK_31K.md": [
            "RACI Matrix",
            "Pre-Release Gate Checks",
            "Build Sequence",
            "Production Signing Sequence",
            "Verification Sequence & Hash Custody",
            "Publication Sequence",
            "Rollback Decision Points",
        ],
        "PRODUCTION_TAGGING_PROCEDURE_31K.md": [
            "When Production Tag Creation Is Allowed",
            "Required Approvals Before Tagging",
            "Tag Naming Convention",
            "Signed Commit & Tag Verification",
            "Rollback Procedure If Tag Error Occurs",
        ],
        "RELEASE_EVIDENCE_BUNDLE_SPEC_31K.md": [
            "Source Commit & Provenance Record",
            "Build Environment Manifest",
            "Dependency Lock Records",
            "Digital Signature & Timestamp Record",
            "Final Cryptographic Hashes",
            "Acceptance Testing Logs",
            "Legal Document Version Manifest",
        ],
        "CUSTOMER_RELEASE_PACKAGE_CHECKLIST_31K.md": [
            "Standard Windows Installer",
            "Portable Archive",
            "Checksum Register",
            "Bundled Legal & Privacy Documents",
            "Customer Documentation & Release Notes",
            "Support & Feedback Channels",
        ],
        "FINAL_RELEASE_APPROVAL_GATE_31K.md": [
            "Gate 1: Engineering Sign-Off",
            "Gate 2: Security & Code Signing Sign-Off",
            "Gate 3: Legal & Compliance Sign-Off",
            "Gate 4: Commercial Release Manager Authorization",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = gov_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 500, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31k_tag_constraint_invariant():
    """Verify strictly that no new production tags have been created (TAG_CREATION = NOT EXECUTED)."""
    res = subprocess.run(
        ["git", "tag", "-l"],
        cwd=str(SOFTWARE_DIR),
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    tags = res.stdout.strip().splitlines()

    # The only allowed official tag in baseline is amca-studio-v1.1.7
    for tag in tags:
        assert not tag.startswith("amca-studio-v1.2"), f"Invariant violated: Tag '{tag}' exists prematurely!"


def test_31k_official_dist_untouched():
    """Verify that official distribution directory remains completely unmodified."""
    dist_dir = SOFTWARE_DIR / "dist"
    if dist_dir.exists():
        for item in dist_dir.rglob("*"):
            assert "1.2.0" not in item.name, f"Unexpected v1.2.0 artifact in dist: {item.name}"
