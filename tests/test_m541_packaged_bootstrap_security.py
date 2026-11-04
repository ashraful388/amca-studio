"""M5.4.1 packaged bootstrap security verification tests.

Covers the bounded correction work order: exact Tauri origin
determination with lookalike rejection, production CORS/Origin
validation (null and missing Origin per the documented non-browser
policy), the security-mode contract (secure_bootstrap /
mutation_security_ready machine-enforced distinction), and the future
mutation guard (fail-closed in legacy dev HTTP bootstrap mode).
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

from amca.intelligence import (
    ContractError,
    ContractErrorCode,
    IntelligenceRuntimeBridge,
    Sensitivity,
    require_mutation_ready,
)
from amca.service import ALLOWED_ORIGINS, create_server

CREATED_TOKEN = "m541-injected-ephemeral-token-0123456789abcdef-0123456789"


def meta_factory(record_id):
    from amca.intelligence import ContractMetadata
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-m541",
        producer="m5.4.1-test",
        created_at="2026-08-27T00:00:00+00:00",
        sensitivity=Sensitivity.INTERNAL,
    )


# The ACTUAL origins this build uses (from tauri.conf.json devUrl and the
# Tauri Windows webview scheme) - the allowlist must contain exactly these
# and nothing else.
ACTUAL_BUILD_ORIGINS = {
    "http://127.0.0.1:1420": "Vite development Studio (IPv4)",
    "http://localhost:1420": "Vite development Studio (hostname)",
    "http://tauri.localhost": "Packaged Windows Studio (HTTP scheme)",
    "https://tauri.localhost": "Packaged Windows Studio (HTTPS scheme)",
    "tauri://localhost": "Packaged Studio (custom scheme)",
}

LOOKALIKE_ORIGINS = [
    "http://localhost:1420.evil.com",
    "http://localhost:14200",
    "http://localhost:1420.evil.com:1420",
    "https://localhost:1420",
    "http://evil-localhost:1420",
    "http://tauri.localhost.evil.com",
    "tauri://evil.com",
    "http://127.0.0.1:1421",
]


class TauriOriginDeterminationTests(unittest.TestCase):
    """Scenario 1 (M5.4.1): the allowlist contains exactly the origins the
    build actually uses, with no wildcard/suffix/substring matching, and
    every build origin is accepted while lookalikes are rejected."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True)
        root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        self.server = create_server(
            host="127.0.0.1", port=0, root=root, database=self.database)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        _, session_payload = self._get("/api/session")
        self.token = session_payload["session_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def _get(self, path: str, origin: str | None = None,
             token: str | None = None):
        request = Request(self.base + path)
        if origin is not None:
            request.add_header("Origin", origin)
        if token is not None:
            request.add_header("X-AMCA-Session", token)
        try:
            with urlopen(request) as response:
                return response.status, json.loads(
                    response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_allowlist_contains_exactly_the_build_origins(self):
        self.assertEqual(set(ALLOWED_ORIGINS), set(ACTUAL_BUILD_ORIGINS))

    def test_no_wildcard_or_substring_entries(self):
        for origin in ALLOWED_ORIGINS:
            self.assertNotIn("*", origin)

    def test_all_build_origins_accepted(self):
        for origin in ACTUAL_BUILD_ORIGINS:
            with self.subTest(origin=origin):
                status, _ = self._get(
                    "/api/health", origin=origin)
                self.assertEqual(status, 200)

    def test_lookalike_origins_rejected(self):
        for origin in LOOKALIKE_ORIGINS:
            with self.subTest(origin=origin):
                status, _ = self._get(
                    "/api/health", origin=origin)
                self.assertEqual(status, 403)


class ProductionOriginValidationTests(unittest.TestCase):
    """Scenario 2 (M5.4.1): production CORS/Origin behavior proven per
    origin class - accepted, attacker-lookalike rejected, null rejected,
    missing Origin handled per the documented non-browser policy."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        self.server = create_server(
            host="127.0.0.1", port=0, root=root, database=self.database)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        _, session_payload = self._raw_request("/api/session", origin=None)
        self.token = session_payload["session_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def _raw_request(self, path, *, origin=None, token=None,
                     method="GET"):
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        headers: dict = {}
        if origin is not None:
            headers["Origin"] = origin
        if token is not None:
            headers["X-AMCA-Session"] = token
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        status = response.status
        connection.close()
        return status, payload

    def test_exact_packaged_studio_origin_accepted(self):
        for origin in ("http://tauri.localhost", "https://tauri.localhost",
                       "tauri://localhost"):
            with self.subTest(origin=origin):
                request_headers = {"Origin": origin,
                                   "X-AMCA-Session": "irrelevant"}
                import http.client
                connection = http.client.HTTPConnection("127.0.0.1", self.port)
                connection.request(
                    "GET", "/api/health", headers=request_headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                connection.close()

    def test_similar_attacker_origins_rejected(self):
        for origin in ("http://tauri.localhost.evil.com",
                        "http://tauri-localhost",
                        "https://tauri.localhost.evil.com:1420"):
            with self.subTest(origin=origin):
                import http.client
                connection = http.client.HTTPConnection("127.0.0.1", self.port)
                connection.request("GET", "/api/health",
                                   headers={"Origin": origin})
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()

    def test_null_origin_rejected(self):
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        connection.request("GET", "/api/health", headers={"Origin": "null"})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.close()

    def test_missing_origin_handled_per_non_browser_policy(self):
        # non-browser clients (curl, scripts) send no Origin header; the
        # request proceeds and is still protected by session auth
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        connection.close()


class SecurityModeContractTests(unittest.TestCase):
    """Scenario 4 (M5.4.1): machine-enforced secure_bootstrap distinction."""

    def test_legacy_dev_http_mode_is_insecure_and_fail_closed(self):
        bridge = IntelligenceRuntimeBridge(
            meta_factory=meta_factory, bootstrap_mode="legacy_dev_http"
        )
        context = bridge.security_context()
        self.assertEqual(context["bootstrap_mode"], "legacy_dev_http")
        self.assertFalse(context["secure_bootstrap"])
        self.assertFalse(context["mutation_security_ready"])
        with self.assertRaises(ContractError) as ctx:
            require_mutation_ready(bridge)
        self.assertEqual(ctx.exception.code, ContractErrorCode.BLOCKED)

    def test_tauri_injected_mode_is_secure_and_mutation_ready(self):
        bridge = IntelligenceRuntimeBridge(
            meta_factory=meta_factory, bootstrap_mode="tauri_injected"
        )
        context = bridge.security_context()
        self.assertEqual(context["bootstrap_mode"], "tauri_injected")
        self.assertTrue(context["secure_bootstrap"])
        self.assertTrue(context["mutation_security_ready"])
        self.assertEqual(
            require_mutation_ready(bridge)["secure_bootstrap"], True
        )

    def test_unknown_bootstrap_mode_rejected(self):
        with self.assertRaises(ContractError):
            IntelligenceRuntimeBridge(
                meta_factory=meta_factory, bootstrap_mode="teleport"
            )

    def _make_context(self):
        import os
        from amca.service import ServiceContext
        self.temporary_context = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        previous = os.environ.get("AMCA_SESSION_TOKEN")
        os.environ["AMCA_SESSION_TOKEN"] = (
            "m541-injected-token-0123456789abcdef-0123456789"
        )
        if previous is None:
            self.addCleanup(
                lambda: os.environ.pop("AMCA_SESSION_TOKEN", None))
        else:
            os.environ["AMCA_SESSION_TOKEN"] = previous
            self.addCleanup(
                lambda: os.environ.__setitem__(
                    "AMCA_SESSION_TOKEN", previous))
        context = ServiceContext(
            root=Path(self.temporary_context.name),
            database=Path(self.temporary_context.name) / "amca.db",
            benchmarks=Path(self.temporary_context.name) / "benchmarks",
        )
        self.addCleanup(context.close)
        self.addCleanup(self.temporary_context.cleanup)
        return context

    def _make_server(self, injected: bool):
        import os
        import shutil
        self.temp_dir = str(Path(tempfile.gettempdir()) / f"m541-{id(self)}")
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        if injected:
            os.environ["AMCA_SESSION_TOKEN"] = (
                "m541-injected-token-0123456789abcdef-0123456789"
            )
        else:
            os.environ.pop("AMCA_SESSION_TOKEN", None)
        self.server3 = create_server(
            host="127.0.0.1", port=0,
            root=Path(__file__).resolve().parents[1],
            database=Path(self.temp_dir) / "amca.db")
        self.thread3 = threading.Thread(
            target=self.server3.serve_forever, daemon=True)
        self.thread3.start()
        self.addCleanup(self._cleanup_server3)
        return self.server3.amca_context

    def _cleanup_server3(self):
        import os
        import shutil
        self.server3.shutdown()
        self.server3.server_close()
        self.thread3.join(timeout=2)
        os.environ.pop("AMCA_SESSION_TOKEN", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_service_context_reports_correct_mode(self):
        context = self._make_server(injected=True)
        self.assertEqual(
            context.intelligence_runtime.bootstrap_mode, "tauri_injected"
        )
        self.assertTrue(context.intelligence_runtime.secure_bootstrap)
        self.assertTrue(context.intelligence_runtime.mutation_security_ready)

    def test_service_context_legacy_mode_without_env(self):
        import os
        context = self._make_server(injected=False)
        self.assertEqual(
            context.intelligence_runtime.bootstrap_mode, "legacy_dev_http"
        )
        self.assertFalse(context.intelligence_runtime.secure_bootstrap)



class FutureMutationGuardTests(unittest.TestCase):
    """Scenario 5 (M5.4.1): future state-changing intelligence APIs are
    impossible unless the security context reports the secure bootstrap."""

    def test_legacy_dev_http_mode_blocks_mutation_authority(self):
        bridge = IntelligenceRuntimeBridge(
            meta_factory=meta_factory, bootstrap_mode="legacy_dev_http"
        )
        with self.assertRaises(ContractError) as ctx:
            require_mutation_ready(bridge)
        self.assertEqual(ctx.exception.code, ContractErrorCode.BLOCKED)
        self.assertFalse(bridge.mutation_security_ready)

    def test_secure_mode_grants_mutation_readiness(self):
        bridge = IntelligenceRuntimeBridge(
            meta_factory=meta_factory, bootstrap_mode="tauri_injected"
        )
        context = require_mutation_ready(bridge)
        self.assertTrue(context["mutation_security_ready"])


if __name__ == "__main__":
    unittest.main()
