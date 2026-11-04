"""AMCA Studio v0.8.0 Governed Knowledge Context Service (AG-AMCA-STUDIO-V0.8.0).

Provides explicit, permission-checked, and auditable knowledge context retrieval
for research and document workflows.
Strictly prohibits automatic or hidden background context injection.
100% offline, zero cloud dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Any, Sequence
from uuid import uuid4

from amca.hybrid_retrieval import HybridRetrievalEngine, RetrievalMode
from amca.v080_context_integration_design import KnowledgeContextItem


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


ALLOWED_ROLE_ACTIONS: dict[str, set[str]] = {
    "researcher": {"knowledge.search", "knowledge.fetch_excerpt"},
    "coder": {"knowledge.search", "knowledge.fetch_excerpt"},
    "reviewer": {"knowledge.search", "knowledge.verify_provenance"},
    "admin": {"knowledge.search", "knowledge.fetch_excerpt", "knowledge.verify_provenance"},
}


@dataclass(frozen=True, slots=True)
class KnowledgeAuditEvent:
    event_id: str
    workspace_id: str
    requester_role: str
    action: str
    query_intent: str
    retrieved_artifact_ids: tuple[str, ...]
    permission_decision: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "workspace_id": self.workspace_id,
            "requester_role": self.requester_role,
            "action": self.action,
            "query_intent": self.query_intent,
            "retrieved_artifact_ids": list(self.retrieved_artifact_ids),
            "permission_decision": self.permission_decision,
            "timestamp": self.timestamp,
        }


class KnowledgeAuditStore:
    """Local SQLite audit ledger recording knowledge context access without storing sensitive text."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_audit_events (
                    event_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    requester_role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    query_intent TEXT NOT NULL,
                    retrieved_ids_json TEXT NOT NULL,
                    permission_decision TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_ws_time 
                ON knowledge_audit_events (workspace_id, timestamp);
                """
            )

    def record_event(self, event: KnowledgeAuditEvent) -> None:
        import json
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT INTO knowledge_audit_events 
                (event_id, workspace_id, requester_role, action, query_intent, retrieved_ids_json, permission_decision, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.workspace_id,
                    event.requester_role,
                    event.action,
                    event.query_intent,
                    json.dumps(list(event.retrieved_artifact_ids)),
                    event.permission_decision,
                    event.timestamp,
                ),
            )

    def list_events(self, *, workspace_id: str) -> list[KnowledgeAuditEvent]:
        import json
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT event_id, workspace_id, requester_role, action, query_intent, retrieved_ids_json, permission_decision, timestamp
                FROM knowledge_audit_events
                WHERE workspace_id = ?
                ORDER BY timestamp DESC
                """,
                (str(workspace_id).strip(),),
            ).fetchall()
            return [
                KnowledgeAuditEvent(
                    event_id=str(r["event_id"]),
                    workspace_id=str(r["workspace_id"]),
                    requester_role=str(r["requester_role"]),
                    action=str(r["action"]),
                    query_intent=str(r["query_intent"]),
                    retrieved_artifact_ids=tuple(json.loads(r["retrieved_ids_json"])),
                    permission_decision=str(r["permission_decision"]),
                    timestamp=str(r["timestamp"]),
                )
                for r in rows
            ]


@dataclass(frozen=True, slots=True)
class KnowledgeContextPackage:
    package_id: str
    workspace_id: str
    requester_role: str
    items: tuple[KnowledgeContextItem, ...]
    audit_event_id: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "workspace_id": self.workspace_id,
            "requester_role": self.requester_role,
            "items": [i.to_dict() for i in self.items],
            "audit_event_id": self.audit_event_id,
            "created_at": self.created_at,
        }


class KnowledgeContextService:
    """Governed knowledge context retrieval service with permission validation and audit logging."""

    def __init__(
        self,
        *,
        retrieval_engine: HybridRetrievalEngine,
        audit_store: KnowledgeAuditStore,
    ) -> None:
        self.engine = retrieval_engine
        self.audit_store = audit_store

    def execute_explicit_knowledge_search(
        self,
        *,
        workspace_id: str,
        requester_role: str,
        query: str,
        mode: RetrievalMode = "hybrid_rrf",
        max_items: int = 5,
        intent: str = "research_context",
    ) -> KnowledgeContextPackage:
        """Explicit knowledge search tool interface with role validation and audit logging."""
        norm_role = str(requester_role).strip().lower()
        norm_ws = str(workspace_id).strip()
        event_id = f"aud-{uuid4().hex[:16]}"
        now_ts = _now()

        # 1. Permission Validation
        allowed_actions = ALLOWED_ROLE_ACTIONS.get(norm_role, set())
        if "knowledge.search" not in allowed_actions:
            denied_event = KnowledgeAuditEvent(
                event_id=event_id,
                workspace_id=norm_ws,
                requester_role=norm_role,
                action="knowledge.search",
                query_intent=str(intent).strip(),
                retrieved_artifact_ids=(),
                permission_decision="DENIED",
                timestamp=now_ts,
            )
            self.audit_store.record_event(denied_event)
            return KnowledgeContextPackage(
                package_id=f"ctx-pkg-{uuid4().hex[:16]}",
                workspace_id=norm_ws,
                requester_role=norm_role,
                items=(),
                audit_event_id=event_id,
                created_at=now_ts,
            )

        # 2. Execute retrieval via engine
        raw_results, diag = self.engine.retrieve(
            workspace_id=norm_ws,
            query=query,
            mode=mode,
            limit=max_items,
        )

        # 3. Assemble structured context items with provenance attached
        items: list[KnowledgeContextItem] = []
        retrieved_ids: list[str] = []
        for r in raw_results:
            retrieved_ids.append(r.artifact_id)
            items.append(
                KnowledgeContextItem(
                    artifact_id=r.artifact_id,
                    workspace_id=r.workspace_id,
                    title=r.title,
                    excerpt=r.snippet,
                    relevance_score=r.rrf_score,
                    provenance_reference=r.provenance_reference,
                    permission_verified=True,
                )
            )

        # 4. Record successful audit event (without logging raw query text or private payloads)
        audit_event = KnowledgeAuditEvent(
            event_id=event_id,
            workspace_id=norm_ws,
            requester_role=norm_role,
            action="knowledge.search",
            query_intent=str(intent).strip(),
            retrieved_artifact_ids=tuple(retrieved_ids),
            permission_decision="ALLOWED",
            timestamp=now_ts,
        )
        self.audit_store.record_event(audit_event)

        return KnowledgeContextPackage(
            package_id=f"ctx-pkg-{uuid4().hex[:16]}",
            workspace_id=norm_ws,
            requester_role=norm_role,
            items=tuple(items),
            audit_event_id=event_id,
            created_at=now_ts,
        )

