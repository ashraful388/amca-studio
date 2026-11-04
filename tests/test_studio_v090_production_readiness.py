"""AMCA Studio v0.9.0 Production Readiness and Integration Hardening Test Suite (AG-AMCA-STUDIO-V0.9.0).

Validates:
1. Production error formatting & deterministic recovery guidance
2. Anti-AI diagnosis / anti-reasoning invariant in error explanations
3. Commercial boundary declaration (anti-hype verification)
4. Release readiness version consistency audit across pyproject.toml, package.json, and __init__.py
5. Offline readiness audit
6. Complete user journey integration test
7. Model Router and Agent Fabric architectural decoupling
"""

import inspect
from pathlib import Path
import tempfile
import unittest

from amca.agent_capability_governance import ModelCapabilityProfile
from amca.production_readiness import (
    CommercialBoundaryDeclaration,
    ProductionErrorExplanation,
    ProductionErrorFormatter,
    ReleaseReadinessAuditor,
)


class StudioV090ProductionReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_production_error_formatting_and_recovery(self) -> None:
        """Verify production error formatting generates clear explanations and deterministic recovery steps."""
        codes = [
            "CAPABILITY_MISMATCH",
            "SAFETY_GATE_REJECTED",
            "PROVIDER_UNAVAILABLE",
            "PLAN_UNAPPROVED",
            "OPERATOR_CANCELLED",
        ]
        for code in codes:
            err = ProductionErrorFormatter.format_failure(
                error_code=code,
                raw_message="Sample detail",
                recorded_facts={"session_id": "rts-123"},
            )
            self.assertEqual(err.error_code, code)
            self.assertIn("Sample detail", err.explanation)
            self.assertGreater(len(err.recovery_steps), 0)
            self.assertEqual(err.recorded_context["session_id"], "rts-123")

    def test_anti_reasoning_and_anti_ai_diagnosis(self) -> None:
        """Verify error formatting strips forbidden reasoning keys and avoids speculative AI diagnosis."""
        err = ProductionErrorFormatter.format_failure(
            error_code="CAPABILITY_MISMATCH",
            raw_message="Missing required capability",
            recorded_facts={
                "allowed_fact": "test",
                "chain_of_thought": "secret speculation",
                "hidden_reasoning": "internal thought",
            },
        )
        self.assertNotIn("chain_of_thought", err.recorded_context)
        self.assertNotIn("hidden_reasoning", err.recorded_context)
        self.assertEqual(err.recorded_context["allowed_fact"], "test")

    def test_commercial_boundary_declaration(self) -> None:
        """Verify explicit commercial boundaries prevent unsupported hype claims."""
        status = CommercialBoundaryDeclaration.get_commercial_status()
        self.assertTrue(status["local_first"])
        self.assertTrue(status["telemetry_free"])
        self.assertFalse(status["autonomous_agency"])
        self.assertIn("local_first_desktop_platform", status["available_features"])
        self.assertIn("autonomous_ai_employee", status["unclaimed_features"])
        self.assertIn("cloud_saas_platform", status["unclaimed_features"])

    def test_release_readiness_version_audit(self) -> None:
        """Verify version consistency across pyproject.toml, package.json, and __init__.py."""
        root = Path(__file__).resolve().parents[1]
        pyproject_path = root / "pyproject.toml"
        package_json_path = root / "desktop-ui" / "package.json"
        init_path = root / "src" / "amca" / "__init__.py"

        audit = ReleaseReadinessAuditor.audit_versions(
            pyproject_path=pyproject_path,
            package_json_path=package_json_path,
            init_path=init_path,
        )
        # The audit must report the live canonical version everywhere and
        # confirm consistency; no historical version may be pinned here.
        import re

        live_version = re.search(
            r'__version__\s*=\s*"([^"]+)"', init_path.read_text(encoding="utf-8")
        ).group(1)
        self.assertEqual(audit["init_version"], live_version)
        self.assertEqual(audit["pyproject_version"], live_version)
        self.assertEqual(audit["package_json_version"], live_version)
        self.assertTrue(audit["consistent"])

    def test_offline_readiness_audit(self) -> None:
        """Verify offline model execution readiness detection."""
        profiles = [
            ModelCapabilityProfile(
                model_id="qwen2.5-coder-32b",
                provider="ollama",
                locality="LOCAL_OFFLINE",
                supported_capabilities=("python",),
                context_window=32768,
                governance_requirements=("offline_local",),
            ),
            ModelCapabilityProfile(
                model_id="cloud-gpt-4o",
                provider="openai",
                locality="CLOUD_ONLY",
                supported_capabilities=("general",),
                context_window=128000,
                governance_requirements=(),
            ),
        ]
        audit = ReleaseReadinessAuditor.audit_offline_readiness(profiles)
        self.assertEqual(audit["total_registered_models"], 2)
        self.assertEqual(audit["offline_models_count"], 1)
        self.assertTrue(audit["offline_ready"])
        self.assertIn("qwen2.5-coder-32b", audit["offline_model_ids"])

    def test_complete_user_journey_error_handling(self) -> None:
        """Verify user journey gracefully produces structured recovery guidance upon encountering an issue."""
        from amca.adaptive_coordination_intelligence import (
            AdaptiveCoordinationEngine,
            AdaptiveCoordinationStore,
        )
        from amca.adaptive_execution_planning import (
            AdaptiveExecutionPlanningEngine,
            AdaptiveExecutionPlanningStore,
        )
        from amca.governed_execution_plan_bridge import (
            GovernedExecutionPlanBridge,
            GovernedExecutionPlanBridgeStore,
        )

        coord_store = AdaptiveCoordinationStore(self.tmp_path / "coord.sqlite3")
        coord_engine = AdaptiveCoordinationEngine(coord_store)
        plan_store = AdaptiveExecutionPlanningStore(self.tmp_path / "plan.sqlite3")
        plan_engine = AdaptiveExecutionPlanningEngine(store=plan_store, coordination_store=coord_store)
        bridge_store = GovernedExecutionPlanBridgeStore(self.tmp_path / "bridge.sqlite3")
        plan_bridge = GovernedExecutionPlanBridge(store=bridge_store, planning_store=plan_store)

        ctx = coord_engine.capture_coordination_context(
            workflow_id="wf-journey-01",
            workspace_id="ws-journey",
            task_domain="testing",
        )
        prop = coord_engine.create_proposal(
            context_id=ctx.context_id,
            workspace_id="ws-journey",
            proposal_type="REVISE_EXECUTION_STRATEGY",
            rationale="Strategy revision",
            proposed_changes={},
        )
        coord_engine.approve_proposal(
            proposal_id=prop.proposal_id,
            workspace_id="ws-journey",
            reviewed_by="admin",
        )
        plan = plan_engine.convert_proposal_to_plan(
            proposal_id=prop.proposal_id,
            workspace_id="ws-journey",
            goal="Test plan",
            steps_spec=[],
        )
        plan_engine.approve_plan(
            plan_id=plan.plan_id,
            workspace_id="ws-journey",
            reviewed_by="admin",
        )

        # Safety gate failure (e.g. provider unavailable)
        handoff = plan_bridge.request_plan_execution_handoff(
            plan_id=plan.plan_id,
            workspace_id="ws-journey",
            requested_by="admin",
            runtime_session_id="rts-01",
            resources_valid=True,
            capabilities_satisfied=True,
            locality_satisfied=True,
            provider_approvals_satisfied=False,  # Fails gate!
        )
        self.assertEqual(handoff.status, "REJECTED")

        # Format user explanation
        user_err = ProductionErrorFormatter.format_failure(
            error_code="SAFETY_GATE_REJECTED",
            raw_message=handoff.rejection_reason or "Safety gate rejected",
            recorded_facts={"handoff_id": handoff.handoff_id},
        )
        self.assertEqual(user_err.error_code, "SAFETY_GATE_REJECTED")
        self.assertIn("safety gates failed", user_err.explanation)
        self.assertGreater(len(user_err.recovery_steps), 0)

    def test_architectural_decoupling(self) -> None:
        """Verify Model Router and Agent Fabric remain untouched."""
        import amca.intelligence.cross_provider_router as cpr
        src = inspect.getsource(cpr)
        self.assertNotIn("ProductionErrorFormatter", src)
        self.assertNotIn("ReleaseReadinessAuditor", src)


if __name__ == "__main__":
    unittest.main()

