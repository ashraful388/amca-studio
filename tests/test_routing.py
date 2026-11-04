from __future__ import annotations

import unittest

from amca.domain import (
    ComputePolicy,
    CostState,
    GoalContract,
    PrivacyMode,
    ProjectState,
)
from amca.local_runtime import (
    LocalModelMeasurement,
    LocalModelRecord,
    LocalRuntimeSnapshot,
)
from amca.models.scripted import ScriptedAdapter
from amca.routing import CostLatencyController, ModelRouter, route_project


class RoutingTests(unittest.TestCase):
    def test_complex_science_goal_uses_joint_mode(self) -> None:
        state = ProjectState(
            project_id="p",
            goal="Derive and prove a complex scientific hypothesis",
            privacy_mode=PrivacyMode.FULL_FRONTIER,
        )
        decision = route_project(
            state,
            [
                ScriptedAdapter(["a"], adapter_id="one"),
                ScriptedAdapter(["b"], adapter_id="two"),
            ],
        )
        self.assertEqual(decision.collaboration_mode.value, "joint")
        self.assertGreaterEqual(len(decision.selected_adapter_ids), 2)

    def test_offline_policy_blocks_cloud_adapter(self) -> None:
        state = ProjectState(
            project_id="p",
            goal="test",
            privacy_mode=PrivacyMode.OFFLINE,
            goal_contract=GoalContract(goal="test", max_api_cost_usd=1.0),
        )
        cloud = ScriptedAdapter(["x"], adapter_id="cloud", provider="cloud")
        admission = CostLatencyController().admit(
            state, cloud, estimated_input_tokens=100, estimated_output_tokens=100
        )
        self.assertFalse(admission.allowed)
        self.assertIn("Offline", admission.reason)

    def test_local_calls_do_not_consume_economy_frontier_call_allowance(self) -> None:
        state = ProjectState(
            project_id="p",
            goal="test",
            privacy_mode=PrivacyMode.FULL_FRONTIER,
            compute_policy=ComputePolicy.ECONOMY,
        )
        state.cost_state.record_usage(calls=5, local_calls=5)
        cloud = ScriptedAdapter(["x"], adapter_id="cloud", provider="cloud")
        controller = CostLatencyController()

        self.assertTrue(
            controller.admit(
                state, cloud, estimated_input_tokens=10, estimated_output_tokens=10
            ).allowed
        )
        state.cost_state.record_usage(calls=1, api_calls=1)
        self.assertFalse(
            controller.admit(
                state, cloud, estimated_input_tokens=10, estimated_output_tokens=10
            ).allowed
        )

    def test_private_hybrid_rejects_unguarded_cloud_adapter(self) -> None:
        state = ProjectState(
            project_id="p",
            goal="private task",
            privacy_mode=PrivacyMode.PRIVATE_HYBRID,
        )
        cloud = ScriptedAdapter(["x"], adapter_id="cloud", provider="cloud")

        admission = CostLatencyController().admit(
            state, cloud, estimated_input_tokens=10, estimated_output_tokens=10
        )

        self.assertFalse(admission.allowed)
        self.assertIn("approved excerpt", admission.reason)

    def test_desktop_telemetry_baseline_resets_per_run_not_per_call(self) -> None:
        """The run baseline is snapshotted once and consumed within the run.

        After start_run on a desktop telemetry contract with exhausted lifetime
        calls, the fresh allowance matches the policy limit, is consumed by
        admitted cloud calls, hits zero, and a new run() call restores it.  A
        non-telemetry state keeps the lifetime comparison even after start_run.
        """
        state = ProjectState(
            project_id="p",
            goal="desktop chat",
            privacy_mode=PrivacyMode.FULL_FRONTIER,
            compute_policy=ComputePolicy.BALANCED,
            goal_contract=GoalContract(goal="desktop chat"),
            cost_state=CostState(api_model_calls=2),
        )
        state.goal_contract.budget_is_telemetry = True
        cloud = ScriptedAdapter(["x"], adapter_id="cloud", provider="cloud")
        controller = CostLatencyController()

        # Before start_run the inherited lifetime ledger still blocks.
        self.assertFalse(
            controller.admit(
                state, cloud, estimated_input_tokens=10, estimated_output_tokens=10
            ).allowed
        )

        controller.start_run(state)
        self.assertEqual(controller.remaining_frontier_calls(state), 2)
        state.cost_state.record_usage(calls=1, api_calls=1)
        self.assertEqual(controller.remaining_frontier_calls(state), 1)
        self.assertTrue(
            controller.admit(
                state, cloud, estimated_input_tokens=10, estimated_output_tokens=10
            ).allowed
        )
        state.cost_state.record_usage(calls=1, api_calls=1)
        self.assertEqual(controller.remaining_frontier_calls(state), 0)
        self.assertFalse(
            controller.admit(
                state, cloud, estimated_input_tokens=10, estimated_output_tokens=10
            ).allowed
        )

        # A fresh run re-snapshots the (grown) lifetime ledger.
        controller.start_run(state)
        self.assertEqual(controller.remaining_frontier_calls(state), 2)
        # Lifetime telemetry was never erased.
        self.assertEqual(state.cost_state.api_model_calls, 4)

        # Non-telemetry contracts ignore the baseline entirely.
        state.goal_contract.budget_is_telemetry = False
        controller.start_run(state)
        self.assertEqual(controller.remaining_frontier_calls(state), 0)


    def test_local_runtime_inventory_and_current_measurement_drive_selection(self) -> None:
        state = ProjectState(
            project_id="p",
            goal="private local task",
            privacy_mode=PrivacyMode.FULL_FRONTIER,
            compute_policy=ComputePolicy.ECONOMY,
        )
        adapters = (
            ScriptedAdapter(
                ["x"], adapter_id="slow", provider="ollama", model="slow", is_local=True
            ),
            ScriptedAdapter(
                ["x"], adapter_id="missing", provider="ollama", model="missing", is_local=True
            ),
            ScriptedAdapter(
                ["x"], adapter_id="fast", provider="ollama", model="fast", is_local=True
            ),
            ScriptedAdapter(["x"], adapter_id="cloud", provider="cloud"),
        )
        snapshot = LocalRuntimeSnapshot(
            hardware_profile=None,
            hardware_profile_sha256="h" * 64,
            models=(
                LocalModelRecord("ollama", "slow", "a", 100),
                LocalModelRecord("ollama", "fast", "b", 200),
            ),
            measurements=(
                LocalModelMeasurement(
                    id="m",
                    provider="ollama",
                    model="fast",
                    hardware_profile_sha256="h" * 64,
                    prompt_sha256="p" * 64,
                    started_at="2026-01-01T00:00:00Z",
                    completed_at="2026-01-01T00:00:01Z",
                    wall_seconds=1,
                    prompt_tokens=1,
                    output_tokens=20,
                    tokens_per_second=20,
                    success=True,
                ),
            ),
            inventory_providers=("ollama",),
        )

        selected = ModelRouter().select(state, adapters, maximum=4, local_runtime=snapshot)

        self.assertEqual([adapter.adapter_id for adapter in selected], ["fast", "slow", "cloud"])


if __name__ == "__main__":
    unittest.main()
