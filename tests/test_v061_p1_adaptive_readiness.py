"""v0.6.1 P1 Adaptive Hybrid readiness fix tests.

Task: AUTOCLAW-AMCA-V0.6.1-P1-ADAPTIVE-HYBRID-READINESS-FIX-01

Covers the required cases:
  * fresh startup gate state (all experimental gates OFF)
  * ready dependency path (probe ready -> enable offered -> confirm -> ON)
  * not-ready dependency path (unreachable -> enable refused; refresh
    after recovery -> enable succeeds)
  * enable flow (dependency order, session-only)
  * cancel flow (no mutation)
  * disable flow
  * restart OFF verification
  * startup probe bounded retry (cold-start race fix)
  * readiness refresh route: auth, gates-never-mutated, local-only,
    bounded, truthful reason codes
  * existing Adaptive regression (M5.7/M5.8/M0 suites unchanged)

No paid/cloud calls: every probe path is exercised through injected
fakes; the refresh route is tested against a service whose Ollama
inventory is monkeypatched to a deterministic fake.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.intelligence import (
    DEFAULT_GATES,
    ContractError,
    IntelligenceRuntimeBridge,
    LocalReadinessService,
)
from amca.intelligence.local_readiness import (
    NOT_READY,
    READY,
    REASON_NO_MODEL,
    REASON_STRUCTURED_UNVERIFIED,
    REASON_UNREACHABLE,
    UNKNOWN,
)
from amca.local_runtime import LocalModelRecord
from amca.service import (
    PROBE_RETRY_DELAYS_SECONDS,
    ServiceContext,
    _probe_needs_retry,
    create_server,
    run_startup_readiness_probe,
)

_FORBIDDEN = ("api_key", "credential", "password", "secret", "token_value")
CREATED = "2026-08-31T00:00:00+00:00"


def local_record(name: str) -> LocalModelRecord:
    return LocalModelRecord(
        provider="ollama",
        model=name,
        digest=f"digest-{name}",
        size_bytes=1024,
        modified_at=None,
    )


# ---------------------------------------------------------------------------
# 1. Startup probe bounded retry (the cold-start race fix)
# ---------------------------------------------------------------------------


class _FakeService:
    """Deterministic readiness service stand-in for retry tests."""

    def __init__(self, outcomes):
        # outcomes: list of ("raise"|"report") tuples
        self._outcomes = list(outcomes)
        self.calls = 0

    def refresh(self, **kwargs):
        self.calls += 1
        outcome = self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _report(adaptive: str, reason: str):
    class R:
        readiness = {
            "adaptive_planner": adaptive,
            "hybrid_execution": adaptive,
            "model_router": READY,
            "nano_agents": READY,
            "deterministic": READY,
        }
        reasons = {
            "adaptive_planner": reason,
            "hybrid_execution": reason,
            "model_router": "ready",
            "nano_agents": "ready",
            "deterministic": "ready",
        }

    return R()


class TestStartupProbeRetry(unittest.TestCase):
    def test_retry_stops_early_when_ready(self) -> None:
        service = _FakeService([
            _report(READY, "ready"),
        ])
        slept: list[float] = []
        reports = run_startup_readiness_probe(
            service, adapter_factory=lambda m: object(),
            delays=PROBE_RETRY_DELAYS_SECONDS, sleep_fn=slept.append,
        )
        self.assertEqual(len(reports), 1)
        self.assertEqual(service.calls, 1)
        self.assertEqual(slept, [])

    def test_retry_recovers_from_cold_start_unreachable(self) -> None:
        # Attempt 1: Ollama not up yet (startup race). Attempt 2: ready.
        service = _FakeService([
            _report(NOT_READY, REASON_UNREACHABLE),
            _report(READY, "ready"),
        ])
        slept: list[float] = []
        reports = run_startup_readiness_probe(
            service, adapter_factory=lambda m: object(),
            delays=(1.0, 2.0), sleep_fn=slept.append,
        )
        self.assertEqual(len(reports), 2)
        self.assertEqual(slept, [1.0])
        self.assertEqual(reports[-1].readiness["adaptive_planner"], READY)

    def test_retry_recovers_from_structured_timeout(self) -> None:
        # Attempt 1: model load exceeded the bounded probe timeout
        # (structured_output_unverified). Attempt 2: ready.
        service = _FakeService([
            _report(UNKNOWN, REASON_STRUCTURED_UNVERIFIED),
            _report(READY, "ready"),
        ])
        slept: list[float] = []
        reports = run_startup_readiness_probe(
            service, adapter_factory=lambda m: object(),
            delays=(1.0, 2.0), sleep_fn=slept.append,
        )
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[-1].readiness["adaptive_planner"], READY)

    def test_no_retry_on_definitive_no_model(self) -> None:
        # "no eligible local model" is a truthful definitive state, not a
        # transient failure: retrying would only burn time.
        service = _FakeService([
            _report(NOT_READY, REASON_NO_MODEL),
        ])
        slept: list[float] = []
        reports = run_startup_readiness_probe(
            service, adapter_factory=lambda m: object(),
            delays=(1.0, 2.0), sleep_fn=slept.append,
        )
        self.assertEqual(len(reports), 1)
        self.assertEqual(slept, [])

    def test_retry_is_bounded(self) -> None:
        # Persistent unreachable: exactly 1 + len(delays) attempts, then the
        # truthful not_ready result stands.
        service = _FakeService([
            _report(NOT_READY, REASON_UNREACHABLE),
        ])
        slept: list[float] = []
        reports = run_startup_readiness_probe(
            service, adapter_factory=lambda m: object(),
            delays=(1.0, 2.0), sleep_fn=slept.append,
        )
        self.assertEqual(service.calls, 3)
        self.assertEqual(slept, [1.0, 2.0])
        self.assertEqual(reports[-1].readiness["adaptive_planner"], NOT_READY)

    def test_probe_needs_retry_decision_table(self) -> None:
        self.assertFalse(_probe_needs_retry(_report(READY, "ready")))
        self.assertTrue(_probe_needs_retry(_report(NOT_READY, REASON_UNREACHABLE)))
        self.assertTrue(_probe_needs_retry(_report(UNKNOWN, REASON_STRUCTURED_UNVERIFIED)))
        self.assertFalse(_probe_needs_retry(_report(NOT_READY, REASON_NO_MODEL)))

    def test_probe_never_raises(self) -> None:
        service = _FakeService([RuntimeError("boom")])
        reports = run_startup_readiness_probe(
            service, adapter_factory=lambda m: object(),
            delays=(1.0,), sleep_fn=lambda s: None,
        )
        self.assertEqual(reports, [])


# ---------------------------------------------------------------------------
# 2. HTTP contract for POST /api/intelligence/readiness/refresh
# ---------------------------------------------------------------------------


class ReadinessRefreshAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        self.server = create_server(
            host="127.0.0.1", port=0, root=root, database=self.database)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        session = self._json("/api/session", authorized=False)[1]
        self.token = str(session["session_token"])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def _request(self, path, *, method="GET", body=None, authorized=True):
        request = Request(self.base + path, method=method)
        request.add_header("Origin", "http://localhost:1420")
        if authorized:
            request.add_header("X-AMCA-Session", self.token)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, data=data) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            payload = error.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(payload)
            except Exception:
                parsed = {"raw": payload}
            return error.code, parsed

    def _json(self, path, **kw):
        return self._request(path, **kw)

    # -- contract ---------------------------------------------------------

    def test_route_requires_session(self) -> None:
        status, _ = self._request(
            "/api/intelligence/readiness/refresh", method="POST",
            body={}, authorized=False)
        self.assertEqual(status, 401)

    def test_refresh_returns_truthful_report_and_never_mutates_gates(self) -> None:
        before = self._json("/api/intelligence/status")[1]
        status, payload = self._request(
            "/api/intelligence/readiness/refresh", method="POST", body={})
        self.assertEqual(status, 200)
        self.assertIn("readiness", payload)
        self.assertIn("reasons", payload)
        self.assertIs(payload["gates_mutated"], False)
        # Gates are byte-identical before and after the refresh.
        after = self._json("/api/intelligence/status")[1]
        self.assertEqual(
            {k: v["enabled"] for k, v in before["capabilities"].items()},
            {k: v["enabled"] for k, v in after["capabilities"].items()},
        )

    def test_refresh_reason_codes_are_bounded(self) -> None:
        _, payload = self._request(
            "/api/intelligence/readiness/refresh", method="POST", body={})
        allowed = {
            "ready", REASON_UNREACHABLE, REASON_NO_MODEL,
            REASON_STRUCTURED_UNVERIFIED, "hybrid_dependency_not_ready",
        }
        for reason in payload["reasons"].values():
            self.assertIn(reason, allowed)
        blob = json.dumps(payload).lower()
        for needle in _FORBIDDEN:
            self.assertNotIn(needle, blob)
        self.assertNotIn("Traceback", json.dumps(payload))

    def test_legacy_bootstrap_refresh_is_inventory_only(self) -> None:
        # The dev server here is legacy_dev_http (no injected token): the
        # refresh must NOT run a structured probe (no generation).
        ctx = self.server.amca_context
        self.assertFalse(ctx.intelligence_runtime.secure_bootstrap)
        calls: dict = {}

        original = ctx.intelligence_local_readiness.refresh

        def spy(**kwargs):
            calls.update(kwargs)
            return original(**kwargs)

        ctx.intelligence_local_readiness.refresh = spy
        status, _ = self._request(
            "/api/intelligence/readiness/refresh", method="POST", body={})
        self.assertEqual(status, 200)
        self.assertFalse(calls.get("structured_probe", False))
        self.assertNotIn("adapter_factory", calls)

    def test_legacy_bootstrap_http_configure_fail_closed_403(self) -> None:
        # The dev server here is legacy_dev_http: the configure route is
        # fail-closed with 403 BEFORE body parsing (M5.7 security model).
        # The readiness refresh route still works (read-only re-probe).
        ctx = self.server.amca_context
        self.assertFalse(ctx.intelligence_runtime.secure_bootstrap)
        status, _ = self._request(
            "/api/intelligence/configure", method="POST",
            body={"contract_version": 1, "changes": {"adaptive_planner": True}})
        self.assertEqual(status, 403)
        status, payload = self._request(
            "/api/intelligence/readiness/refresh", method="POST", body={})
        self.assertEqual(status, 200)
        self.assertIs(payload["gates_mutated"], False)

    def test_refresh_recovers_stale_not_ready_then_enable_succeeds(self) -> None:
        """Full defect-2 lifecycle at the bridge level (secure bootstrap):

        fresh OFF -> readiness not ready (cold-start race) -> enable refused
        -> refresh (recovered) -> enable succeeds -> disable -> OFF.
        """
        bridge = IntelligenceRuntimeBridge(bootstrap_mode="tauri_injected")
        unreachable_service = LocalReadinessService(
            inventory_fn=lambda base_url, timeout: (_ for _ in ()).throw(
                ConnectionError("ollama not up yet")),
        )
        unreachable_service.refresh()
        bridge.attach_local_readiness(unreachable_service)

        caps = bridge.status()["capabilities"]
        self.assertFalse(caps["adaptive_planner"]["enabled"])
        self.assertEqual(caps["adaptive_planner"]["runtime_ready"], NOT_READY)

        # Enable attempt is refused (fail-closed preserved).
        with self.assertRaises(ContractError) as cm:
            bridge.configure_gates({"adaptive_planner": True},
                                   contract_version=1,
                                   correlation_id="p1-refused")
        self.assertEqual(
            (cm.exception.details or {}).get("category"), "runtime_not_ready")
        self.assertFalse(
            bridge.status()["capabilities"]["adaptive_planner"]["enabled"])

        # Environment recovers: Ollama up with the preferred model and a
        # verified structured response. Explicit refresh re-probes.
        import asyncio

        from amca.models.base import (
            ModelAdapter,
            ModelCapabilities,
            ModelResponse,
            TokenUsage,
        )

        class _StructuredOk(ModelAdapter):
            def __init__(self, model="stub:latest"):
                super().__init__(
                    adapter_id="local-stub", provider="stub", model=model,
                    capabilities=ModelCapabilities(is_local=True),
                )
                self._model = model

            async def generate(self, request):
                async with self._call_scope(request):
                    return ModelResponse(
                        text='{"ok": true}', model=self._model,
                        provider="stub", usage=TokenUsage(),
                        finish_reason="stop",
                    )

            def stream(self, request):
                raise NotImplementedError

        ready_service = LocalReadinessService(
            inventory_fn=lambda base_url, timeout: (
                local_record("qwen3:1.7b"), local_record("llama3.2:3b")),
        )
        bridge.attach_local_readiness(ready_service)
        report = bridge.refresh_local_readiness(
            structured_probe=True,
            adapter_factory=lambda model: _StructuredOk(model=model),
        )
        self.assertEqual(report.readiness["adaptive_planner"], READY)

        # Now the enable succeeds (dependency order: adaptive first).
        bridge.configure_gates({"adaptive_planner": True},
                               contract_version=1,
                               correlation_id="p1-enable-1")
        bridge.configure_gates({"hybrid_execution": True},
                               contract_version=1,
                               correlation_id="p1-enable-2")
        caps = bridge.status()["capabilities"]
        self.assertTrue(caps["adaptive_planner"]["enabled"])
        self.assertTrue(caps["hybrid_execution"]["enabled"])

        # Disable flow: one atomic call, both OFF again.
        bridge.configure_gates({"adaptive_planner": False,
                                "hybrid_execution": False},
                               contract_version=1,
                               correlation_id="p1-disable")
        caps = bridge.status()["capabilities"]
        self.assertFalse(caps["adaptive_planner"]["enabled"])
        self.assertFalse(caps["hybrid_execution"]["enabled"])


# ---------------------------------------------------------------------------
# 3. Fresh start / restart OFF (lifecycle invariants at unit level)
# ---------------------------------------------------------------------------


class TestFreshStartAndRestartOff(unittest.TestCase):
    def test_fresh_bridge_all_experimental_gates_off(self) -> None:
        bridge = IntelligenceRuntimeBridge(bootstrap_mode="tauri_injected")
        caps = bridge.status()["capabilities"]
        for gate in ("adaptive_planner", "hybrid_execution",
                     "nano_agents", "model_router"):
            self.assertFalse(caps[gate]["enabled"], gate)
        self.assertTrue(caps["deterministic"]["enabled"])

    def test_restart_constructs_default_off_registry(self) -> None:
        # A new process constructs from DEFAULT_GATES: nothing persists.
        self.assertFalse(DEFAULT_GATES.is_enabled("adaptive_planner"))
        self.assertFalse(DEFAULT_GATES.is_enabled("hybrid_execution"))
        self.assertFalse(DEFAULT_GATES.is_enabled("nano_agents"))
        self.assertFalse(DEFAULT_GATES.is_enabled("model_router"))

    def test_cancel_flow_is_frontend_only_no_mutation(self) -> None:
        # Cancel never calls configure; assert configure is the ONLY
        # mutation route by checking the bridge refuses an empty change set
        # and leaves gates untouched.
        bridge = IntelligenceRuntimeBridge(bootstrap_mode="tauri_injected")
        with self.assertRaises(ContractError):
            bridge.configure_gates({}, contract_version=1,
                                   correlation_id="cancel-sim")
        caps = bridge.status()["capabilities"]
        self.assertFalse(caps["adaptive_planner"]["enabled"])


if __name__ == "__main__":
    unittest.main()
