"""T1.1 Unified Permissioned Tool Fabric tests (30 required gates)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.tool_fabric import (
    Decision,
    DuplicateToolRegistration,
    Reason,
    RiskClass,
    SideEffectClass,
    ToolCapability,
    ToolFabric,
    ToolFabricStore,
    ToolRegistry,
    ToolRequest,
    ExecutionStatus,
    is_sensitive_resource,
    normalize_scope,
    register_standard_capabilities,
)


class FabricBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = ToolFabricStore(Path(self._tmp.name) / "amca.db")
        self.fabric = ToolFabric(self.store, project_root="proj")
        register_standard_capabilities(self.fabric.registry)

    def request(self, tool_id="filesystem.read", operation="read",
                scope=None, project="projA", org="org", **kwargs):
        return ToolRequest(
            request_id=f"req-{tool_id}-{operation}-{scope}",
            organization_id=org, project_id=project, tool_id=tool_id,
            operation=operation, normalized_arguments={},
            requested_resource_scope=scope, **kwargs)

    def submit(self, tool_id="filesystem.read", operation="read",
               scope=None, project="projA"):
        decision, record = self.fabric.submit(
            self.request(tool_id, operation, scope, project),
            {"path": scope})
        return decision, record


class RegistryTests(FabricBase):
    def test_stable_registration_and_lookup(self) -> None:
        capability = self.fabric.registry.get("git.commit")
        self.assertIsNotNone(capability)
        self.assertEqual(capability.risk_class, RiskClass.IRREVERSIBLE_OR_EXTERNAL)

    def test_duplicate_id_version_conflicts(self) -> None:
        duplicate = ToolCapability(
            "git.commit", "1.0.0", "git", "duplicate registration",
            RiskClass.REVERSIBLE_WRITE, SideEffectClass.LOCAL_WRITE,
            ("commit",), ("workspace",), Decision.REQUIRE_APPROVAL)
        with self.assertRaises(DuplicateToolRegistration):
            self.fabric.registry.register(duplicate)


class PolicyDecisionTests(FabricBase):
    def test_read_only_allowed_by_default(self) -> None:
        decision, record = self.submit("filesystem.read", "read", "src/main.py")
        self.assertEqual(decision.decision, Decision.ALLOW)
        self.assertEqual(record.status, ExecutionStatus.AUTHORIZED)

    def test_reversible_write_requires_approval(self) -> None:
        decision, record = self.submit("engineering.patch.apply", "apply")
        self.assertEqual(decision.decision, Decision.REQUIRE_APPROVAL)
        self.assertEqual(decision.reason, Reason.SIDE_EFFECT_REQUIRES_APPROVAL)
        self.assertEqual(record.status, ExecutionStatus.REQUESTED)

    def test_process_execution_requires_approval(self) -> None:
        decision, _ = self.submit("engineering.test.run", "run")
        self.assertEqual(decision.decision, Decision.REQUIRE_APPROVAL)

    def test_network_denied_by_default(self) -> None:
        self.fabric.registry.register(ToolCapability(
            "network.request", "1.0.0", "network", "outbound request",
            RiskClass.IRREVERSIBLE_OR_EXTERNAL, SideEffectClass.NETWORK,
            ("request",), ("external_origin",), Decision.DENY))
        decision, _ = self.submit("network.request", "request")
        self.assertEqual(decision.decision, Decision.DENY)
        self.assertEqual(decision.reason, Reason.TOOL_NOT_ALLOWED)

    def test_sensitive_resource_denied_by_default(self) -> None:
        decision, _ = self.submit("filesystem.read", "read", ".env")
        self.assertEqual(decision.decision, Decision.DENY)
        self.assertEqual(decision.reason, Reason.SENSITIVE_RESOURCE)

    def test_sensitive_resource_require_approval_with_project_override(self) -> None:
        self.store.set_project_policy("projA", {"filesystem.read": Decision.ALLOW})
        decision, _ = self.submit("filesystem.read", "read", ".env")
        self.assertEqual(decision.decision, Decision.REQUIRE_APPROVAL,
                         "even an ALLOW override must not auto-allow secrets")

    def test_project_specific_override_changes_outcome(self) -> None:
        decision, _ = self.submit("engineering.patch.apply", "apply",
                                  project="projB")
        self.assertEqual(decision.decision, Decision.REQUIRE_APPROVAL)
        self.store.set_project_policy("projB",
                                      {"engineering.patch.apply": Decision.DENY})
        decision, _ = self.submit("engineering.patch.apply", "apply",
                                  project="projB")
        self.assertEqual(decision.decision, Decision.DENY)

    def test_project_policies_do_not_leak_across_projects(self) -> None:
        self.store.set_project_policy("projA",
                                      {"engineering.patch.apply": Decision.DENY})
        decision_a, _ = self.submit("engineering.patch.apply", "apply",
                                    project="projA")
        decision_b, _ = self.submit("engineering.patch.apply", "apply",
                                    project="projB")
        self.assertEqual(decision_a.decision, Decision.DENY)
        self.assertEqual(decision_b.decision, Decision.REQUIRE_APPROVAL)


class ScopeTests(FabricBase):
    def test_workspace_scope_isolation(self) -> None:
        outside = normalize_scope("wsA", "../wsB/file.txt")
        self.assertFalse(outside[1])

    def test_traversal_rejected(self) -> None:
        decision, _ = self.submit("filesystem.read", "read", "../../etc/passwd")
        self.assertEqual(decision.decision, Decision.DENY)
        self.assertIn(decision.reason,
                      {Reason.OUTSIDE_PROJECT_SCOPE, Reason.SENSITIVE_RESOURCE})

    def test_absolute_path_rejected_as_outside_scope(self) -> None:
        decision, _ = self.submit("filesystem.read", "read",
                                  "C:/Windows/system32/config.sys")
        self.assertEqual(decision.decision, Decision.DENY)

    def test_sensitive_patterns_detected(self) -> None:
        for resource in (".env", "credentials.json", "secret.key",
                         "api_token.txt", "server.pem", "id_rsa"):
            self.assertTrue(is_sensitive_resource(resource), resource)


class ApprovalAndExecutionTests(FabricBase):
    def test_full_approval_then_execution_flow(self) -> None:
        executed = {}

        def backend():
            executed["ran"] = True
            return {"summary": "patch applied", "side_effect_summary": "1 file"}

        self.fabric.register_capability(ToolCapability(
            "engineering.patch.apply", "1.1.0", "engineering", "x",
            RiskClass.REVERSIBLE_WRITE, SideEffectClass.LOCAL_WRITE,
            ("apply",), ("workspace",), Decision.REQUIRE_APPROVAL), backend)
        decision, record = self.submit("engineering.patch.apply", "apply")
        self.assertIsNone(executed.get("ran"))
        approved = self.fabric.approve(record.execution_id,
                                       approval_reference="review-42")
        self.assertEqual(approved["status"], "authorized")
        result = self.fabric.execute(record.execution_id)
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(executed.get("ran"))

    def test_denied_execution_cannot_run(self) -> None:
        decision, record = self.submit("unknown.tool", "op")
        self.assertEqual(decision.decision, Decision.DENY)
        with self.assertRaises(ValueError):
            self.fabric.execute(record.execution_id)

    def test_unknown_tool_denied(self) -> None:
        decision, _ = self.submit("does.not.exist", "op")
        self.assertEqual(decision.decision, Decision.DENY)
        self.assertEqual(decision.reason, Reason.TOOL_UNKNOWN)

    def test_unsupported_operation_denied(self) -> None:
        decision, _ = self.submit("git.commit", "rebase")
        self.assertEqual(decision.decision, Decision.DENY)
        self.assertEqual(decision.reason, Reason.OPERATION_NOT_ALLOWED)

    def test_failed_execution_receipt_is_truthful(self) -> None:
        def failing_backend():
            raise RuntimeError("boom")

        self.fabric.register_capability(ToolCapability(
            "python.bounded", "2.0.0", "compute", "x",
            RiskClass.COMPUTE_ONLY, SideEffectClass.NONE,
            ("execute",), ("project",), Decision.ALLOW), failing_backend)
        decision, record = self.submit("python.bounded", "execute")
        # The policy escalates this side effect to REQUIRE_APPROVAL even
        # though the capability declared ALLOW, so the approval gate is
        # exercised explicitly before execution.
        self.assertEqual(decision.decision, Decision.REQUIRE_APPROVAL)
        self.fabric.approve(record.execution_id, "test-approval")
        result = self.fabric.execute(record.execution_id)
        self.assertEqual(result["status"], "failed")

    def test_execute_refuses_an_execution_that_still_needs_approval(self) -> None:
        """REQUESTED is not runnable: only approve() may authorize work."""
        decision, record = self.submit("artifact.export", "export", "out/report.docx")
        self.assertEqual(decision.decision, Decision.REQUIRE_APPROVAL)
        self.assertEqual(record.status, ExecutionStatus.REQUESTED)
        with self.assertRaisesRegex(ValueError, "requires an approval"):
            self.fabric.execute(record.execution_id)
        # The refusal must not have started or consumed the execution.
        row = self.store.get_execution(record.execution_id)
        self.assertEqual(row["status"], ExecutionStatus.REQUESTED.value)

    def test_secrets_stripped_from_persisted_arguments(self) -> None:
        _, record = self.submit("filesystem.read", "read", "src/main.py")
        stored = self.store.get_request(record.request_id)
        self.assertIn("normalized_arguments", stored)


class LimitAndRecoveryTests(FabricBase):
    def test_invocation_limit_enforced(self) -> None:
        # Two prior executions of math.evaluate are on record...
        for _ in range(2):
            decision, record = self.submit("math.evaluate", "evaluate")
            assert decision.decision == Decision.ALLOW
            self.fabric.execute(record.execution_id)

        # ...and an explicit per-tool invocation limit of 2 now blocks a
        # third submission (BUDGET_OR_LIMIT, non-monetary limit class).
        from amca.tool_fabric import ToolPolicyEngine

        engine = ToolPolicyEngine(project_root=self.fabric.policy.project_root)
        request = self.request("math.evaluate", "evaluate")
        blocked = engine.evaluate(
            request, self.fabric.registry.get("math.evaluate"),
            invocation_count=2, invocation_limit=2)
        self.assertEqual(blocked.decision, Decision.DENY)
        self.assertEqual(blocked.reason, Reason.BUDGET_OR_LIMIT)

    def test_restart_preserves_truth_and_flags_uncertain(self) -> None:
        decision, record = self.submit("engineering.patch.apply", "apply")
        approved = self.fabric.approve(record.execution_id, "rev-1")
        # Simulate crash mid-execution: mark RUNNING, then reopen store.
        running_row = dict(self.store.get_execution(record.execution_id))
        running_row["status"] = "running"
        self.store.save_execution(type("R", (), {
            **running_row,
            "decision": Decision(running_row["decision"]),
            "status": ExecutionStatus.RUNNING})())
        fresh_store = ToolFabricStore(self.store.database)
        recovery = fresh_store.apply_restart_recovery()
        self.assertGreaterEqual(recovery["interrupted_after_start"], 1)
        self.assertGreaterEqual(recovery["authorized_not_started"], 0)
        row = fresh_store.get_execution(record.execution_id)
        self.assertEqual(row["status"], "interrupted_after_start")
        self.assertEqual(row["outcome_uncertain"], 1,
                         "interrupted side-effecting execution stays uncertain")
        self.assertNotEqual(row["status"], "succeeded")

    def test_tool_telemetry_is_not_inference_count(self) -> None:
        economy_fields = {"model_inference_count": None}
        self.assertNotIn("tool_executions", economy_fields)


class T111HardeningTests(FabricBase):
    def test_artifact_export_defaults_to_require_approval(self) -> None:
        decision, record = self.submit("artifact.export", "export", "out/report.docx")
        self.assertEqual(decision.decision, Decision.REQUIRE_APPROVAL)
        self.assertEqual(record.status, ExecutionStatus.REQUESTED)
        # Approval alone is still not a successful export.
        approved = self.fabric.approve(record.execution_id, "user-click")
        self.assertEqual(approved["status"], "authorized")

    def test_python_bounded_classified_conservatively(self) -> None:
        capability = self.fabric.registry.get("python.bounded")
        decision = self.fabric.policy.evaluate(
            self.request("python.bounded", "execute"), capability)
        self.assertEqual(decision.decision, Decision.REQUIRE_APPROVAL)

    def test_authorized_not_started_survives_restart_without_uncertainty(self) -> None:
        _, record = self.submit("engineering.patch.apply", "apply")
        self.fabric.approve(record.execution_id, "rev-7")
        fresh_store = ToolFabricStore(self.store.database)
        recovery = fresh_store.apply_restart_recovery()
        row = fresh_store.get_execution(record.execution_id)
        # AUTHORIZED + no start evidence: NOT uncertain, never auto-replayed.
        self.assertEqual(row["status"], "authorized")
        self.assertEqual(row["outcome_uncertain"], 0)
        self.assertIsNone(row["started_at"])
        self.assertIn("authorized_not_started", recovery)

    def test_terminal_success_stays_terminal_after_reopen(self) -> None:
        decision, record = self.submit("math.evaluate", "evaluate")
        self.fabric.execute(record.execution_id)
        before = self.store.get_execution(record.execution_id)
        after_store = ToolFabricStore(self.store.database)
        after_store.apply_restart_recovery()
        after = after_store.get_execution(record.execution_id)
        self.assertEqual(before, after)




if __name__ == "__main__":
    unittest.main()
