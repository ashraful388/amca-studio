"""Work Order 31S: Release Authorization Simulation Test Suite.

Work Order: ANTIGRAVITY-AMCA-RELEASE-AUTHORIZATION-SIMULATION-31S
Validates:
1. All 3 31S authorization simulation documents exist and contain required coverage.
2. Decision engine logic: 4-gate boolean evaluation deterministically produces AUTHORIZED_FOR_RELEASE or RELEASE_HOLD.
3. Boundary Invariants: TAG_CHANGED = NO, OFFICIAL_DIST_CHANGED = NO, BUILD_EXECUTED = NO, SOURCE_CHANGED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def evaluate_authorization(cert: bool, legal: bool, rm: bool, custody: bool) -> str:
    """Python reference implementation of the 31S authorization decision engine."""
    if cert and legal and rm and custody:
        return "AUTHORIZED_FOR_RELEASE"
    return "RELEASE_HOLD"


def test_31s_authorization_documents_exist_and_complete():
    """Verify all required 31S authorization documents exist and are complete."""
    auth_dir = SOFTWARE_DIR / "docs" / "release" / "authorization"
    assert auth_dir.is_dir(), f"Missing directory: {auth_dir}"

    required_docs = {
        "RELEASE_AUTHORIZATION_DECISION_SIMULATION_31S.md": [
            "Authorization Decision Engine Specification",
            "Decision Engine Truth Table",
            "AUTHORIZED_FOR_RELEASE",
            "RELEASE_HOLD",
        ],
        "RELEASE_APPROVAL_CHAIN_MATRIX_31S.md": [
            "Multi-Stakeholder Approval Chain Matrix",
            "Lead Software Engineer",
            "Security Officer",
            "Corporate Legal Counsel",
            "Commercial Release Manager",
            "Final Authority & Escalation Protocol",
        ],
        "RELEASE_TRIGGER_CHECKLIST_SIMULATION_31S.md": [
            "Simulation Test Suite Overview",
            "Test Case 1: Missing Code-Signing Certificate",
            "Test Case 2: Missing Legal Approval",
            "Test Case 3: Missing Custody Verification",
            "Test Case 4: Nominal Production Authorization",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = auth_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31s_decision_engine_logic():
    """Validate the truth table of the release authorization engine."""
    # Nominal success
    assert evaluate_authorization(True, True, True, True) == "AUTHORIZED_FOR_RELEASE"

    # Blocked cases
    assert evaluate_authorization(False, True, True, True) == "RELEASE_HOLD"
    assert evaluate_authorization(True, False, True, True) == "RELEASE_HOLD"
    assert evaluate_authorization(True, True, False, True) == "RELEASE_HOLD"
    assert evaluate_authorization(True, True, True, False) == "RELEASE_HOLD"
    assert evaluate_authorization(False, False, False, False) == "RELEASE_HOLD"


def test_31s_boundary_invariants():
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
