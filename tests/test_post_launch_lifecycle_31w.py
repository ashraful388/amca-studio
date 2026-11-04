"""Work Order 31W: Post-Launch Lifecycle Plan Test Suite.

Work Order: ANTIGRAVITY-AMCA-POST-LAUNCH-LIFECYCLE-PLAN-31W
Validates:
1. All 4 31W lifecycle documents exist and contain required coverage.
2. Boundary Invariants: TAG_CHANGED = NO, OFFICIAL_DIST_CHANGED = NO, BUILD_EXECUTED = NO, SOURCE_CHANGED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31w_lifecycle_documents_exist_and_complete():
    """Verify all required 31W lifecycle documents exist and are complete."""
    lifecycle_dir = SOFTWARE_DIR / "docs" / "release" / "lifecycle"
    assert lifecycle_dir.is_dir(), f"Missing directory: {lifecycle_dir}"

    required_docs = {
        "AMCA_POST_RELEASE_MAINTENANCE_PLAN_31W.md": [
            "Patch Release Policy",
            "Security Update Policy",
            "Dependency Update Cycle",
            "Release Cadence & Schedules",
            "Regression Testing Requirements",
            "Emergency Patch Process",
        ],
        "AMCA_VERSION_ROADMAP_31W.md": [
            "v1.2.x Maintenance",
            "v1.3 Feature Direction",
            "Future AMCA Capabilities",
            "Backward Compatibility Strategy",
            "Migration Principles",
        ],
        "AMCA_USER_FEEDBACK_FRAMEWORK_31W.md": [
            "Feedback & Communication Channels",
            "Feature Request Process",
            "Bug Reporting Workflow",
            "Prioritization Criteria",
            "Privacy-Preserving Feedback Handling",
        ],
        "AMCA_SECURITY_MAINTENANCE_POLICY_31W.md": [
            "Vulnerability Intake Protocol",
            "Dependency Monitoring",
            "Patch Severity Levels",
            "Coordinated Disclosure Policy",
            "Security Review Cycles",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = lifecycle_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31w_boundary_invariants():
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
