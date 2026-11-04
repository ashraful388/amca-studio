"""
AMCA Studio Beta B8 Agent Ecosystem and Template Foundation Test Suite
Validates:
1. AgentPackageDefinition packaging and schema
2. WorkflowTemplateDefinition registration and retrieval
3. Pre-flight dependency & capability safety validation
4. Local SQLite offline registry with zero external cloud dependencies
"""

import tempfile
import unittest
from pathlib import Path

from amca.agent_ecosystem import (
    AgentPackageDefinition,
    WorkflowTemplateDefinition,
    AgentEcosystemStore,
    validate_template_safety,
)


class StudioBetaAgentEcosystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_eco.sqlite3"
        self.store = AgentEcosystemStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_agent_package_registration_and_listing(self) -> None:
        """Verify local package definitions persist and deserialize cleanly."""
        pkg = AgentPackageDefinition(
            package_id="pkg-python-refactor",
            name="Python Architecture Refactoring Agent",
            version="1.0.0",
            author="AMCA Open Engineering",
            description="Autonomous refactoring agent specialized in AST transforms and unit verification.",
            capabilities=("code_analysis", "refactoring", "pytest_runner"),
            required_tools=("ast_parser", "pytest"),
            compatible_runtimes=("local_subprocess", "container"),
        )
        self.store.register_package(pkg)

        pkgs = self.store.list_packages()
        self.assertEqual(len(pkgs), 1)
        self.assertEqual(pkgs[0]["package_id"], "pkg-python-refactor")
        self.assertIn("refactoring", pkgs[0]["capabilities"])

    def test_workflow_template_safety_validation(self) -> None:
        """Verify pre-flight template safety checks identify missing capabilities or model dependencies."""
        tmpl = WorkflowTemplateDefinition(
            template_id="tmpl-security-audit",
            name="End-to-End Security Audit",
            version="1.2.0",
            purpose="Conducts static code analysis, vulnerability scanning, and compliance report generation.",
            required_capabilities=("static_analysis", "secret_scanning"),
            recommended_mode="hybrid",
            required_models=("ollama/qwen2.5-coder:7b",),
            steps_blueprint=({"step": 1, "action": "scan_secrets"}, {"step": 2, "action": "audit_ast"}),
        )
        self.store.register_template(tmpl)

        # Test passing validation
        is_safe, reasons = validate_template_safety(
            tmpl,
            available_capabilities=["static_analysis", "secret_scanning", "extra_cap"],
            available_models=["ollama/qwen2.5-coder:7b", "cloud/model"],
        )
        self.assertTrue(is_safe)
        self.assertTrue(any("satisfied" in r for r in reasons))

        # Test failing validation on missing dependency
        is_safe_fail, fail_reasons = validate_template_safety(
            tmpl,
            available_capabilities=["static_analysis"],  # missing secret_scanning
            available_models=["ollama/qwen2.5-coder:7b"],
        )
        self.assertFalse(is_safe_fail)
        self.assertTrue(any("Missing capability: 'secret_scanning'" in r for r in fail_reasons))


if __name__ == "__main__":
    unittest.main()

