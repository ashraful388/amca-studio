"""Canonical convergence and full regression inventory checks.

ZCODE-AMCA-STUDIO-V1.0.0-I1-CANONICAL-BRANCH-CONVERGENCE-AND-FULL-REGRESSION-INVENTORY-01

Validates the convergence state: both lines' key modules exist, test
universes are complete, migration continuity is preserved, and
architecture boundaries hold.
"""

import os
import unittest

SOFTWARE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP_UI_SRC = os.path.join(SOFTWARE_ROOT, "desktop-ui", "src")


class TestConvergenceModelFiles(unittest.TestCase):
    """All key model files from both lines exist on the canonical tree."""

    def test_zcode_ux_models_exist(self):
        models = [
            "feUxBatchModel.ts",
            "adaptiveWorkflowReadinessUxModel.ts",
            "consentGovernanceUxModel.ts",
            "crossModuleWorkflowContinuityModel.ts",
            "cognitiveWorkflowUxModel.ts",
            "cognitiveWorkflowInsightsModel.ts",
            "cognitiveWorkflowInteractionModel.ts",
            "cognitiveWorkflowUxConsolidationModel.ts",
            "decisionTransparencyUxPreparationModel.ts",
            "decisionEvidenceRegistryDesignModel.ts",
            "intelligentDecisionTransparencyUxDesignModel.ts",
            "endUserWorkflowValidationUxModel.ts",
            "modelHubManagementUxModel.ts",
            "workspaceProductivityAssistanceUxModel.ts",
            "workflowTemplateReuseUxModel.ts",
            "reviewCenterWorkflowUxModel.ts",
            "operationalWorkspaceIntelligenceModel.ts",
            "knowledgeIntelligenceModel.ts",
        ]
        for model in models:
            path = os.path.join(DESKTOP_UI_SRC, model)
            self.assertTrue(os.path.isfile(path), f"missing: {model}")

    def test_antigravity_backend_modules_exist(self):
        modules = [
            "model_evaluation_evidence.py",
            "model_selection_workspace.py",
            "manual_model_selection_workflow_binding.py",
            "governed_execution_plan_bridge.py",
            "routing.py",
            "collaboration.py",
        ]
        for module in modules:
            path = os.path.join(SOFTWARE_ROOT, "src", "amca", module)
            self.assertTrue(os.path.isfile(path), f"missing: {module}")

    def test_frontend_test_files_exist(self):
        tests_dir = os.path.join(SOFTWARE_ROOT, "desktop-ui", "tests")
        expected = [
            "feUxBatchModel.test.mjs",
            "adaptiveWorkflowReadinessUxModel.test.mjs",
            "consentGovernanceUxModel.test.mjs",
            "decisionTransparencyUxPreparationModel.test.mjs",
            "modelHubManagementUxModel.test.mjs",
            "endUserWorkflowValidationUx.test.mjs",
            "intelligentDecisionTransparencyUxDesignModel.test.mjs",
            "decisionEvidenceRegistryDesignModel.test.mjs",
        ]
        for test_file in expected:
            path = os.path.join(tests_dir, test_file)
            self.assertTrue(os.path.isfile(path), f"missing: {test_file}")


class TestMigrationContinuity(unittest.TestCase):
    """Key data-preserving surfaces are accessible on the canonical tree."""

    def test_cost_budget_module_exists(self):
        path = os.path.join(SOFTWARE_ROOT, "src", "amca", "cost_budget.py")
        self.assertTrue(os.path.isfile(path))

    def test_cost_routes_module_exists(self):
        path = os.path.join(SOFTWARE_ROOT, "src", "amca", "cost_routes.py")
        self.assertTrue(os.path.isfile(path))

    def test_runtime_budget_integration_exists(self):
        path = os.path.join(SOFTWARE_ROOT, "src", "amca", "runtime_budget_integration.py")
        self.assertTrue(os.path.isfile(path))


class TestArchitectureBoundaries(unittest.TestCase):
    """Governance boundaries hold in the converged tree."""

    def test_no_telemetry_in_backend(self):
        amca_root = os.path.join(SOFTWARE_ROOT, "src", "amca")
        violations = []
        for root, dirs, files in os.walk(amca_root):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    content = handle.read().lower()
                for marker in ("telemetry_endpoint", "analytics.push", "cloud_telemetry"):
                    if marker in content:
                        violations.append(f"{name}: {marker}")
        self.assertEqual(violations, [])

    def test_approval_gates_preserved(self):
        governance_doc = os.path.join(
            SOFTWARE_ROOT, "docs", "architecture",
            "AMCA-Studio-v0.8.0-Intelligent-Decision-Transparency-UX-Design.md",
        )
        self.assertTrue(os.path.isfile(governance_doc))

    def test_designed_controls_are_not_implemented(self):
        design_model = os.path.join(DESKTOP_UI_SRC, "intelligentDecisionTransparencyUxDesignModel.ts")
        with open(design_model, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("implementedInThisBuild: false", content)


if __name__ == "__main__":
    unittest.main()
