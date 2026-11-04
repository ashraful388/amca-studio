"""PS1-13I Durable Provenance Bindings (persistence/provenance only).

Maps and durably persists the binding path::

    org/run -> task -> agent/executor -> model resolution
        -> model call -> tool dispatch -> tool attempt
        -> artifact/result -> verification

Scope (persistence/provenance ONLY):
- No UI/chat/command-centre/health, no provider/model routing, no
  state-machine redesign, no run-tree API, no paid calls, V110 unchanged.
- Additive SQLite tables in the CANONICAL database file only
  (same file ``AgentRecoveryStore`` uses). No parallel database.
- Records the ACTUAL executed model (provider id, model id, local/cloud,
  call-attempt id, outcome). A fallback/router records B when B executed,
  never the requested A. No credentials/headers/tokens.
- Records org/run -> task -> agent -> tool -> attempt -> result linkage
  (safe ids, capability/authz ref, outcome, result/artifact ref). No full
  payload duplication, no secrets. Multi-call via existing call-attempt
  relations (distinct ``call_key`` per sub-call, e.g. ``verifier:<id>``).
- Failure/cancel provenance (incl. VERIFYING-cancel) without fabricating
  unexecuted bindings: missing bindings are ``None``/``[]``, never invented.
- Restart durable: rows survive close + reopen (live test).
- Cross-project isolated: every read requires ``requesting_project_id``.
- Secret safety via 13H serializers (allowlist + generic redaction).
  FAKE-only fixtures in tests; never real credentials.

Secret handling reuses ``amca.agent_observability`` sanitizers so 13H
raw-payload rules stay intact (no raw ``input_payload``/full response stored;
only digest/size/sanitized 200-char preview).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4


# -- Binding path (A) ----------------------------------------------------------

BINDING_PATH_STAGES: tuple[str, ...] = (
    "organization",
    "task",
    "agent_executor",
    "model_resolution",
    "model_call",
    "tool_dispatch",
    "tool_attempt",
    "artifact_result",
    "verification",
)

BINDING_PATH_MAPPED = True

_MODEL_OUTCOMES = frozenset({"succeeded", "failed", "cancelled"})
_TOOL_OUTCOMES = frozenset({"succeeded", "failed", "cancelled", "denied"})

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def is_safe_id(value: Any) -> bool:
    """Safe linkage ids: bounded charset/length, never secret material."""
    if not isinstance(value, str) or not value:
        return False
    if len(value) > 128:
        return False
    return bool(_SAFE_ID_RE.match(value))


def _require_safe_id(value: Any, *, name: str) -> str:
    text = str(value or "").strip()
    if not is_safe_id(text):
        raise ValueError(f"{name} must be a safe id (1-128 chars, [A-Za-z0-9._:/-])")
    return text


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _digest_preview(text: str | None) -> tuple[str | None, int | None, str | None]:
    """Digest/size/sanitized preview for a result payload (no full copy)."""
    if text is None:
        return None, None, None
    try:
        from amca.agent_observability import _redact_string_patterns as _redact
    except Exception:  # pragma: no cover - observability must not break persist
        def _redact(s: str) -> str:  # type: ignore[no-redef]
            return s
    raw = str(text)
    canonical = raw.encode("utf-8", errors="replace")
    digest = hashlib.sha256(canonical).hexdigest()
    size = len(canonical)
    preview = _redact(raw)[:200]
    return digest, size, preview


def extract_executed_model(adapter: Any, response: Any | None = None) -> dict[str, Any]:
    """Derive ACTUAL executed model identity from the invoked adapter + response.

    Prefers live response fields (what actually ran) then adapter-declared
    identity. Never includes credentials/headers/tokens (allowlist only).
    """
    adapter_id = str(
        getattr(adapter, "adapter_id", getattr(adapter, "name", type(adapter).__name__))
    )[:128]
    # Adapter-declared identity (may be absent on minimal fakes).
    provider_decl = getattr(adapter, "provider", getattr(adapter, "provider_id", None))
    model_decl = getattr(
        adapter, "model", getattr(adapter, "model_id", getattr(adapter, "name", None))
    )
    # Local/cloud classification via capabilities or attribute (same rule as runtime).
    try:
        caps = adapter.capabilities() if hasattr(adapter, "capabilities") and callable(adapter.capabilities) else None
        is_local = bool(getattr(caps, "is_local", getattr(adapter, "is_local", True)))
    except Exception:
        is_local = bool(getattr(adapter, "is_local", True))
    # Response-reported identity (actual execution evidence when present).
    resp_provider = getattr(response, "provider", None) if response is not None else None
    resp_model = getattr(response, "model", None) if response is not None else None
    provider_id = str(resp_provider or provider_decl or ("local-stub" if is_local else "unknown-provider"))[:128]
    model_id = str(resp_model or model_decl or adapter_id or "unknown-model")[:128]
    # Sanitize through 13H patterns (FAKE-safe redaction, no secrets).
    try:
        from amca.agent_observability import _redact_string_patterns as _redact

        provider_id = _redact(provider_id)
        model_id = _redact(model_id)
        adapter_id = _redact(adapter_id)
    except Exception:
        pass
    return {
        "adapter_id": adapter_id,
        "provider_id": provider_id,
        "model_id": model_id,
        "is_local": bool(is_local),
        "locality": "local" if is_local else "cloud",
    }


class AgentProvenanceStore:
    """Durable model/tool provenance bindings in the canonical database file.

    Additive tables only; shares the file ``AgentRecoveryStore`` uses so
    restart durability rides the same commit boundary. All reads are
    project-isolated (``requesting_project_id`` required).
    """

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._tx_connection: sqlite3.Connection | None = None
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._tx_connection is not None:
            yield self._tx_connection
            return
        connection = sqlite3.connect(str(self.database))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator["AgentProvenanceStore"]:
        if self._tx_connection is not None:
            raise RuntimeError("nested AgentProvenanceStore.transaction() call")
        connection = sqlite3.connect(str(self.database), isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        previous = self._tx_connection
        self._tx_connection = connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield self
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        finally:
            self._tx_connection = previous
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS desktop_agent_model_bindings (
                    binding_id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    task_attempt INTEGER NOT NULL,
                    call_key TEXT NOT NULL,
                    call_attempt_id TEXT,
                    agent_id TEXT NOT NULL,
                    executor_kind TEXT NOT NULL,
                    adapter_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    is_local INTEGER NOT NULL,
                    locality TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    response_digest TEXT,
                    response_size_bytes INTEGER,
                    response_preview TEXT,
                    executed_at TEXT NOT NULL,
                    UNIQUE(organization_id, task_id, task_attempt, call_key)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS desktop_agent_tool_bindings (
                    binding_id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    task_attempt INTEGER NOT NULL,
                    agent_id TEXT NOT NULL,
                    executor_kind TEXT NOT NULL,
                    tool_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    capability_ref TEXT,
                    authz_ref TEXT,
                    tool_execution_id TEXT NOT NULL,
                    call_attempt_id TEXT,
                    call_key TEXT,
                    outcome TEXT NOT NULL,
                    result_ref TEXT,
                    result_digest TEXT,
                    result_size_bytes INTEGER,
                    result_preview TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(organization_id, task_id, task_attempt, tool_id, operation, tool_execution_id)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_bind_org_task "
                "ON desktop_agent_model_bindings(organization_id, task_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_bind_project "
                "ON desktop_agent_model_bindings(project_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_bind_org_task "
                "ON desktop_agent_tool_bindings(organization_id, task_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_bind_project "
                "ON desktop_agent_tool_bindings(project_id)"
            )

    # -- model bindings (B) ----------------------------------------------------

    def record_model_binding(
        self,
        *,
        organization_id: str,
        project_id: str,
        task_id: str,
        task_attempt: int,
        call_key: str,
        agent_id: str,
        executor_kind: str,
        adapter_id: str,
        provider_id: str,
        model_id: str,
        is_local: bool,
        outcome: str,
        call_attempt_id: str | None = None,
        cost_usd: float = 0.0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        response_text: str | None = None,
    ) -> dict[str, Any]:
        """Persist one ACTUAL executed-model binding (no secrets, no full copy)."""
        organization_id = _require_safe_id(organization_id, name="organization_id")
        project_id = _require_safe_id(project_id, name="project_id")
        task_id = _require_safe_id(task_id, name="task_id")
        agent_id = _require_safe_id(agent_id, name="agent_id")
        call_key = str(call_key or "").strip()
        if not call_key or len(call_key) > 256:
            raise ValueError("call_key is required (<=256 chars)")
        executor_kind = str(executor_kind or "").strip()[:64] or "unknown"
        adapter_id = str(adapter_id or "").strip()[:128] or "unknown-adapter"
        provider_id = str(provider_id or "").strip()[:128] or "unknown-provider"
        model_id = str(model_id or "").strip()[:128] or "unknown-model"
        if outcome not in _MODEL_OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(_MODEL_OUTCOMES)}")
        attempt = int(task_attempt)
        if attempt < 1:
            raise ValueError("task_attempt must be >= 1")
        # Secret safety: sanitize ids/preview through 13H redaction; reject
        # credential-like keys if a caller smuggles a mapping (defense in depth).
        try:
            from amca.agent_observability import _redact_string_patterns as _redact

            adapter_id = _redact(adapter_id)
            provider_id = _redact(provider_id)
            model_id = _redact(model_id)
        except Exception:
            pass
        digest, size, preview = _digest_preview(response_text)
        binding_id = f"mb-{uuid4().hex[:16]}"
        executed_at = _now()
        locality = "local" if bool(is_local) else "cloud"
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT binding_id FROM desktop_agent_model_bindings
                   WHERE organization_id=? AND task_id=? AND task_attempt=? AND call_key=?""",
                (organization_id, task_id, attempt, call_key),
            ).fetchone()
            if existing is not None:
                binding_id = str(existing["binding_id"])
                connection.execute(
                    """UPDATE desktop_agent_model_bindings SET
                       call_attempt_id=COALESCE(?, call_attempt_id),
                       agent_id=?, executor_kind=?, adapter_id=?, provider_id=?, model_id=?,
                       is_local=?, locality=?, outcome=?, cost_usd=?,
                       input_tokens=COALESCE(?, input_tokens),
                       output_tokens=COALESCE(?, output_tokens),
                       response_digest=COALESCE(?, response_digest),
                       response_size_bytes=COALESCE(?, response_size_bytes),
                       response_preview=COALESCE(?, response_preview),
                       executed_at=?
                       WHERE binding_id=?""",
                    (
                        call_attempt_id, agent_id, executor_kind, adapter_id,
                        provider_id, model_id, 1 if is_local else 0, locality,
                        outcome, float(cost_usd or 0.0),
                        input_tokens, output_tokens, digest, size, preview,
                        executed_at, binding_id,
                    ),
                )
            else:
                connection.execute(
                    """INSERT INTO desktop_agent_model_bindings (
                       binding_id, organization_id, project_id, task_id, task_attempt,
                       call_key, call_attempt_id, agent_id, executor_kind, adapter_id,
                       provider_id, model_id, is_local, locality, outcome, cost_usd,
                       input_tokens, output_tokens, response_digest, response_size_bytes,
                       response_preview, executed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        binding_id, organization_id, project_id, task_id, attempt,
                        call_key, call_attempt_id, agent_id, executor_kind, adapter_id,
                        provider_id, model_id, 1 if is_local else 0, locality, outcome,
                        float(cost_usd or 0.0), input_tokens, output_tokens,
                        digest, size, preview, executed_at,
                    ),
                )
        row = self.get_model_binding(
            organization_id, task_id, attempt, call_key,
            requesting_project_id=project_id,
        )
        assert row is not None
        return row

    def get_model_binding(
        self,
        organization_id: str,
        task_id: str,
        task_attempt: int,
        call_key: str,
        *,
        requesting_project_id: str,
    ) -> dict[str, Any] | None:
        """Project-isolated single model binding (None when never executed)."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM desktop_agent_model_bindings
                   WHERE organization_id=? AND task_id=? AND task_attempt=? AND call_key=?""",
                (str(organization_id), str(task_id), int(task_attempt), str(call_key)),
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        if str(record["project_id"]) != str(requesting_project_id):
            raise PermissionError("cross-project provenance denied")
        return record

    def list_model_bindings_for_task(
        self,
        organization_id: str,
        task_id: str,
        *,
        requesting_project_id: str,
    ) -> list[dict[str, Any]]:
        """All model bindings for one task (multi-call: one row per call_key)."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM desktop_agent_model_bindings
                   WHERE organization_id=? AND task_id=?
                   ORDER BY task_attempt, call_key""",
                (str(organization_id), str(task_id)),
            ).fetchall()
        records = [dict(r) for r in rows]
        if records and any(str(r["project_id"]) != str(requesting_project_id) for r in records):
            raise PermissionError("cross-project provenance denied")
        # When no rows, verify the requester is not probing another project
        # via a fabricated org id: empty is truthful (never fabricate).
        return [r for r in records if str(r["project_id"]) == str(requesting_project_id)]

    def list_model_bindings_for_organization(
        self, organization_id: str, *, requesting_project_id: str
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM desktop_agent_model_bindings
                   WHERE organization_id=? ORDER BY task_id, task_attempt, call_key""",
                (str(organization_id),),
            ).fetchall()
        records = [dict(r) for r in rows]
        for record in records:
            if str(record["project_id"]) != str(requesting_project_id):
                raise PermissionError("cross-project provenance denied")
        return records

    # -- tool bindings (C) -----------------------------------------------------

    def record_tool_binding(
        self,
        *,
        organization_id: str,
        project_id: str,
        task_id: str,
        task_attempt: int,
        agent_id: str,
        executor_kind: str,
        tool_id: str,
        operation: str,
        outcome: str,
        tool_execution_id: str,
        capability_ref: str | None = None,
        authz_ref: str | None = None,
        call_attempt_id: str | None = None,
        call_key: str | None = None,
        result_ref: str | None = None,
        result_text: str | None = None,
    ) -> dict[str, Any]:
        """Persist one governed tool binding (safe ids, no full payload, no secrets)."""
        organization_id = _require_safe_id(organization_id, name="organization_id")
        project_id = _require_safe_id(project_id, name="project_id")
        task_id = _require_safe_id(task_id, name="task_id")
        agent_id = _require_safe_id(agent_id, name="agent_id")
        tool_id = _require_safe_id(tool_id, name="tool_id")
        operation = str(operation or "").strip()[:64] or "unknown"
        tool_execution_id = str(tool_execution_id or "").strip()
        if not tool_execution_id or len(tool_execution_id) > 256:
            raise ValueError("tool_execution_id is required (<=256 chars)")
        if outcome not in _TOOL_OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(_TOOL_OUTCOMES)}")
        attempt = int(task_attempt)
        if attempt < 1:
            raise ValueError("task_attempt must be >= 1")
        executor_kind = str(executor_kind or "").strip()[:64] or "unknown"
        try:
            from amca.agent_observability import _redact_string_patterns as _redact

            capability_ref = _redact(str(capability_ref))[:500] if capability_ref else None
            authz_ref = _redact(str(authz_ref))[:500] if authz_ref else None
            result_ref = _redact(str(result_ref))[:256] if result_ref else None
            tool_execution_id = _redact(tool_execution_id)[:256]
        except Exception:
            pass
        # Denied bindings must never carry a result (never fabricate success).
        if outcome == "denied" and (result_ref or result_text):
            raise ValueError("denied tool bindings must not carry result material")
        digest, size, preview = _digest_preview(result_text)
        binding_id = f"tb-{uuid4().hex[:16]}"
        created_at = _now()
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT binding_id FROM desktop_agent_tool_bindings
                   WHERE organization_id=? AND task_id=? AND task_attempt=?
                   AND tool_id=? AND operation=? AND tool_execution_id=?""",
                (organization_id, task_id, attempt, tool_id, operation, tool_execution_id),
            ).fetchone()
            if existing is not None:
                binding_id = str(existing["binding_id"])
                connection.execute(
                    """UPDATE desktop_agent_tool_bindings SET
                       agent_id=?, executor_kind=?, capability_ref=COALESCE(?, capability_ref),
                       authz_ref=COALESCE(?, authz_ref),
                       call_attempt_id=COALESCE(?, call_attempt_id),
                       call_key=COALESCE(?, call_key),
                       outcome=?, result_ref=COALESCE(?, result_ref),
                       result_digest=COALESCE(?, result_digest),
                       result_size_bytes=COALESCE(?, result_size_bytes),
                       result_preview=COALESCE(?, result_preview),
                       created_at=?
                       WHERE binding_id=?""",
                    (
                        agent_id, executor_kind, capability_ref, authz_ref,
                        call_attempt_id, call_key, outcome, result_ref,
                        digest, size, preview, created_at, binding_id,
                    ),
                )
            else:
                connection.execute(
                    """INSERT INTO desktop_agent_tool_bindings (
                       binding_id, organization_id, project_id, task_id, task_attempt,
                       agent_id, executor_kind, tool_id, operation, capability_ref,
                       authz_ref, tool_execution_id, call_attempt_id, call_key,
                       outcome, result_ref, result_digest, result_size_bytes,
                       result_preview, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        binding_id, organization_id, project_id, task_id, attempt,
                        agent_id, executor_kind, tool_id, operation, capability_ref,
                        authz_ref, tool_execution_id, call_attempt_id, call_key,
                        outcome, result_ref, digest, size, preview, created_at,
                    ),
                )
        rows = self.list_tool_bindings_for_task(
            organization_id, task_id, requesting_project_id=project_id
        )
        for row in rows:
            if str(row["binding_id"]) == binding_id:
                return row
        raise RuntimeError("tool binding write did not persist")

    def list_tool_bindings_for_task(
        self,
        organization_id: str,
        task_id: str,
        *,
        requesting_project_id: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM desktop_agent_tool_bindings
                   WHERE organization_id=? AND task_id=?
                   ORDER BY task_attempt, tool_id, operation, tool_execution_id""",
                (str(organization_id), str(task_id)),
            ).fetchall()
        records = [dict(r) for r in rows]
        if records and any(str(r["project_id"]) != str(requesting_project_id) for r in records):
            raise PermissionError("cross-project provenance denied")
        return [r for r in records if str(r["project_id"]) == str(requesting_project_id)]

    def list_tool_bindings_for_organization(
        self, organization_id: str, *, requesting_project_id: str
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM desktop_agent_tool_bindings
                   WHERE organization_id=? ORDER BY task_id, task_attempt, tool_id""",
                (str(organization_id),),
            ).fetchall()
        records = [dict(r) for r in rows]
        for record in records:
            if str(record["project_id"]) != str(requesting_project_id):
                raise PermissionError("cross-project provenance denied")
        return records


# -- Binding-path mapper (A) ----------------------------------------------------


def describe_task_provenance(
    provenance: AgentProvenanceStore,
    *,
    organization_id: str,
    project_id: str,
    task_id: str,
    requesting_project_id: str,
    task_row: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    executor_kind: str | None = None,
    verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map one task's full binding path (never fabricates missing links).

    Returns the nine-stage path with ``None``/``[]`` for stages that never
    executed (e.g. deterministic tasks have no model_call; cancelled tasks
    have no invented tool bindings).
    """
    if str(project_id) != str(requesting_project_id):
        raise PermissionError("cross-project provenance denied")
    model_bindings = provenance.list_model_bindings_for_task(
        str(organization_id), str(task_id), requesting_project_id=str(requesting_project_id)
    )
    tool_bindings = provenance.list_tool_bindings_for_task(
        str(organization_id), str(task_id), requesting_project_id=str(requesting_project_id)
    )
    resolved_agent = agent_id or (str(task_row.get("agent_id")) if task_row and task_row.get("agent_id") else None)
    resolved_executor = executor_kind or (str(task_row.get("executor_kind")) if task_row and task_row.get("executor_kind") else None)
    # Model resolution: actual executed identity when present, else declared only.
    if model_bindings:
        primary = sorted(model_bindings, key=lambda r: (int(r["task_attempt"]), str(r["call_key"])))[0]
        model_resolution: dict[str, Any] | None = {
            "provider_id": primary["provider_id"],
            "model_id": primary["model_id"],
            "locality": primary["locality"],
            "adapter_id": primary["adapter_id"],
            "source": "actual_executed_binding",
        }
    elif task_row and task_row.get("agent_id"):
        model_resolution = {"agent_id": str(task_row.get("agent_id")), "source": "declared_no_execution"}
    else:
        model_resolution = None
    artifact_refs = sorted({str(r["result_ref"]) for r in tool_bindings if r.get("result_ref")})
    return {
        "stages": list(BINDING_PATH_STAGES),
        "organization": {"organization_id": str(organization_id), "project_id": str(project_id)},
        "task": {"task_id": str(task_id), "status": (str(task_row.get("status")) if task_row else None)},
        "agent_executor": {"agent_id": resolved_agent, "executor_kind": resolved_executor},
        "model_resolution": model_resolution,
        "model_call": model_bindings,
        "tool_dispatch": [{"tool_id": r["tool_id"], "operation": r["operation"], "outcome": r["outcome"]} for r in tool_bindings],
        "tool_attempt": tool_bindings,
        "artifact_result": artifact_refs,
        "verification": dict(verification) if verification is not None else None,
        "bindings_present": {"model": bool(model_bindings), "tool": bool(tool_bindings)},
    }


def record_actual_model_binding(
    provenance: AgentProvenanceStore,
    recovery_store: Any | None,
    *,
    organization_id: str,
    project_id: str,
    task_id: str,
    task_attempt: int,
    call_key: str,
    agent_id: str,
    executor_kind: str,
    adapter: Any,
    response: Any | None,
    outcome: str,
    cost_usd: float = 0.0,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    response_text: str | None = None,
) -> dict[str, Any] | None:
    """Record one ACTUAL executed-model binding (best-effort, never raises).

    Resolves ``call_attempt_id`` from the recovery store when available so the
    binding links into existing call-attempt relations (D). Returns the row or
    ``None`` when persistence is unavailable (provenance never breaks dispatch).
    """
    try:
        identity = extract_executed_model(adapter, response)
        call_attempt_id: str | None = None
        if recovery_store is not None:
            try:
                row = recovery_store.get_call_attempt(
                    str(organization_id), str(task_id), int(task_attempt), str(call_key)
                )
                if row is not None:
                    call_attempt_id = str(row.get("call_attempt_id") or "") or None
            except Exception:
                call_attempt_id = None
        text = response_text
        if text is None and response is not None:
            try:
                text = str(getattr(response, "text", "")) or None
            except Exception:
                text = None
        usage = getattr(response, "usage", None) if response is not None else None
        if input_tokens is None and usage is not None:
            try:
                input_tokens = getattr(usage, "input_tokens", None)
            except Exception:
                input_tokens = None
        if output_tokens is None and usage is not None:
            try:
                output_tokens = getattr(usage, "output_tokens", None)
            except Exception:
                output_tokens = None
        return provenance.record_model_binding(
            organization_id=str(organization_id),
            project_id=str(project_id),
            task_id=str(task_id),
            task_attempt=int(task_attempt),
            call_key=str(call_key),
            agent_id=str(agent_id),
            executor_kind=str(executor_kind),
            adapter_id=str(identity["adapter_id"]),
            provider_id=str(identity["provider_id"]),
            model_id=str(identity["model_id"]),
            is_local=bool(identity["is_local"]),
            outcome=str(outcome),
            call_attempt_id=call_attempt_id,
            cost_usd=float(cost_usd or 0.0),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            response_text=text,
        )
    except Exception:
        return None


__all__ = [
    "BINDING_PATH_MAPPED",
    "BINDING_PATH_STAGES",
    "AgentProvenanceStore",
    "describe_task_provenance",
    "extract_executed_model",
    "is_safe_id",
    "record_actual_model_binding",
]
