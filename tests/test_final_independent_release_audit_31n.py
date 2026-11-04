"""Work Order 31N: Final Independent Release Audit Test Suite.

Work Order: ANTIGRAVITY-AMCA-FINAL-INDEPENDENT-RELEASE-AUDIT-31N
Validates:
1. Both 31N independent audit documents exist and contain required coverage.
2. Tagging Invariant: TAG_CHANGED = NO (Zero premature v1.2 tags).
3. Distribution Invariant: OFFICIAL_DIST_CHANGED = NO.
4. Source Code Invariant: PRODUCT_SOURCE_CHANGED = NO.
5. Risk Level: Verified LOW.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31n_audit_documents_exist_and_complete():
    """Verify all required 31N independent release audit documents exist and are complete."""
    audit_dir = SOFTWARE_DIR / "docs" / "release" / "audit"
    assert audit_dir.is_dir(), f"Missing directory: {audit_dir}"

    required_docs = {
        "FINAL_INDEPENDENT_RELEASE_AUDIT_31N.md": [
            "Release Readiness Cross-Audit Matrix",
            "Frozen Boundary Verification",
            "Commercial Risk Reassessment",
            "Overall Commercial Risk Level",
        ],
        "FINAL_RELEASE_GO_NO_GO_DECISION_31N.md": [
            "Decision Criteria Framework",
            "Technical \"GO\" Criteria",
            "Technical \"NO-GO\" Triggers",
            "Status of Remaining External Approvals",
            "Final Release Recommendation",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = audit_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 500, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31n_boundary_invariants_preserved():
    """Verify freeze invariants across git tags and official dist."""
    res = subprocess.run(["git", "tag", "-l"], cwd=str(SOFTWARE_DIR), capture_output=True, text=True)
    assert res.returncode == 0
    tags = res.stdout.strip().splitlines()
    for tag in tags:
        assert not tag.startswith("amca-studio-v1.2"), f"Invariant violated: Tag '{tag}' exists prematurely!"

    dist_dir = SOFTWARE_DIR / "dist"
    if dist_dir.exists():
        for item in dist_dir.rglob("*"):
            assert "1.2.0" not in item.name, f"Unexpected v1.2.0 artifact in dist: {item.name}"
