"""33A model-usability repair tests.

Milestone: model-working fixes for every observed failure variation of the
desktop Chat model gate:

- MR1  a persisted READY verdict stays runnable across service restarts;
       only a newer verification reclassifies it
- MR2  a run whose selected models are all blocked re-verifies once
       (bounded, metadata-level) and proceeds on current READY evidence
- MR3  the guided refusal stands when the re-verification still fails
- MR4  Auto Intelligence never offers a known-failed candidate while a
       healthier one exists, and revives when every candidate is suspect
- MR5  distinct BYOK connections are distinct providers for the router's
       independence rule, so two healthy connections assemble two participants
- MR6  when the mandated architecture needs two participants but the auto
       resolution produced one, the participant set is re-resolved to two
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import urllib.error
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

import pytest  # noqa: E402

from amca.model_health import (  # noqa: E402
    AUTH_REQUIRED,
    PROVIDER_UNAVAILABLE,
    READY,
    ModelHealthStore,
    record_for_config,
)
from amca.model_hub import (  # noqa: E402
    AutoIntelligenceRouter,
    CostQualityGate,
    ModelSelectionDecision,
    SelectionPolicy,
)
from amca.credentials import MemoryCredentialStore  # noqa: E402
from amca.service import ServiceContext  # noqa: E402

_contexts: list = []


def _close_all_contexts() -> None:
    while _contexts:
        context = _contexts.pop()
        try:
            context.close()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _cleanup_service_contexts():
    yield
    _close_all_contexts()


def _context(td: str) -> ServiceContext:
    context = ServiceContext(
        root=Path(td),
        database=Path(td) / "amca.sqlite3",
        benchmarks=Path(td),
        credential_store=MemoryCredentialStore(),
    )
    _contexts.append(context)
    return context


class _FakeSuccessResponse:
    """Minimal urlopen() stand-in for a provider that accepts the config."""

    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        return b'{"choices":[{"message":{"content":"pong"}}]}'


def _fake_urlopen_success(request, timeout=0):
    return _FakeSuccessResponse()


def _fake_urlopen_refused(request, timeout=0):
    raise urllib.error.URLError("connection refused (test)")


def _create_hub_provider(context: ServiceContext, name: str, model: str, endpoint: str):
    # Service-level creation writes the secret into the (memory) credential
    # store, which the Auto Intelligence candidate builder requires.
    result = context.create_hub_provider({
        "provider_kind": "custom",
        "name": name,
        "endpoint": endpoint,
        "secret": "test-key",
        "model_ids": [model],
    })
    return result["provider"]


def test_mr1_persisted_ready_runnable_in_new_session():
    """A READY verdict persists as authorization for the same config
    fingerprint across a service restart; a failure verdict does not."""

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        database = Path(td) / "health.db"
        store_a = ModelHealthStore(database)
        ready_record = record_for_config(
            kind="profile", ref="r-ready", endpoint="https://e", model="m",
            state=READY, detail="session-1", evidence="connection_test",
        )
        failed_record = record_for_config(
            kind="profile", ref="r-failed", endpoint="https://e", model="m",
            state=AUTH_REQUIRED, detail="HTTP 403", evidence="connection_test",
        )
        store_a.record(ready_record)
        store_a.record(failed_record)
        # Age both verdicts into a previous session.
        connection = sqlite3.connect(database)
        connection.execute("UPDATE model_health SET verified_at='2020-01-01T00:00:00+00:00'")
        connection.commit()
        connection.close()

        store_b = ModelHealthStore(database)  # new service session
        snapshot = store_b.snapshot()
        assert snapshot[ready_record.config_key]["fresh"] is False
        assert snapshot[ready_record.config_key]["runnable"] is True
        assert snapshot[failed_record.config_key]["runnable"] is False


def test_mr2_all_blocked_selected_models_revive_and_run(monkeypatch):
    """Every selected participant blocked on a persisted failure verdict is
    re-verified once at run time; current READY evidence lets the run start."""

    monkeypatch.setattr("amca.service._open_verification_probe", _fake_urlopen_success)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        context = _context(td)
        provider = _create_hub_provider(
            context, "Provider MR2", "test-model", "https://mr2.example/v1"
        )
        ref = f"{provider['id']}:test-model"
        context.model_health_store.record(record_for_config(
            kind="hub", ref=ref, endpoint=provider["endpoint"], model="test-model",
            state=AUTH_REQUIRED, detail="HTTP 403", evidence="connection_test",
        ))
        assert context.model_health_store.snapshot()[
            record_for_config(
                kind="hub", ref=ref, endpoint=provider["endpoint"], model="test-model",
                state=AUTH_REQUIRED, detail="x", evidence="x",
            ).config_key
        ]["runnable"] is False

        result = context.start_chat_run(
            goal="Reply exactly: AMCA 33A model usability repair successful.",
            participant_ids=[ref],
            mode="solo",
            selection_policy="manual_model_set",
        )
        # Past the health gate: the run is either started or parked at the
        # normal cloud-transmission approval gate — never refused with
        # "No usable model is configured".
        assert result.get("mode") == "solo"
        assert result.get("status") != "failed"
        snapshot = context.model_health_store.snapshot()
        revived = record_for_config(
            kind="hub", ref=ref, endpoint=provider["endpoint"], model="test-model",
            state=READY, detail="x", evidence="x",
        ).config_key
        assert snapshot[revived]["state"] == READY
        assert snapshot[revived]["runnable"] is True


def test_mr3_guided_refusal_when_repair_confirms_failure(monkeypatch):
    """When the pre-run re-verification still fails, the run is refused with
    the exact guided-setup error and current evidence."""

    monkeypatch.setattr("amca.service._open_verification_probe", _fake_urlopen_refused)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        context = _context(td)
        provider = _create_hub_provider(
            context, "Provider MR3", "test-model", "https://mr3.example/v1"
        )
        ref = f"{provider['id']}:test-model"
        context.model_health_store.record(record_for_config(
            kind="hub", ref=ref, endpoint=provider["endpoint"], model="test-model",
            state=PROVIDER_UNAVAILABLE, detail="endpoint unreachable",
            evidence="connection_test",
        ))
        with pytest.raises(ValueError, match="No usable model is configured"):
            context.start_chat_run(
                goal="Reply exactly: AMCA 33A model usability repair successful.",
                participant_ids=[ref],
                mode="solo",
                selection_policy="manual_model_set",
            )
        snapshot = context.model_health_store.snapshot()
        key = record_for_config(
            kind="hub", ref=ref, endpoint=provider["endpoint"], model="test-model",
            state=PROVIDER_UNAVAILABLE, detail="x", evidence="x",
        ).config_key
        assert snapshot[key]["state"] == PROVIDER_UNAVAILABLE


def test_mr4_auto_candidates_exclude_failed_when_healthy_exist():
    """A candidate with a recorded failed verdict is not offered to the
    router while a candidate with healthy (or no) evidence exists."""

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        context = _context(td)
        bad = _create_hub_provider(
            context, "Provider Bad", "bad-model", "https://bad.example/v1"
        )
        good = _create_hub_provider(
            context, "Provider Good", "good-model", "https://good.example/v1"
        )
        context.model_health_store.record(record_for_config(
            kind="hub", ref=f"{bad['id']}:bad-model", endpoint=bad["endpoint"],
            model="bad-model", state=PROVIDER_UNAVAILABLE,
            detail="endpoint unreachable", evidence="connection_test",
        ))
        candidates, _required, _quality, _target = context._auto_model_candidates(
            goal="Reply briefly.",
            attachment_names=[],
            inventory={"models": [], "runtime_reachable": False},
            profiles=[],
        )
        refs = [item.model_ref for item in candidates]
        assert refs == [f"{good['id']}:good-model"]


def test_mr4b_auto_candidates_revive_when_every_candidate_suspect(monkeypatch):
    """When every candidate carries a failed verdict, the pre-run repair
    re-verifies once and returns the configurations that now pass."""

    monkeypatch.setattr("amca.service._open_verification_probe", _fake_urlopen_success)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        context = _context(td)
        left = _create_hub_provider(
            context, "Provider Left", "left-model", "https://left.example/v1"
        )
        right = _create_hub_provider(
            context, "Provider Right", "right-model", "https://right.example/v1"
        )
        for provider, model in ((left, "left-model"), (right, "right-model")):
            context.model_health_store.record(record_for_config(
                kind="hub", ref=f"{provider['id']}:{model}",
                endpoint=provider["endpoint"], model=model,
                state=AUTH_REQUIRED, detail="HTTP 403", evidence="connection_test",
            ))
        candidates, _required, _quality, _target = context._auto_model_candidates(
            goal="Reply briefly.",
            attachment_names=[],
            inventory={"models": [], "runtime_reachable": False},
            profiles=[],
        )
        refs = sorted(item.model_ref for item in candidates)
        # Provider ids are random per run, so compare as an order-insensitive set.
        assert refs == sorted([
            f"{left['id']}:left-model",
            f"{right['id']}:right-model",
        ])


def test_mr5_distinct_hub_connections_are_independent():
    """Two distinct BYOK connections under one protocol kind are distinct
    providers for the independence rule, so two participants resolve."""

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        context = _context(td)
        left = _create_hub_provider(
            context, "Provider Left", "left-model", "https://left.example/v1"
        )
        right = _create_hub_provider(
            context, "Provider Right", "right-model", "https://right.example/v1"
        )
        candidates, _required, _quality, _target = context._auto_model_candidates(
            goal="Reply briefly.",
            attachment_names=[],
            inventory={"models": [], "runtime_reachable": False},
            profiles=[],
        )
        providers = {item.provider for item in candidates}
        assert providers == {
            f"custom:{left['id']}",
            f"custom:{right['id']}",
        }
        decision = AutoIntelligenceRouter().resolve(
            candidates,
            minimum_quality=0.65,
            required_capabilities=frozenset({"text", "reasoning"}),
            required_context=8_192,
            required_participants=2,
        )
        assert len(decision.model_refs) == 2
        assert set(decision.model_refs) == {
            f"{left['id']}:left-model",
            f"{right['id']}:right-model",
        }


def test_mr6_auto_resolution_upgraded_to_two_participants(monkeypatch):
    """When the mandated architecture needs two participants but the auto
    resolution produced one, the participant set is re-resolved to two so
    healthy models are not refused by the participant-count validation."""

    calls: list[int] = []
    gate = CostQualityGate()

    def fake_resolve(
        self,
        candidates,
        *,
        policy=SelectionPolicy.AUTO_INTELLIGENCE,
        manual_refs=(),
        minimum_quality=0.7,
        required_capabilities=(),
        required_context=0,
        required_participants=1,
        optimize_cost=True,
    ):
        viable = [
            item for item in candidates
            if gate.accepts(
                item,
                minimum_quality=minimum_quality,
                required_capabilities=required_capabilities,
                required_context=required_context,
            )
        ]
        # First routing pass collapses to one participant no matter what the
        # task asked for (the independence-fallback shape); the upgrade pass
        # (required_participants=2) honors the request.
        take = 1 if not calls else required_participants
        calls.append(required_participants)
        return ModelSelectionDecision(
            SelectionPolicy.AUTO_INTELLIGENCE,
            tuple(item.model_ref for item in viable[:take]),
            None,
            "fake deterministic selection",
            "high",
        )

    monkeypatch.setattr(AutoIntelligenceRouter, "resolve", fake_resolve)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        context = _context(td)
        provider = _create_hub_provider(
            context, "Provider MR6", "model-a", "https://mr6.example/v1"
        )
        provider_id = provider["id"]
        # Register the second model on the same connection so one credential
        # backs both candidates.
        context.model_hub_store.replace_provider_catalog(
            provider_id, ["model-a", "model-b"]
        )
        # v1.1.8 Phase 1: discovery alone does not publish a model into the
        # selectors, so this test publishes both explicitly before routing.
        for model in ("model-a", "model-b"):
            context.model_hub_store.set_model_visibility(provider_id, model, True)
        # "verify" makes the task verification-heavy (architecture mandates a
        # multi-participant mode) and complex (routing target is two).
        result = context.start_chat_run(
            goal="Verify the claim and report the verified result.",
            participant_ids=[],
            mode="solo",
            selection_policy="auto_intelligence",
        )
        # The upgrade re-resolved two participants: the mandated
        # independent-discuss architecture stands and the run proceeds to the
        # normal approval gate instead of the participant-count refusal.
        assert calls == [2, 2]
        assert result.get("mode") == "independent_discuss"
        assert result.get("status") != "failed"


def test_mr6b_upgrade_gives_up_cleanly_with_one_connection(monkeypatch):
    """With a single BYOK connection the upgrade cannot assemble two
    independent participants; the original guided refusal stands instead of
    manufacturing a participant."""

    def fake_resolve(
        self,
        candidates,
        *,
        policy=SelectionPolicy.AUTO_INTELLIGENCE,
        manual_refs=(),
        minimum_quality=0.7,
        required_capabilities=(),
        required_context=0,
        required_participants=1,
        optimize_cost=True,
    ):
        viable = list(candidates)
        take = min(1, len(viable))
        return ModelSelectionDecision(
            SelectionPolicy.AUTO_INTELLIGENCE,
            tuple(item.model_ref for item in viable[:take]),
            None,
            "fake deterministic selection",
            "high",
        )

    monkeypatch.setattr(AutoIntelligenceRouter, "resolve", fake_resolve)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        context = _context(td)
        provider = _create_hub_provider(
            context, "Provider Solo", "model-a", "https://solo.example/v1"
        )
        # Two models on ONE connection: both pass the quality gate
        # (eligible >= 2) but a single connection can never supply two
        # independent participants.
        context.model_hub_store.replace_provider_catalog(
            provider["id"], ["model-a", "model-b"]
        )
        # v1.1.8 Phase 1: discovery alone does not publish a model into the
        # selectors, so this test publishes both explicitly before routing.
        for model in ("model-a", "model-b"):
            context.model_hub_store.set_model_visibility(provider["id"], model, True)
        with pytest.raises(ValueError, match="requires at least two participants"):
            context.start_chat_run(
                goal="Verify the claim and report the verified result.",
                participant_ids=[],
                mode="solo",
                selection_policy="auto_intelligence",
            )
