"""AMCA Studio v0.8.0 Runtime Observability & Recovery Test Suite (AG-AMCA-STUDIO-V0.8.0).

Validates:
1. Runtime session snapshots capture facts only (no quality scores, no rankings)
2. Append-only event stream ordering and sequence integrity
3. Explicit recovery state transitions (HEALTHY, DEGRADED, FAILED, AWAITING_RECOVERY, RECOVERED)
4. Component failure boundary capture (records facts and metadata without auto-repair)
5. Zero hidden reasoning / chain-of-thought persistence
6. Architectural boundaries and decoupling preserved
"""

import inspect
from pathlib import Path
import tempfile
import unittest

from amca.cognitive_loop_orchestrator import (
    CognitiveWorkflowOrchestrator,
    CognitiveWorkflowStore,
)
from amca.cognitive_runtime_bridge import (
    CognitiveRuntimeBridge,
    CognitiveRuntimeBridgeStore,
)
from amca.cognitive_runtime_observability import (
    CognitiveRuntimeObservabilityStore,
    CognitiveRuntimeObserver,
    RuntimeEventStreamItem,
    RuntimeFailureRecord,
    RuntimeSessionSnapshot,
)


class StudioV080RuntimeObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp_dir.name)

        self.wf_store = CognitiveWorkflowStore(self.tmp_path / "wf.sqlite3")
        self.bridge_store = CognitiveRuntimeBridgeStore(self.tmp_path / "bridge.sqlite3")
        self.obs_store = CognitiveRuntimeObservabilityStore(self.tmp_path / "obs.sqlite3")

        self.orchestrator = CognitiveWorkflowOrchestrator(store=self.wf_store)
        self.bridge = CognitiveRuntimeBridge(
            store=self.bridge_store,
            orchestrator=self.orchestrator,
        )
        self.observer = CognitiveRuntimeObserver(
            store=self.obs_store,
            bridge_store=self.bridge_store,
        )

        # Seed planned workflow
        self.wf = self.orchestrator.initialize_workflow(
            workspace_id="ws-obs",
            raw_goal="Train medical diagnostic model",
            structured_intent="TRAIN_MODEL",
        )
        self.wf = self.orchestrator.attach_plan(
            workflow_id=self.wf.workflow_id,
            workspace_id="ws-obs",
            planned_steps=("load_data", "train", "validate"),
            required_capabilities=("gpu", "ml"),
        )
        self.wf = self.orchestrator.select_resources(
            workflow_id=self.wf.workflow_id,
            workspace_id="ws-obs",
            selected_models=("med-llama-8b-local",),
            selected_agents=("trainer",),
            provider_health_references=("ollama:HEALTHY",),
            decision_reason="HIPAA offline requirement",
        )
        self.session = self.bridge.create_runtime_session(
            workflow_id=self.wf.workflow_id,
            workspace_id="ws-obs",
            execution_mode="solo",
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_session_snapshots_capture_facts_only(self) -> None:
        """Verify snapshot captures execution facts with zero subjective rankings or AI scores."""
        snapshot = self.observer.capture_session_snapshot(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
        )
        self.assertEqual(snapshot.session_id, self.session.session_id)
        self.assertEqual(snapshot.workflow_id, self.wf.workflow_id)
        self.assertEqual(snapshot.status, "INITIALIZED")
        self.assertEqual(snapshot.recovery_state, "HEALTHY")
        self.assertEqual(snapshot.execution_mode, "solo")
        self.assertEqual(snapshot.participating_models, ("med-llama-8b-local",))
        self.assertEqual(snapshot.participating_agents, ("trainer",))
        self.assertFalse(snapshot.governance_passed)
        self.assertEqual(snapshot.failure_count, 0)

        # Invariant: Snapshot contains NO subjective rating fields
        snap_dict = snapshot.to_dict()
        for forbidden in ("score", "quality", "rating", "rank", "efficiency"):
            self.assertNotIn(forbidden, snap_dict)

    def test_event_stream_ordering_and_append_only(self) -> None:
        """Verify events are assigned strict monotonic sequence numbers."""
        e1 = self.observer.emit_event(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
            event_type="DATA_PREPARED",
            payload={"batch_size": 32},
        )
        e2 = self.observer.emit_event(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
            event_type="TRAINING_STEP_1",
            payload={"epoch": 1},
        )
        e3 = self.observer.emit_event(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
            event_type="TRAINING_STEP_2",
            payload={"epoch": 2},
        )

        self.assertEqual(e1.seq_num, 0)
        self.assertEqual(e2.seq_num, 1)
        self.assertEqual(e3.seq_num, 2)

        events = self.obs_store.list_events(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
        )
        self.assertEqual(len(events), 3)
        self.assertEqual([e.event_type for e in events], ["DATA_PREPARED", "TRAINING_STEP_1", "TRAINING_STEP_2"])

    def test_recovery_state_transitions(self) -> None:
        """Verify explicit recovery state tracking without autonomous decision logic."""
        # Initial is HEALTHY
        state0 = self.obs_store.get_recovery_state(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
        )
        self.assertEqual(state0, "HEALTHY")

        # Explicit transition to DEGRADED
        self.observer.transition_recovery_state(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
            target_state="DEGRADED",
            reason="High memory pressure detected",
        )
        state1 = self.obs_store.get_recovery_state(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
        )
        self.assertEqual(state1, "DEGRADED")

        # Explicit transition to AWAITING_RECOVERY
        self.observer.transition_recovery_state(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
            target_state="AWAITING_RECOVERY",
            reason="Worker paused for operator checkpoint",
        )
        state2 = self.obs_store.get_recovery_state(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
        )
        self.assertEqual(state2, "AWAITING_RECOVERY")

    def test_component_failure_boundary_recording(self) -> None:
        """Verify component failures record metadata and transition recovery state to FAILED."""
        fail = self.observer.record_component_failure(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
            failed_component="ollama_local",
            error_code="CUDA_OUT_OF_MEMORY",
            error_message="CUDA out of memory allocating 4GB tensor",
            error_metadata={"vram_free_mb": 128, "vram_required_mb": 4096},
        )
        self.assertIsNotNone(fail.failure_id)
        self.assertEqual(fail.failed_component, "ollama_local")
        self.assertEqual(fail.error_code, "CUDA_OUT_OF_MEMORY")

        # Recovery state updated
        state = self.obs_store.get_recovery_state(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
        )
        self.assertEqual(state, "FAILED")

        # Audit event emitted
        events = self.obs_store.list_events(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "COMPONENT_FAILURE_DETECTED")

    def test_anti_reasoning_invariant_in_events_and_failures(self) -> None:
        """Verify chain-of-thought and hidden reasoning keys are stripped from payloads."""
        event = self.observer.emit_event(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
            event_type="CHECKPOINT",
            payload={
                "batch": 100,
                "chain_of_thought": "Private deliberation details",
                "hidden_reasoning": "Should not be persisted",
                "private_deliberation": "Secret",
            },
        )
        events = self.obs_store.list_events(
            session_id=self.session.session_id,
            workspace_id="ws-obs",
        )
        saved_payload = events[0].payload
        self.assertEqual(saved_payload["batch"], 100)
        self.assertNotIn("chain_of_thought", saved_payload)
        self.assertNotIn("hidden_reasoning", saved_payload)
        self.assertNotIn("private_deliberation", saved_payload)

    def test_architectural_decoupling(self) -> None:
        """Verify Model Router and Orchestrator do not depend on observer."""
        import amca.intelligence.cross_provider_router as cpr
        source_cpr = inspect.getsource(cpr)
        self.assertNotIn("CognitiveRuntimeObserver", source_cpr)

        import amca.cognitive_loop_orchestrator as clo
        source_clo = inspect.getsource(clo)
        self.assertNotIn("CognitiveRuntimeObserver", source_clo)


if __name__ == "__main__":
    unittest.main()

