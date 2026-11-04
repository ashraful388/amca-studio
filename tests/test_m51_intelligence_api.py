"""M5.1 Intelligence API Controls tests.

Service-level coverage required by the issued M5.1 work order: the
versioned read-only status endpoint, the non-executing validation endpoint,
strict versioned contracts, truthful disabled behavior, fail-closed
conflict/unknown handling, session authorization reuse, privacy, and
existing API behavior remaining green.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.service import create_server

_FORBIDDEN = ("api_key", "credential", "password", "secret", "token_value")


class IntelligenceAPITests(unittest.TestCase):
    """HTTP contract for the M5.1 intelligence status/validate endpoints."""

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

    # -- helpers ---------------------------------------------------------

    def _request(self, path: str, *, method: str = "GET", body=None,
                 authorized: bool = True):
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
                return response.status, json.loads(
                    response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def _json(self, path: str, *, authorized: bool = True, body=None,
              method: str | None = None):
        if method is None:
            method = "POST" if body is not None else "GET"
        return self._request(
            path, method=method, body=body, authorized=authorized
        )

    # -- status endpoint ---------------------------------------------------

    def test_status_endpoint_success(self):
        status, payload = self._json("/api/intelligence/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["contract_version"], 2)
        self.assertEqual(payload["seam_id"], "amca.intelligence.runtime_bridge")

    def test_four_feature_states_correctly_reported(self):
        status, payload = self._json("/api/intelligence/status")
        for name in (
            "adaptive_planner",
            "hybrid_execution",
            "nano_agents",
            "model_router",
        ):
            entry = payload["capabilities"][name]
            self.assertFalse(entry["enabled"])
            self.assertTrue(entry["implemented"])

    def test_execution_authority_reported_correctly(self):
        status, payload = self._json("/api/intelligence/status")
        self.assertEqual(payload["execution_authority"], "existing_runtime")

    def test_status_contains_no_sensitive_fields(self):
        status, payload = self._json("/api/intelligence/status")
        serialized = json.dumps(payload).lower()
        for word in _FORBIDDEN:
            self.assertNotIn(word, serialized)

    def test_status_requires_session(self):
        status, _ = self._json(
            "/api/intelligence/status", authorized=False)
        self.assertEqual(status, 401)

    def test_status_uses_bridge_state_not_independent_state(self):
        # M5.8: the served state must be the CONTEXT bridge's state —
        # including its attached local-readiness source — never an
        # independent bridge constructed here.
        _, payload = self._json("/api/intelligence/status")
        expected = self.server.amca_context.intelligence_runtime.status()
        self.assertEqual(payload["capabilities"], expected["capabilities"])
        self.assertEqual(
            payload["execution_authority"], expected["execution_authority"]
        )

    # -- validation endpoint -------------------------------------------------

    def test_deterministic_validation_accepted(self):
        status, payload = self._json(
            "/api/intelligence/validate",
            method="POST",
            body={"contract_version": 1, "requested": ["deterministic"]},
        )
        self.assertEqual(status, 200)
        decision = payload["decisions"]["deterministic"]
        self.assertEqual(decision["decision"], "allowed")
        self.assertTrue(decision["enabled"])

    def test_adaptive_request_reports_disabled_truthfully(self):
        status, payload = self._json(
            "/api/intelligence/validate",
            method="POST",
            body={"contract_version": 1, "requested": ["adaptive_planner"]},
        )
        self.assertEqual(status, 200)
        decision = payload["decisions"]["adaptive_planner"]
        self.assertFalse(decision["enabled"])
        self.assertFalse(decision["available"])
        self.assertEqual(decision["decision"], "unavailable_or_disabled")

    def test_hybrid_request_reports_disabled(self):
        status, payload = self._json(
            "/api/intelligence/validate",
            method="POST",
            body={"contract_version": 1, "requested": ["hybrid_execution"]},
        )
        self.assertEqual(
            payload["decisions"]["hybrid_execution"]["decision"],
            "unavailable_or_disabled",
        )

    def test_nano_request_reports_disabled(self):
        status, payload = self._json(
            "/api/intelligence/validate",
            method="POST",
            body={"contract_version": 1, "requested": ["nano_agents"]},
        )
        self.assertEqual(
            payload["decisions"]["nano_agents"]["decision"],
            "unavailable_or_disabled",
        )

    def test_model_router_request_reports_disabled(self):
        status, payload = self._json(
            "/api/intelligence/validate",
            method="POST",
            body={"contract_version": 1, "requested": ["model_router"]},
        )
        self.assertEqual(
            payload["decisions"]["model_router"]["decision"],
            "unavailable_or_disabled",
        )

    def test_unknown_capability_rejected(self):
        status, payload = self._json(
            "/api/intelligence/validate",
            method="POST",
            body={"contract_version": 1, "requested": ["teleport"]},
        )
        self.assertEqual(status, 400)
        self.assertIn("unknown capability", payload["error"])

    def test_malformed_request_rejected(self):
        status, _ = self._json(
            "/api/intelligence/validate",
            method="POST",
            body={"contract_version": 1, "requested": "deterministic"},
        )
        self.assertEqual(status, 400)
        status, _ = self._json(
            "/api/intelligence/validate",
            method="POST",
            body={"contract_version": 1, "requested": [True]},
        )
        self.assertEqual(status, 400)
        status, _ = self._json(
            "/api/intelligence/validate", method="POST", body={},
        )
        self.assertEqual(status, 400)

    def test_conflicting_request_rejected(self):
        status, payload = self._json(
            "/api/intelligence/validate",
            method="POST",
            body={
                "contract_version": 1,
                "requested": ["deterministic", "adaptive_planner"],
            },
        )
        self.assertEqual(status, 409)
        self.assertIn("conflicting", payload["error"])

    def test_unsupported_contract_version_rejected(self):
        status, payload = self._json(
            "/api/intelligence/validate",
            method="POST",
            body={"contract_version": 99, "requested": ["deterministic"]},
        )
        self.assertEqual(status, 400)
        self.assertIn("unsupported contract version", payload["error"])

    def test_unknown_request_fields_rejected(self):
        status, payload = self._json(
            "/api/intelligence/validate",
            method="POST",
            body={
                "contract_version": 1,
                "requested": ["deterministic"],
                "enable": True,
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("unknown request fields", payload["error"])

    def test_validate_requires_session(self):
        status, _ = self._json(
            "/api/intelligence/validate",
            method="POST",
            body={"contract_version": 1, "requested": ["deterministic"]},
            authorized=False,
        )
        self.assertEqual(status, 401)

    def test_no_intelligence_execution_occurs_during_validation(self):
        status, payload = self._json(
            "/api/intelligence/validate",
            method="POST",
            body={
                "contract_version": 1,
                "requested": [
                    "adaptive_planner",
                    "hybrid_execution",
                    "nano_agents",
                    "model_router",
                ],
            },
        )
        self.assertEqual(status, 200)
        # a validation response carries decisions only: no run ids,
        # strategies, or execution markers of any kind
        self.assertNotIn("run_id", json.dumps(payload))
        self.assertNotIn("strategy", json.dumps(payload))

    def test_bridge_remains_source_of_intelligence_state(self):
        # M5.8: the context bridge (with its attached local-readiness
        # source) remains the single source of intelligence state.
        _, payload = self._json("/api/intelligence/status")
        expected = self.server.amca_context.intelligence_runtime.status()
        self.assertEqual(payload["capabilities"], expected["capabilities"])
        self.assertEqual(
            payload["execution_authority"], expected["execution_authority"]
        )


class ExistingAPIUnchangedTests(unittest.TestCase):
    """Existing API/session behavior remains unchanged (required area 16/17)."""

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

    def _json(self, path: str, *, authorized: bool = True):
        request = Request(self.base + path)
        request.add_header("Origin", "http://localhost:1420")
        if authorized:
            request.add_header("X-AMCA-Session", self.token)
        try:
            with urlopen(request) as response:
                return response.status, json.loads(
                    response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_health_and_session_endpoints_remain_green(self):
        status, payload = self._json("/api/health", authorized=False)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        status, payload = self._json("/api/session", authorized=False)
        self.assertEqual(status, 200)
        self.assertIn("session_token", payload)

    def test_existing_dashboard_requires_session_unchanged(self):
        status, _ = self._json("/api/dashboard", authorized=False)
        self.assertEqual(status, 401)
        status, _ = self._json("/api/dashboard")
        self.assertEqual(status, 200)

    def test_unknown_endpoints_still_404(self):
        status, _ = self._json("/api/intelligence/unknown")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
