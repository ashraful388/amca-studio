"""M5.7 secure intelligence enablement tests.

Required coverage (manager work order
ZC-AMCA-V0.5.0-M5.7-SECURE-INTELLIGENCE-ENABLEMENT-01): all 22 required
security scenarios plus the functional Nano enable/execute/disable loop.

The ONLY intelligence mutation route is POST /api/intelligence/configure,
guarded by the M5.4.1 hardened bootstrap guard (require_mutation_ready).
The legacy dev bootstrap is fail-closed and can never enable a feature.
"""

import inspect
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock

from amca.intelligence import (
    DEFAULT_GATES,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    FeatureGateRegistry,
    IntelligenceRuntimeBridge,
    Sensitivity,
)
from amca.service import AMCARequestHandler, create_server

CREATED_TOKEN = "m57-injected-ephemeral-token-0123456789abcdef-0123456789abcdef"
ALLOWED_ORIGIN = "http://localhost:1420"


def meta_factory(record_id):
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-m57",
        producer="m5.7-test",
        created_at="2026-08-27T00:00:00+00:00",
        sensitivity=Sensitivity.INTERNAL,
    )


def secure_bridge():
    return IntelligenceRuntimeBridge(
        meta_factory=meta_factory, bootstrap_mode="tauri_injected",
    )


class SecureServerTestCase(unittest.TestCase):
    """HTTP fixture: backend started with the injected (secure) token."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self._previous_env = os.environ.get("AMCA_SESSION_TOKEN")
        os.environ["AMCA_SESSION_TOKEN"] = CREATED_TOKEN
        root = Path(__file__).resolve().parents[1]
        self.server = create_server(
            host="127.0.0.1", port=0, root=root,
            database=Path(self.temporary.name) / "amca.db",
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.port = self.server.server_port

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        if self._previous_env is None:
            os.environ.pop("AMCA_SESSION_TOKEN", None)
        else:
            os.environ["AMCA_SESSION_TOKEN"] = self._previous_env
        self.temporary.cleanup()

    def _request(self, body=None, *, token=CREATED_TOKEN, origin=ALLOWED_ORIGIN):
        request = urllib.request.Request(
            self.base + "/api/intelligence/configure", method="POST")
        if origin is not None:
            request.add_header("Origin", origin)
        if token is not None:
            request.add_header("X-AMCA-Session", token)
        data = json.dumps(body or {}).encode("utf-8")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, data=data) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            payload = error.read().decode()
            try:
                return error.code, json.loads(payload)
            except ValueError:
                return error.code, {"error": payload}

    def _raw_with_host(self, host_header, token=CREATED_TOKEN):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(
                "POST", "/api/intelligence/configure",
                body=json.dumps(
                    {"contract_version": 1,
                     "changes": {"nano_agents": True}}),
                headers={
                    "Host": host_header,
                    "Origin": ALLOWED_ORIGIN,
                    "X-AMCA-Session": token,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode())
            return response.status, payload
        finally:
            connection.close()


class StartupAndGuardTests(SecureServerTestCase):
    def test_all_gates_off_at_startup(self):
        with urllib.request.urlopen(
            urllib.request.Request(
                self.base + "/api/intelligence/status",
                headers={"X-AMCA-Session": CREATED_TOKEN}),
        ) as response:
            status = json.loads(response.read().decode())
        for name in ("adaptive_planner", "hybrid_execution",
                     "nano_agents", "model_router"):
            self.assertFalse(status["capabilities"][name]["enabled"])

    def test_secure_tauri_mode_may_mutate(self):
        status, payload = self._request(
            {"contract_version": 1, "changes": {"nano_agents": True}})
        self.assertEqual(status, 200)
        self.assertEqual(payload["gates"]["nano_agents"], True)
        self.assertEqual(payload["snapshot_version"], 1)
        context = self.server.amca_context
        self.assertTrue(
            context.intelligence_runtime.status()["gates"]["nano_agents"])

    def test_legacy_dev_bootstrap_cannot_mutate(self):
        previous = os.environ.get("AMCA_SESSION_TOKEN")
        os.environ.pop("AMCA_SESSION_TOKEN", None)
        legacy_server = create_server(
            host="127.0.0.1", port=0,
            root=Path(__file__).resolve().parents[1],
            database=Path(self.temporary.name) / "legacy.db")
        legacy_thread = threading.Thread(
            target=legacy_server.serve_forever, daemon=True)
        legacy_thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{legacy_server.server_port}"
                "/api/intelligence/configure",
                method="POST")
            request.add_header("Origin", ALLOWED_ORIGIN)
            # legacy dev mode issues tokens via /api/session; use a real one
            with urllib.request.urlopen(
                urllib.request.Request(
                    f"http://127.0.0.1:{legacy_server.server_port}/api/session"),
            ) as response:
                token = json.loads(response.read().decode())["session_token"]
            request.add_header("X-AMCA-Session", str(token))
            data = json.dumps(
                {"contract_version": 1, "changes": {"nano_agents": True}}
            ).encode()
            request.add_header("Content-Type", "application/json")
            try:
                urllib.request.urlopen(request, data=data)
                raise AssertionError("legacy mutation must fail")
            except urllib.error.HTTPError as error:
                self.assertEqual(error.code, 403)
            bridge = legacy_server.amca_context.intelligence_runtime
            self.assertFalse(bridge.status()["gates"]["nano_agents"])
        finally:
            legacy_server.shutdown()
            legacy_server.server_close()
            legacy_thread.join(timeout=2)
            if previous is None:
                os.environ.pop("AMCA_SESSION_TOKEN", None)
            else:
                os.environ["AMCA_SESSION_TOKEN"] = previous

    def test_missing_session_cannot_mutate(self):
        status, _ = self._request(
            {"contract_version": 1, "changes": {"nano_agents": True}},
            token=None)
        self.assertEqual(status, 401)

    def test_invalid_session_cannot_mutate(self):
        status, _ = self._request(
            {"contract_version": 1, "changes": {"nano_agents": True}},
            token="wrong-token")
        self.assertEqual(status, 401)

    def test_foreign_host_cannot_mutate(self):
        status, _ = self._raw_with_host("evil.example.com:1234")
        self.assertEqual(status, 403)

    def test_forbidden_origin_cannot_mutate(self):
        status, _ = self._request(
            {"contract_version": 1, "changes": {"nano_agents": True}},
            origin="http://evil.example.com:1420")
        self.assertEqual(status, 403)

    def test_lookalike_origin_cannot_mutate(self):
        status, _ = self._request(
            {"contract_version": 1, "changes": {"nano_agents": True}},
            origin="http://localhost:1420.evil.com")
        self.assertEqual(status, 403)


class ControllerRuleTests(unittest.TestCase):
    def setUp(self):
        self.bridge = secure_bridge()

    def test_nano_enable_succeeds_when_ready(self):
        result = self.bridge.configure_gates(
            {"nano_agents": True}, contract_version=1,
            correlation_id="corr-1")
        self.assertTrue(result["gates"]["nano_agents"])
        self.assertTrue(self.bridge.status()["gates"]["nano_agents"])
        self.assertTrue(self.bridge._gates.is_enabled("nano_agents"))

    def test_nano_disable_succeeds(self):
        self.bridge.configure_gates(
            {"nano_agents": True}, contract_version=1, correlation_id="c1")
        result = self.bridge.configure_gates(
            {"nano_agents": False}, contract_version=1, correlation_id="c2")
        self.assertFalse(result["gates"]["nano_agents"])

    def test_disable_all_atomic(self):
        self.bridge.configure_gates(
            {"nano_agents": True}, contract_version=1, correlation_id="c1")
        result = self.bridge.disable_all_experimental(correlation_id="c2")
        self.assertEqual(
            result["gates"],
            {
                "adaptive_planner": False,
                "hybrid_execution": False,
                "nano_agents": False,
                "model_router": False,
            },
        )
        self.assertEqual(self.bridge.gate_snapshot()["snapshot_version"], 2)

    def test_rejection_leaves_state_atomic(self):
        with self.assertRaises(ContractError):
            self.bridge.configure_gates(
                {"nano_agents": True, "adaptive_planner": True},
                contract_version=1, correlation_id="c1")
        # the valid half was NOT applied
        self.assertFalse(self.bridge.status()["gates"]["nano_agents"])
        self.assertFalse(self.bridge.status()["gates"]["adaptive_planner"])

    def test_adaptive_enable_rejected_while_readiness_unknown(self):
        with self.assertRaises(ContractError) as ctx:
            self.bridge.configure_gates(
                {"adaptive_planner": True}, contract_version=1,
                correlation_id="c1")
        self.assertEqual(
            ctx.exception.details["category"], "runtime_not_ready")
        self.assertFalse(self.bridge.status()["gates"]["adaptive_planner"])

    def test_router_enable_rejected_while_readiness_unknown(self):
        with self.assertRaises(ContractError) as ctx:
            self.bridge.configure_gates(
                {"model_router": True}, contract_version=1,
                correlation_id="c1")
        self.assertEqual(
            ctx.exception.details["category"], "runtime_not_ready")

    def test_hybrid_enable_rejected_while_readiness_unknown(self):
        with self.assertRaises(ContractError) as ctx:
            self.bridge.configure_gates(
                {"hybrid_execution": True}, contract_version=1,
                correlation_id="c1")
        self.assertEqual(
            ctx.exception.details["category"], "runtime_not_ready")

    def test_hybrid_dependency_conflict_rejected(self):
        # test-only readiness provider: both report ready, but Adaptive is
        # still OFF, so Hybrid must fail with DEPENDENCY_NOT_ENABLED and
        # Adaptive must never be auto-enabled
        controller = self.bridge.gate_controller
        controller._readiness_provider = lambda: {
            "deterministic": "ready", "nano_agents": "ready",
            "adaptive_planner": "ready", "hybrid_execution": "ready",
            "model_router": "ready",
        }
        with self.assertRaises(ContractError) as ctx:
            self.bridge.configure_gates(
                {"hybrid_execution": True}, contract_version=1,
                correlation_id="c1")
        self.assertEqual(
            ctx.exception.details["category"], "dependency_not_enabled")
        self.assertFalse(self.bridge.status()["gates"]["adaptive_planner"])
        self.assertFalse(self.bridge.status()["gates"]["hybrid_execution"])

    def test_unknown_gate_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            self.bridge.configure_gates(
                {"teleportation": True}, contract_version=1,
                correlation_id="c1")
        self.assertEqual(
            ctx.exception.details["category"], "unknown_capability")

    def test_malformed_request_rejected(self):
        cases = (
            {"nano_agents": "yes"},
            {"nano_agents": 1},
            "not-a-dict",
            {},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ContractError) as ctx:
                    self.bridge.configure_gates(
                        changes, contract_version=1, correlation_id="c1")
                self.assertEqual(
                    ctx.exception.details["category"], "malformed_request")

    def test_unsupported_contract_version_rejected(self):
        for version in (None, 0, 2, 99):
            with self.subTest(version=version):
                with self.assertRaises(ContractError) as ctx:
                    self.bridge.configure_gates(
                        {"nano_agents": True}, contract_version=version,
                        correlation_id="c1")
                self.assertEqual(
                    ctx.exception.details["category"],
                    "unsupported_contract_version")

    def test_active_run_mutation_protected(self):
        self.bridge.configure_gates(
            {"nano_agents": True}, contract_version=1, correlation_id="c1")
        self.bridge.begin_intelligence_execution("nano_agents", "run-1")
        with self.assertRaises(ContractError) as ctx:
            self.bridge.configure_gates(
                {"nano_agents": False}, contract_version=1,
                correlation_id="c2")
        self.assertEqual(
            ctx.exception.details["category"], "active_execution")
        # still enabled: the refusal is atomic
        self.assertTrue(self.bridge.status()["gates"]["nano_agents"])
        # the bounded emergency path is the fail-safe rollback
        result = self.bridge.disable_all_experimental(
            correlation_id="c3", emergency=True)
        self.assertFalse(result["gates"]["nano_agents"])
        self.bridge.end_intelligence_execution("nano_agents", "run-1")

    def test_audit_record_generated_safely(self):
        self.bridge.configure_gates(
            {"nano_agents": True}, contract_version=1, correlation_id="corr-x")
        records = self.bridge.gate_audit_records()
        self.assertTrue(records)
        record = records[-1]
        for field in ("feature", "old_state", "new_state", "at",
                      "bootstrap_mode", "correlation_id",
                      "decision", "category"):
            self.assertIn(field, record)
        self.assertEqual(record["feature"], "nano_agents")
        self.assertEqual(record["correlation_id"], "corr-x")
        self.assertEqual(record["bootstrap_mode"], "tauri_injected")

    def test_no_credential_or_token_leakage(self):
        self.bridge.configure_gates(
            {"nano_agents": True}, contract_version=1,
            correlation_id="corr-leak-check")
        serialized = json.dumps(
            list(self.bridge.gate_audit_records())
            + [self.bridge.gate_snapshot()]
        ).lower()
        for fragment in (CREATED_TOKEN, "session_token", "api_key",
                         "secret", "credential", "x-amca-session"):
            self.assertNotIn(fragment, serialized)


class StructuralGuardTests(SecureServerTestCase):
    def test_configure_route_invokes_the_guard_behaviorally(self):
        bridge = self.server.amca_context.intelligence_runtime
        with mock.patch.object(
            bridge,
            "require_mutation_ready",
            side_effect=ContractError(
                ContractErrorCode.BLOCKED, "guard raised under test"),
        ):
            status, payload = self._request(
                {"contract_version": 1, "changes": {"nano_agents": True}})
        # even in the secure bootstrap with a valid session, the route
        # refuses when the guard refuses: the route provably calls it
        self.assertEqual(status, 403)
        self.assertIn("guard raised under test", payload["error"])
        self.assertFalse(bridge.status()["gates"]["nano_agents"])

    def test_configure_is_the_only_gate_mutation_route(self):
        source = inspect.getsource(AMCARequestHandler.do_POST)
        self.assertIn("require_mutation_ready", source)
        self.assertIn("is_intelligence_configure", source)
        # exactly one call site wires the mutation through the controller
        self.assertEqual(source.count("configure_gates"), 1)

    def test_previous_enable_style_paths_still_do_not_exist(self):
        for path in ("/api/intelligence/enable", "/api/intelligence/gates",
                     "/api/intelligence/config"):
            connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
            try:
                connection.request(
                    "POST", path, body="{}",
                    headers={"X-AMCA-Session": CREATED_TOKEN,
                             "Origin": ALLOWED_ORIGIN,
                             "Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, 404)
            finally:
                connection.close()


class SessionScopeTests(unittest.TestCase):
    def test_new_context_restores_default_off(self):
        bridge = secure_bridge()
        bridge.configure_gates(
            {"nano_agents": True}, contract_version=1, correlation_id="c1")
        self.assertTrue(bridge.status()["gates"]["nano_agents"])
        # a fresh bridge (new session / restart) starts all-OFF again
        fresh = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        self.assertEqual(
            fresh.status()["gates"],
            {
                "adaptive_planner": False,
                "hybrid_execution": False,
                "nano_agents": False,
                "model_router": False,
            },
        )
        self.assertEqual(fresh.gate_snapshot()["snapshot_version"], 0)


class FunctionalNanoEnablementTests(unittest.TestCase):
    """Work order section 13: the full controlled enablement loop."""

    def _m57_proposal(self, proposal_id="prop-m57"):
        from amca.intelligence import NanoSpawnProposal
        return NanoSpawnProposal(
            meta=meta_factory(proposal_id),
            proposal_id=proposal_id,
            run_ref="run-m57",
            parent_task_id="task:root",
            parent_depth=0,
            task_purpose="verify citation claims",
            task_type="verification",
            requested_child_count=1,
            capabilities=("citation_check",),
            permissions=("local_search",),
            expected_cost=0.0,
            correlation_id="corr-m57",
        )

    def test_nano_executes_only_after_controlled_enablement(self):
        from amca.agent_runtime import NanoScheduler
        from amca.intelligence import NanoExecutionCoordinator, NanoRunRegistry

        bridge = secure_bridge()
        reg = NanoRunRegistry(
            meta_factory=meta_factory, run_ref="run-m57",
            allowed_capabilities=("citation_check",),
        )
        coordinator = NanoExecutionCoordinator(
            registry=reg,
            scheduler=NanoScheduler({}, budget_usd=5.0),
            meta_factory=meta_factory,
            known_parent_task_ids=("task:root",),
        )
        # controlled enablement through the bridge: only then may the M3
        # front seal assignments at all
        bridge.configure_gates(
            {"nano_agents": True}, contract_version=1, correlation_id="fn-1")
        assignment = reg.authorize_spawn(
            self._m57_proposal(),
            parent_permissions=("local_search",),
            gates=FeatureGateRegistry(**bridge.status()["gates"]),
        )
        # controlled disablement: an authorized assignment can no longer
        # execute (further execution refused)
        bridge.configure_gates(
            {"nano_agents": False}, contract_version=1, correlation_id="fn-2")
        with self.assertRaises(ContractError):
            coordinator.execute([assignment], gates=bridge._gates)
        # re-enable: the authorized Nano execution is now allowed
        bridge.configure_gates(
            {"nano_agents": True}, contract_version=1, correlation_id="fn-3")
        outcome = coordinator.execute([assignment], gates=bridge._gates)
        self.assertEqual(
            outcome["results"][assignment.nano_id].disposition.value,
            "complete",
        )
        # deterministic behavior remained available the whole time
        selection = bridge.resolve_planner("req-det")
        self.assertEqual(selection.selected.value, "deterministic")

    def test_disabling_does_not_damage_runtime(self):
        bridge = secure_bridge()
        bridge.configure_gates(
            {"nano_agents": True}, contract_version=1, correlation_id="d1")
        bridge.disable_all_experimental(correlation_id="d2")
        status = bridge.status()
        self.assertFalse(status["gates"]["nano_agents"])
        self.assertTrue(status["capabilities"]["deterministic"]["enabled"])
        selection = bridge.resolve_planner("req-after")
        self.assertEqual(selection.selected.value, "deterministic")


if __name__ == "__main__":
    unittest.main()
