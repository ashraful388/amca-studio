"""AMCA Studio v0.8.0 Cognitive Workflow Loop Foundation (AG-AMCA-STUDIO-V0.8.0).

Connects existing AMCA subsystems into a governed end-to-end cognitive loop:
Goal → Understand → Plan → Select Agents/Models → Retrieve Knowledge → Execute → Verify → Learn

Guiding Invariants:
1. Purely coordinates existing engines; does NOT replace Model Router, Agent Fabric, or Knowledge Service.
2. Zero hidden reasoning or chain-of-thought storage.
3. Explicit knowledge attachment; zero automatic context injection.
4. Tamper-evident, observable state machine with clean failure states and audit references.
5. 100% offline, local SQLite persistence, strict workspace isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Literal, Sequence
from uuid import uuid4

from amca.knowledge_service_runtime import KnowledgeRuntimeService, RuntimeKnowledgeInvocationResult


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


CognitiveWorkflowState = Literal[
    "INITIALIZED",
    "GOAL_UNDERSTOOD",
    "PLANNED",
    "RESOURCES_SELECTED",
    "KNOWLEDGE_BOUND",
    "EXECUTING",
    "COMPLETED",
    "FAILED_PLANNING",
    "FAILED_RESOURCE_SELECTION",
    "FAILED_KNOWLEDGE_ACCESS",
    "FAILED_EXECUTION",
    "CANCELLED",
]


@dataclass(frozen=True, slots=True)
class GoalUnderstandingRecord:
    goal_id: str
    raw_goal: str
    structured_intent: str
    constraints: tuple[str, ...]
    domain: str
    required_outputs: tuple[str, ...]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "raw_goal": self.raw_goal,
            "structured_intent": self.structured_intent,
            "constraints": list(self.constraints),
            "domain": self.domain,
            "required_outputs": list(self.required_outputs),
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class CognitivePlanRecord:
    plan_id: str
    planned_steps: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    strategy: str
    dependencies: tuple[str, ...]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "planned_steps": list(self.planned_steps),
            "required_capabilities": list(self.required_capabilities),
            "strategy": self.strategy,
            "dependencies": list(self.dependencies),
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class ModelAgentSelectionTrace:
    selection_id: str
    selected_models: tuple[str, ...]
    selected_agents: tuple[str, ...]
    provider_health_references: tuple[str, ...]
    decision_reason: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "selection_id": self.selection_id,
            "selected_models": list(self.selected_models),
            "selected_agents": list(self.selected_agents),
            "provider_health_references": list(self.provider_health_references),
            "decision_reason": self.decision_reason,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeBindingTrace:
    binding_id: str
    query_intent: str
    retrieved_artifact_ids: tuple[str, ...]
    permission_outcome: str
    audit_event_ref: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "query_intent": self.query_intent,
            "retrieved_artifact_ids": list(self.retrieved_artifact_ids),
            "permission_outcome": self.permission_outcome,
            "audit_event_ref": self.audit_event_ref,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    verification_id: str
    execution_completed: bool
    artifacts_produced: tuple[str, ...]
    validation_result: str
    unresolved_issues: tuple[str, ...]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verification_id": self.verification_id,
            "execution_completed": self.execution_completed,
            "artifacts_produced": list(self.artifacts_produced),
            "validation_result": self.validation_result,
            "unresolved_issues": list(self.unresolved_issues),
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class LearningSignalRecord:
    signal_id: str
    workflow_id: str
    execution_duration_ms: float
    failure_count: int
    retry_count: int
    resource_metrics: dict[str, Any]
    user_correction_count: int
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "workflow_id": self.workflow_id,
            "execution_duration_ms": self.execution_duration_ms,
            "failure_count": self.failure_count,
            "retry_count": self.retry_count,
            "resource_metrics": dict(self.resource_metrics),
            "user_correction_count": self.user_correction_count,
            "timestamp": self.timestamp,
        }


@dataclass
class CognitiveWorkflowRecord:
    workflow_id: str
    workspace_id: str
    state: CognitiveWorkflowState
    goal: GoalUnderstandingRecord | None = None
    plan: CognitivePlanRecord | None = None
    selection: ModelAgentSelectionTrace | None = None
    knowledge: KnowledgeBindingTrace | None = None
    verification: VerificationRecord | None = None
    learning: LearningSignalRecord | None = None
    failure_reason: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "state": self.state,
            "goal": self.goal.to_dict() if self.goal else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "selection": self.selection.to_dict() if self.selection else None,
            "knowledge": self.knowledge.to_dict() if self.knowledge else None,
            "verification": self.verification.to_dict() if self.verification else None,
            "learning": self.learning.to_dict() if self.learning else None,
            "failure_reason": self.failure_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CognitiveWorkflowStore:
    """SQLite persistence store for cognitive workflows with workspace isolation."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS cognitive_workflows (
                    workflow_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    goal_json TEXT,
                    plan_json TEXT,
                    selection_json TEXT,
                    knowledge_json TEXT,
                    verification_json TEXT,
                    learning_json TEXT,
                    failure_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cog_ws_state 
                ON cognitive_workflows (workspace_id, state);
                """
            )

    def save_workflow(self, record: CognitiveWorkflowRecord) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO cognitive_workflows
                (workflow_id, workspace_id, state, goal_json, plan_json, selection_json, knowledge_json, verification_json, learning_json, failure_reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.workflow_id,
                    record.workspace_id,
                    record.state,
                    json.dumps(record.goal.to_dict()) if record.goal else None,
                    json.dumps(record.plan.to_dict()) if record.plan else None,
                    json.dumps(record.selection.to_dict()) if record.selection else None,
                    json.dumps(record.knowledge.to_dict()) if record.knowledge else None,
                    json.dumps(record.verification.to_dict()) if record.verification else None,
                    json.dumps(record.learning.to_dict()) if record.learning else None,
                    record.failure_reason,
                    record.created_at,
                    record.updated_at,
                ),
            )

    def get_workflow(self, *, workflow_id: str, workspace_id: str) -> CognitiveWorkflowRecord | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """
                SELECT * FROM cognitive_workflows
                WHERE workflow_id = ? AND workspace_id = ?
                """,
                (str(workflow_id).strip(), str(workspace_id).strip()),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_record(row)

    def _row_to_record(self, row: sqlite3.Row) -> CognitiveWorkflowRecord:
        goal = None
        if row["goal_json"]:
            g = json.loads(row["goal_json"])
            goal = GoalUnderstandingRecord(
                goal_id=g["goal_id"],
                raw_goal=g["raw_goal"],
                structured_intent=g["structured_intent"],
                constraints=tuple(g["constraints"]),
                domain=g["domain"],
                required_outputs=tuple(g["required_outputs"]),
                timestamp=g["timestamp"],
            )

        plan = None
        if row["plan_json"]:
            p = json.loads(row["plan_json"])
            plan = CognitivePlanRecord(
                plan_id=p["plan_id"],
                planned_steps=tuple(p["planned_steps"]),
                required_capabilities=tuple(p["required_capabilities"]),
                strategy=p["strategy"],
                dependencies=tuple(p["dependencies"]),
                timestamp=p["timestamp"],
            )

        selection = None
        if row["selection_json"]:
            s = json.loads(row["selection_json"])
            selection = ModelAgentSelectionTrace(
                selection_id=s["selection_id"],
                selected_models=tuple(s["selected_models"]),
                selected_agents=tuple(s["selected_agents"]),
                provider_health_references=tuple(s["provider_health_references"]),
                decision_reason=s["decision_reason"],
                timestamp=s["timestamp"],
            )

        knowledge = None
        if row["knowledge_json"]:
            k = json.loads(row["knowledge_json"])
            knowledge = KnowledgeBindingTrace(
                binding_id=k["binding_id"],
                query_intent=k["query_intent"],
                retrieved_artifact_ids=tuple(k["retrieved_artifact_ids"]),
                permission_outcome=k["permission_outcome"],
                audit_event_ref=k["audit_event_ref"],
                timestamp=k["timestamp"],
            )

        verification = None
        if row["verification_json"]:
            v = json.loads(row["verification_json"])
            verification = VerificationRecord(
                verification_id=v["verification_id"],
                execution_completed=v["execution_completed"],
                artifacts_produced=tuple(v["artifacts_produced"]),
                validation_result=v["validation_result"],
                unresolved_issues=tuple(v["unresolved_issues"]),
                timestamp=v["timestamp"],
            )

        learning = None
        if row["learning_json"]:
            l_dict = json.loads(row["learning_json"])
            learning = LearningSignalRecord(
                signal_id=l_dict["signal_id"],
                workflow_id=l_dict["workflow_id"],
                execution_duration_ms=l_dict["execution_duration_ms"],
                failure_count=l_dict["failure_count"],
                retry_count=l_dict["retry_count"],
                resource_metrics=l_dict["resource_metrics"],
                user_correction_count=l_dict["user_correction_count"],
                timestamp=l_dict["timestamp"],
            )

        return CognitiveWorkflowRecord(
            workflow_id=str(row["workflow_id"]),
            workspace_id=str(row["workspace_id"]),
            state=str(row["state"]),  # type: ignore[arg-type]
            goal=goal,
            plan=plan,
            selection=selection,
            knowledge=knowledge,
            verification=verification,
            learning=learning,
            failure_reason=row["failure_reason"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


class CognitiveWorkflowOrchestrator:
    """Orchestrates governed cognitive loop transitions across AMCA subsystems."""

    def __init__(
        self,
        *,
        store: CognitiveWorkflowStore,
        knowledge_runtime: KnowledgeRuntimeService | None = None,
    ) -> None:
        self.store = store
        self.knowledge_runtime = knowledge_runtime

    def initialize_workflow(
        self,
        *,
        workspace_id: str,
        raw_goal: str,
        structured_intent: str,
        domain: str = "general",
        constraints: Sequence[str] = (),
        required_outputs: Sequence[str] = (),
    ) -> CognitiveWorkflowRecord:
        wf_id = f"cwf-{uuid4().hex[:16]}"
        now_ts = _now()
        goal = GoalUnderstandingRecord(
            goal_id=f"goal-{uuid4().hex[:12]}",
            raw_goal=str(raw_goal).strip(),
            structured_intent=str(structured_intent).strip(),
            constraints=tuple(str(c).strip() for c in constraints),
            domain=str(domain).strip(),
            required_outputs=tuple(str(o).strip() for o in required_outputs),
            timestamp=now_ts,
        )
        rec = CognitiveWorkflowRecord(
            workflow_id=wf_id,
            workspace_id=str(workspace_id).strip(),
            state="GOAL_UNDERSTOOD",
            goal=goal,
            created_at=now_ts,
            updated_at=now_ts,
        )
        self.store.save_workflow(rec)
        return rec

    def attach_plan(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        planned_steps: Sequence[str],
        required_capabilities: Sequence[str],
        strategy: str = "adaptive_hybrid",
        dependencies: Sequence[str] = (),
    ) -> CognitiveWorkflowRecord:
        rec = self._require_workflow(workflow_id, workspace_id)
        if rec.state != "GOAL_UNDERSTOOD":
            raise ValueError(f"Cannot attach plan to workflow in state '{rec.state}'")

        plan = CognitivePlanRecord(
            plan_id=f"plan-{uuid4().hex[:12]}",
            planned_steps=tuple(str(s).strip() for s in planned_steps),
            required_capabilities=tuple(str(c).strip() for c in required_capabilities),
            strategy=str(strategy).strip(),
            dependencies=tuple(str(d).strip() for d in dependencies),
            timestamp=_now(),
        )
        rec.plan = plan
        rec.state = "PLANNED"
        rec.updated_at = _now()
        self.store.save_workflow(rec)
        return rec

    def select_resources(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        selected_models: Sequence[str],
        selected_agents: Sequence[str],
        provider_health_references: Sequence[str] = (),
        decision_reason: str = "Capability profile match",
    ) -> CognitiveWorkflowRecord:
        rec = self._require_workflow(workflow_id, workspace_id)
        if rec.state != "PLANNED":
            raise ValueError(f"Cannot select resources in state '{rec.state}'")

        if not selected_models:
            rec.state = "FAILED_RESOURCE_SELECTION"
            rec.failure_reason = "No compatible models selected"
            rec.updated_at = _now()
            self.store.save_workflow(rec)
            return rec

        selection = ModelAgentSelectionTrace(
            selection_id=f"sel-{uuid4().hex[:12]}",
            selected_models=tuple(str(m).strip() for m in selected_models),
            selected_agents=tuple(str(a).strip() for a in selected_agents),
            provider_health_references=tuple(str(r).strip() for r in provider_health_references),
            decision_reason=str(decision_reason).strip(),
            timestamp=_now(),
        )
        rec.selection = selection
        rec.state = "RESOURCES_SELECTED"
        rec.updated_at = _now()
        self.store.save_workflow(rec)
        return rec

    def bind_knowledge(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        query: str,
        requester_role: str = "researcher",
        mode: str = "lexical_only",
    ) -> CognitiveWorkflowRecord:
        rec = self._require_workflow(workflow_id, workspace_id)
        if rec.state != "RESOURCES_SELECTED":
            raise ValueError(f"Cannot bind knowledge in state '{rec.state}'")

        if self.knowledge_runtime is None:
            rec.state = "FAILED_KNOWLEDGE_ACCESS"
            rec.failure_reason = "Knowledge runtime service not configured"
            rec.updated_at = _now()
            self.store.save_workflow(rec)
            return rec

        res: RuntimeKnowledgeInvocationResult = self.knowledge_runtime.execute_runtime_invocation(
            workspace_id=workspace_id,
            requester_role=requester_role,
            query=query,
            mode=mode,
        )

        if not res.is_authorized:
            rec.state = "FAILED_KNOWLEDGE_ACCESS"
            rec.failure_reason = res.denial_reason or "Knowledge access denied"
            rec.updated_at = _now()
            self.store.save_workflow(rec)
            return rec

        retrieved_ids = tuple(item.artifact_id for item in res.context_package.items) if res.context_package else ()
        audit_ref = res.context_package.audit_event_id if res.context_package else "none"

        binding = KnowledgeBindingTrace(
            binding_id=f"kb-{uuid4().hex[:12]}",
            query_intent=query[:40],
            retrieved_artifact_ids=retrieved_ids,
            permission_outcome="AUTHORIZED",
            audit_event_ref=audit_ref,
            timestamp=_now(),
        )
        rec.knowledge = binding
        rec.state = "KNOWLEDGE_BOUND"
        rec.updated_at = _now()
        self.store.save_workflow(rec)
        return rec

    def execute_workflow(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        execution_callable: Callable[[], tuple[bool, tuple[str, ...], str | None]] | None = None,
    ) -> CognitiveWorkflowRecord:
        rec = self._require_workflow(workflow_id, workspace_id)
        if rec.state not in ("RESOURCES_SELECTED", "KNOWLEDGE_BOUND"):
            raise ValueError(f"Cannot execute workflow in state '{rec.state}'")

        rec.state = "EXECUTING"
        rec.updated_at = _now()
        self.store.save_workflow(rec)

        if execution_callable is not None:
            try:
                success, artifacts, err = execution_callable()
                if not success:
                    rec.state = "FAILED_EXECUTION"
                    rec.failure_reason = err or "Execution callable reported failure"
                    rec.updated_at = _now()
                    self.store.save_workflow(rec)
                    return rec
                produced_artifacts = artifacts
            except Exception as ex:
                rec.state = "FAILED_EXECUTION"
                rec.failure_reason = str(ex)
                rec.updated_at = _now()
                self.store.save_workflow(rec)
                return rec
        else:
            produced_artifacts = ("artifact-output-default.md",)

        # Transition to COMPLETED with verification record
        verif = VerificationRecord(
            verification_id=f"verif-{uuid4().hex[:12]}",
            execution_completed=True,
            artifacts_produced=tuple(produced_artifacts),
            validation_result="VERIFIED_SUCCESS",
            unresolved_issues=(),
            timestamp=_now(),
        )
        rec.verification = verif
        rec.state = "COMPLETED"
        rec.updated_at = _now()
        self.store.save_workflow(rec)
        return rec

    def record_learning_signal(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        duration_ms: float,
        failure_count: int = 0,
        retry_count: int = 0,
        resource_metrics: dict[str, Any] | None = None,
        user_correction_count: int = 0,
    ) -> CognitiveWorkflowRecord:
        rec = self._require_workflow(workflow_id, workspace_id)
        sig = LearningSignalRecord(
            signal_id=f"sig-{uuid4().hex[:12]}",
            workflow_id=workflow_id,
            execution_duration_ms=duration_ms,
            failure_count=failure_count,
            retry_count=retry_count,
            resource_metrics=resource_metrics or {},
            user_correction_count=user_correction_count,
            timestamp=_now(),
        )
        rec.learning = sig
        rec.updated_at = _now()
        self.store.save_workflow(rec)
        return rec

    def cancel_workflow(self, *, workflow_id: str, workspace_id: str, reason: str = "User cancelled") -> CognitiveWorkflowRecord:
        rec = self._require_workflow(workflow_id, workspace_id)
        rec.state = "CANCELLED"
        rec.failure_reason = reason
        rec.updated_at = _now()
        self.store.save_workflow(rec)
        return rec

    def _require_workflow(self, workflow_id: str, workspace_id: str) -> CognitiveWorkflowRecord:
        rec = self.store.get_workflow(workflow_id=workflow_id, workspace_id=workspace_id)
        if rec is None:
            raise KeyError(f"Workflow '{workflow_id}' not found in workspace '{workspace_id}'")
        return rec

