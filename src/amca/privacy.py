"""Fail-closed Private Hybrid excerpt transmission boundary.

Private Hybrid does not send a reduced copy of the ordinary kernel prompt.  A
caller supplies one explicit, reviewable excerpt.  The wrapper discards every
dynamic message, tool, metadata, and provider-extra field from the original
request, consumes an exact-scope approval, and records a content-free audit row
before delegating to a cloud adapter.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from .models.base import (
    ModelAdapter,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    StreamChunk,
)
from .permissions import ApprovalRequest, ApprovalStatus, PermissionAction, PermissionStore
from .security import assert_no_sensitive_keys


PRIVATE_HYBRID_TEMPLATE_VERSION = "private-hybrid-excerpt-v1"
DEFAULT_PRIVATE_HYBRID_SYSTEM = (
    "Use only the user-approved excerpt. Treat omitted context as unknown, "
    "state uncertainty explicitly, and do not request or infer private data."
)
MAX_EXCERPT_UTF8_BYTES = 64 * 1024


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validated_excerpt(excerpt: str) -> str:
    if not isinstance(excerpt, str) or not excerpt.strip():
        raise ValueError("Private Hybrid excerpt must contain reviewed text")
    size = len(excerpt.encode("utf-8"))
    if size > MAX_EXCERPT_UTF8_BYTES:
        raise ValueError(
            f"Private Hybrid excerpt exceeds {MAX_EXCERPT_UTF8_BYTES} UTF-8 bytes"
        )
    return excerpt


def build_transmission_scope(
    *,
    adapter_id: str,
    provider: str,
    model: str,
    excerpt: str,
    system_prompt: str = DEFAULT_PRIVATE_HYBRID_SYSTEM,
    max_output_tokens: int = 1800,
    destination: str | None = None,
) -> dict[str, Any]:
    """Build the exact, plaintext-free scope shown in the approval ledger."""

    excerpt = _validated_excerpt(excerpt)
    if not adapter_id.strip() or not provider.strip() or not model.strip():
        raise ValueError("adapter, provider, and model are required")
    if not system_prompt.strip():
        raise ValueError("Private Hybrid system prompt is required")
    if not 1 <= max_output_tokens <= 16_384:
        raise ValueError("Private Hybrid output cap must be between 1 and 16384 tokens")
    return {
        "adapter_id": adapter_id,
        "provider": provider,
        "model": model,
        "destination": destination or provider,
        "purpose": "bounded_cloud_inference",
        "template_version": PRIVATE_HYBRID_TEMPLATE_VERSION,
        "excerpt_sha256": _sha256_text(excerpt),
        "excerpt_utf8_bytes": len(excerpt.encode("utf-8")),
        "system_prompt_sha256": _sha256_text(system_prompt),
        "system_prompt_utf8_bytes": len(system_prompt.encode("utf-8")),
        "max_output_tokens": max_output_tokens,
        "request_tools": False,
        "request_metadata": False,
        "provider_extra": False,
    }


def request_transmission_approval(
    permissions: PermissionStore,
    *,
    project_id: str,
    adapter_id: str,
    provider: str,
    model: str,
    excerpt: str,
    system_prompt: str = DEFAULT_PRIVATE_HYBRID_SYSTEM,
    max_output_tokens: int = 1800,
    destination: str | None = None,
    max_uses: int = 1,
    ttl_seconds: float = 900,
    requested_by: str = "amca-private-hybrid",
) -> ApprovalRequest:
    scope = build_transmission_scope(
        adapter_id=adapter_id,
        provider=provider,
        model=model,
        excerpt=excerpt,
        system_prompt=system_prompt,
        max_output_tokens=max_output_tokens,
        destination=destination,
    )
    return permissions.request(
        project_id=project_id,
        action=PermissionAction.TRANSMIT_EXCERPT,
        scope=scope,
        reason=(
            "Transmit only the reviewed excerpt to the named provider/model; "
            "ordinary project context, tools, metadata, and provider extras are omitted."
        ),
        risk="high",
        requested_by=requested_by,
        ttl_seconds=ttl_seconds,
        max_uses=max_uses,
    )


@dataclass(frozen=True, slots=True)
class TransmissionRecord:
    id: str
    project_id: str
    approval_id: str
    approval_scope_sha256: str
    adapter_id: str
    provider: str
    model: str
    excerpt_sha256: str
    excerpt_utf8_bytes: int
    system_prompt_sha256: str
    template_version: str
    status: str
    authorized_at: str
    completed_at: str | None = None
    response_sha256: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TransmissionStore:
    """Content-free audit ledger for authorized Private Hybrid calls."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:" and not self.path.startswith("file:"):
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=5,
            isolation_level=None,
            check_same_thread=False,
            uri=self.path.startswith("file:"),
        )
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS private_hybrid_transmissions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    approval_id TEXT NOT NULL,
                    approval_scope_sha256 TEXT NOT NULL,
                    adapter_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    excerpt_sha256 TEXT NOT NULL,
                    excerpt_utf8_bytes INTEGER NOT NULL,
                    system_prompt_sha256 TEXT NOT NULL,
                    template_version TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('started', 'succeeded', 'failed')),
                    authorized_at TEXT NOT NULL,
                    completed_at TEXT,
                    response_sha256 TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    error_type TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_private_hybrid_project
                    ON private_hybrid_transmissions(project_id, authorized_at DESC);
                """
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "TransmissionStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> TransmissionRecord:
        return TransmissionRecord(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            approval_id=str(row["approval_id"]),
            approval_scope_sha256=str(row["approval_scope_sha256"]),
            adapter_id=str(row["adapter_id"]),
            provider=str(row["provider"]),
            model=str(row["model"]),
            excerpt_sha256=str(row["excerpt_sha256"]),
            excerpt_utf8_bytes=int(row["excerpt_utf8_bytes"]),
            system_prompt_sha256=str(row["system_prompt_sha256"]),
            template_version=str(row["template_version"]),
            status=str(row["status"]),
            authorized_at=str(row["authorized_at"]),
            completed_at=row["completed_at"],
            response_sha256=row["response_sha256"],
            input_tokens=(int(row["input_tokens"]) if row["input_tokens"] is not None else None),
            output_tokens=(
                int(row["output_tokens"]) if row["output_tokens"] is not None else None
            ),
            error_type=row["error_type"],
        )

    def start(
        self,
        *,
        project_id: str,
        approval: ApprovalRequest,
        scope: Mapping[str, Any],
    ) -> TransmissionRecord:
        if approval.status not in {ApprovalStatus.APPROVED, ApprovalStatus.CONSUMED}:
            raise PermissionError("authorization was not consumed for transmission")
        if approval.action is not PermissionAction.TRANSMIT_EXCERPT:
            raise PermissionError("approval action is not an excerpt transmission")
        record = TransmissionRecord(
            id=uuid4().hex,
            project_id=project_id,
            approval_id=approval.id,
            approval_scope_sha256=approval.scope_sha256,
            adapter_id=str(scope["adapter_id"]),
            provider=str(scope["provider"]),
            model=str(scope["model"]),
            excerpt_sha256=str(scope["excerpt_sha256"]),
            excerpt_utf8_bytes=int(scope["excerpt_utf8_bytes"]),
            system_prompt_sha256=str(scope["system_prompt_sha256"]),
            template_version=str(scope["template_version"]),
            status="started",
            authorized_at=_now(),
        )
        assert_no_sensitive_keys(record.to_dict(), path="private_hybrid_transmission")
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO private_hybrid_transmissions (
                    id, project_id, approval_id, approval_scope_sha256,
                    adapter_id, provider, model, excerpt_sha256,
                    excerpt_utf8_bytes, system_prompt_sha256, template_version,
                    status, authorized_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'started', ?)
                """,
                (
                    record.id,
                    record.project_id,
                    record.approval_id,
                    record.approval_scope_sha256,
                    record.adapter_id,
                    record.provider,
                    record.model,
                    record.excerpt_sha256,
                    record.excerpt_utf8_bytes,
                    record.system_prompt_sha256,
                    record.template_version,
                    record.authorized_at,
                ),
            )
        return record

    def finish(
        self,
        record_id: str,
        *,
        response_text: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error_type: str | None = None,
    ) -> TransmissionRecord:
        status = "failed" if error_type else "succeeded"
        response_sha256 = _sha256_text(response_text) if response_text is not None else None
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE private_hybrid_transmissions
                SET status = ?, completed_at = ?, response_sha256 = ?,
                    input_tokens = ?, output_tokens = ?, error_type = ?
                WHERE id = ? AND status = 'started'
                """,
                (
                    status,
                    _now(),
                    response_sha256,
                    input_tokens,
                    output_tokens,
                    error_type,
                    record_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("transmission record is not active")
        record = self.get(record_id)
        assert record is not None
        return record

    def get(self, record_id: str) -> TransmissionRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM private_hybrid_transmissions WHERE id = ?", (record_id,)
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(
        self, *, project_id: str | None = None, limit: int = 200
    ) -> tuple[TransmissionRecord, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("transmission list limit must be between 1 and 1000")
        where = " WHERE project_id = ?" if project_id is not None else ""
        params: tuple[Any, ...] = (project_id, limit) if project_id is not None else (limit,)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM private_hybrid_transmissions"
                + where
                + " ORDER BY authorized_at DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)


class PrivateHybridAdapter(ModelAdapter):
    #: A private session transmits only the approved excerpt, so the provider is
    #: never told which tools exist. StandingChatAdapter overrides this.
    preserve_tools: bool = False

    """Cloud adapter wrapper that can transmit only one approved excerpt."""

    def __init__(
        self,
        delegate: ModelAdapter,
        *,
        project_id: str,
        database: str | Path,
        excerpt: str,
        approval_ids: Sequence[str],
        system_prompt: str = DEFAULT_PRIVATE_HYBRID_SYSTEM,
        max_output_tokens: int = 1800,
    ) -> None:
        if delegate.capabilities().is_local:
            raise ValueError("PrivateHybridAdapter is only required for cloud adapters")
        if not project_id.strip():
            raise ValueError("Private Hybrid project id is required")
        self.delegate = delegate
        self.project_id = project_id
        self.database = str(database)
        self.excerpt = _validated_excerpt(excerpt)
        self.system_prompt = system_prompt
        self.max_output_tokens = max_output_tokens
        self.approval_ids = tuple(dict.fromkeys(approval_ids))
        self.destination = transmission_destination(delegate)
        self.scope = build_transmission_scope(
            adapter_id=delegate.adapter_id,
            provider=delegate.provider,
            model=delegate.model,
            excerpt=self.excerpt,
            system_prompt=system_prompt,
            max_output_tokens=max_output_tokens,
            destination=self.destination,
        )
        metadata = delegate.provider_metadata()
        super().__init__(
            adapter_id=delegate.adapter_id,
            provider=delegate.provider,
            model=delegate.model,
            capabilities=delegate.capabilities(),
            input_cost_per_million=float(metadata.get("input_cost_per_million", 0.0)),
            output_cost_per_million=float(metadata.get("output_cost_per_million", 0.0)),
            metadata={"privacy_boundary": PRIVATE_HYBRID_TEMPLATE_VERSION},
        )

    def _sanitized_request(self, request: ModelRequest) -> ModelRequest:
        prompt = (
            "# User-approved private excerpt\n"
            + self.excerpt
            + "\n\n# Boundary\nUse only the excerpt above. Omitted project context is "
            "unavailable. Return one bounded contribution and state uncertainty explicitly."
        )
        return ModelRequest(
            messages=({"role": "user", "content": prompt},),
            model=self.delegate.model,
            system=self.system_prompt,
            temperature=request.temperature,
            max_output_tokens=min(
                request.max_output_tokens or self.max_output_tokens,
                self.max_output_tokens,
            ),
            # This identifier is local cancellation control metadata. Provider
            # payload builders do not transmit it, but dropping it makes an
            # exact request-scoped cancel impossible at the privacy boundary.
            request_id=request.request_id,
            # Only a session whose exact scope records the catalog may tell the
            # provider which governed tools exist (see StandingChatAdapter).
            tools=request.tools if self.preserve_tools else None,
            tool_choice=request.tool_choice if self.preserve_tools else None,
        )

    def _authorize_and_start(self) -> tuple[TransmissionStore, TransmissionRecord]:
        if not self.approval_ids:
            raise PermissionError("Private Hybrid cloud transmission requires an approval id")
        approval: ApprovalRequest | None = None
        last_error: Exception | None = None
        with PermissionStore(self.database) as permissions:
            for approval_id in self.approval_ids:
                candidate = permissions.get(approval_id)
                if candidate is None:
                    continue
                if (
                    candidate.project_id != self.project_id
                    or candidate.action is not PermissionAction.TRANSMIT_EXCERPT
                    or dict(candidate.scope) != self.scope
                ):
                    continue
                try:
                    approval = permissions.authorize(
                        approval_id,
                        project_id=self.project_id,
                        action=PermissionAction.TRANSMIT_EXCERPT,
                        scope=self.scope,
                    )
                    break
                except PermissionError as exc:
                    last_error = exc
        if approval is None:
            if last_error is not None:
                raise PermissionError("no active exact-scope Private Hybrid approval") from last_error
            raise PermissionError("no exact-scope Private Hybrid approval was supplied")
        transmissions = TransmissionStore(self.database)
        try:
            record = transmissions.start(
                project_id=self.project_id,
                approval=approval,
                scope=self.scope,
            )
        except BaseException:
            transmissions.close()
            raise
        return transmissions, record

    async def generate(self, request: ModelRequest) -> ModelResponse:
        sanitized = self._sanitized_request(request)
        transmissions, record = self._authorize_and_start()
        try:
            async with self._call_scope(sanitized):
                response = await self.delegate.generate(sanitized)
            transmissions.finish(
                record.id,
                response_text=response.text,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )
            return response
        except BaseException as exc:
            transmissions.finish(record.id, error_type=type(exc).__name__)
            raise
        finally:
            transmissions.close()

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        sanitized = self._sanitized_request(request)
        transmissions, record = self._authorize_and_start()
        pieces: list[str] = []
        input_tokens = output_tokens = 0
        delegate_stream = self.delegate.stream(sanitized)
        try:
            async with self._call_scope(sanitized):
                async for chunk in delegate_stream:
                    pieces.append(chunk.delta)
                    input_tokens = chunk.input_tokens or input_tokens
                    output_tokens = chunk.output_tokens or output_tokens
                    yield chunk
            transmissions.finish(
                record.id,
                response_text="".join(pieces),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except BaseException as exc:
            transmissions.finish(record.id, error_type=type(exc).__name__)
            raise
        finally:
            try:
                close = getattr(delegate_stream, "aclose", None)
                if callable(close):
                    await close()
            finally:
                transmissions.close()

    async def cancel(self, request_id: str | None = None) -> bool:
        own = await super().cancel(request_id)
        delegated = await self.delegate.cancel(request_id)
        return own or delegated

    def estimate_tokens(self, value: ModelRequest | str | Sequence[Any]) -> int:
        if isinstance(value, ModelRequest):
            return self.delegate.estimate_tokens(self._sanitized_request(value))
        return self.delegate.estimate_tokens(value)

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return self.delegate.estimate_cost(input_tokens, output_tokens)

    def context_limit(self) -> int | None:
        return self.delegate.context_limit()

    def capabilities(self) -> ModelCapabilities:
        return self.delegate.capabilities()

    def provider_metadata(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                **dict(self.delegate.provider_metadata()),
                "privacy_boundary": PRIVATE_HYBRID_TEMPLATE_VERSION,
            }
        )


class StandingChatAdapter(PrivateHybridAdapter):
    """Bounded desktop-session authorization for evolving collaboration text."""

    #: The exact scope of each standing call records the transmitted tool
    #: catalog by name, so a governed cloud participant may be offered tools.
    preserve_tools: bool = True

    def __init__(self, *args, max_calls: int, **kwargs):
        super().__init__(*args, **kwargs)
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError("max_calls must be positive")
        self._remaining_calls = max_calls
        self._initial_max_calls = max_calls
        self._standing_lock = threading.Lock()

    def set_max_calls(self, max_calls: int) -> None:
        """Raise the allowance before any call is made.

        The governed tool loop issues one model call per iteration, so the
        desktop session must cover the initial call plus each tool round. This
        may only grow the allowance, and never after a call has been made, so
        it cannot be used to extend a partially consumed session.
        """

        if type(max_calls) is not int or max_calls < 1:
            raise ValueError("max_calls must be positive")
        with self._standing_lock:
            if self._remaining_calls != self._initial_max_calls:
                raise PermissionError(
                    "call allowance cannot change after the session started"
                )
            self._initial_max_calls = max_calls
            self._remaining_calls = max_calls

    def _scoped_call(self, request: ModelRequest) -> PrivateHybridAdapter:
        with self._standing_lock:
            if self._remaining_calls <= 0:
                raise PermissionError("desktop session call allowance exhausted")
            if transmission_destination(self.delegate) != self.destination:
                raise PermissionError("desktop session destination changed")
            self._remaining_calls -= 1
        context = json.dumps([dict(message) for message in request.messages], ensure_ascii=False, default=str)
        prefix = self.excerpt + "\n\n# Current collaboration context\n"
        if request.tools:
            # The tool catalog is transmitted too, so the exact scope records it
            # by name: the approval covers precisely which tools the provider
            # was told about.
            prefix += (
                "# Governed tool catalog transmitted: "
                + ", ".join(
                    str(spec.get("name") or (spec.get("function") or {}).get("name"))
                    for spec in request.tools
                )
                + "\n"
            )
        available = max(0, MAX_EXCERPT_UTF8_BYTES - len(prefix.encode("utf-8")))
        excerpt = prefix + context.encode("utf-8")[:available].decode("utf-8", errors="ignore")
        with PermissionStore(self.database) as permissions:
            approval = request_transmission_approval(
                permissions, project_id=self.project_id, adapter_id=self.adapter_id,
                provider=self.provider, model=self.model, excerpt=excerpt,
                system_prompt=self.system_prompt, max_output_tokens=self.max_output_tokens,
                destination=self.destination, max_uses=1,
                requested_by="desktop-session-policy",
            )
            approval = permissions.decide(
                approval.id, approve=True, decided_by="desktop-session-policy",
                reason="Standing desktop Chat authorization; bounded collaboration context",
            )
        scoped = PrivateHybridAdapter(
            self.delegate, project_id=self.project_id, database=self.database,
            excerpt=excerpt, approval_ids=(approval.id,), system_prompt=self.system_prompt,
            max_output_tokens=self.max_output_tokens,
        )
        # The per-call scope above records the transmitted catalog by name, so
        # this standing call may carry it; a private session still may not.
        scoped.preserve_tools = self.preserve_tools
        return scoped

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return await self._scoped_call(request).generate(request)

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        async for chunk in self._scoped_call(request).stream(request):
            yield chunk


def probe_cloud_tool_capability(
    delegate: ModelAdapter,
    *,
    project_id: str,
    database: str | Path,
    system_prompt: str = DEFAULT_PRIVATE_HYBRID_SYSTEM,
    max_output_tokens: int = 64,
    timeout_seconds: float = 30.0,
) -> Any | None:
    """Send ONE audited tool-capability probe to a cloud provider.

    The provider's own answer decides the verdict (agent A's adapter records
    it); this wrapper exists so the probe is not an unlogged transmission:

    * the exact scope is approved under the standing desktop policy before the
      call, exactly like any other desktop-session transmission;
    * the call is written to the content-free transmission ledger with its
      scope digest, token usage and outcome;
    * the probe prompt carries no project or user content - it is a fixed
      capability handshake for a tool with an empty argument schema.

    Returns the provider's record (or ``None`` when the adapter cannot probe).
    """

    import asyncio

    probe = getattr(delegate, "probe_tool_capability", None)
    if probe is None:
        return None
    excerpt = (
        "# Bounded tool-capability probe\n"
        "Call the amca_tool_capability_probe tool. No project or user content is "
        "transmitted by this request."
    )
    destination = transmission_destination(delegate)
    scope = build_transmission_scope(
        adapter_id=delegate.adapter_id,
        provider=delegate.provider,
        model=delegate.model,
        excerpt=excerpt,
        system_prompt=system_prompt,
        destination=destination,
        max_output_tokens=max_output_tokens,
    )
    with PermissionStore(database) as permissions:
        approval = request_transmission_approval(
            permissions,
            project_id=project_id,
            adapter_id=delegate.adapter_id,
            provider=delegate.provider,
            model=delegate.model,
            excerpt=excerpt,
            system_prompt=system_prompt,
            max_output_tokens=max_output_tokens,
            destination=destination,
            max_uses=1,
            requested_by="desktop-session-policy",
        )
        approval = permissions.decide(
            approval.id,
            approve=True,
            decided_by="desktop-session-policy",
            reason=(
                "Standing desktop Chat authorization; bounded tool-capability "
                "probe (no project content)"
            ),
        )
    transmissions = TransmissionStore(database)
    record = transmissions.start(project_id=project_id, approval=approval, scope=scope)
    try:
        result = asyncio.run(probe(timeout_seconds=timeout_seconds))
    except BaseException as error:
        transmissions.finish(record.id, error_type=type(error).__name__)
        transmissions.close()
        return None
    transmissions.finish(
        record.id,
        response_text=(
            f"tool_capability supported={getattr(result, 'supported', None)}"
            if result is not None
            else "tool_capability inconclusive"
        ),
    )
    transmissions.close()
    return result


__all__ = [
    "DEFAULT_PRIVATE_HYBRID_SYSTEM",
    "probe_cloud_tool_capability",
    "MAX_EXCERPT_UTF8_BYTES",
    "PRIVATE_HYBRID_TEMPLATE_VERSION",
    "PrivateHybridAdapter",
    "TransmissionRecord",
    "TransmissionStore",
    "build_transmission_scope",
    "request_transmission_approval",
    "transmission_destination",
]


def transmission_destination(adapter: ModelAdapter) -> str:
    """Return the non-secret endpoint identity bound into an exact approval."""

    metadata = adapter.provider_metadata()
    value = metadata.get("base_url")
    return str(value) if isinstance(value, str) and value.strip() else adapter.provider
