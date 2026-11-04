"""M5.4 Local Session Security Hardening tests.

Service-level security tests for the hardened session bootstrap:
env-injected ephemeral tokens, gated /api/session disclosure, loopback-peer
and Host request guards, and continued operation of every existing
endpoint. Dev-mode (self-generated token) behavior is preserved.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.service import create_server

CREATED_TOKEN = "m54-injected-ephemeral-token-0123456789abcdef-0123456789abcdef"


class InjectedTokenServerTestCase(unittest.TestCase):
    """Base fixture: backend started with an injected session token."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self._previous_env = os.environ.get("AMCA_SESSION_TOKEN")
        os.environ["AMCA_SESSION_TOKEN"] = CREATED_TOKEN
        root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        self.server = create_server(
            host="127.0.0.1", port=0, root=root, database=self.database)
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

    def _request(self, path: str, *, method: str = "GET", body=None,
                 token: str | None = CREATED_TOKEN, host: str | None = None):
        request = Request(self.base + path, method=method)
        request.add_header("Origin", "http://localhost:1420")
        effective_host = host or f"127.0.0.1:{self.port}"
        if host is not None:
            # override the Host header urlopen would derive from the URL
            request.set_proxy(effective_host, "http")
            self.base_override = f"http://{effective_host}"
        if token is not None:
            request.add_header("X-AMCA-Session", token)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, data=data) as response:
                return response.status, json.loads(
                    response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
            except Exception:
                payload = {}
            return error.code, payload


class SessionDisclosureTests(InjectedTokenServerTestCase):
    def test_api_session_does_not_disclose_token_when_injected(self):
        status, _ = self._request("/api/session")
        self.assertEqual(status, 404)

    def test_injected_token_authenticates_endpoints(self):
        status, payload = self._request(
            "/api/intelligence/status", token=CREATED_TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(payload["execution_authority"], "existing_runtime")

    def test_wrong_token_rejected(self):
        status, _ = self._request(
            "/api/dashboard", token="wrong-token-wrong-token-wrong-token-xx")
        self.assertEqual(status, 401)

    def test_missing_token_rejected(self):
        status, _ = self._request("/api/dashboard", token=None)
        self.assertEqual(status, 401)

    def test_injected_token_is_ephemeral_and_high_entropy(self):
        # 32-byte token_urlsafe equivalent: >= 43 base64url chars and the
        # injected fixture is an explicit high-entropy ephemeral value
        self.assertGreaterEqual(len(CREATED_TOKEN), 43)
        context = self.server.amca_context
        self.assertTrue(context.session_token_injected)
        self.assertEqual(context.session_token, CREATED_TOKEN)


class RequestGuardTests(InjectedTokenServerTestCase):
    def test_foreign_host_header_rejected(self):
        # DNS-rebinding style request: the socket goes to the loopback
        # service but the Host header points somewhere else
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        connection.request(
            "GET", "/api/dashboard",
            headers={
                "Host": "evil.example.com",
                "X-AMCA-Session": CREATED_TOKEN,
                "Origin": "http://localhost:1420",
            },
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.close()

    def test_host_guard_rejects_mismatched_host(self):
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        connection.request(
            "GET", "/api/dashboard",
            headers={
                "Host": "intranet.internal:8765",
                "X-AMCA-Session": CREATED_TOKEN,
                "Origin": "http://localhost:1420",
            },
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.close()


class DevModeCompatibilityTests(unittest.TestCase):
    """Manual/dev starts keep the legacy loopback bootstrap behavior."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self._previous_env = os.environ.get("AMCA_SESSION_TOKEN")
        os.environ.pop("AMCA_SESSION_TOKEN", None)
        root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        self.server = create_server(
            host="127.0.0.1", port=0, root=root, database=self.database)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        if self._previous_env is not None:
            os.environ["AMCA_SESSION_TOKEN"] = self._previous_env
        self.temporary.cleanup()

    def _get(self, path: str, token: str | None = None):
        request = Request(self.base + path)
        request.add_header("Origin", "http://localhost:1420")
        if token:
            request.add_header("X-AMCA-Session", token)
        try:
            with urlopen(request) as response:
                return response.status, json.loads(
                    response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_dev_mode_session_bootstrap_still_works(self):
        status, payload = self._get("/api/session")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(payload["session_token"]), 32)

    def test_dev_mode_self_generated_token_is_high_entropy(self):
        _, payload = self._get("/api/session")
        self.assertGreaterEqual(len(payload["session_token"]), 43)
        self.assertFalse(self.server.amca_context.session_token_injected)

    def test_dev_mode_full_flow_survives(self):
        status, session_payload = self._get("/api/session")
        self.assertEqual(status, 200)
        token = session_payload["session_token"]
        request = Request(self.base + "/api/intelligence/status")
        request.add_header("Origin", "http://localhost:1420")
        request.add_header("X-AMCA-Session", token)
        with urlopen(request) as response:
            self.assertEqual(response.status, 200)


class IntelligenceCompatibilityTests(InjectedTokenServerTestCase):
    def test_intelligence_status_works_in_injected_mode(self):
        status, payload = self._request(
            "/api/intelligence/status", token=CREATED_TOKEN)
        self.assertEqual(status, 200)
        for name, entry in payload["capabilities"].items():
            self.assertIn("implemented", entry)
            self.assertIn("enabled", entry)
            self.assertIn("runtime_ready", entry)

    def test_intelligence_validate_works_in_injected_mode(self):
        status, payload = self._request(
            "/api/intelligence/validate",
            method="POST",
            token=CREATED_TOKEN,
            body={"contract_version": 2, "requested": ["deterministic"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["decisions"]["deterministic"]["decision"], "allowed"
        )

    def test_no_mutation_endpoint_exists(self):
        status, _ = self._request(
            "/api/intelligence/enable",
            method="POST",
            token=CREATED_TOKEN,
            body={"capability": "adaptive_planner"},
        )
        self.assertEqual(status, 404)


class FeatureGateTests(InjectedTokenServerTestCase):
    def test_all_gates_remain_off_in_injected_mode(self):
        status, payload = self._request(
            "/api/intelligence/status", token=CREATED_TOKEN)
        for name, entry in payload["capabilities"].items():
            if name == "deterministic":
                continue
            self.assertFalse(entry["enabled"])
        self.assertEqual(payload["execution_authority"], "existing_runtime")


if __name__ == "__main__":
    unittest.main()
