"""AMCA Studio v1.0.0 Manual Model Selection to Workflow Binding.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MANUAL-MODEL-SELECTION-TO-WORKFLOW-BINDING-01

Responsibilities:
1. Workflow Model Binding Record:
   - Binds ManualModelSelectionRecord to a workflow/session context using explicit selection IDs
     and project/workflow references.
   - Preserves: selected model(s), operator rationale, reviewed evidence references, locality,
     and provider-approval state in the workflow trace.
2. Manual Selection State:
   - Distinguishes MANUAL_OPERATOR_SELECTION from ROUTER_SELECTED and UNRECORDED states.
   - Unavailable manually selected models produce BLOCKED_UNAVAILABLE state requiring
     explicit user-approved re-selection — NO silent substitution.
3. Provider Approval Gate:
   - Provider models retain full provider-approval gate context; local models remain local-first.
4. Cross-Trace Navigation:
   - Produces a WorkflowCrossTraceNavigator linking:
     Model Selection → Workflow → Runtime Session → Evidence / Artifacts.

RESTRICTIONS:
   - MUST NOT modify Model Router algorithms, Agent Fabric, Decision Engine,
     Adaptive Coordination, Execution Planning, or Telemetry.
   - Only: binding records, workflow trace augmentation, state enumeration,
     unavailable-model handling, documentation, validation tests.
"""

from __future__ import annotations

import datetime
import hashlib
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from amca.model_selection_workspace import ManualModelSelectionRecord, ModelSelectionManager


# ---------------------------------------------------------------------------
# Model Selection Binding State
# ---------------------------------------------------------------------------

class WorkflowModelBindingState(str, Enum):
    """Distinguishes how a model arrived in a workflow trace."""
    MANUAL_OPERATOR_SELECTION = "MANUAL_OPERATOR_SELECTION"
    ROUTER_SELECTED = "ROUTER_SELECTED"
    UNRECORDED = "UNRECORDED"
    BLOCKED_UNAVAILABLE = "BLOCKED_UNAVAILABLE"  # operator selection blocked; explicit re-selection required
    PENDING_PROVIDER_APPROVAL = "PENDING_PROVIDER_APPROVAL"


# ---------------------------------------------------------------------------
# Workflow Model Binding Record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkflowModelBindingRecord:
    """Binds a ManualModelSelectionRecord to a specific workflow and session context.

    Captures exactly what the operator selected and why, so the trace is
    complete and verifiable without modifying any routing logic.
    """

    binding_id: str
    workflow_id: str
    session_id: str
    selection_id: str                        # references ManualModelSelectionRecord.selection_id
    model_id: str
    model_locality: str                      # "local" | "cloud"
    provider_approval_state: Optional[str]   # only relevant for provider/cloud models
    operator_rationale_notes: str
    evidence_references_reviewed: List[str]
    binding_state: WorkflowModelBindingState
    timestamp: str

    def is_blocked(self) -> bool:
        return self.binding_state == WorkflowModelBindingState.BLOCKED_UNAVAILABLE

    def is_manual(self) -> bool:
        return self.binding_state == WorkflowModelBindingState.MANUAL_OPERATOR_SELECTION

    def requires_provider_gate(self) -> bool:
        return self.binding_state == WorkflowModelBindingState.PENDING_PROVIDER_APPROVAL


# ---------------------------------------------------------------------------
# Unavailable Model Block Record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnavailableModelBlockRecord:
    """Recorded when a manually selected model is unavailable.

    No silent substitution is performed. Operator must explicitly approve
    an alternative or wait for the original model to become available.
    """

    block_id: str
    workflow_id: str
    session_id: str
    selection_id: str
    model_id: str
    reason: str                             # e.g. "Model file not found", "Provider unreachable"
    requires_explicit_user_action: bool = True
    silent_fallback_performed: bool = False  # ALWAYS False — invariant
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Cross-Trace Navigation Record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkflowCrossTraceNavigator:
    """Navigation pointers linking Model Selection → Workflow → Runtime Session → Evidence.

    Enables operators to trace exactly which selection, rationale, and evidence
    contributed to a given workflow execution without any implicit inference.
    """

    navigator_id: str
    selection_id: str       # → ManualModelSelectionRecord
    workflow_id: str        # → WorkflowModelBindingRecord
    session_id: str         # → Runtime session
    evidence_refs: List[str]  # → BenchmarkEvidenceRegistry records reviewed
    artifact_refs: List[str]  # → Produced artifacts


# ---------------------------------------------------------------------------
# Workflow Binding Manager
# ---------------------------------------------------------------------------

class WorkflowModelBindingManager:
    """Manages bindings between ManualModelSelectionRecords and workflow/session contexts."""

    def __init__(self) -> None:
        self._bindings: Dict[str, WorkflowModelBindingRecord] = {}
        self._blocks: Dict[str, UnavailableModelBlockRecord] = {}
        self._navigators: Dict[str, WorkflowCrossTraceNavigator] = {}

    # ------------------------------------------------------------------
    # Binding creation
    # ------------------------------------------------------------------

    def bind_selection_to_workflow(
        self,
        selection: ManualModelSelectionRecord,
        workflow_id: str,
        session_id: str,
        model_locality: str,
        model_availability: str,  # "available" | "unavailable" | "pending_provider_approval"
        provider_approval_state: Optional[str] = None,
    ) -> WorkflowModelBindingRecord:
        """Bind a ManualModelSelectionRecord to a workflow context.

        Determines binding state based on availability without modifying
        any router or execution engine logic.
        """
        if model_availability == "unavailable":
            # Unavailable: produce BLOCKED_UNAVAILABLE binding — no silent substitution
            state = WorkflowModelBindingState.BLOCKED_UNAVAILABLE
            self._record_unavailability_block(
                workflow_id=workflow_id,
                session_id=session_id,
                selection=selection,
                reason="Manually selected model is not currently available",
            )
        elif model_locality == "cloud" and provider_approval_state not in (None, "approved"):
            state = WorkflowModelBindingState.PENDING_PROVIDER_APPROVAL
        else:
            state = WorkflowModelBindingState.MANUAL_OPERATOR_SELECTION

        seed = f"{selection.selection_id}:{workflow_id}:{session_id}"
        bid = f"bind-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:10]}"

        rec = WorkflowModelBindingRecord(
            binding_id=bid,
            workflow_id=workflow_id,
            session_id=session_id,
            selection_id=selection.selection_id,
            model_id=selection.model_id,
            model_locality=model_locality,
            provider_approval_state=provider_approval_state,
            operator_rationale_notes=selection.operator_rationale_notes,
            evidence_references_reviewed=list(selection.evidence_references_reviewed),
            binding_state=state,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
        self._bindings[bid] = rec
        return rec

    def _record_unavailability_block(
        self,
        workflow_id: str,
        session_id: str,
        selection: ManualModelSelectionRecord,
        reason: str,
    ) -> UnavailableModelBlockRecord:
        seed = f"block:{selection.selection_id}:{workflow_id}:{session_id}"
        block_id = f"blk-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:10]}"
        blk = UnavailableModelBlockRecord(
            block_id=block_id,
            workflow_id=workflow_id,
            session_id=session_id,
            selection_id=selection.selection_id,
            model_id=selection.model_id,
            reason=reason,
            requires_explicit_user_action=True,
            silent_fallback_performed=False,
        )
        self._blocks[block_id] = blk
        return blk

    # ------------------------------------------------------------------
    # Cross-trace navigator
    # ------------------------------------------------------------------

    def build_cross_trace_navigator(
        self,
        binding: WorkflowModelBindingRecord,
        artifact_refs: Optional[List[str]] = None,
    ) -> WorkflowCrossTraceNavigator:
        seed = f"nav:{binding.binding_id}"
        nid = f"nav-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:10]}"
        nav = WorkflowCrossTraceNavigator(
            navigator_id=nid,
            selection_id=binding.selection_id,
            workflow_id=binding.workflow_id,
            session_id=binding.session_id,
            evidence_refs=list(binding.evidence_references_reviewed),
            artifact_refs=list(artifact_refs or []),
        )
        self._navigators[nid] = nav
        return nav

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_binding(self, binding_id: str) -> Optional[WorkflowModelBindingRecord]:
        return self._bindings.get(binding_id)

    def list_bindings_for_workflow(self, workflow_id: str) -> List[WorkflowModelBindingRecord]:
        return [b for b in self._bindings.values() if b.workflow_id == workflow_id]

    def list_blocks_for_workflow(self, workflow_id: str) -> List[UnavailableModelBlockRecord]:
        return [b for b in self._blocks.values() if b.workflow_id == workflow_id]

    def get_navigator(self, navigator_id: str) -> Optional[WorkflowCrossTraceNavigator]:
        return self._navigators.get(navigator_id)

    def audit_no_silent_fallback(self) -> bool:
        """Invariant: NO block record may have silent_fallback_performed=True."""
        return all(not b.silent_fallback_performed for b in self._blocks.values())
