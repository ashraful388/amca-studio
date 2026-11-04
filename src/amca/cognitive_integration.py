"""Comprehensive Cognitive Integration and Pipeline Validation (AG-AMCA-STUDIO-BETA-B10).

Synthesizes an end-to-end trace connecting all Beta cognitive pillars (Agent Fabric,
Persistent Memory, Model Orchestration, Collaboration Sessions, Workflow Optimization,
Workspace Governance, and User Preferences) into an auditable cognitive operating loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class CognitiveHealthReport:
    report_id: str
    project_id: str
    capability_coverage: float
    model_suitability_coverage: float
    collaboration_completeness: float
    memory_utilization_ratio: float
    optimization_availability: bool
    governance_compliance: bool
    evaluated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "project_id": self.project_id,
            "capability_coverage": self.capability_coverage,
            "model_suitability_coverage": self.model_suitability_coverage,
            "collaboration_completeness": self.collaboration_completeness,
            "memory_utilization_ratio": self.memory_utilization_ratio,
            "optimization_availability": self.optimization_availability,
            "governance_compliance": self.governance_compliance,
            "evaluated_at": self.evaluated_at,
        }


@dataclass(frozen=True, slots=True)
class CognitivePipelineTrace:
    trace_id: str
    project_id: str
    task_understanding_id: str
    agent_capability_ids: tuple[str, ...]
    orchestration_decision_ids: tuple[str, ...]
    collaboration_session_ids: tuple[str, ...]
    relevant_memory_ids: tuple[str, ...]
    workflow_analysis_id: str
    governance_audit_ids: tuple[str, ...]
    health_report: CognitiveHealthReport
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "project_id": self.project_id,
            "task_understanding_id": self.task_understanding_id,
            "agent_capability_ids": list(self.agent_capability_ids),
            "orchestration_decision_ids": list(self.orchestration_decision_ids),
            "collaboration_session_ids": list(self.collaboration_session_ids),
            "relevant_memory_ids": list(self.relevant_memory_ids),
            "workflow_analysis_id": self.workflow_analysis_id,
            "governance_audit_ids": list(self.governance_audit_ids),
            "health_report": self.health_report.to_dict(),
            "created_at": self.created_at,
        }


def validate_cognitive_pipeline_trace(
    project_id: str,
    *,
    task_understanding_id: str,
    agent_capability_ids: Sequence[str],
    orchestration_decision_ids: Sequence[str],
    collaboration_session_ids: Sequence[str],
    relevant_memory_ids: Sequence[str],
    workflow_analysis_id: str,
    governance_audit_ids: Sequence[str],
) -> CognitivePipelineTrace:
    """Builds and validates an end-to-end cognitive operating trace across all AMCA pillars."""
    cap_cov = 1.0 if agent_capability_ids else 0.0
    model_cov = 1.0 if orchestration_decision_ids else 0.0
    collab_comp = 1.0 if collaboration_session_ids else 0.5
    mem_util = 1.0 if relevant_memory_ids else 0.0
    opt_avail = bool(workflow_analysis_id)
    gov_comp = bool(governance_audit_ids)

    health = CognitiveHealthReport(
        report_id=f"rep-{uuid4().hex[:16]}",
        project_id=str(project_id).strip(),
        capability_coverage=cap_cov,
        model_suitability_coverage=model_cov,
        collaboration_completeness=collab_comp,
        memory_utilization_ratio=mem_util,
        optimization_availability=opt_avail,
        governance_compliance=gov_comp,
        evaluated_at=_now(),
    )

    return CognitivePipelineTrace(
        trace_id=f"trc-{uuid4().hex[:16]}",
        project_id=str(project_id).strip(),
        task_understanding_id=str(task_understanding_id).strip(),
        agent_capability_ids=tuple(str(a).strip() for a in agent_capability_ids if str(a).strip()),
        orchestration_decision_ids=tuple(str(o).strip() for o in orchestration_decision_ids if str(o).strip()),
        collaboration_session_ids=tuple(str(c).strip() for c in collaboration_session_ids if str(c).strip()),
        relevant_memory_ids=tuple(str(m).strip() for m in relevant_memory_ids if str(m).strip()),
        workflow_analysis_id=str(workflow_analysis_id).strip(),
        governance_audit_ids=tuple(str(g).strip() for g in governance_audit_ids if str(g).strip()),
        health_report=health,
        created_at=_now(),
    )

