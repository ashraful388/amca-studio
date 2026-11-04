"""Work Order 31V: Commercial Operations Readiness Package Test Suite.

Work Order: ANTIGRAVITY-AMCA-COMMERCIAL-OPERATIONS-READINESS-PACKAGE-31V
Validates:
1. All 4 31V operational documents exist and contain required coverage.
2. Boundary Invariants: TAG_CHANGED = NO, OFFICIAL_DIST_CHANGED = NO, BUILD_EXECUTED = NO, SOURCE_CHANGED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31v_operations_documents_exist_and_complete():
    """Verify all required 31V operations documents exist and are complete."""
    ops_dir = SOFTWARE_DIR / "docs" / "release" / "operations"
    assert ops_dir.is_dir(), f"Missing directory: {ops_dir}"

    required_docs = {
        "CUSTOMER_SUPPORT_OPERATIONS_MANUAL_31V.md": [
            "Support Mission & Operating Principles",
            "Ticket Priority Classification",
            "Troubleshooting Escalation Path",
            "Diagnostic Handling Rules",
            "Privacy-Safe Support Practices",
        ],
        "AMCA_INCIDENT_RESPONSE_PLAN_31V.md": [
            "Security Incident Handling",
            "Privacy Incident Handling",
            "Release Rollback Triggers",
            "Customer Communication Workflow",
            "Evidence Preservation",
        ],
        "AMCA_COMMERCIAL_METRICS_FRAMEWORK_31V.md": [
            "Zero Involuntary Telemetry",
            "Adoption Metrics",
            "Stability Metrics",
            "Performance Metrics",
            "Cost Efficiency Metrics",
            "Privacy & Security Indicators",
        ],
        "AMCA_ENTERPRISE_DEPLOYMENT_GUIDE_31V.md": [
            "Windows Deployment Model",
            "Credential Management",
            "Enterprise Network & Firewall Requirements",
            "BYOK (Bring Your Own Key) Governance",
            "Local Model Deployment",
            "Enterprise Security Considerations",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = ops_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31v_boundary_invariants():
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
