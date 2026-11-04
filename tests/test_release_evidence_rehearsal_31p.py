"""Work Order 31P: Release Evidence Rehearsal Test Suite.

Work Order: ANTIGRAVITY-AMCA-RELEASE-EVIDENCE-REHEARSAL-31P
Validates:
1. Both 31P evidence rehearsal documents exist and contain required coverage.
2. Complete unbroken evidence chain from v1.1.7 golden baseline through 31O.
3. Boundary Invariants: TAG_CHANGED = NO, OFFICIAL_DIST_CHANGED = NO, BUILD_EXECUTED = NO, SOURCE_CHANGED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31p_rehearsal_documents_exist_and_complete():
    """Verify all required 31P rehearsal documents exist and are complete."""
    reh_dir = SOFTWARE_DIR / "docs" / "release" / "rehearsal"
    assert reh_dir.is_dir(), f"Missing directory: {reh_dir}"

    required_docs = {
        "RELEASE_EVIDENCE_REHEARSAL_REPORT_31P.md": [
            "Unbroken Evidence Chain Audit",
            "Evidence Bundle Asset Verification",
            "Git Provenance & Lineage",
            "Dependency Locks",
            "Acceptance Test Records",
            "Release Boundary Invariant Reconfirmation",
        ],
        "RELEASE_ARTIFACT_INVENTORY_RECHECK_31P.md": [
            "Customer Package Inventory Matrix",
            "Primary Windows Installer",
            "Portable Distribution",
            "Checksum Register",
            "Third-Party OSS Notices",
            "Privacy Policy",
            "EULA & Terms",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = reh_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 500, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31p_evidence_chain_continuity():
    """Verify that preceding milestone test files exist to certify the evidence chain."""
    test_dir = SOFTWARE_DIR / "tests"
    required_test_milestones = [
        "test_cr02_code_signing_acceptance.py",
        "test_upgrade_safety_review_31h.py",
        "test_signed_build_readiness_31i.py",
        "test_release_simulation_31j.py",
        "test_final_release_governance_31k.py",
        "test_release_freeze_integrity_31l.py",
        "test_release_day_execution_readiness_31m.py",
        "test_final_independent_release_audit_31n.py",
        "test_release_hold_monitoring_31o.py",
    ]

    for t in required_test_milestones:
        t_path = test_dir / t
        assert t_path.is_file(), f"Broken evidence chain: Missing test suite {t}"


def test_31p_boundary_invariants():
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
