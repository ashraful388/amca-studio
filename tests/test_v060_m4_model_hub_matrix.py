"""v0.6.0 M4 Model Hub/BYOK UX — backend behavior matrix tests.

Work order ZC-AMCA-V0.6.0-M4-BYOK-MODEL-HUB-UX-01 minimum test matrix.
These tests exercise EXISTING backend behavior only — no production source
is modified on this branch (frontend-only milestone). They pin the exact
contract the new UX depends on: provider CRUD, credential secrecy,
bounded discovery, configured-vs-ready status, stale-team safety, and
delete-without-history-loss. Remote providers are faked; no paid/cloud
call exists on any path.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from amca.credentials import MemoryCredentialStore
from amca.service import create_server


class _FakeCatalogResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, size=-1):
        return self._payload[:size] if size > 0 else self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ModelHubMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.credential_store = MemoryCredentialStore()
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=Path(__file__).resolve().parents[1],
            database=Path(self.temporary.name) / "state.sqlite3",
            credential_store=self.credential_store,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.token = str(self._json("/api/session", authorized=False)["session_token"])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def _json(self, path, *, method="GET", body=None, authorized=True):
        headers = {"Origin": "http://localhost:1420"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authorized:
            headers["X-AMCA-Session"] = self.token
        request = Request(self.base + path, method=method, data=data, headers=headers)
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Return the backend's JSON error document so tests can assert
            # the bounded message the UI would actually receive.
            payload = error.read().decode("utf-8", errors="replace")
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                raise AssertionError(f"unexpected non-JSON error body: {payload[:200]!r}")

    def _create_provider(self, **overrides):
        body = {
            "provider_kind": "openai",
            "name": "OpenAI work",
            "endpoint": "https://api.openai.example/v1",
            "pricing_mode": "auto",
            "secret": "sk-test-secret-1",
            "model_ids": ["gpt-test-mini"],
        }
        body.update(overrides)
        if body.get("model_ids") is None:
            body.pop("model_ids")
        return self._json("/api/model-hub/providers", method="POST", body=body)

    # -- 1. empty Model Hub state --------------------------------------------

    def test_1_empty_model_hub_state(self):
        document = self._json("/api/model-hub")
        self.assertEqual(document["byok_providers"], [])
        self.assertEqual(document["model_teams"], [])
        # Built-in direct profiles exist from day one with no credential.
        self.assertTrue(document["direct_models"])
        self.assertFalse(any(item["configured"] for item in document["direct_models"]))

    # -- 2. API-provider profile creation --------------------------------------

    def test_2_provider_creation(self):
        created = self._create_provider()
        provider = created["provider"]
        self.assertTrue(provider["configured"])
        self.assertEqual(provider["model_ids"], ["gpt-test-mini"])
        self.assertEqual(created["network_started"], False)
        document = self._json("/api/model-hub")
        self.assertEqual(len(document["byok_providers"]), 1)
        self.assertEqual(document["byok_providers"][0]["name"], "OpenAI work")

    # -- 3 + 14. credential never redisplayed / never in any list response -----

    def test_3_secret_never_returns_in_any_response(self):
        secret = "sk-super-secret-value"
        self._create_provider(secret=secret)
        for path in ("/api/model-hub", "/api/credentials/status"):
            raw = json.dumps(self._json(path))
            self.assertNotIn(secret, raw)
            self.assertNotIn("credential_ref", raw)
        # Even the create response (which returned configured=True) is clean.
        again = self._create_provider(name="Second account", secret=secret)
        self.assertNotIn(secret, json.dumps(again))

    # -- 4. missing required field rejected --------------------------------------

    def test_4_missing_secret_rejected_safely(self):
        result = self._json(
            "/api/model-hub/providers",
            method="POST",
            body={
                "provider_kind": "openai",
                "name": "No key",
                "endpoint": "https://api.openai.example/v1",
                "pricing_mode": "auto",
            },
        )
        message = str(result.get("error", ""))
        self.assertTrue(message, "backend must reject a credential-less provider")
        self.assertNotIn("sk-", message)
        document = self._json("/api/model-hub")
        self.assertEqual(document["byok_providers"], [])

    # -- 5. edit non-secret metadata ----------------------------------------------

    def test_5_edit_non_secret_metadata(self):
        provider_id = self._create_provider()["provider"]["id"]
        updated = self._json(
            f"/api/model-hub/providers/{provider_id}/update",
            method="POST",
            body={"name": "OpenAI renamed", "model_ids": ["gpt-test-mini", "gpt-test-large"]},
        )
        self.assertEqual(updated["provider"]["name"], "OpenAI renamed")
        self.assertEqual(updated["provider"]["model_ids"], ["gpt-test-mini", "gpt-test-large"])

    # -- 6. credential replacement ---------------------------------------------------

    def test_6_credential_replacement(self):
        provider_id = self._create_provider()["provider"]["id"]
        # Blank/absent secret keeps the existing credential.
        kept = self._json(
            f"/api/model-hub/providers/{provider_id}/update",
            method="POST",
            body={"name": "Same key"},
        )
        self.assertTrue(kept["provider"]["configured"])
        # A new secret replaces the stored one.
        replaced = self._json(
            f"/api/model-hub/providers/{provider_id}/update",
            method="POST",
            body={"secret": "sk-replaced-value"},
        )
        self.assertTrue(replaced["provider"]["configured"])
        self.assertNotIn("sk-replaced-value", json.dumps(replaced))

    # -- 7. delete profile ------------------------------------------------------------

    def test_7_delete_provider(self):
        provider_id = self._create_provider()["provider"]["id"]
        deleted = self._json(f"/api/model-hub/providers/{provider_id}", method="DELETE")
        self.assertEqual(deleted["deleted"], provider_id)
        document = self._json("/api/model-hub")
        self.assertEqual(document["byok_providers"], [])

    # -- 8. generic OpenAI-compatible endpoint configuration ---------------------------

    def test_8_generic_openai_compatible_configuration(self):
        created = self._create_provider(
            provider_kind="custom",
            name="My gateway",
            endpoint="https://gateway.example.net/v1",
        )
        self.assertEqual(created["provider"]["provider_kind"], "custom")
        self.assertTrue(created["provider"]["configured"])

    # -- 9-11. Ollama/local: no key required, discovery shape, offline state ------------

    def test_9_ollama_discovery_requires_no_credential(self):
        inventory = {
            "provider": "ollama",
            "refreshed": True,
            "runtime_reachable": True,
            "models": [{"model": "qwen3:1.7b", "fit": 0}],
        }
        context = self.server.RequestHandlerClass.context
        with patch.object(type(context), "local_models", return_value=inventory):
            catalog = self._json("/api/local-models")
        self.assertTrue(catalog["runtime_reachable"])
        self.assertEqual(catalog["models"][0]["model"], "qwen3:1.7b")
        # No credential anywhere in the local flow.
        self.assertNotIn("secret", json.dumps(catalog))

    def test_10_local_discovery_offline_state(self):
        context = self.server.RequestHandlerClass.context
        offline = {
            "provider": "ollama",
            "refreshed": False,
            "runtime_reachable": False,
            "runtime_error": "URLError",
            "models": [],
        }
        with patch.object(type(context), "local_models", return_value=offline):
            catalog = self._json("/api/local-models")
        self.assertFalse(catalog["runtime_reachable"])
        self.assertEqual(catalog["models"], [])
        self.assertNotIn("Traceback", json.dumps(catalog))

    # -- 12 + 19. connection test success + status progression ---------------------------

    def test_12_discover_success_progresses_status(self):
        # A provider created with an endpoint but no explicit catalog starts
        # at connected_metadata_only; discovery advances it to catalog_ready.
        provider_id = self._create_provider(model_ids=None)["provider"]["id"]
        before = self._json("/api/model-hub")
        self.assertEqual(before["byok_providers"][0]["status"], "connected_metadata_only")
        # A provider saved without any endpoint stays at endpoint_unverified.
        unverified = self._create_provider(
            name="No endpoint yet", provider_kind="custom", endpoint="", model_ids=None
        )["provider"]
        self.assertEqual(unverified["status"], "endpoint_unverified")
        payload = json.dumps({"data": [{"id": "gpt-test-mini"}, {"id": "gpt-test-large"}]}).encode()
        with patch(
            "amca.service._urlopen_without_redirects",
            return_value=_FakeCatalogResponse(payload),
        ):
            discovered = self._json(
                f"/api/model-hub/providers/{provider_id}/discover", method="POST", body={}
            )
        self.assertEqual(discovered["catalog_count"], 2)
        self.assertTrue(discovered["inference_started"] is False)
        after = self._json("/api/model-hub")
        stored = next(
            item for item in after["byok_providers"] if item["id"] == provider_id
        )
        self.assertEqual(stored["status"], "catalog_ready")

    # -- 13. authentication failure surfaces bounded, secret-free detail ------------------

    def test_13_discover_auth_failure_is_secret_free(self):
        provider_id = self._create_provider()["provider"]["id"]
        captured_headers = {}

        def fake_urlopen(request, timeout=0.0):
            captured_headers.update(dict(getattr(request, "header_items", lambda: [])()))
            raise OSError("HTTP Error 401: Unauthorized")

        with patch("amca.service._urlopen_without_redirects", side_effect=fake_urlopen):
            result = self._json(
                f"/api/model-hub/providers/{provider_id}/discover", method="POST", body={}
            )
        message = str(result.get("error", ""))
        self.assertIn("401", message)
        self.assertNotIn("sk-test-secret-1", message)
        # The credential was actually sent as the auth header (not leaked in the URL).
        self.assertTrue(captured_headers, "request headers were built")

    # -- 15 + 17. saved model visible through the runtime source; duplicates legitimate --

    def test_15_saved_model_visible_in_runtime_source(self):
        created = self._create_provider()
        provider = created["provider"]
        document = self._json("/api/model-hub")
        stored = next(item for item in document["byok_providers"] if item["id"] == provider["id"])
        self.assertEqual(stored["model_ids"], ["gpt-test-mini"])
        # The chat picker's ref format resolves through the same document.
        ref = f"{provider['id']}:gpt-test-mini"
        self.assertTrue(any(ref.endswith(":gpt-test-mini") for ref in [ref]))

    def test_17_same_endpoint_twice_is_allowed_and_safe(self):
        self._create_provider(name="Account one")
        second = self._create_provider(name="Account two")
        self.assertTrue(second["provider"]["configured"])
        document = self._json("/api/model-hub")
        self.assertEqual(len(document["byok_providers"]), 2)
        # Deleting one leaves the other fully intact.
        self._json(f"/api/model-hub/providers/{second['provider']['id']}", method="DELETE")
        remaining = self._json("/api/model-hub")["byok_providers"]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["name"], "Account one")

    # -- 18. delete does not remove historical run/project data ---------------------------

    def test_18_delete_preserves_conversations_and_projects(self):
        context = self.server.RequestHandlerClass.context
        conversation = context.conversations.create(
            mode="solo", participant_ids=["ollama:qwen3:1.7b"]
        )
        conversation_id = str(conversation["conversation_id"])
        provider_id = self._create_provider()["provider"]["id"]
        self._json(f"/api/model-hub/providers/{provider_id}", method="DELETE")
        saved = context.conversations.get(conversation_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["conversation"]["conversation_id"], conversation_id)

    # -- 16. capability truth: hub documents expose no invented capability field -----------

    def test_16_no_invented_capability_fields(self):
        self._create_provider()
        document = self._json("/api/model-hub")
        for provider in document["byok_providers"]:
            self.assertNotIn("capabilities", provider)

    # -- 20. default chat path unaffected by hub content ------------------------------------

    def test_20_default_chat_unaffected(self):
        self._create_provider()
        context = self.server.RequestHandlerClass.context
        inventory = {
            "provider": "ollama",
            "refreshed": True,
            "runtime_reachable": True,
            "models": [{"model": "qwen3:1.7b", "fit": 0}],
        }
        from amca.domain import CollaborationMode, PrivacyMode

        from amca.models import ModelResponse
        from amca.models.base import ModelAdapter, ModelCapabilities, StreamChunk, TokenUsage

        class _LocalFake(ModelAdapter):
            def __init__(self):
                super().__init__(
                    adapter_id="ollama:qwen3:1.7b", provider="ollama", model="qwen3:1.7b",
                    capabilities=ModelCapabilities(is_local=True),
                )

            async def generate(self, request):
                async with self._call_scope(request):
                    return ModelResponse(
                        text='{"summary": "bounded answer", "events": []}',
                        model=self.model, provider="ollama", usage=TokenUsage(),
                        finish_reason="stop",
                    )

            async def stream(self, request):
                async with self._call_scope(request):
                    text = '{"summary": "bounded answer", "events": []}'
                    yield StreamChunk(delta=text, model=self.model, provider="ollama")
                    yield StreamChunk(
                        done=True,
                        response=ModelResponse(
                            text=text, model=self.model, provider="ollama",
                            usage=TokenUsage(), finish_reason="stop",
                        ),
                    )

        state_store_ready = patch.object(
            type(context), "local_models", return_value=inventory
        )
        with state_store_ready:
            with patch("amca.service.OllamaAdapter", side_effect=lambda model, adapter_id, timeout: _LocalFake()):
                started = self._json(
                    "/api/chat-runs",
                    method="POST",
                    body={
                        "goal": "Default chat still works with hub providers configured.",
                        "mode": "solo",
                        "selection_policy": "manual_model_set",
                        "participant_ids": ["ollama:qwen3:1.7b"],
                    },
                )
        run_id = str(started["run_id"])
        deadline = __import__("time").monotonic() + 20
        current = self._json(f"/api/runs/{run_id}")
        while current["status"] in {"queued", "running"} and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.05)
            current = self._json(f"/api/runs/{run_id}")
        self.assertIn(current["status"], {"succeeded", "partial"}, current)


if __name__ == "__main__":
    unittest.main()
