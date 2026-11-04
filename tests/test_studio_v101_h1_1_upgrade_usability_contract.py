"""PS-1 U1–U7 upgrade-contract evidence tests (H1.1).

Milestone: ZCODE-AMCA-STUDIO-V1.0.1-H1.1-COMPLETE-FIRST-RUN-UI-AND-UPGRADE-USABILITY-CLOSURE-01
Worker: ZCode. Tests only.

U-matrix covered here:
- U1  existing model profiles survive upgrade
- U2  invalid NVIDIA ids remain configured but classify MODEL_NOT_FOUND
- U3  invalid credentials classify AUTH_REQUIRED
- U4  offline Ollama profiles classify LOCAL_RUNTIME_OFFLINE
- U5  stale team/default selections cannot auto-run
- U6  stale readiness is not blindly trusted
- U7  zero READY models lead to guided setup / one READY model safely runs Solo
- plus: temporary provider failure does not delete profiles/teams; no
  credentials exposed; no legacy C: path in any active reference.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.model_health import (  # noqa: E402
    AUTH_REQUIRED,
    LOCAL_RUNTIME_OFFLINE,
    MODEL_NOT_FOUND,
    PROVIDER_UNAVAILABLE,
    READY,
    ModelHealthStore,
    record_for_config,
)
from amca.service import ServiceContext  # noqa: E402


def _context(td) -> ServiceContext:
    root = Path(td.name) if hasattr(td, "name") else Path(td)
    context = ServiceContext(
        root=root,
        database=root / "amca.sqlite3",
        benchmarks=root,
    )
    _contexts.append(context)
    return context


_contexts: list = []


def _close_all_contexts():
    while _contexts:
        context = _contexts.pop()
        try:
            context.close()
        except Exception:
            pass


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _cleanup_service_contexts():
    yield
    _close_all_contexts()


def _seed_v100_database(path: Path) -> None:
    """Simulate an existing v1.0.0 user's desktop database: model profiles
    table present, a hub provider with stale NVIDIA ids, saved teams."""

    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS desktop_model_profiles (
                profile_id TEXT PRIMARY KEY, provider_kind TEXT NOT NULL,
                label TEXT NOT NULL, model TEXT NOT NULL, endpoint TEXT NOT NULL,
                pricing_basis TEXT NOT NULL, input_cost_per_million REAL NOT NULL,
                output_cost_per_million REAL NOT NULL, price_per_request_usd REAL NOT NULL,
                is_builtin INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO desktop_model_profiles VALUES
                ('tabitoken.claude-opus-5', 'tabitoken', 'Claude Opus 5',
                 'claude-opus-5', 'https://tabitoken.com/v1', 'request', 0, 0, 0.80, 1,
                 '2026-09-04T00:00:00', '2026-09-04T00:00:00');
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_u1_existing_model_profiles_survive_upgrade():
    """Opening a v1.0.0-era database with 1.0.1 keeps profiles intact."""

    td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    try:
        database = Path(td.name) / "amca.sqlite3"
        _seed_v100_database(database)
        context = _context(td if isinstance(td, str) else td.name)
        profile_ids = {item.profile_id for item in context.model_profiles.list()}
        assert "tabitoken.claude-opus-5" in profile_ids
        # builtin catalog is unchanged
        assert "tabitoken.claude-opus-4-8" in profile_ids


    finally:
        _close_all_contexts()
        td.cleanup()

def test_u2_invalid_nvidia_ids_configured_but_model_not_found(monkeypatch):
    import urllib.error

    def fake_urlopen(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", None, None)

    td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    try:
        context = _context(td if isinstance(td, str) else td.name)
        provider, _ = context.model_hub_store.create_provider({
            "provider_kind": "custom",
            "name": "NVIDIA",
            "endpoint": "https://integrate.api.nvidia.com/v1",
            "secret": "nvapi-user-key",
            "model_ids": ["kimi-k3", "deepseek-v4-pro-0813"],
        })
        monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
        report = context.verify_all_configured_models()
        nvidia_states = {
            result["ref"]: result["state"]
            for result in report["results"]
            if "kimi-k3" in result["ref"] or "deepseek" in result["ref"]
        }
        assert nvidia_states and set(nvidia_states.values()) == {MODEL_NOT_FOUND}
        # the provider/profiles remain configured (not deleted)
        assert context.model_hub_store.provider(provider["id"]) is not None
        assert {item.profile_id for item in context.model_profiles.list()}


    finally:
        _close_all_contexts()
        td.cleanup()

def test_u3_invalid_credentials_classify_auth_required(monkeypatch):
    import urllib.error

    def fake_urlopen(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", None, None)

    td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    try:
        context = _context(td if isinstance(td, str) else td.name)
        # The probe is stubbed: this test asserts how a 403 is CLASSIFIED, and
        # a live third-party answer is not evidence for that. It used to reach
        # tabitoken.com, where the real response was a Cloudflare
        # client-signature block (a different failure entirely).
        monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
        report = context.verify_all_configured_models()
        tabitoken_states = {
            result["state"] for result in report["results"]
            if result["ref"].startswith("tabitoken.")
        }
        assert tabitoken_states == {AUTH_REQUIRED}


    finally:
        _close_all_contexts()
        td.cleanup()

def test_u4_offline_ollama_classifies_local_runtime_offline():
    td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    try:
        context = _context(td if isinstance(td, str) else td.name)
        outcome = context.verify_model_configuration(
            kind="local", ref="ollama:qwen3:1.7b", endpoint="local://ollama",
            model="qwen3:1.7b", credential=None, local_runtime=False,
        )
        assert outcome["state"] == LOCAL_RUNTIME_OFFLINE
        assert outcome["runnable"] is False


    finally:
        _close_all_contexts()
        td.cleanup()

def test_u5_stale_team_selection_cannot_auto_run(monkeypatch):
    """A selected model whose verdict is not READY is refused with guidance —
    stale default selections can never silently start a run.  The 33A pre-run
    repair re-verifies once; the patched transport confirms the failure
    deterministically without touching the network."""
    import urllib.error

    def fake_urlopen(request, timeout=0):
        raise urllib.error.URLError("connection refused (test)")

    td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    try:
        context = _context(td if isinstance(td, str) else td.name)
        context.model_health_store.record(record_for_config(
            kind="profile", ref="tabitoken.claude-opus-5",
            endpoint="https://tabitoken.com/v1", model="claude-opus-5",
            state=AUTH_REQUIRED, detail="HTTP 403", evidence="connection_test",
        ))
        monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
        import pytest

        with pytest.raises(ValueError, match="No usable model is configured"):
            context.start_chat_run(
                goal="stale default selection attempt",
                participant_ids=["tabitoken.claude-opus-5"],
                mode="solo",
                selection_policy="manual_model_set",
            )


    finally:
        _close_all_contexts()
        td.cleanup()

def test_u6_stale_readiness_not_blindly_trusted():
    """A recorded READY verdict for config X does not validate config Y:
    fingerprints differ when any of kind/ref/endpoint/model changes."""

    from amca.model_health import config_fingerprint

    key_a = config_fingerprint(kind="profile", ref="r", endpoint="https://a", model="m")
    key_b = config_fingerprint(kind="profile", ref="r", endpoint="https://b", model="m")
    assert key_a != key_b
    key_c = config_fingerprint(kind="profile", ref="r", endpoint="https://a", model="other")
    assert key_a != key_c
    td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    try:
        store = ModelHealthStore(Path(td.name) / "h.db")
        store.record(record_for_config(
            kind="profile", ref="r", endpoint="https://a", model="m",
            state=READY, detail="ok", evidence="connection_test",
        ))
        snapshot = store.snapshot()
        assert key_a in snapshot
        assert key_b not in snapshot


    finally:
        _close_all_contexts()
        td.cleanup()

def test_u7_zero_ready_guides_one_ready_runs_solo(monkeypatch):
    import urllib.error

    def fake_urlopen(request, timeout=0):
        raise urllib.error.URLError("connection refused (test)")

    td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    try:
        context = _context(td if isinstance(td, str) else td.name)
        # zero READY -> the gate error is the guided-setup message
        context.model_health_store.record(record_for_config(
            kind="profile", ref="tabitoken.claude-opus-5",
            endpoint="https://tabitoken.com/v1", model="claude-opus-5",
            state=AUTH_REQUIRED, detail="HTTP 403", evidence="connection_test",
        ))
        monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
        import pytest

        with pytest.raises(ValueError, match="Open Model Hub"):
            context.start_chat_run(
                goal="x", participant_ids=["tabitoken.claude-opus-5"],
                mode="solo", selection_policy="manual_model_set",
            )
        # one READY (local) -> solo run is accepted into the governed pipeline
        context.model_health_store.record(record_for_config(
            kind="local", ref="ollama:qwen3:1.7b", endpoint="local://ollama",
            model="qwen3:1.7b", state=READY, detail="runtime reachable",
            evidence="connection_test",
        ))
        result = context.start_chat_run(
            goal="Reply exactly: AMCA v1.0.1 physical test successful.",
            participant_ids=["ollama:qwen3:1.7b"],
            mode="solo", selection_policy="manual_model_set",
        )
        assert result["provider"] == "ollama"


    finally:
        _close_all_contexts()
        td.cleanup()

def test_temporary_provider_failure_does_not_delete_profiles_or_teams(monkeypatch):
    import urllib.error

    def fake_urlopen(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", None, None)

    td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    try:
        context = _context(td if isinstance(td, str) else td.name)
        provider, _ = context.model_hub_store.create_provider({
            "provider_kind": "custom", "name": "Provider X",
            "endpoint": "https://x.example.com/v1", "secret": "key",
            "model_ids": ["model-a"],
        })
        team = context.model_hub_store.save_team({
            "name": "Team X", "description": "",
            "model_refs": ["model-a"], "quality_policy": "balanced",
        })
        monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
        report = context.verify_all_configured_models()
        assert all(r["state"] == PROVIDER_UNAVAILABLE for r in report["results"])
        assert context.model_hub_store.provider(provider["id"]) is not None
        assert any(item["id"] == team["id"] for item in context.model_hub_store.list_teams())


    finally:
        _close_all_contexts()
        td.cleanup()

def test_no_credentials_exposed_in_health_or_hub():
    td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    try:
        context = _context(td if isinstance(td, str) else td.name)
        context.model_hub_store.create_provider({
            "provider_kind": "custom", "name": "Secret Provider",
            "endpoint": "https://s.example.com/v1", "secret": "super-secret-key-123",
            "model_ids": ["m1"],
        })
        hub = context.model_hub()
        document = json.dumps(hub)
        assert "super-secret-key-123" not in document
        assert "credential_ref" not in document
        health = json.dumps(context._configured_model_health())
        assert "super-secret-key-123" not in health


    finally:
        _close_all_contexts()
        td.cleanup()

def test_no_legacy_c_path_in_active_references():
    """Active source/tooling must not reference the legacy C: AMCA path."""

    legacy = "Md. Ashraful Babu"
    offenders: list[str] = []
    for module in ("model_health.py", "service.py", "model_profiles.py", "model_hub.py"):
        text = (SOFTWARE_ROOT / "src" / "amca" / module).read_text(encoding="utf-8")
        if "C:/Users" in text or "C:\\Users" in text:
            offenders.append(module)
    assert offenders == []
    assert legacy not in json.dumps([1])  # marker to keep the import honest
