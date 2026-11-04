"""Deterministic, privacy-conscious AMCA audit exports."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .domain import CognitiveEvent, ProjectState
from .storage import SQLiteStore


EXPORT_SCHEMA_VERSION = "1.0.0"
_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "access_token",
    "refresh_token",
    "secret",
)


class ExportProjectNotFoundError(KeyError):
    """Raised when an export target has no persisted snapshot."""


def _canonical_json(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=None if indent is not None else (",", ":"),
        indent=indent,
    )


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).casefold().replace("-", "_").replace(" ", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def redact_secrets(value: Any) -> Any:
    """Redact values under credential-like keys without altering source data."""

    if isinstance(value, Mapping):
        return {
            str(key): _REDACTED if _is_sensitive_key(key) else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        normalized = value.casefold().replace("-", "_").replace(" ", "_")
        if any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS):
            # Reducers may preserve a mapping as a reportable summary string.
            # Once a credential-like key appears in such a string, redacting
            # only its value reliably would require unsafe format guessing.
            return _REDACTED
    return value


def _state_record(state: ProjectState) -> dict[str, Any]:
    raw_json = state.to_json()
    return {
        "version": state.version,
        "sha256": _digest_text(raw_json),
        "state": redact_secrets(state.to_dict()),
    }


def _event_version_map(state: ProjectState) -> dict[str, int]:
    versions: dict[str, int] = {}
    for contribution in state.model_contributions:
        event_id = contribution.get("event_id")
        integrated = contribution.get("integrated_state_version")
        if isinstance(event_id, str) and isinstance(integrated, int):
            versions[event_id] = integrated
    return versions


def _event_record(
    event: CognitiveEvent,
    *,
    sequence: int,
    status: str,
    applied_state_version: int | None,
    state_event_digest: str | None,
    previous_chain_digest: str,
) -> tuple[dict[str, Any], str]:
    event_digest = _digest_text(event.to_json())
    chain_input = _canonical_json(
        {
            "sequence": sequence,
            "previous_chain_sha256": previous_chain_digest,
            "event_sha256": event_digest,
        }
    )
    chain_digest = _digest_text(chain_input)
    return (
        {
            "sequence": sequence,
            "event_id": event.id,
            "status": status,
            "start_state_version": event.state_version,
            "applied_state_version": applied_state_version,
            "event_sha256": event_digest,
            "digest_matches_state": (
                event_digest == state_event_digest
                if state_event_digest is not None
                else None
            ),
            "previous_chain_sha256": previous_chain_digest,
            "chain_sha256": chain_digest,
            "event": redact_secrets(event.to_dict()),
        },
        chain_digest,
    )


def build_audit_export(
    store: SQLiteStore,
    project_id: str,
    *,
    include_snapshots: bool = True,
) -> dict[str, Any]:
    """Build a deterministic local export with snapshot and event hash evidence."""

    state = store.load_state(project_id)
    if state is None:
        raise ExportProjectNotFoundError(f"project {project_id!r} was not found")

    events = store.load_events(project_id)
    pending_ids = {event.id for event in store.pending_events(project_id)}
    applied_ids = set(state.applied_event_ids)
    applied_versions = _event_version_map(state)
    previous_chain_digest = "0" * 64
    event_log: list[dict[str, Any]] = []
    for sequence, event in enumerate(events, start=1):
        if event.id in pending_ids:
            status = "pending"
        elif event.id in applied_ids:
            status = "applied"
        else:
            status = "unknown"
        record, previous_chain_digest = _event_record(
            event,
            sequence=sequence,
            status=status,
            applied_state_version=applied_versions.get(event.id),
            state_event_digest=state.applied_event_digests.get(event.id),
            previous_chain_digest=previous_chain_digest,
        )
        event_log.append(record)

    latest = _state_record(state)
    snapshots = (
        [
            _state_record(snapshot)
            for version in store.snapshot_versions(project_id)
            if (snapshot := store.load_state(project_id, version)) is not None
        ]
        if include_snapshots
        else []
    )
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "kind": "amca_project_audit_export",
        "project_id": project_id,
        "database_schema_version": store.SCHEMA_VERSION,
        "privacy": {
            "secret_key_redaction": True,
            "redaction_marker": _REDACTED,
            "content_warning": (
                "This local export contains project conclusions, evidence, and "
                "event content. Protect it according to the project's sensitivity."
            ),
        },
        "latest_state": latest,
        "snapshots": snapshots,
        "event_log": event_log,
        "audit": {
            "hash_algorithm": "sha256",
            "snapshot_count": len(store.snapshot_versions(project_id)),
            "event_count": len(event_log),
            "pending_event_count": len(pending_ids),
            "unknown_event_count": sum(
                record["status"] == "unknown" for record in event_log
            ),
            "event_digest_mismatch_count": sum(
                record["digest_matches_state"] is False for record in event_log
            ),
            "latest_state_sha256": latest["sha256"],
            "event_chain_sha256": previous_chain_digest,
            "digest_scope": (
                "Digests cover the exact canonical persisted state/event JSON "
                "before export-only secret-key redaction."
            ),
            "tamper_evidence_scope": (
                "The chain proves internal consistency of this export and checks "
                "applied events against state-recorded digests. It is not a signed, "
                "externally anchored audit ledger."
            ),
        },
    }


def audit_log_view(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return the compact collaboration-log view of a full audit export."""

    return {
        "schema_version": document["schema_version"],
        "kind": "amca_auditable_collaboration_log",
        "project_id": document["project_id"],
        "privacy": document["privacy"],
        "audit": document["audit"],
        "event_log": document["event_log"],
    }


def write_json_document(
    document: Mapping[str, Any],
    output: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write JSON without silently replacing an existing file by default."""

    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = _canonical_json(document, indent=2) + "\n"
    if not overwrite:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
        return target

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_name = handle.name
        os.replace(temporary_name, target)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return target


__all__ = [
    "EXPORT_SCHEMA_VERSION",
    "ExportProjectNotFoundError",
    "audit_log_view",
    "build_audit_export",
    "redact_secrets",
    "write_json_document",
]
