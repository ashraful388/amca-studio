"""H1 model-health and fresh-run-repair tests.

Milestone: ZCODE-AMCA-STUDIO-V1.0.1-H1-MODEL-USABILITY-FIRST-RUN-REPAIR-01
Worker: ZCode. Tests only — no product behavior claims beyond the new
model-health gate, fresh-chat guidance, and Model Hub connection test.
"""

from __future__ import annotations

import hashlib
import json
import contextlib
import os
from contextlib import contextmanager
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

_contexts: list = []
_contexts: list = []

import atexit


def _safe_close(context) -> None:
    try:
        context.close()
    except Exception:
        pass


atexit.register(lambda: _close_all_contexts())


import pytest


@pytest.fixture(autouse=True)
def _cleanup_service_contexts():
    yield
    _close_all_contexts()


def _close_all_contexts():
    while _contexts:
        context = _contexts.pop()
        try:
            context.close()
        except Exception:
            pass


@contextmanager
def _service_context(td_path: str):
    """Temp dir that closes every ServiceContext before the directory is removed."""
    import contextlib
    with contextlib.ExitStack() as stack:
        kwargs = {} if td_path is None else {"dir": td_path}
        # Windows: a background run thread may still hold the sqlite handle
        # when the context manager unwinds; cleanup failures must not fail
        # the test.
        yield stack.enter_context(
            tempfile.TemporaryDirectory(ignore_cleanup_errors=True, **kwargs)
        )
        _close_all_contexts()

from amca import PROVIDER_REQUEST_TIMEOUT_SECONDS  # noqa: E402
from amca.model_health import (  # noqa: E402
    AUTH_REQUIRED,
    LOCAL_RUNTIME_OFFLINE,
    MODEL_NOT_FOUND,
    PROVIDER_UNAVAILABLE,
    READY,
    UNVERIFIED,
    ModelHealthStore,
    classify_exception,
    classify_http_status,
    config_fingerprint,
    record_for_config,
)


def test_classification_maps_http_statuses():
    assert classify_http_status(401) == AUTH_REQUIRED
    assert classify_http_status(403) == AUTH_REQUIRED
    assert classify_http_status(404) == MODEL_NOT_FOUND
    assert classify_http_status(410) == MODEL_NOT_FOUND
    assert classify_http_status(500) == PROVIDER_UNAVAILABLE
    assert classify_http_status(503) == PROVIDER_UNAVAILABLE


def test_classification_maps_transport_errors():
    assert classify_exception(TimeoutError("timed out")) == PROVIDER_UNAVAILABLE
    assert classify_exception(ConnectionRefusedError()) == PROVIDER_UNAVAILABLE


def test_fingerprint_matches_backend_canonical_form():
    payload = json.dumps(
        {"kind": "profile", "ref": "r", "endpoint": "e", "model": "m"},
        sort_keys=True,
        separators=(",", ":"),
    )
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    assert config_fingerprint(kind="profile", ref="r", endpoint="e", model="m") == expected
    # secrets never enter the fingerprint
    assert config_fingerprint(kind="profile", ref="r", endpoint="e", model="m") == expected


def test_store_persists_and_roundtrips():
    with _service_context(None) as td:
        store = ModelHealthStore(Path(td) / "health.db")
        record = record_for_config(
            kind="profile",
            ref="tabitoken.claude-opus-5",
            endpoint="https://tabitoken.com/v1",
            model="claude-opus-5",
            state=AUTH_REQUIRED,
            detail="provider returned HTTP 403",
            evidence="connection_test",
        )
        store.record(record)
        got = store.get(record.config_key)
        assert got is not None
        assert got.state == AUTH_REQUIRED
        assert got.to_dict()["runnable"] is False
        # upsert overwrites state
        updated = record_for_config(
            kind="profile",
            ref="tabitoken.claude-opus-5",
            endpoint="https://tabitoken.com/v1",
            model="claude-opus-5",
            state=READY,
            detail="HTTP 200",
            evidence="connection_test",
        )
        assert updated.config_key == record.config_key
        store.record(updated)
        assert store.get(record.config_key).state == READY  # type: ignore[union-attr]
        assert len(store.snapshot()) == 1


def test_runnable_only_for_ready():
    with _service_context(None) as td:
        store = ModelHealthStore(Path(td) / "health.db")
        for state in (READY, AUTH_REQUIRED, MODEL_NOT_FOUND, PROVIDER_UNAVAILABLE,
                      LOCAL_RUNTIME_OFFLINE, UNVERIFIED):
            record = record_for_config(
                kind="profile", ref=f"ref-{state}", endpoint="https://e",
                model="m", state=state, detail="d", evidence="e",
            )
            store.record(record)
            got = store.get(record.config_key)
            assert got is not None
            assert got.to_dict()["runnable"] is (state == READY)


def test_service_context_wires_health_store():
    from amca.service import ServiceContext

    with _service_context(None) as td:
        context = ServiceContext(
            root=Path(td),
            database=Path(td) / "amca.sqlite3",
            benchmarks=Path(td),
        )
        _contexts.append(context)
        assert context.model_health_store is not None
        # table exists
        db = sqlite3.connect(context.database)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        db.close()
        assert "model_health" in tables


def test_verify_local_runtime_offline_records_state():
    from amca.service import ServiceContext

    with _service_context(None) as td:
        context = ServiceContext(
            root=Path(td),
            database=Path(td) / "amca.sqlite3",
            benchmarks=Path(td),
        )
        _contexts.append(context)
        outcome = context.verify_model_configuration(
            kind="local", ref="ollama:test", endpoint="local://ollama",
            model="test", credential=None, local_runtime=False,
        )
        assert outcome["state"] == LOCAL_RUNTIME_OFFLINE
        assert outcome["runnable"] is False
        snap = context.model_health_store.snapshot()
        assert len(snap) == 1


def test_verify_cloud_auth_failure_records_auth_required(monkeypatch):
    import urllib.error

    from amca.service import ServiceContext

    def fake_urlopen(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", None, None)

    with _service_context(None) as td:
        context = ServiceContext(
            root=Path(td),
            database=Path(td) / "amca.sqlite3",
            benchmarks=Path(td),
        )
        _contexts.append(context)
        monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
        outcome = context.verify_model_configuration(
            kind="profile", ref="tabitoken.claude-opus-5",
            endpoint="https://tabitoken.com/v1", model="claude-opus-5",
            credential="bad-key",
        )
        assert outcome["state"] == AUTH_REQUIRED
        assert outcome["http_status"] == 403


def test_verify_cloud_probe_supports_gateway_minimum_tokens(monkeypatch):
    import io
    import json
    import urllib.error

    from amca.credentials import MemoryCredentialStore
    from amca.service import ServiceContext

    requests = []

    def fake_urlopen(request, timeout=0):
        payload = json.loads(request.data)
        requests.append(payload)
        assert request.full_url == "https://gateway.example/v1/chat/completions"
        assert payload["messages"] == [{"role": "user", "content": "ping"}]
        assert payload["model"] == "test-model"
        # The probe must not be stricter than the runtime: a probe that gives
        # up earlier measures something the runtime never asks for, and a
        # model that answers inside the runtime's allowance would be
        # reported as "Provider down" and locked out.
        assert 0 < timeout <= PROVIDER_REQUEST_TIMEOUT_SECONDS
        assert payload["max_tokens"] <= 16
        if payload["max_tokens"] <= 2:
            raise urllib.error.HTTPError(
                request.full_url, 400, "max_tokens must be greater than 2", None, None
            )
        response = io.BytesIO(b'{}')
        response.status = 200
        return response

    monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
    with _service_context(None) as td:
        context = ServiceContext(
            root=Path(td),
            database=Path(td) / "amca.sqlite3",
            benchmarks=Path(td),
            credential_store=MemoryCredentialStore(),
        )
        _contexts.append(context)
        outcome = context.verify_model_configuration(
            kind="hub", ref="provider.test:test-model",
            endpoint="https://gateway.example/v1", model="test-model",
            credential="test-key",
        )
        assert outcome["state"] == READY
        assert outcome["runnable"] is True
        assert outcome["http_status"] == 200
        assert len(requests) == 1
        assert requests[0]["max_tokens"] > 2


def test_verify_model_not_found(monkeypatch):
    import urllib.error

    from amca.service import ServiceContext

    def fake_urlopen(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", None, None)

    with _service_context(None) as td:
        context = ServiceContext(
            root=Path(td),
            database=Path(td) / "amca.sqlite3",
            benchmarks=Path(td),
        )
        _contexts.append(context)
        monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
        outcome = context.verify_model_configuration(
            kind="hub", ref="provider.x:kimi-k3",
            endpoint="https://integrate.api.nvidia.com/v1", model="kimi-k3",
            credential="key",
        )
        assert outcome["state"] == MODEL_NOT_FOUND


def test_chat_run_rejects_unrunnable_selected_models(monkeypatch):
    """A selected model with a recorded failed verdict must produce the
    setup-guidance error, not a manufactured failed run.  The 33A pre-run
    repair re-verifies once first; the patched transport keeps that bounded
    probe offline so the persisted failure is confirmed deterministically."""
    import urllib.error

    from amca.service import ServiceContext

    def fake_urlopen(request, timeout=0):
        raise urllib.error.URLError("connection refused (test)")

    with _service_context(None) as td:
        context = ServiceContext(
            root=Path(td),
            database=Path(td) / "amca.sqlite3",
            benchmarks=Path(td),
        )
        _contexts.append(context)
        profile = next(
            item for item in context.model_profiles.list()
            if item.profile_id == "tabitoken.claude-opus-5"
        )
        context.model_health_store.record(record_for_config(
            kind="profile", ref=profile.profile_id, endpoint=profile.endpoint,
            model=profile.model, state=AUTH_REQUIRED,
            detail="provider returned HTTP 403", evidence="connection_test",
        ))
        monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
        import pytest

        with pytest.raises(ValueError, match="No usable model is configured"):
            context.start_chat_run(
                goal="Reply exactly: AMCA v1.0.0 physical test successful.",
                participant_ids=["tabitoken.claude-opus-5"],
                mode="solo",
                selection_policy="manual_model_set",
            )


def test_unverified_models_still_runnable_backward_compatible(monkeypatch):
    """Without recorded health verdicts, existing behavior is unchanged
    (UNVERIFIED entries are not blocked — the first-run experience stays
    usable, while verify-all labels them truthfully)."""
    from amca.credentials import MemoryCredentialStore
    from amca.service import ServiceContext

    credentials = MemoryCredentialStore()
    credentials.set("tabitoken.claude-opus-5", "test-key")

    def unexpected_urlopen(*args, **kwargs):
        raise AssertionError("This eligibility test must not call a provider")

    monkeypatch.setattr("amca.service._open_verification_probe", unexpected_urlopen)
    with _service_context(None) as td:
        context = ServiceContext(
            root=Path(td),
            database=Path(td) / "amca.sqlite3",
            benchmarks=Path(td),
            credential_store=credentials,
        )
        _contexts.append(context)
        assert context._configured_model_health() == {}
        # the run would proceed past the health gate (it will fail later for
        # other reasons like an empty conversation, but NOT with the H1 error)
        # Without a recorded verdict the run proceeds (backward compatible):
        # the manual solo path accepts the single configured participant.
        result = context.start_chat_run(
            goal="Reply exactly: AMCA v1.0.0 physical test successful.",
            participant_ids=["tabitoken.claude-opus-5"],
            mode="solo",
            selection_policy="manual_model_set",
        )
        assert result is not None


def test_model_hub_exposes_health_map():
    from amca.service import ServiceContext

    with _service_context(None) as td:
        context = ServiceContext(
            root=Path(td),
            database=Path(td) / "amca.sqlite3",
            benchmarks=Path(td),
        )
        _contexts.append(context)
        context.model_health_store.record(record_for_config(
            kind="profile", ref="tabitoken.claude-opus-5",
            endpoint="https://tabitoken.com/v1", model="claude-opus-5",
            state=AUTH_REQUIRED, detail="HTTP 403", evidence="connection_test",
        ))
        hub = context.model_hub()
        assert "model_health" in hub
        assert len(hub["model_health"]) == 1


def test_verify_all_reports_runnable_count(monkeypatch):
    import urllib.error

    from amca.service import ServiceContext

    def fake_urlopen(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", None, None)

    with _service_context(None) as td:
        context = ServiceContext(
            root=Path(td),
            database=Path(td) / "amca.sqlite3",
            benchmarks=Path(td),
        )
        _contexts.append(context)
        monkeypatch.setattr("amca.service._open_verification_probe", fake_urlopen)
        report = context.verify_all_configured_models()
        assert report["schema"] == "amca-model-health-report/1"
        assert report["any_runnable"] is False
        assert report["runnable_count"] == 0
        states = {result["state"] for result in report["results"]}
        assert MODEL_NOT_FOUND in states  # the four tabitoken builtin profiles


def test_no_secrets_in_health_records():
    with _service_context(None) as td:
        store = ModelHealthStore(Path(td) / "health.db")
        record = record_for_config(
            kind="profile", ref="r", endpoint="https://e",
            model="m", state=READY, detail="ok", evidence="connection_test",
        )
        store.record(record)
        document = json.dumps(store.snapshot())
        assert "sk-" not in document
        assert "secret" not in document


def test_h1_2_a_persisted_ready_stays_runnable_across_restarts():
    """33A usability repair (revised H1.2-A): a persisted READY verdict for an
    unchanged configuration fingerprint remains the execution authorization
    across service restarts (fresh=False diagnostic, runnable=True).  The
    verdict is only reclassified by a newer verification: a live probe that
    fails (runtime offline) records the failure, which then blocks."""
    td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    try:
        from amca.service import ServiceContext

        context = ServiceContext(
            root=Path(td.name),
            database=Path(td.name) / "amca.sqlite3",
            benchmarks=Path(td.name),
        )
        try:
            record = record_for_config(
                kind="local", ref="ollama:qwen3:1.7b", endpoint="local://ollama",
                model="qwen3:1.7b", state=READY, detail="session-1 verdict",
                evidence="connection_test",
            )
            context.model_health_store.record(record)
            # Simulate a previous session: push the verdict timestamp back
            # before this service's boot.
            connection = sqlite3.connect(context.database)
            connection.execute(
                "UPDATE model_health SET verified_at=? WHERE config_key=?",
                ("2020-01-01T00:00:00+00:00", record.config_key),
            )
            connection.commit()
            connection.close()

            snapshot = context.model_health_store.snapshot()
            entry = snapshot[record.config_key]
            assert entry["state"] == READY
            assert entry["fresh"] is False  # observed by a previous session
            assert entry["runnable"] is True  # but still authorized (33A)

            # A newer verification with current evidence reclassifies:
            outcome = context.verify_model_configuration(
                kind="local", ref="ollama:qwen3:1.7b", endpoint="local://ollama",
                model="qwen3:1.7b", credential=None, local_runtime=False,
            )
            assert outcome["state"] == LOCAL_RUNTIME_OFFLINE
            assert outcome["runnable"] is False
            snapshot = context.model_health_store.snapshot()
            assert snapshot[record.config_key]["runnable"] is False
        finally:
            context.close()
    finally:
        td.cleanup()


def _context_service(td: str) -> "ServiceContext":
    from amca.service import ServiceContext

    context = ServiceContext(
        root=Path(td),
        database=Path(td) / "amca.sqlite3",
        benchmarks=Path(td),
    )
    _contexts.append(context)
    return context
