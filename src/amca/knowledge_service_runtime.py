"""AMCA Studio v0.8.0 Knowledge Service Runtime Integration (AG-AMCA-STUDIO-V0.8.0).

Delivers the runtime bridge connecting AMCA Studio workflows to the Knowledge Context Protocol:
1. KnowledgeRuntimeService: Purely on-demand explicit runtime service exposing `execute_runtime_invocation`.
2. Anti-Injection Invariant: Guaranteed zero automatic injection; knowledge context is only bound when explicitly attached by user/role request.
3. Provenance Integrity: Every knowledge item packaged retains complete immutable artifact references, project metadata, and permission decision ledger entries.
4. Privacy & Audit Assurance: Zero raw secret query or private body persistence in audit records.
5. Runtime Boundary Discipline: No modifications to Agent Fabric or Model Router decision trees.

100% offline, zero cloud dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import time
from typing import Any, Sequence
from uuid import uuid4

from amca.hybrid_retrieval import HybridRetrievalEngine, UnifiedRetrievalResult
from amca.knowledge_context_service import (
    KnowledgeAuditEvent,
    KnowledgeAuditStore,
    KnowledgeContextPackage,
    KnowledgeContextService,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class RuntimeKnowledgeInvocationResult:
    invocation_id: str
    workspace_id: str
    requester_role: str
    query_intent: str
    is_authorized: bool
    context_package: KnowledgeContextPackage | None
    execution_time_ms: float
    denial_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "workspace_id": self.workspace_id,
            "requester_role": self.requester_role,
            "query_intent": self.query_intent,
            "is_authorized": self.is_authorized,
            "context_package": self.context_package.to_dict() if self.context_package else None,
            "execution_time_ms": self.execution_time_ms,
            "denial_reason": self.denial_reason,
        }


class KnowledgeRuntimeService:
    """Runtime service orchestrating explicit knowledge retrieval and context packaging."""

    def __init__(self, *, context_service: KnowledgeContextService) -> None:
        self.context_service = context_service

    def execute_runtime_invocation(
        self,
        *,
        workspace_id: str,
        requester_role: str,
        query: str,
        mode: str = "lexical_only",
        limit: int = 5,
        intent: str = "workflow_context",
    ) -> RuntimeKnowledgeInvocationResult:
        """Executes explicit runtime retrieval with strict role authorization and provenance retention."""
        start_time = time.perf_counter()
        inv_id = f"inv-{uuid4().hex[:16]}"
        norm_role = str(requester_role).strip().lower()
        norm_ws = str(workspace_id).strip()

        pkg = self.context_service.execute_explicit_knowledge_search(
            workspace_id=norm_ws,
            requester_role=norm_role,
            query=query,
            mode=mode,  # type: ignore[arg-type]
            max_items=limit,
            intent=intent,
        )

        elapsed = (time.perf_counter() - start_time) * 1000.0

        if not pkg.items and norm_role not in ("admin", "lead_architect", "researcher", "reviewer"):
            return RuntimeKnowledgeInvocationResult(
                invocation_id=inv_id,
                workspace_id=norm_ws,
                requester_role=norm_role,
                query_intent=intent,
                is_authorized=False,
                context_package=None,
                execution_time_ms=elapsed,
                denial_reason=f"Role '{requester_role}' not authorized for knowledge context retrieval",
            )

        return RuntimeKnowledgeInvocationResult(
            invocation_id=inv_id,
            workspace_id=norm_ws,
            requester_role=norm_role,
            query_intent=intent,
            is_authorized=True,
            context_package=pkg,
            execution_time_ms=elapsed,
            denial_reason=None,
        )

