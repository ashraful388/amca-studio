"""Configured-model health classification for the desktop model layer (H1).

Milestone: ZCODE-AMCA-STUDIO-V1.0.1-H1-MODEL-USABILITY-FIRST-RUN-REPAIR-01

A configured model entry is not a usable model.  This module classifies every
direct-profile and Model-Hub provider model into one of five health states
from *evidence AMCA already observed* — never from optimistic defaults:

READY                  the application verified this exact configuration works
AUTH_REQUIRED          the provider rejected the credential (HTTP 401/403)
MODEL_NOT_FOUND        the provider accepted auth but not the model id (404/410)
PROVIDER_UNAVAILABLE   the provider endpoint failed (5xx / network)
LOCAL_RUNTIME_OFFLINE  the local runtime (Ollama) is not reachable
UNVERIFIED             configured but never verified (not offered as usable)

Verdicts are persisted (SQLite ``model_health`` table, keyed by config
fingerprint) so a classification survives restarts, and every Auto/Manual
eligibility path consults them: a failing model is never silently
auto-selected, and the Chat composer can tell the user exactly which setup
action to take instead of manufacturing a failed run.

33A usability repair: a persisted READY verdict remains the execution
authorization for that exact config fingerprint across restarts (the
fingerprint already covers kind/ref/endpoint/model, so a changed
configuration is a different key and re-verification is required).  A
failure verdict blocks until a newer verification reclassifies it, and a
run that would be rejected on a failed verdict re-verifies the blocked
configurations once on the spot (bounded, metadata-level) before giving
up, so transient outages never permanently disable a model.  ``fresh``
stays available as a diagnostic ("observed by this service session").

The fingerprint deliberately does NOT cover the credential: a verdict
records whether a configuration worked, and the same configuration
(endpoint + model id) is still the same configuration when the key
changes.  A replaced key therefore needs explicit invalidation
(``ModelHealthStore.delete_for_ref_prefix``) — otherwise a failure
measured with the old key would keep blocking a model whose new key has
never been tested, and a READY verdict would keep authorizing a model
whose key was removed.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

READY = "READY"
AUTH_REQUIRED = "AUTH_REQUIRED"
MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
LOCAL_RUNTIME_OFFLINE = "LOCAL_RUNTIME_OFFLINE"
QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
UNVERIFIED = "UNVERIFIED"

#: States a user may actually run a task against.
RUNNABLE_STATES = frozenset({READY})

#: Operator-facing labels. The internal state constant is a machine
#: vocabulary and never reaches the operator: the UI, the Model settings
#: badge and the run-refusal message all speak these words. The frontend
#: badge table (desktop-ui/src/modelSettingsNavModel.ts) uses the same
#: words for the same states.
HEALTH_STATE_LABELS = {
    READY: "Runnable",
    AUTH_REQUIRED: "Auth required",
    MODEL_NOT_FOUND: "Not found",
    PROVIDER_UNAVAILABLE: "Provider down",
    LOCAL_RUNTIME_OFFLINE: "Runtime offline",
    QUOTA_EXHAUSTED: "No credit",
    UNVERIFIED: "Not configured",
}


def health_state_label(state: str | None) -> str:
    """The operator-facing label for one health state."""

    return HEALTH_STATE_LABELS.get(str(state or ""), "Unverified")


#: States that describe a DEFECT in the configuration itself: the provider
#: rejected the credential, the model id does not exist there, or no
#: endpoint was configured.  Only these exclude a model.  A transient
#: failure (provider down, rate limited, no answer in time) says nothing
#: about whether the configuration is correct, and a run re-verifies a
#: blocked configuration before it gives up — so a provider hiccup must not
#: lock a working model out of the composer.
#:
#: A model with NO recorded verdict at all is a different thing entirely
#: (never measured) and is usable.
CONFIGURATION_DEFECT_STATES = frozenset(
    {AUTH_REQUIRED, MODEL_NOT_FOUND, QUOTA_EXHAUSTED, UNVERIFIED}
)


def is_configuration_defect(state: str | None) -> bool:
    """True when the recorded verdict means the CONFIGURATION is wrong."""

    return str(state or "") in CONFIGURATION_DEFECT_STATES

#: HTTP-status → health classification for OpenAI-compatible style failures.
HTTP_STATUS_MAP = {
    401: AUTH_REQUIRED,
    403: AUTH_REQUIRED,
    404: MODEL_NOT_FOUND,
    410: MODEL_NOT_FOUND,
}

SCHEMA = "amca-model-health/1"


def config_fingerprint(*, kind: str, ref: str, endpoint: str, model: str) -> str:
    """Stable fingerprint of one model configuration (never includes secrets)."""

    payload = json.dumps(
        {"kind": str(kind), "ref": str(ref), "endpoint": str(endpoint), "model": str(model)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def classify_http_status(status: int) -> str:
    """Map an observed provider HTTP status to a model-health state."""

    mapped = HTTP_STATUS_MAP.get(int(status))
    if mapped is not None:
        return mapped
    if 500 <= int(status) < 600:
        return PROVIDER_UNAVAILABLE
    if 429:
        return PROVIDER_UNAVAILABLE
    return PROVIDER_UNAVAILABLE


def classify_exception(exc: BaseException) -> str:
    """Classify a transport-level failure (connection refused, timeout, …)."""

    text = str(exc).casefold()
    if "timed out" in text or "timeout" in text:
        return PROVIDER_UNAVAILABLE
    return PROVIDER_UNAVAILABLE


@dataclass(frozen=True, slots=True)
class HealthRecord:
    config_key: str
    kind: str
    ref: str
    endpoint: str
    model: str
    state: str
    detail: str
    verified_at: str
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_key": self.config_key,
            "kind": self.kind,
            "ref": self.ref,
            "endpoint": self.endpoint,
            "model": self.model,
            "state": self.state,
            "detail": self.detail,
            "verified_at": self.verified_at,
            "evidence": self.evidence,
            "runnable": self.state in RUNNABLE_STATES,
        }


class ModelHealthStore:
    """SQLite persistence for observed model-health verdicts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # H1.2-A (revised 33A): a record is FRESH only if it was observed by
        # this service session (verified_at >= boot timestamp); freshness is a
        # diagnostic.  A persisted READY stays the execution authorization for
        # its exact config fingerprint across restarts; failures block until a
        # newer verification reclassifies them, and runs re-verify on demand.
        self.boot_timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_health (
                    config_key TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    ref TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    model TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    evidence TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.rollback()
                raise
            else:
                db.commit()

    def record(self, record: HealthRecord) -> None:
        with self._transaction() as db:
            db.execute(
                "INSERT INTO model_health VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(config_key) DO UPDATE SET state=excluded.state, "
                "detail=excluded.detail, verified_at=excluded.verified_at, "
                "evidence=excluded.evidence",
                (
                    record.config_key,
                    record.kind,
                    record.ref,
                    record.endpoint,
                    record.model,
                    record.state,
                    record.detail,
                    record.verified_at,
                    record.evidence,
                ),
            )

    def get(self, config_key: str) -> HealthRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM model_health WHERE config_key=?", (config_key,)
            ).fetchone()
        if row is None:
            return None
        return HealthRecord(
            config_key=row[0], kind=row[1], ref=row[2], endpoint=row[3],
            model=row[4], state=row[5], detail=row[6], verified_at=row[7],
            evidence=row[8],
        )

    def delete_for_ref_prefix(self, *, kind: str, ref_prefix: str) -> int:
        """Drop every verdict recorded for a reference or its model children.

        A verdict describes ONE configuration: the endpoint, the model id and
        the credential that was in use when it was observed.  When the
        credential is replaced, the old verdict describes a configuration that
        no longer exists, so it must not keep blocking a configuration whose
        new key has never been tested.  Hub references are
        ``<provider id>:<model id>``, so a provider's verdicts are dropped by
        prefix.
        """

        with self._transaction() as db:
            cursor = db.execute(
                "DELETE FROM model_health WHERE kind=? AND (ref=? OR instr(ref, ?)=1)",
                (str(kind), str(ref_prefix), f"{ref_prefix}:"),
            )
            return int(cursor.rowcount or 0)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM model_health").fetchall()
        snapshot: dict[str, dict[str, Any]] = {}
        for row in rows:
            record = HealthRecord(
                config_key=row[0], kind=row[1], ref=row[2], endpoint=row[3],
                model=row[4], state=row[5], detail=row[6], verified_at=row[7],
                evidence=row[8],
            )
            entry = record.to_dict()
            # Diagnostic freshness marker — True when READY was observed by
            # THIS service session.  It no longer gates runnability: a
            # persisted READY stays the authorization for this exact config
            # fingerprint across restarts, and a run re-verifies on demand
            # when it would otherwise be rejected on a failed verdict.
            fresh = bool(
                record.state == READY
                and record.verified_at >= self.boot_timestamp
            )
            entry["fresh"] = fresh
            # Runnable = the last observed verdict for this exact
            # configuration was READY.  Reclassification happens only through
            # a newer verification (manual verify, verify-all, or the bounded
            # pre-run repair in service.start_chat_run / _auto_model_candidates).
            entry["runnable"] = record.state in RUNNABLE_STATES
            snapshot[row[0]] = entry
        return snapshot


def record_for_config(
    *,
    kind: str,
    ref: str,
    endpoint: str,
    model: str,
    state: str,
    detail: str,
    evidence: str,
) -> HealthRecord:
    """Build a HealthRecord with the canonical fingerprint and timestamp."""

    return HealthRecord(
        config_key=config_fingerprint(kind=kind, ref=ref, endpoint=endpoint, model=model),
        kind=kind,
        ref=ref,
        endpoint=endpoint,
        model=model,
        state=state,
        detail=detail,
        verified_at=datetime.now(UTC).isoformat(timespec="seconds"),
        evidence=evidence,
    )
