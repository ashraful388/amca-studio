"""v0.6.0 M0 Main Chat runtime-integration tests.

Work order ZC-AMCA-V0.6.0-M0-MAIN-CHAT-RUNTIME-INTEGRATION-01: the normal
Studio Main Chat user path (POST /api/chat-runs) must expose every existing
collaboration mode, route gated Adaptive/Hybrid intelligence through the
bridge when (and only when) the secure gates are enabled, and keep every
existing safety property: deterministic fallback authority, end-to-end
cancellation, late-result rejection, user-safe failures, gate restart
clearance, and an easy default chat.

All provider behavior is faked locally; no paid or cloud call exists on any
tested path.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request, urlopen

from amca.domain import CollaborationMode, ComputePolicy, PrivacyMode, ProjectState
from amca.intelligence import (
    ContractError,
    ContractMetadata,
    IntelligenceRuntimeBridge,
    LocalReadinessService,
    Sensitivity,
)
from amca.intelligence.model_planner import PLANNER_RESPONSE_FORMAT
from amca.local_runtime import LocalModelRecord
from amca.models import ModelResponse
from amca.models.base import (
    ModelAdapter,
    ModelCapabilities,
    StreamChunk,
    TokenUsage,
)
from amca.service import create_server
from amca.storage import SQLiteStore

CREATED = "2026-08-27T00:00:00+00:00"
PREFERRED = "qwen3:1.7b"
PLANNER_KEY_PREFIX = "m1.2:"

VALID_PLANNER_JSON = json.dumps(
    {
        "response_version": 1,
        "policy_id": "amca-desktop-chat",
        "policy_version": "1",
        "recommendations": [
            {
                "candidate_id": "chat-cand-0",
                "model_id": PREFERRED,
                "confidence": 0.9,
                "reason_code": "bounded_local_choice",
            }
        ],
    }
)


def _meta(record_id):
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-v060",
        producer="v0.6.0-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def _local_record(name):
    return LocalModelRecord(
        provider="ollama",
        model=name,
        digest=f"digest-{name}",
        size_bytes=1024,
        modified_at=None,
        family="qwen",
        parameter_size="1.7b",
        quantization_level="q4",
        model_format="gguf",
        observed_at=CREATED,
    )


def _inventory_fn(models=()):
    def _read(base_url, timeout=2.0):
        return tuple(_local_record(name) for name in models)

    return _read


class _FakeLocalAdapter(ModelAdapter):
    """Local adapter fake: structured planner JSON for planner calls, canned
    chat text otherwise. Optional delay, failure injection, and a
    late-completing mode that answers anyway after cancellation.

    Implements the real dual protocol: the planner runtime drives
    ``generate()`` while the collaboration kernel streams via ``stream()``.
    """

    def __init__(
        self,
        *,
        adapter_id,
        model=PREFERRED,
        chat_text='{"summary": "Bounded local analysis complete.", "events": []}',
        planner_text=VALID_PLANNER_JSON,
        delay=0.0,
        fail=False,
        late_after_cancel=False,
    ):
        super().__init__(
            adapter_id=adapter_id,
            provider="ollama",
            model=model,
            capabilities=ModelCapabilities(is_local=True),
        )
        self._chat_text = chat_text
        self._planner_text = planner_text
        self._delay = delay
        self._fail = fail
        self._late_after_cancel = late_after_cancel
        self.cancel_invocations = 0
        self.planner_requests = 0

    def _is_planner(self, request):
        # The planner runtime rebuilds the adapter-level request, so match on
        # prompt content: only the M1.2 planner prompt renders candidate
        # descriptors containing "candidate_id".
        messages = getattr(request, "messages", ()) or ()
        first = messages[0] if messages else ""
        if isinstance(first, dict):
            first = str(first.get("content") or "")
        return "candidate_id" in str(first)

    def _text_for(self, request):
        if self._is_planner(request):
            return self._planner_text
        return self._chat_text

    async def _advance(self, request):
        if self._delay:
            try:
                await asyncio.sleep(self._delay)
            except asyncio.CancelledError:
                if not self._late_after_cancel:
                    raise
        if self._fail:
            raise RuntimeError("connection refused (fake provider down)")
        if self._is_planner(request):
            self.planner_requests += 1

    async def generate(self, request):
        async with self._call_scope(request):
            await self._advance(request)
            return ModelResponse(
                text=self._text_for(request),
                model=self.model,
                provider="ollama",
                usage=TokenUsage(),
                finish_reason="stop",
            )

    async def stream(self, request):
        async with self._call_scope(request):
            await self._advance(request)
            text = self._text_for(request)
            yield StreamChunk(delta=text, model=self.model, provider="ollama")
            yield StreamChunk(
                done=True,
                response=ModelResponse(
                    text=text,
                    model=self.model,
                    provider="ollama",
                    usage=TokenUsage(),
                    finish_reason="stop",
                ),
            )

    async def cancel(self, request_id=None):
        self.cancel_invocations += 1
        return await super().cancel(request_id)


class _RecorderCoordinator:
    """Records the exact kwargs a hybrid session receives."""

    def __init__(self):
        self.kwargs = None

    def run(self, **kwargs):
        self.kwargs = kwargs
        from amca.intelligence import HybridDecisionPolicy, HybridSessionState

        return SimpleNamespace(
            state=HybridSessionState.COMPLETED,
            decision=HybridDecisionPolicy.ADAPTIVE_PROPOSAL,
            result=None,
            fallback_reason=None,
        )


class MainChatIntegrationTests(unittest.TestCase):
    """Required test cases 1-14 exercised through the real HTTP surface."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"
        self.server = None
        self.thread = None
        self._start_server()
        context = self.server.RequestHandlerClass.context
        inventory = {
            "provider": "ollama",
            "refreshed": True,
            "runtime_reachable": True,
            "models": [
                {"model": PREFERRED, "fit": 0},
                {"model": "local-b:1b", "fit": 0},
            ],
        }
        patcher = patch.object(
            type(context), "local_models", return_value=inventory
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._stop_server()
        self.temporary.cleanup()

    def _start_server(self) -> None:
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=Path(__file__).resolve().parents[1],
            database=self.database,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.token = str(self._json("/api/session")["session_token"])

    def _stop_server(self) -> None:
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = None
        self.thread = None

    def _json(self, path, *, method="GET", body=None, authorized=False):
        headers = {"Origin": "http://localhost:1420"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authorized:
            headers["X-AMCA-Session"] = self.token
        request = Request(self.base + path, method=method, data=data, headers=headers)
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    def _context(self):
        return self.server.RequestHandlerClass.context

    def _enable_gates(self) -> None:
        context = self._context()
        service = LocalReadinessService(
            inventory_fn=_inventory_fn((PREFERRED,)),
            preferred_model=PREFERRED,
            meta_factory=_meta,
        )
        context.intelligence_runtime.attach_local_readiness(service)
        context.intelligence_runtime.refresh_local_readiness(
            structured_probe=True,
            # The readiness probe validates a structured {"ok": true} reply.
            adapter_factory=lambda model: _FakeLocalAdapter(
                adapter_id=f"probe-{model}",
                model=model,
                chat_text='{"ok": true}',
            ),
        )
        context.intelligence_runtime.configure_gates(
            {"adaptive_planner": True}, contract_version=1,
            correlation_id="v060-adaptive",
        )
        context.intelligence_runtime.configure_gates(
            {"hybrid_execution": True}, contract_version=1,
            correlation_id="v060-hybrid",
        )

    def _start_chat(self, *, goal, mode, participants, fake_kwargs=None,
                    selection_policy="manual_model_set"):
        """POST /api/chat-runs with every ollama: participant resolved to a
        fresh fake local adapter; returns (started, fakes_by_adapter_id)."""
        fakes: dict[str, _FakeLocalAdapter] = {}
        fake_kwargs = dict(fake_kwargs or {})

        def fake_adapter_factory(model, adapter_id, **service_kwargs):
            fake = _FakeLocalAdapter(
                adapter_id=adapter_id, model=model, **fake_kwargs
            )
            fakes[adapter_id] = fake
            return fake

        body = {"goal": goal, "mode": mode, "participant_ids": participants}
        if selection_policy is not None:
            body["selection_policy"] = selection_policy
        with patch(
            "amca.service.OllamaAdapter", side_effect=fake_adapter_factory
        ):
            started = self._json(
                "/api/chat-runs", method="POST", authorized=True, body=body
            )
        return started, fakes

    def _await_terminal(self, run_id, deadline_seconds=25):
        deadline = time.monotonic() + deadline_seconds
        current = self._json(f"/api/runs/{run_id}", authorized=True)
        while current["status"] in {"queued", "running", "cancelling"} and (
            time.monotonic() < deadline
        ):
            time.sleep(0.05)
            current = self._json(f"/api/runs/{run_id}", authorized=True)
        return current

    def _wait_running(self, run_id, deadline_seconds=10):
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            if self._json(f"/api/runs/{run_id}", authorized=True)["status"] in {
                "running", "cancelling"
            }:
                return
            time.sleep(0.05)

    # -- 1. Solo Main Chat run -------------------------------------------------

    def test_1_solo_chat_run_completes(self):
        started, _ = self._start_chat(
            goal="Summarize the bounded integration evidence.",
            mode="solo",
            participants=[f"ollama:{PREFERRED}"],
        )
        self.assertNotEqual(started.get("status"), "waiting_for_approval")
        current = self._await_terminal(str(started["run_id"]))
        self.assertIn(current["status"], {"succeeded", "partial"}, current)
        self.assertEqual(current["mode"], "solo")
        snapshot = current["intelligence"]
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["contract_version"], 1)
        self.assertEqual(snapshot["resolved_mode"], "solo")
        self.assertFalse(snapshot["gates"]["adaptive_planner"])
        self.assertEqual(snapshot["planner_participation"], "not_applicable")

    # -- 2. Joint mode ----------------------------------------------------------

    def test_2_joint_mode_chat_run_completes(self):
        started, _ = self._start_chat(
            goal="Compare two bounded local analyses.",
            mode="joint",
            participants=[f"ollama:{PREFERRED}", "ollama:local-b:1b"],
        )
        current = self._await_terminal(str(started["run_id"]))
        self.assertIn(current["status"], {"succeeded", "partial"}, current)
        self.assertEqual(current["mode"], "joint")

    # -- 3. Independent -> Discuss ----------------------------------------------

    def test_3_independent_discuss_mode_lock(self):
        started, _ = self._start_chat(
            goal="Verify both claims independently before discussing.",
            mode="independent_discuss",
            participants=[f"ollama:{PREFERRED}", "ollama:local-b:1b"],
        )
        current = self._await_terminal(str(started["run_id"]))
        self.assertIn(current["status"], {"succeeded", "partial"}, current)
        self.assertEqual(current["mode"], "independent_discuss")

    # -- 4. Parallel mode ---------------------------------------------------------

    def test_4_parallel_mode_lock(self):
        started, _ = self._start_chat(
            goal="Research sources then analyze the data.",
            mode="parallel",
            participants=[f"ollama:{PREFERRED}", "ollama:local-b:1b"],
        )
        current = self._await_terminal(str(started["run_id"]))
        self.assertIn(current["status"], {"succeeded", "partial"}, current)
        self.assertEqual(current["mode"], "parallel")

    # -- 5. Adaptive Hybrid enabled + ready --------------------------------------

    def test_5_adaptive_hybrid_enabled_routes_through_gated_planner(self):
        self._enable_gates()
        started, fakes = self._start_chat(
            goal="Plan and answer with the adaptive hybrid path.",
            mode="adaptive_hybrid",
            participants=[f"ollama:{PREFERRED}", "ollama:local-b:1b"],
        )
        current = self._await_terminal(str(started["run_id"]))
        self.assertIn(current["status"], {"succeeded", "partial"}, current)
        snapshot = current["intelligence"]
        self.assertEqual(snapshot["planner_participation"], "model_backed")
        hybrid = snapshot["hybrid"]
        self.assertIsNotNone(hybrid)
        self.assertEqual(hybrid["decision"], "adaptive_proposal")
        self.assertIsNone(hybrid["error"])
        self.assertEqual(hybrid["mode"], "joint")
        # The gated planner consulted the real adapter protocol.
        self.assertGreaterEqual(
            sum(fake.planner_requests for fake in fakes.values()), 1
        )
        # The audited supervisor received the accepted proposal as the
        # phase-0 manual instruction and recorded it truthfully.
        mode_trace = (current.get("result") or {}).get("mode_trace") or []
        self.assertTrue(mode_trace)
        self.assertEqual(mode_trace[0]["policy_rule"], "user.manual_transition")
        self.assertEqual(mode_trace[0]["manual_instruction"], "joint")

    # -- 6. Adaptive Hybrid gate OFF ----------------------------------------------

    def test_6_adaptive_hybrid_gate_off_deterministic_authoritative(self):
        started, _ = self._start_chat(
            goal="Plan and answer without experimental gates.",
            mode="adaptive_hybrid",
            participants=[f"ollama:{PREFERRED}", "ollama:local-b:1b"],
        )
        current = self._await_terminal(str(started["run_id"]))
        self.assertIn(current["status"], {"succeeded", "partial"}, current)
        snapshot = current["intelligence"]
        self.assertEqual(snapshot["planner_participation"], "deterministic")
        self.assertIsNone(snapshot["hybrid"])
        self.assertIn("OFF", str(snapshot.get("hybrid_skip_reason")))

    # -- 7. Unavailable model -------------------------------------------------------

    def _save_project(self, project_id: str, goal: str) -> None:
        state = ProjectState(
            project_id=project_id,
            goal=goal,
            privacy_mode=PrivacyMode.OFFLINE,
            compute_policy=ComputePolicy.ECONOMY,
        )
        with SQLiteStore(self._context().database) as store:
            store.save_state(state)

    def test_7_unavailable_model_fails_user_safe(self):
        context = self._context()
        self._save_project("desktop-unavailable-model", "Answer with the local model.")
        conversation = context.conversations.create(
            mode="solo", participant_ids=[PREFERRED]
        )
        failing = _FakeLocalAdapter(adapter_id="down-a", fail=True)
        started = context.runs.start_chat(
            goal="Answer with the local model.",
            project_id="desktop-unavailable-model",
            adapters=[failing],
            mode=CollaborationMode.SOLO,
            privacy=PrivacyMode.OFFLINE,
            conversation_id=str(conversation["conversation_id"]),
        )
        deadline = time.monotonic() + 15
        current = started
        while current["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.02)
            current = context.runs.get(str(started["run_id"])) or current
        self.assertEqual(current["status"], "failed", current)
        self.assertIn("connection refused", str(current["error"]))
        self.assertNotIn("Traceback", str(current["error"]))
        saved = context.conversations.get(str(conversation["conversation_id"]))
        self.assertTrue(saved["messages"])
        self.assertNotIn("Traceback", saved["messages"][-1]["content"])

    # -- 8. One-model failure inside a multi-model run ------------------------------

    def test_8_one_model_failure_inside_multi_model_run_is_graceful(self):
        context = self._context()
        self._save_project(
            "desktop-partial-failure", "Compare two bounded local analyses."
        )
        conversation = context.conversations.create(
            mode="joint", participant_ids=[PREFERRED, "local-b:1b"]
        )
        failing = _FakeLocalAdapter(adapter_id="down-a", fail=True)
        healthy = _FakeLocalAdapter(
            adapter_id="ok-b", chat_text='{"summary": "Healthy bounded contribution.", "events": []}'
        )
        started = context.runs.start_chat(
            goal="Compare two bounded local analyses.",
            project_id="desktop-partial-failure",
            adapters=[failing, healthy],
            mode=CollaborationMode.JOINT,
            privacy=PrivacyMode.OFFLINE,
            conversation_id=str(conversation["conversation_id"]),
        )
        deadline = time.monotonic() + 20
        current = started
        while current["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.02)
            current = context.runs.get(str(started["run_id"])) or current
        self.assertIn(current["status"], {"succeeded", "partial", "failed"})
        self.assertNotIn("Traceback", str(current["error"]))
        self.assertTrue(
            any(
                "Healthy bounded contribution" in str(call.get("summary") or "")
                for call in (current.get("result") or {}).get("calls", [])
            )
        )

    # -- 9. Cancellation end-to-end ---------------------------------------------------

    def test_9_cancellation_reaches_the_authoritative_chain(self):
        started, _ = self._start_chat(
            goal="Long running bounded analysis.",
            mode="solo",
            participants=[f"ollama:{PREFERRED}"],
            fake_kwargs={"delay": 30.0},
        )
        run_id = str(started["run_id"])
        self._wait_running(run_id)
        cancelled = self._json(
            f"/api/runs/{run_id}/cancel", method="POST", authorized=True, body={}
        )
        self.assertIn(cancelled["status"], {"cancelling", "cancelled"})
        current = self._await_terminal(run_id)
        self.assertEqual(current["status"], "cancelled", current)

    # -- 10. Late-result rejection ------------------------------------------------------

    def test_10_late_result_rejected_after_cancel(self):
        late_marker = "LATE-ANSWER-MARKER"
        started, _ = self._start_chat(
            goal="Answer after a slow bounded call.",
            mode="solo",
            participants=[f"ollama:{PREFERRED}"],
            fake_kwargs={
                # H2A: a wide cancel window — under full-suite load the HTTP
                # round trips can approach 1.5s, making this race flaky.
                "delay": 8.0,
                "late_after_cancel": True,
                "chat_text": json.dumps({"summary": late_marker, "events": []}),
            },
        )
        run_id = str(started["run_id"])
        self._wait_running(run_id)
        self._json(f"/api/runs/{run_id}/cancel", method="POST", authorized=True, body={})
        current = self._await_terminal(run_id, deadline_seconds=15)
        self.assertEqual(current["status"], "cancelled", current)
        saved = self._context().conversations.get(str(current["conversation_id"]))
        joined = "\n".join(
            str(message.get("content") or "") for message in saved["messages"]
        )
        self.assertNotIn(late_marker, joined)

    # -- 11. Persistence of the final response --------------------------------------------

    def test_11_final_response_is_persisted(self):
        started, _ = self._start_chat(
            goal="Persist this bounded answer.",
            mode="solo",
            participants=[f"ollama:{PREFERRED}"],
        )
        current = self._await_terminal(str(started["run_id"]))
        self.assertIn(current["status"], {"succeeded", "partial"})
        saved = self._context().conversations.get(str(current["conversation_id"]))
        self.assertTrue(saved["messages"])
        final = saved["messages"][-1]
        self.assertEqual(final["role"], "assistant")
        self.assertTrue(str(final["content"]).strip())

    # -- 12. Planner failure falls back deterministically ---------------------------------

    def test_12_planner_failure_falls_back_deterministically(self):
        self._enable_gates()
        started, _ = self._start_chat(
            goal="Planner output will be malformed; run must still answer.",
            mode="adaptive_hybrid",
            participants=[f"ollama:{PREFERRED}", "ollama:local-b:1b"],
            fake_kwargs={"planner_text": "{not valid json"},
        )
        current = self._await_terminal(str(started["run_id"]))
        self.assertIn(current["status"], {"succeeded", "partial"}, current)
        snapshot = current["intelligence"]
        self.assertEqual(snapshot["planner_participation"], "model_backed")
        hybrid = snapshot["hybrid"]
        self.assertIsNotNone(hybrid)
        self.assertEqual(hybrid["decision"], "deterministic_fallback")
        self.assertTrue(hybrid["fallback_reason"] or hybrid["error"])
        # The kernel still answered through the deterministic path.
        self.assertTrue(
            (current.get("result") or {}).get("calls")
        )

    # -- 13. Restart returns all experimental gates OFF ------------------------------------

    def test_13_a_users_gate_choice_persists_across_a_restart(self):
        """v1.1.8: an explicit choice survives a restart.

        This test previously asserted that a restart returned every gate to OFF.
        That encoded a session-scoped design which the v1.1.8 plan replaces with
        "Auto settings persistence".  The security half of the property is
        unchanged and is asserted separately below and in
        ``test_13b_a_fresh_install_still_starts_with_every_gate_off``: the
        SYSTEM never auto-enables a gate.  Only a choice the user made is
        restored.
        """
        self._enable_gates()
        capabilities = self._json("/api/intelligence/status", authorized=True)[
            "capabilities"
        ]
        self.assertTrue(capabilities["adaptive_planner"]["enabled"])
        self.assertTrue(capabilities["hybrid_execution"]["enabled"])
        self._stop_server()
        self._start_server()
        restored = self._json("/api/intelligence/status", authorized=True)[
            "capabilities"
        ]
        self.assertTrue(
            restored["adaptive_planner"]["enabled"],
            "a gate the user enabled must survive a restart",
        )
        self.assertTrue(restored["hybrid_execution"]["enabled"])
        # A gate the user never touched is still off.
        self.assertFalse(restored["model_router"]["enabled"])

    def test_13b_a_fresh_install_still_starts_with_every_gate_off(self):
        """The security property: nothing auto-enables."""
        self._stop_server()
        self.database = Path(self.temporary.name) / "fresh-install.sqlite3"
        self._start_server()
        fresh = self._json("/api/intelligence/status", authorized=True)[
            "capabilities"
        ]
        for name in ("adaptive_planner", "hybrid_execution", "nano_agents", "model_router"):
            self.assertFalse(fresh[name]["enabled"], name)

    # -- 14. Default simple chat still works --------------------------------------------------

    def test_14_default_auto_chat_unchanged(self):
        # The default UI path: Auto Intelligence with no explicit models.
        started, _ = self._start_chat(
            goal="Reply only: DEFAULT_OK",
            mode="solo",
            participants=[],
            selection_policy="auto_intelligence",
        )
        self.assertIn(started["status"], {"queued", "running"})
        current = self._await_terminal(str(started["run_id"]))
        self.assertIn(current["status"], {"succeeded", "partial"}, current)
        snapshot = current["intelligence"]
        self.assertIsNone(snapshot["requested_mode"])
        self.assertIn(snapshot["resolved_mode"], {"solo", "joint"})


class BridgeSeamTests(unittest.TestCase):
    """The two additive bridge extension points stay gate-subordinate."""

    def _ready_bridge(self):
        bridge = IntelligenceRuntimeBridge(
            meta_factory=_meta, bootstrap_mode="tauri_injected"
        )
        service = LocalReadinessService(
            inventory_fn=_inventory_fn((PREFERRED,)),
            preferred_model=PREFERRED,
            meta_factory=_meta,
        )
        bridge.attach_local_readiness(service)
        bridge.refresh_local_readiness(
            structured_probe=True,
            adapter_factory=lambda model: _FakeLocalAdapter(
                adapter_id=f"probe-{model}",
                model=model,
                chat_text='{"ok": true}',
            ),
        )
        return bridge

    def test_resolve_local_planner_runtime_binds_response_format(self):
        bridge = self._ready_bridge()
        bridge.configure_gates(
            {"adaptive_planner": True}, contract_version=1,
            correlation_id="v060-rt",
        )
        runtime = bridge.resolve_local_planner_runtime(
            adapter=_FakeLocalAdapter(adapter_id="planner-a"),
            response_format=PLANNER_RESPONSE_FORMAT,
        )
        self.assertIs(runtime._response_format, PLANNER_RESPONSE_FORMAT)

    def test_resolve_local_planner_runtime_still_refuses_when_gate_off(self):
        bridge = self._ready_bridge()
        with self.assertRaises(ContractError):
            bridge.resolve_local_planner_runtime(
                adapter=_FakeLocalAdapter(adapter_id="planner-a"),
                response_format=PLANNER_RESPONSE_FORMAT,
            )

    def test_request_hybrid_session_forwards_candidates_and_policy(self):
        bridge = self._ready_bridge()
        bridge.configure_gates(
            {"adaptive_planner": True}, contract_version=1,
            correlation_id="v060-hf-a",
        )
        bridge.configure_gates(
            {"hybrid_execution": True}, contract_version=1,
            correlation_id="v060-hf-b",
        )
        recorder = _RecorderCoordinator()

        class _PlannerInput:
            request_id = "req-forward"

        outcome = bridge.request_hybrid_session(
            coordinator_factory=lambda: recorder,
            planner_input=_PlannerInput(),
            candidates=("candidate-a",),
            policy_context={"max_total_cost": 0.0},
        )
        self.assertEqual(recorder.kwargs["candidates"], ("candidate-a",))
        self.assertEqual(recorder.kwargs["policy_context"], {"max_total_cost": 0.0})
        self.assertIsNotNone(outcome)

    def test_request_hybrid_session_fails_closed_when_gate_off(self):
        bridge = self._ready_bridge()

        class _PlannerInput:
            request_id = "req-closed"

        with self.assertRaises(ContractError):
            bridge.request_hybrid_session(
                coordinator_factory=lambda: _RecorderCoordinator(),
                planner_input=_PlannerInput(),
                candidates=("candidate-a",),
            )


if __name__ == "__main__":
    unittest.main()
