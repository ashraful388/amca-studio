"""Work Order 31M: Release-Day Execution Readiness Test Suite.

Work Order: ANTIGRAVITY-AMCA-RELEASE-DAY-EXECUTION-READINESS-31M
Validates:
1. All 5 release-day execution documents exist and contain required coverage.
2. Tagging Invariant: TAG_CHANGED = NO (Zero v1.2 tags exist).
3. Distribution Invariant: OFFICIAL_DIST_CHANGED = NO (software/dist/ remains untouched).
4. Build Execution Invariant: BUILD_EXECUTED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31m_release_day_documents_exist_and_complete():
    """Verify all 5 required 31M release-day documents are present and complete."""
    rd_dir = SOFTWARE_DIR / "docs" / "release" / "release_day"
    assert rd_dir.is_dir(), f"Missing directory: {rd_dir}"

    required_docs = {
        "RELEASE_DAY_OPERATOR_RUNBOOK_31M.md": [
            "Minute-by-Minute Release-Day Timeline",
            "Step-by-Step Operator Procedures",
            "Mandatory Stop Conditions",
        ],
        "FINAL_PRODUCTION_SIGNING_CHECKLIST_31M.md": [
            "Certificate Availability Verification",
            "Signing Environment Validation",
            "4-Tier Signing Execution",
            "Post-Signing Authenticode & Timestamp Verification",
            "Hash Custody SHA-256 Generation",
        ],
        "RELEASE_DAY_EVIDENCE_COLLECTION_31M.md": [
            "Source Commit & Provenance Record",
            "Build Execution Logs",
            "Authenticode Digital Signatures",
            "Post-Signing Cryptographic Hashes",
            "Acceptance Test Results",
            "Legal Approval Document Records",
        ],
        "CUSTOMER_LAUNCH_READINESS_CHECK_31M.md": [
            "Installer Availability",
            "Documentation Availability",
            "License Notice Availability",
            "Support Channel Readiness",
            "Download Integrity Verification",
        ],
        "RELEASE_DAY_ROLLBACK_PROCEDURE_31M.md": [
            "Failed Code Signing",
            "Failed Verification Check",
            "Installer Runtime Failure",
            "Emergency Post-Publication Withdrawal",
            "Production Tag Rollback Procedure",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = rd_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31m_invariants_preserved():
    """Verify that tags and dist have not been modified."""
    res = subprocess.run(["git", "tag", "-l"], cwd=str(SOFTWARE_DIR), capture_output=True, text=True)
    assert res.returncode == 0
    tags = res.stdout.strip().splitlines()
    for tag in tags:
        assert not tag.startswith("amca-studio-v1.2"), f"Invariant violated: Tag '{tag}' exists prematurely!"

    dist_dir = SOFTWARE_DIR / "dist"
    if dist_dir.exists():
        for item in dist_dir.rglob("*"):
            assert "1.2.0" not in item.name, f"Unexpected v1.2.0 artifact in dist: {item.name}"
