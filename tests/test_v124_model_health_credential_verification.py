"""Round-10 regression tests: model-health verification must use the real key.

Defect this file pins:

1. ``POST /api/model-hub/verify`` reached the provider WITHOUT the stored
   credential (the browser never holds an API key), so every BYOK provider
   answered HTTP 401.  AMCA then recorded a false AUTH_REQUIRED verdict and
   the composer disabled the model — for a key that works.

2. A verdict describes the credential that was in use when it was observed.
   Replacing an API key left the old verdict in place, so a model stayed
   blocked by a failure that belonged to a configuration that no longer
   exists.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import urllib.error
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

_contexts: list = []


def _close_all_contexts() -> None:
    while _contexts:
        context = _contexts.pop()
        try:
            context.close()
        except Exception:
            pass


@contextlib.contextmanager
def _service_context(td_path: str):
    import contextlib as _contextlib

    with _contextlib.ExitStack() as stack:
        kwargs = {} if td_path is None else {"dir": td_path}
        yield stack.enter_context(
            tempfile.TemporaryDirectory(ignore_cleanup_errors=True, **kwargs)
        )
        _close_all_contexts()


def _context(td: str, credential_store=None):
    from amca.service import ServiceContext

    kwargs = {}
    if credential_store is not None:
        kwargs["credential_store"] = credential_store
    context = ServiceContext(
        root=Path(td),
        database=Path(td) / "amca.sqlite3",
        benchmarks=Path(td),
        **kwargs,
    )
    _contexts.append(context)
    return context


def _register_provider(context, *, name="Test Router", endpoint="https://gateway.example/v1",
                       provider_kind="openai", model_ids=("test-model",)):
    """Register a provider so the credential can be resolved for it."""

    provider, credential_ref = context.model_hub_store.create_provider(
        {
            "provider_kind": provider_kind,
            "name": name,
            "endpoint": endpoint,
            "model_ids": list(model_ids),
        }
    )
    return provider, credential_ref


def _ok_response():
    response = io.BytesIO(b"{}")
    response.status = 200
    return response


def test_verify_uses_the_stored_credential_when_the_caller_sends_none(monkeypatch):
    """The probe must carry the key AMCA already holds for that provider."""

    from amca.credentials import MemoryCredentialStore

    seen: list = []

    def fake_urlopen(request, timeout=0):
        seen.append(request.get_header("Authorization"))
        return _ok_response()

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(context)
        store.set(credential_ref, "stored-key")
        outcome = context.verify_model_configuration(
            kind="hub",
            ref=f"{provider['id']}:test-model",
            endpoint="https://gateway.example/v1",
            model="test-model",
        )
        assert outcome["state"] == "READY", outcome
        assert outcome["runnable"] is True
        assert seen == ["Bearer stored-key"]


def test_verify_uses_the_stored_credential_for_a_direct_profile(monkeypatch):
    from amca.credentials import MemoryCredentialStore

    seen: list = []

    def fake_urlopen(request, timeout=0):
        seen.append(request.get_header("Authorization"))
        return _ok_response()

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    store.set("tabitoken.claude-opus-5", "profile-key")
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        outcome = context.verify_model_configuration(
            kind="profile",
            ref="tabitoken.claude-opus-5",
            endpoint="https://tabitoken.com/v1",
            model="claude-opus-5",
        )
        assert outcome["state"] == "READY", outcome
        assert seen == ["Bearer profile-key"]


def test_verify_without_any_stored_credential_reports_auth_required(monkeypatch):
    """No key at all is a true AUTH_REQUIRED, not a false one."""

    def fake_urlopen(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", None, None)

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    from amca.credentials import MemoryCredentialStore

    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, _credential_ref = _register_provider(context)
        outcome = context.verify_model_configuration(
            kind="hub",
            ref=f"{provider['id']}:test-model",
            endpoint="https://gateway.example/v1",
            model="test-model",
        )
        assert outcome["state"] == "AUTH_REQUIRED"
        assert outcome["http_status"] == 401


def test_the_stored_key_is_not_sent_to_an_endpoint_the_provider_does_not_use(monkeypatch):
    """A caller may not aim a stored key at an address of its own."""

    from amca.credentials import MemoryCredentialStore

    seen: list = []

    def fake_urlopen(request, timeout=0):
        seen.append(request.get_header("Authorization"))
        return _ok_response()

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(context)
        store.set(credential_ref, "stored-key")
        context.verify_model_configuration(
            kind="hub",
            ref=f"{provider['id']}:test-model",
            endpoint="https://attacker.example/v1",
            model="test-model",
        )
        assert seen == [None]


def test_anthropic_is_probed_with_its_own_auth_scheme(monkeypatch):
    """Anthropic answers /v1/messages with x-api-key, not Bearer."""

    from amca.credentials import MemoryCredentialStore

    seen: list = []

    def fake_urlopen(request, timeout=0):
        seen.append((request.full_url, request.get_header("X-api-key"),
                     request.get_header("Authorization"), json.loads(request.data)))
        return _ok_response()

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(
            context,
            name="Anthropic",
            endpoint="https://api.anthropic.com",
            provider_kind="anthropic",
        )
        store.set(credential_ref, "anthropic-key")
        outcome = context.verify_model_configuration(
            kind="hub",
            ref=f"{provider['id']}:claude-opus-5",
            endpoint="https://api.anthropic.com",
            model="claude-opus-5",
        )
        assert outcome["state"] == "READY", outcome
        url, x_api_key, bearer, body = seen[0]
        assert url == "https://api.anthropic.com/v1/messages"
        assert x_api_key == "anthropic-key"
        assert bearer is None
        assert body["messages"] == [{"role": "user", "content": "ping"}]
        assert body["max_tokens"] <= 16


def test_google_is_probed_with_its_own_auth_scheme(monkeypatch):
    """Google answers :generateContent with x-goog-api-key."""

    from amca.credentials import MemoryCredentialStore

    seen: list = []

    def fake_urlopen(request, timeout=0):
        seen.append((request.full_url, request.get_header("X-goog-api-key"),
                     request.get_header("Authorization"), json.loads(request.data)))
        return _ok_response()

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(
            context,
            name="Google",
            endpoint="https://generativelanguage.googleapis.com/v1beta",
            provider_kind="google",
        )
        store.set(credential_ref, "google-key")
        outcome = context.verify_model_configuration(
            kind="hub",
            ref=f"{provider['id']}:gemini-2.0-flash",
            endpoint="https://generativelanguage.googleapis.com/v1beta",
            model="gemini-2.0-flash",
        )
        assert outcome["state"] == "READY", outcome
        url, x_goog, bearer, body = seen[0]
        assert url.endswith("/models/gemini-2.0-flash:generateContent")
        assert x_goog == "google-key"
        assert bearer is None
        assert body["contents"] == [{"role": "user", "parts": [{"text": "ping"}]}]


def test_an_explicit_credential_still_wins(monkeypatch):
    """The caller's explicit credential overrides the stored one."""

    from amca.credentials import MemoryCredentialStore

    seen: list = []

    def fake_urlopen(request, timeout=0):
        seen.append(request.get_header("Authorization"))
        return _ok_response()

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    store.set("model-hub.provider.test", "stored-key")
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        context.verify_model_configuration(
            kind="hub",
            ref="provider.test:test-model",
            endpoint="https://gateway.example/v1",
            model="test-model",
            credential="explicit-key",
        )
        assert seen == ["Bearer explicit-key"]


def test_replacing_a_provider_key_drops_that_providers_verdicts():
    """A verdict measured with the old key must not block the new one."""

    from amca.credentials import MemoryCredentialStore
    from amca.model_health import AUTH_REQUIRED, record_for_config

    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = context.model_hub_store.create_provider(
            {
                "provider_kind": "openai",
                "name": "Test Router",
                "endpoint": "https://gateway.example/v1",
                "model_ids": ["model-a", "model-b"],
            }
        )
        provider_id = str(provider["id"])
        store.set(credential_ref, "first-key")
        for model in ("model-a", "model-b"):
            context.model_health_store.record(
                record_for_config(
                    kind="hub",
                    ref=f"{provider_id}:{model}",
                    endpoint="https://gateway.example/v1",
                    model=model,
                    state=AUTH_REQUIRED,
                    detail="provider returned HTTP 401",
                    evidence="connection_test",
                )
            )
        assert len(context.model_health_store.snapshot()) == 2

        context.update_hub_provider(
            provider_id,
            {"secret": "replacement-key", "model_ids": ["model-a", "model-b"]},
        )

        # The old verdicts described the old credential: they are gone, so the
        # models return to "Unverified" (usable, honestly untested).
        assert context.model_health_store.snapshot() == {}
        assert store.get(credential_ref) == "replacement-key"


def test_replacing_a_key_keeps_other_providers_verdicts():
    from amca.credentials import MemoryCredentialStore
    from amca.model_health import AUTH_REQUIRED, record_for_config

    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        first, first_ref = context.model_hub_store.create_provider(
            {
                "provider_kind": "openai",
                "name": "First",
                "endpoint": "https://first.example/v1",
                "model_ids": ["model-a"],
            }
        )
        second, second_ref = context.model_hub_store.create_provider(
            {
                "provider_kind": "openai",
                "name": "Second",
                "endpoint": "https://second.example/v1",
                "model_ids": ["model-b"],
            }
        )
        store.set(first_ref, "first-key")
        store.set(second_ref, "second-key")
        for provider_id, model, endpoint in (
            (first["id"], "model-a", "https://first.example/v1"),
            (second["id"], "model-b", "https://second.example/v1"),
        ):
            context.model_health_store.record(
                record_for_config(
                    kind="hub",
                    ref=f"{provider_id}:{model}",
                    endpoint=endpoint,
                    model=model,
                    state=AUTH_REQUIRED,
                    detail="provider returned HTTP 401",
                    evidence="connection_test",
                )
            )

        context.update_hub_provider(
            str(first["id"]),
            {"secret": "replacement-key", "model_ids": ["model-a"]},
        )

        snapshot = context.model_health_store.snapshot()
        assert len(snapshot) == 1
        assert all(
            entry["ref"].startswith(f"{second['id']}:") for entry in snapshot.values()
        )


def test_replacing_a_profile_credential_drops_the_profile_verdict():
    from amca.credentials import MemoryCredentialStore
    from amca.model_health import AUTH_REQUIRED, record_for_config

    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        context.model_health_store.record(
            record_for_config(
                kind="profile",
                ref="tabitoken.claude-opus-5",
                endpoint="https://tabitoken.com/v1",
                model="claude-opus-5",
                state=AUTH_REQUIRED,
                detail="provider returned HTTP 403",
                evidence="connection_test",
            )
        )
        assert len(context.model_health_store.snapshot()) == 1

        context.save_credential("tabitoken.claude-opus-5", "new-key")

        assert context.model_health_store.snapshot() == {}


def test_delete_for_ref_prefix_only_removes_matching_kind():
    from amca.model_health import ModelHealthStore, record_for_config

    with _service_context(None) as td:
        store = ModelHealthStore(Path(td) / "health.db")
        for kind, ref in (
            ("hub", "provider.test:model-a"),
            ("hub", "provider.test:model-b"),
            ("hub", "provider.other:model-c"),
            ("profile", "provider.test"),
        ):
            store.record(
                record_for_config(
                    kind=kind,
                    ref=ref,
                    endpoint="https://e",
                    model="m",
                    state="AUTH_REQUIRED",
                    detail="d",
                    evidence="e",
                )
            )
        removed = store.delete_for_ref_prefix(kind="hub", ref_prefix="provider.test")
        assert removed == 2
        remaining = {entry["ref"] for entry in store.snapshot().values()}
        assert remaining == {"provider.other:model-c", "provider.test"}


def test_kimi_is_probed_with_max_completion_tokens(monkeypatch):
    """KimiAdapter renames max_tokens; a probe that sends the plain shape
    would be answered with HTTP 400 and report a healthy model as down."""

    from amca.credentials import MemoryCredentialStore

    seen: list = []

    def fake_urlopen(request, timeout=0):
        seen.append(json.loads(request.data))
        return _ok_response()

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(
            context,
            name="Moonshot",
            endpoint="https://api.moonshot.ai/v1",
            provider_kind="moonshot",
        )
        store.set(credential_ref, "kimi-key")
        outcome = context.verify_model_configuration(
            kind="hub",
            ref=f"{provider['id']}:moonshotai/kimi-k3",
            endpoint="https://api.moonshot.ai/v1",
            model="moonshotai/kimi-k3",
        )
        assert outcome["state"] == "READY", outcome
        body = seen[0]
        assert body["max_completion_tokens"] == 16
        assert "max_tokens" not in body


def test_removing_a_model_from_a_provider_drops_its_verdict():
    """A removed model takes its verdict with it."""

    from amca.credentials import MemoryCredentialStore
    from amca.model_health import AUTH_REQUIRED, record_for_config

    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(
            context, model_ids=("model-a", "model-b")
        )
        store.set(credential_ref, "key")
        for model in ("model-a", "model-b"):
            context.model_health_store.record(
                record_for_config(
                    kind="hub",
                    ref=f"{provider['id']}:{model}",
                    endpoint="https://gateway.example/v1",
                    model=model,
                    state=AUTH_REQUIRED,
                    detail="provider returned HTTP 401",
                    evidence="connection_test",
                )
            )
        assert len(context.model_health_store.snapshot()) == 2

        context.update_hub_provider(
            str(provider["id"]),
            {"model_ids": ["model-a"]},
        )

        remaining = {entry["ref"] for entry in context.model_health_store.snapshot().values()}
        assert remaining == {f"{provider['id']}:model-a"}


def test_the_probe_uses_the_same_timeout_as_the_runtime(monkeypatch):
    """A probe stricter than the runtime measures something the runtime never
    asks for: a model that answers in 40s was 'Provider down' and blocked
    while the same model would have run fine."""

    from amca import PROVIDER_REQUEST_TIMEOUT_SECONDS
    from amca.credentials import MemoryCredentialStore

    seen: list = []

    def fake_urlopen(request, timeout=0):
        seen.append(timeout)
        return _ok_response()

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(context)
        store.set(credential_ref, "stored-key")
        context.verify_model_configuration(
            kind="hub",
            ref=f"{provider['id']}:test-model",
            endpoint="https://gateway.example/v1",
            model="test-model",
        )
        assert seen == [PROVIDER_REQUEST_TIMEOUT_SECONDS]
        # The runtime adapter uses the same ceiling.
        from amca.models.openai_compatible import OpenAICompatibleAdapter

        adapter = OpenAICompatibleAdapter(
            model="test-model",
            adapter_id="provider.test:test-model",
            provider="custom",
            api_key_env="AMCA_TEST_KEY",
            base_url="https://gateway.example/v1",
            allow_remote=True,
            api_key="stored-key",
        )
        assert adapter._timeout == PROVIDER_REQUEST_TIMEOUT_SECONDS


def test_a_timeout_is_reported_honestly(monkeypatch):
    """A provider that does not answer is not 'unreachable'."""

    import urllib.error

    from amca.credentials import MemoryCredentialStore

    def fake_urlopen(request, timeout=0):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(context)
        store.set(credential_ref, "stored-key")
        outcome = context.verify_model_configuration(
            kind="hub",
            ref=f"{provider['id']}:test-model",
            endpoint="https://gateway.example/v1",
            model="test-model",
        )
        assert outcome["state"] == "PROVIDER_UNAVAILABLE", outcome
        assert "did not answer" in outcome["detail"], outcome["detail"]


def test_an_account_without_credit_is_not_reported_as_provider_down(monkeypatch):
    """The key and the model are fine; the BALANCE is not. Reporting this as
    "Provider down" sends the operator to repair a connection that works."""

    import urllib.error

    from amca.credentials import MemoryCredentialStore

    def fake_urlopen(request, timeout=0):
        body = json.dumps(
            {
                "error": {
                    "message": "credit insufficient balance: balance=144 required=702",
                    "type": "api_error",
                    "code": "insufficient_user_quota",
                }
            }
        ).encode("utf-8")
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", None, io.BytesIO(body))

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(context)
        store.set(credential_ref, "stored-key")
        outcome = context.verify_model_configuration(
            kind="hub",
            ref=f"{provider['id']}:test-model",
            endpoint="https://gateway.example/v1",
            model="test-model",
        )
        assert outcome["state"] == "QUOTA_EXHAUSTED", outcome
        assert outcome["runnable"] is False
        assert "credit" in outcome["detail"].lower()


def test_a_transient_failure_is_told_apart_from_a_configuration_defect():
    """A provider hiccup and a broken configuration are different problems.

    The RUN path refuses either one (after re-verifying once, with a guided
    message), but the COMPOSER only excludes a model whose CONFIGURATION is
    wrong: a provider that was busy or slow says nothing about the key or
    the model id, and locking the model out until someone re-measures it by
    hand is how a working model ends up unusable.
    """

    from amca.credentials import MemoryCredentialStore
    from amca.model_health import (
        AUTH_REQUIRED,
        MODEL_NOT_FOUND,
        PROVIDER_UNAVAILABLE,
        is_configuration_defect,
        record_for_config,
    )

    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(
            context, model_ids=("transient", "defect", "missing")
        )
        store.set(credential_ref, "key")
        states = {
            "transient": PROVIDER_UNAVAILABLE,
            "defect": AUTH_REQUIRED,
            "missing": MODEL_NOT_FOUND,
        }
        for model, state in states.items():
            context.model_health_store.record(
                record_for_config(
                    kind="hub",
                    ref=f"{provider['id']}:{model}",
                    endpoint="https://gateway.example/v1",
                    model=model,
                    state=state,
                    detail="d",
                    evidence="connection_test",
                )
            )
        health_map = context.model_health_store.snapshot()
        blocked, _configs = context._blocked_model_configs([], health_map)
        # The run path refuses a model that failed its last verification...
        assert f"{provider['id']}:transient" in blocked
        assert f"{provider['id']}:defect" in blocked
        assert f"{provider['id']}:missing" in blocked
        # ... but only a configuration defect excludes the model from
        # selection.
        assert is_configuration_defect("AUTH_REQUIRED") is True
        assert is_configuration_defect("MODEL_NOT_FOUND") is True
        assert is_configuration_defect("QUOTA_EXHAUSTED") is True
        assert is_configuration_defect("PROVIDER_UNAVAILABLE") is False
        assert is_configuration_defect("LOCAL_RUNTIME_OFFLINE") is False


def test_the_probe_identifies_amca_with_a_user_agent(monkeypatch):
    """Python's default User-Agent is blocked by some gateways (Cloudflare
    answers it with HTTP 403), which looks like a rejected credential."""

    from amca import USER_AGENT
    from amca.credentials import MemoryCredentialStore

    seen: list = []

    def fake_urlopen(request, timeout=0):
        seen.append(request.get_header("User-agent"))
        return _ok_response()

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(context)
        store.set(credential_ref, "stored-key")
        context.verify_model_configuration(
            kind="hub",
            ref=f"{provider['id']}:test-model",
            endpoint="https://gateway.example/v1",
            model="test-model",
        )
        assert seen == [USER_AGENT]
        assert "Python-urllib" not in (seen[0] or "")


def test_a_client_block_is_not_reported_as_an_authentication_failure(monkeypatch):
    """A bot-protection refusal is a client problem, not a credential one."""

    import urllib.error

    from amca.credentials import MemoryCredentialStore

    def fake_urlopen(request, timeout=0):
        body = json.dumps(
            {
                "error_code": 1010,
                "error_name": "browser_signature_banned",
                "title": "Error 1010: Access denied",
                "detail": "The site owner has blocked access based on your browser's signature.",
            }
        ).encode("utf-8")
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", None, io.BytesIO(body))

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(context)
        store.set(credential_ref, "stored-key")
        outcome = context.verify_model_configuration(
            kind="hub",
            ref=f"{provider['id']}:test-model",
            endpoint="https://gateway.example/v1",
            model="test-model",
        )
        assert outcome["state"] == "PROVIDER_UNAVAILABLE", outcome
        assert outcome["runnable"] is False
        assert "bot protection" in outcome["detail"]


def test_a_plain_403_is_still_an_authentication_failure(monkeypatch):
    """A 403 without a bot-protection signature is still about the key."""

    import urllib.error

    from amca.credentials import MemoryCredentialStore

    def fake_urlopen(request, timeout=0):
        body = json.dumps({"error": {"message": "invalid api key"}}).encode("utf-8")
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", None, io.BytesIO(body))

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(context)
        store.set(credential_ref, "stored-key")
        outcome = context.verify_model_configuration(
            kind="hub",
            ref=f"{provider['id']}:test-model",
            endpoint="https://gateway.example/v1",
            model="test-model",
        )
        assert outcome["state"] == "AUTH_REQUIRED", outcome


def test_the_runtime_adapter_identifies_amca_with_a_user_agent():
    """A verified model must also RUN: the runtime request needs the same
    User-Agent, or the provider blocks the client at run time."""

    from amca import USER_AGENT
    from amca.models.openai_compatible import OpenAICompatibleAdapter

    adapter = OpenAICompatibleAdapter(
        model="test-model",
        adapter_id="provider.test:test-model",
        provider="custom",
        api_key_env="AMCA_TEST_KEY",
        base_url="https://gateway.example/v1",
        allow_remote=True,
        api_key="stored-key",
    )
    headers = adapter._headers()
    assert headers["User-Agent"] == USER_AGENT
    assert headers["Authorization"] == "Bearer stored-key"


def test_the_probe_body_is_unchanged(monkeypatch):
    """The verification request stays a bounded, content-free 1-token ping."""

    from amca.credentials import MemoryCredentialStore

    payloads: list = []

    def fake_urlopen(request, timeout=0):
        payloads.append(json.loads(request.data))
        return _ok_response()

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    store.set("model-hub.provider.test", "stored-key")
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        context.verify_model_configuration(
            kind="hub",
            ref="provider.test:test-model",
            endpoint="https://gateway.example/v1",
            model="test-model",
        )
        assert len(payloads) == 1
        assert payloads[0]["messages"] == [{"role": "user", "content": "ping"}]
        assert payloads[0]["max_tokens"] <= 16


def test_delete_for_ref_prefix_does_not_cross_provider_id_prefixes():
    """'provider.ab' must not delete the verdicts of 'provider.abc'."""

    from amca.model_health import ModelHealthStore, record_for_config

    with _service_context(None) as td:
        store = ModelHealthStore(Path(td) / "health.db")
        for ref in ("provider.ab:model-1", "provider.abc:model-2"):
            store.record(
                record_for_config(
                    kind="hub",
                    ref=ref,
                    endpoint="https://e",
                    model="m",
                    state="AUTH_REQUIRED",
                    detail="d",
                    evidence="e",
                )
            )
        assert store.delete_for_ref_prefix(kind="hub", ref_prefix="provider.ab") == 1
        remaining = {entry["ref"] for entry in store.snapshot().values()}
        assert remaining == {"provider.abc:model-2"}


def test_a_model_id_containing_a_colon_is_resolved_and_deleted_correctly(monkeypatch):
    """Model ids may contain ':' — the provider id must still resolve."""

    from amca.credentials import MemoryCredentialStore
    from amca.model_health import AUTH_REQUIRED, record_for_config

    seen: list = []

    def fake_urlopen(request, timeout=0):
        seen.append(request.get_header("Authorization"))
        return _ok_response()

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(
            context, model_ids=["owner/repo:tag"]
        )
        store.set(credential_ref, "stored-key")
        ref = f"{provider['id']}:owner/repo:tag"
        context.model_health_store.record(
            record_for_config(
                kind="hub",
                ref=ref,
                endpoint="https://gateway.example/v1",
                model="owner/repo:tag",
                state=AUTH_REQUIRED,
                detail="provider returned HTTP 401",
                evidence="connection_test",
            )
        )
        # The provider id still resolves for a model id that contains ':'.
        outcome = context.verify_model_configuration(
            kind="hub",
            ref=ref,
            endpoint="https://gateway.example/v1",
            model="owner/repo:tag",
        )
        assert outcome["state"] == "READY", outcome
        assert seen == ["Bearer stored-key"]

        context.update_hub_provider(
            str(provider["id"]),
            {"secret": "replacement", "model_ids": ["owner/repo:tag"]},
        )
        assert context.model_health_store.snapshot() == {}


def test_verify_resolves_the_provider_id_from_a_ref_whose_model_contains_a_colon(monkeypatch):
    from amca.credentials import MemoryCredentialStore

    seen: list = []

    def fake_urlopen(request, timeout=0):
        seen.append(request.get_header("Authorization"))
        return _ok_response()

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    store = MemoryCredentialStore()
    with _service_context(None) as td:
        context = _context(td, credential_store=store)
        provider, credential_ref = _register_provider(
            context, model_ids=["owner/repo:tag"]
        )
        store.set(credential_ref, "stored-key")
        outcome = context.verify_model_configuration(
            kind="hub",
            ref=f"{provider['id']}:owner/repo:tag",
            endpoint="https://gateway.example/v1",
            model="owner/repo:tag",
        )
        assert outcome["state"] == "READY", outcome
        assert seen == ["Bearer stored-key"]


def test_the_http_verify_route_authenticates_and_never_echoes_the_key(monkeypatch):
    """The HTTP boundary is where the defect lived: pin it there."""

    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from amca.credentials import MemoryCredentialStore
    from amca.service import create_server

    seen: list = []

    def fake_urlopen(request, timeout=0):
        seen.append((request.full_url, request.get_header("Authorization")))
        response = io.BytesIO(b"{}")
        response.status = 200
        return response

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)

    token = "v124-injected-session-token-0123456789abcdef0123456789"
    import os

    previous = os.environ.get("AMCA_SESSION_TOKEN")
    os.environ["AMCA_SESSION_TOKEN"] = token
    store = MemoryCredentialStore()
    try:
        with _service_context(None) as td:
            # create_server owns its own context, so the provider is
            # registered through the same database it serves.
            context = _context(td, credential_store=store)
            provider, credential_ref = _register_provider(context)
            store.set(credential_ref, "stored-key")
            context.close()
            _contexts.clear()

            server = create_server(
                host="127.0.0.1",
                port=0,
                root=Path(td),
                database=Path(td) / "amca.sqlite3",
                credential_store=store,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                body = json.dumps(
                    {
                        "kind": "hub",
                        "ref": f"{provider['id']}:test-model",
                        "endpoint": "https://gateway.example/v1",
                        "model": "test-model",
                    }
                ).encode("utf-8")
                request = Request(
                    base + "/api/model-hub/verify",
                    data=body,
                    method="POST",
                )
                request.add_header("Content-Type", "application/json")
                request.add_header("Origin", "http://localhost:1420")
                request.add_header("X-AMCA-Session", token)
                with urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                assert payload["state"] == "READY", payload
                assert payload["runnable"] is True
                # the key was attached to the provider call ...
                assert seen and seen[0][1] == "Bearer stored-key"
                # ... and it is nowhere in the response.
                assert "stored-key" not in json.dumps(payload)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
    finally:
        if previous is None:
            os.environ.pop("AMCA_SESSION_TOKEN", None)
        else:
            os.environ["AMCA_SESSION_TOKEN"] = previous
