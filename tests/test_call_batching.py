"""Unit and contract tests for Safe Call Batching Planner (AG-A3.2A)."""

from __future__ import annotations

import json
import unittest

from amca.call_batching import (
    BatchItemCandidate,
    BatchItemResult,
    BatchOutcomeStatus,
    BatchPlan,
    BatchRejectionReason,
    SafeBatchingPlanner,
    compute_batch_compatibility_key,
)


class MockBatchAdapter:
    def __init__(
        self,
        name: str = "openai_batch_adapter",
        supports_batch: bool = True,
        cost_usd: float = 0.05,
    ) -> None:
        self.name = name
        self.supports_batch_generation = supports_batch
        self.cost_usd = cost_usd


class TestSafeCallBatchingPlanner(unittest.TestCase):
    # 1. identical independent calls can form a batch
    def test_1_identical_independent_calls_form_batch(self) -> None:
        adapter = MockBatchAdapter()
        item1 = BatchItemCandidate(
            task_id="t1", agent_id="worker", prompt="Summarize page 1",
            model_id="gpt-4o", provider="openai", conservative_cost_usd=0.05,
        )
        item2 = BatchItemCandidate(
            task_id="t2", agent_id="worker", prompt="Summarize page 2",
            model_id="gpt-4o", provider="openai", conservative_cost_usd=0.05,
        )
        can_batch, reason = SafeBatchingPlanner.can_batch_pair(item1, item2, adapter)
        self.assertTrue(can_batch)
        self.assertIsNone(reason)

        plans, rejected = SafeBatchingPlanner.plan_batches([item1, item2], {"worker": adapter}, available_budget_usd=1.00)
        self.assertEqual(len(plans), 1)
        self.assertEqual(len(rejected), 0)
        self.assertEqual(plans[0].logical_task_count, 2)

    # 2. different provider -> rejected
    def test_2_different_provider_rejected(self) -> None:
        adapter = MockBatchAdapter()
        item1 = BatchItemCandidate(
            task_id="t1", agent_id="worker", prompt="p1", model_id="llama-3", provider="aws_bedrock",
        )
        item2 = BatchItemCandidate(
            task_id="t2", agent_id="worker", prompt="p2", model_id="llama-3", provider="azure_ai",
        )
        can_batch, reason = SafeBatchingPlanner.can_batch_pair(item1, item2, adapter)
        self.assertFalse(can_batch)
        self.assertEqual(reason, BatchRejectionReason.DIFFERENT_PROVIDER.value)

    # 3. different model -> rejected
    def test_3_different_model_rejected(self) -> None:
        adapter = MockBatchAdapter()
        item1 = BatchItemCandidate(
            task_id="t1", agent_id="worker", prompt="p1", model_id="gpt-4o", provider="openai",
        )
        item2 = BatchItemCandidate(
            task_id="t2", agent_id="worker", prompt="p2", model_id="gpt-4o-mini", provider="openai",
        )
        can_batch, reason = SafeBatchingPlanner.can_batch_pair(item1, item2, adapter)
        self.assertFalse(can_batch)
        self.assertEqual(reason, BatchRejectionReason.DIFFERENT_MODEL.value)

    # 4. different material inference config -> rejected
    def test_4_different_inference_config_rejected(self) -> None:
        adapter = MockBatchAdapter()
        item1 = BatchItemCandidate(
            task_id="t1", agent_id="worker", prompt="p1", model_id="gpt-4o", provider="openai",
            model_parameters={"temperature": 0.0, "top_p": 1.0},
        )
        item2 = BatchItemCandidate(
            task_id="t2", agent_id="worker", prompt="p2", model_id="gpt-4o", provider="openai",
            model_parameters={"temperature": 0.7, "top_p": 1.0},
        )
        can_batch, reason = SafeBatchingPlanner.can_batch_pair(item1, item2, adapter)
        self.assertFalse(can_batch)
        self.assertEqual(reason, BatchRejectionReason.DIFFERENT_MODEL_PARAMETERS.value)

    # 5. dependency between tasks -> rejected
    def test_5_dependency_between_tasks_rejected(self) -> None:
        adapter = MockBatchAdapter()
        item1 = BatchItemCandidate(
            task_id="t1", agent_id="worker", prompt="Extract raw numbers", model_id="gpt-4o", provider="openai",
        )
        item2 = BatchItemCandidate(
            task_id="t2", agent_id="worker", prompt="Format table using extracted numbers", model_id="gpt-4o", provider="openai",
            depends_on=("t1",),  # Depends on t1!
        )
        can_batch, reason = SafeBatchingPlanner.can_batch_pair(item1, item2, adapter)
        self.assertFalse(can_batch)
        self.assertEqual(reason, BatchRejectionReason.DEPENDENCY_BETWEEN_TASKS.value)

        plans, rejected = SafeBatchingPlanner.plan_batches([item1, item2], {"worker": adapter}, available_budget_usd=1.00)
        self.assertEqual(len(plans), 1)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0][0].task_id, "t2")
        self.assertEqual(rejected[0][1], BatchRejectionReason.DEPENDENCY_BETWEEN_TASKS.value)

    # 6. JOINT calls -> rejected
    def test_6_joint_calls_rejected(self) -> None:
        adapter = MockBatchAdapter()
        item = BatchItemCandidate(
            task_id="t_joint", agent_id="worker", prompt="Turn 1 of joint dialogue",
            model_id="gpt-4o", provider="openai", collaboration_mode="joint",
        )
        ok, reason = SafeBatchingPlanner.evaluate_candidate_eligibility(item, adapter)
        self.assertFalse(ok)
        self.assertEqual(reason, BatchRejectionReason.JOINT_COLLABORATION_EXCLUDED.value)

    # 7. Adaptive dependent phase -> rejected
    def test_7_adaptive_dependent_phase_rejected(self) -> None:
        adapter = MockBatchAdapter()
        item = BatchItemCandidate(
            task_id="t_adapt", agent_id="worker", prompt="Adaptive phase 2 evaluation",
            model_id="gpt-4o", provider="openai", collaboration_mode="adaptive_hybrid",
        )
        ok, reason = SafeBatchingPlanner.evaluate_candidate_eligibility(item, adapter)
        self.assertFalse(ok)
        self.assertEqual(reason, BatchRejectionReason.ADAPTIVE_HYBRID_EXCLUDED.value)

    # 8. Verification Cell -> rejected
    def test_8_verification_cell_rejected(self) -> None:
        adapter = MockBatchAdapter()
        item = BatchItemCandidate(
            task_id="t_vcell", agent_id="worker", prompt="Verify theorem step",
            model_id="gpt-4o", provider="openai", executor_kind="verification_cell",
        )
        ok, reason = SafeBatchingPlanner.evaluate_candidate_eligibility(item, adapter)
        self.assertFalse(ok)
        self.assertEqual(reason, BatchRejectionReason.VERIFICATION_CELL_EXCLUDED.value)

    # 9. side-effecting tool call -> rejected
    def test_9_side_effecting_tool_call_rejected(self) -> None:
        adapter = MockBatchAdapter()
        item = BatchItemCandidate(
            task_id="t_side_effect", agent_id="worker", prompt="Delete old partitions",
            model_id="gpt-4o", provider="openai", tool_side_effects=True,
        )
        ok, reason = SafeBatchingPlanner.evaluate_candidate_eligibility(item, adapter)
        self.assertFalse(ok)
        self.assertEqual(reason, BatchRejectionReason.SIDE_EFFECTING_TOOLS_EXCLUDED.value)

    # 10. adapter without batch capability -> rejected
    def test_10_adapter_without_batch_capability_rejected(self) -> None:
        adapter_no_batch = MockBatchAdapter(supports_batch=False)
        item = BatchItemCandidate(
            task_id="t1", agent_id="worker", prompt="Run task", model_id="gpt-4o", provider="openai",
        )
        ok, reason = SafeBatchingPlanner.evaluate_candidate_eligibility(item, adapter_no_batch)
        self.assertFalse(ok)
        self.assertEqual(reason, BatchRejectionReason.ADAPTER_LACKS_BATCH_CAPABILITY.value)

    # 11. authorization incompatibility -> rejected
    def test_11_authorization_incompatibility_rejected(self) -> None:
        adapter = MockBatchAdapter()
        item1 = BatchItemCandidate(
            task_id="t1", agent_id="worker", prompt="Run task", model_id="gpt-4o", provider="openai",
            envelope_id="envelope_standard",
        )
        item2 = BatchItemCandidate(
            task_id="t2", agent_id="worker", prompt="Run task", model_id="gpt-4o", provider="openai",
            envelope_id="envelope_restricted",
        )
        can_batch, reason = SafeBatchingPlanner.can_batch_pair(item1, item2, adapter)
        self.assertFalse(can_batch)
        self.assertEqual(reason, BatchRejectionReason.AUTHORIZATION_ENVELOPE_MISMATCH.value)

    # 12. budget incompatibility -> rejected
    def test_12_budget_incompatibility_rejected(self) -> None:
        adapter = MockBatchAdapter()
        items = [
            BatchItemCandidate(task_id=f"t{i}", agent_id="worker", prompt="Work", model_id="gpt-4o", provider="openai", conservative_cost_usd=0.80)
            for i in range(3)  # Total cost 2.40 USD
        ]
        # Only 1.00 USD available
        plans, rejected = SafeBatchingPlanner.plan_batches(items, {"worker": adapter}, available_budget_usd=1.00)
        self.assertEqual(len(plans), 0)
        self.assertEqual(len(rejected), 3)
        self.assertTrue(all(r[1] == BatchRejectionReason.BUDGET_INCOMPATIBLE.value for r in rejected))

    # 13. each item retains stable call identity
    def test_13_each_item_retains_stable_call_identity(self) -> None:
        item1 = BatchItemCandidate(
            task_id="task-101", agent_id="w", prompt="p1", model_id="gpt-4o", provider="openai",
            call_key="call-stable-101", reservation_id="res-101",
        )
        item2 = BatchItemCandidate(
            task_id="task-102", agent_id="w", prompt="p2", model_id="gpt-4o", provider="openai",
            call_key="call-stable-102", reservation_id="res-102",
        )
        adapter = MockBatchAdapter()
        plans, _ = SafeBatchingPlanner.plan_batches([item1, item2], {"w": adapter}, available_budget_usd=1.00)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.items[0].call_key, "call-stable-101")
        self.assertEqual(plan.items[0].reservation_id, "res-101")
        self.assertEqual(plan.items[1].call_key, "call-stable-102")
        self.assertEqual(plan.items[1].reservation_id, "res-102")

    # 14. four items preserve model-inference count = 4
    def test_14_four_items_preserve_model_inference_count_4(self) -> None:
        adapter = MockBatchAdapter()
        items = [
            BatchItemCandidate(task_id=f"t{i}", agent_id="w", prompt=f"Prompt {i}", model_id="gpt-4o", provider="openai")
            for i in range(4)
        ]
        plans, _ = SafeBatchingPlanner.plan_batches(items, {"w": adapter}, available_budget_usd=5.00)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.logical_task_count, 4)
        self.assertEqual(plan.model_inference_count, 4)

    # 15. transport request count may equal 1
    def test_15_transport_request_count_equals_1(self) -> None:
        adapter = MockBatchAdapter()
        items = [
            BatchItemCandidate(task_id=f"t{i}", agent_id="w", prompt=f"Prompt {i}", model_id="gpt-4o", provider="openai")
            for i in range(4)
        ]
        plans, _ = SafeBatchingPlanner.plan_batches(items, {"w": adapter}, available_budget_usd=5.00)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].transport_request_count, 1)

    # 16. partial outcomes map to individual calls
    def test_16_partial_outcomes_map_to_individual_calls(self) -> None:
        adapter = MockBatchAdapter()
        items = [
            BatchItemCandidate(task_id="t1", agent_id="w", prompt="p1", model_id="gpt-4o", provider="openai", conservative_cost_usd=0.05),
            BatchItemCandidate(task_id="t2", agent_id="w", prompt="p2", model_id="gpt-4o", provider="openai", conservative_cost_usd=0.05),
            BatchItemCandidate(task_id="t3", agent_id="w", prompt="p3", model_id="gpt-4o", provider="openai", conservative_cost_usd=0.05),
            BatchItemCandidate(task_id="t4", agent_id="w", prompt="p4", model_id="gpt-4o", provider="openai", conservative_cost_usd=0.05),
        ]
        plans, _ = SafeBatchingPlanner.plan_batches(items, {"w": adapter}, available_budget_usd=5.00)
        batch = plans[0]

        provider_response = {
            "t1": {"status": "success", "text": "Answer 1", "cost_usd": 0.04},
            "t2": {"status": "failure", "error": "rate limit exceeded", "cost_usd": 0.00},
            "t3": {"status": "success", "text": "Answer 3", "cost_usd": 0.05},
            "t4": {"status": "rejected", "error": "content filter", "cost_usd": 0.00},
        }
        report = SafeBatchingPlanner.process_batch_response(batch, provider_response)
        self.assertEqual(report.results["t1"].status, BatchOutcomeStatus.SUCCESS)
        self.assertEqual(report.results["t1"].output_text, "Answer 1")
        self.assertEqual(report.results["t2"].status, BatchOutcomeStatus.FAILURE)
        self.assertEqual(report.results["t2"].error, "rate limit exceeded")
        self.assertEqual(report.results["t3"].status, BatchOutcomeStatus.SUCCESS)
        self.assertEqual(report.results["t4"].status, BatchOutcomeStatus.REJECTED)
        self.assertEqual(report.total_settled_cost_usd, 0.09)

    # 17. batch failure does not fabricate per-item success
    def test_17_batch_failure_does_not_fabricate_per_item_success(self) -> None:
        adapter = MockBatchAdapter()
        items = [
            BatchItemCandidate(task_id="t1", agent_id="w", prompt="p1", model_id="gpt-4o", provider="openai"),
            BatchItemCandidate(task_id="t2", agent_id="w", prompt="p2", model_id="gpt-4o", provider="openai"),
        ]
        plans, _ = SafeBatchingPlanner.plan_batches(items, {"w": adapter}, available_budget_usd=5.00)
        batch = plans[0]

        # In case of transport failure/crash, report handles it as unknown external outcome
        report = SafeBatchingPlanner.handle_batch_crash_or_timeout(batch, "HTTP 500 internal server error")
        self.assertEqual(report.results["t1"].status, BatchOutcomeStatus.UNKNOWN_EXTERNAL_OUTCOME)
        self.assertEqual(report.results["t2"].status, BatchOutcomeStatus.UNKNOWN_EXTERNAL_OUTCOME)
        self.assertIsNone(report.results["t1"].output_text)
        self.assertIsNone(report.results["t2"].output_text)

    # 18. recovery design preserves per-item uncertainty
    def test_18_recovery_design_preserves_per_item_uncertainty(self) -> None:
        adapter = MockBatchAdapter()
        items = [
            BatchItemCandidate(task_id="t1", agent_id="w", prompt="p1", model_id="gpt-4o", provider="openai", call_key="call-t1"),
            BatchItemCandidate(task_id="t2", agent_id="w", prompt="p2", model_id="gpt-4o", provider="openai", call_key="call-t2"),
        ]
        plans, _ = SafeBatchingPlanner.plan_batches(items, {"w": adapter}, available_budget_usd=5.00)
        batch = plans[0]

        # Only item 1 was returned by provider; item 2 was dropped before crash
        provider_partial = {
            "t1": {"status": "success", "text": "result 1", "cost_usd": 0.05},
        }
        report = SafeBatchingPlanner.process_batch_response(batch, provider_partial)
        self.assertEqual(report.results["t1"].status, BatchOutcomeStatus.SUCCESS)
        self.assertEqual(report.results["t2"].status, BatchOutcomeStatus.UNKNOWN_EXTERNAL_OUTCOME)
        self.assertEqual(report.results["t2"].call_key, "call-t2")

    # 19. key deterministic after restart
    def test_19_key_deterministic_after_restart(self) -> None:
        candidate_run_1 = BatchItemCandidate(
            task_id="t1", agent_id="w", prompt="Run query", model_id="claude-3-5-sonnet", provider="anthropic",
            model_parameters={"temperature": 0.1, "max_tokens": 1000},
            tool_config={"allowed": ["grep", "read_file"]},
            output_schema={"type": "object"},
            envelope_id="env-standard",
        )
        candidate_run_2 = BatchItemCandidate(
            task_id="t2_other_run", agent_id="w", prompt="Different prompt but same execution profile",
            model_id="claude-3-5-sonnet", provider="anthropic",
            model_parameters={"max_tokens": 1000, "temperature": 0.1},  # reversed dict order
            tool_config={"allowed": ["grep", "read_file"]},
            output_schema={"type": "object"},
            envelope_id="env-standard",
        )
        key1 = compute_batch_compatibility_key(candidate_run_1)
        key2 = compute_batch_compatibility_key(candidate_run_2)
        self.assertEqual(key1, key2)

    # 20. credentials absent from compatibility keys
    def test_20_credentials_absent_from_compatibility_keys(self) -> None:
        secret_token = "sk-supersecret-api-key-12345"
        password = "admin_password_xyz"
        candidate = BatchItemCandidate(
            task_id="t1", agent_id="w", prompt="Run query", model_id="gpt-4o", provider="openai",
            model_parameters={"temperature": 0.1, "api_key": secret_token, "auth_token": secret_token},
            tool_config={"tool_password": password, "tool_name": "curl"},
        )
        key = compute_batch_compatibility_key(candidate)
        self.assertNotIn(secret_token, key)
        self.assertNotIn(password, key)
        self.assertTrue(key.startswith("batch_key:"))


if __name__ == "__main__":
    unittest.main()
