"""Tests for AMCA Studio v1.0.0 Manual Model Selection to Workflow Binding.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MANUAL-MODEL-SELECTION-TO-WORKFLOW-BINDING-01
"""

import pytest

from amca.manual_model_selection_workflow_binding import (
    WorkflowModelBindingManager,
    WorkflowModelBindingState,
    WorkflowCrossTraceNavigator,
    UnavailableModelBlockRecord,
)
from amca.model_selection_workspace import ModelSelectionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_selection(project_ctx: str = "proj-alpha", model_id: str = "llama3-8b"):
    mgr = ModelSelectionManager()
    return mgr.record_manual_selection(
        model_id=model_id,
        project_context=project_ctx,
        evidence_references_reviewed=["eval-001", "eval-002"],
        operator_rationale_notes="Selected for low-latency local inference verified by HumanEval results.",
    )


# ---------------------------------------------------------------------------
# Test 1: Successful local model binding produces MANUAL_OPERATOR_SELECTION state
# ---------------------------------------------------------------------------

def test_v100_binding_local_available_is_manual_operator_selection():
    """Binding an available local model produces MANUAL_OPERATOR_SELECTION state."""
    sel = make_selection()
    mgr = WorkflowModelBindingManager()

    binding = mgr.bind_selection_to_workflow(
        selection=sel,
        workflow_id="wf-001",
        session_id="sess-001",
        model_locality="local",
        model_availability="available",
    )

    assert binding.binding_state == WorkflowModelBindingState.MANUAL_OPERATOR_SELECTION
    assert binding.is_manual()
    assert not binding.is_blocked()
    assert binding.model_id == "llama3-8b"
    assert binding.operator_rationale_notes == sel.operator_rationale_notes
    assert "eval-001" in binding.evidence_references_reviewed
    assert binding.selection_id == sel.selection_id


# ---------------------------------------------------------------------------
# Test 2: Unavailable model produces BLOCKED_UNAVAILABLE with no silent fallback
# ---------------------------------------------------------------------------

def test_v100_binding_unavailable_model_blocked_no_silent_fallback():
    """Unavailable manually selected model produces BLOCKED_UNAVAILABLE; no silent fallback."""
    sel = make_selection(model_id="codestral-7b")
    mgr = WorkflowModelBindingManager()

    binding = mgr.bind_selection_to_workflow(
        selection=sel,
        workflow_id="wf-002",
        session_id="sess-002",
        model_locality="local",
        model_availability="unavailable",
    )

    assert binding.binding_state == WorkflowModelBindingState.BLOCKED_UNAVAILABLE
    assert binding.is_blocked()

    blocks = mgr.list_blocks_for_workflow("wf-002")
    assert len(blocks) == 1
    blk = blocks[0]
    assert blk.model_id == "codestral-7b"
    assert blk.requires_explicit_user_action is True
    assert blk.silent_fallback_performed is False

    # Invariant audit
    assert mgr.audit_no_silent_fallback() is True


# ---------------------------------------------------------------------------
# Test 3: Cloud model without provider approval produces PENDING_PROVIDER_APPROVAL
# ---------------------------------------------------------------------------

def test_v100_binding_cloud_model_without_approval_is_pending():
    """Cloud model without approved provider state yields PENDING_PROVIDER_APPROVAL."""
    sel = make_selection(model_id="gpt4o-cloud", project_ctx="proj-cloud")
    mgr = WorkflowModelBindingManager()

    binding = mgr.bind_selection_to_workflow(
        selection=sel,
        workflow_id="wf-003",
        session_id="sess-003",
        model_locality="cloud",
        model_availability="available",
        provider_approval_state="pending",
    )

    assert binding.binding_state == WorkflowModelBindingState.PENDING_PROVIDER_APPROVAL
    assert binding.requires_provider_gate()
    assert binding.provider_approval_state == "pending"


# ---------------------------------------------------------------------------
# Test 4: Cross-trace navigator links selection → workflow → session → evidence
# ---------------------------------------------------------------------------

def test_v100_cross_trace_navigator_links_all_hops():
    """Cross-trace navigator correctly links selection, workflow, session, and evidence."""
    sel = make_selection()
    mgr = WorkflowModelBindingManager()

    binding = mgr.bind_selection_to_workflow(
        selection=sel,
        workflow_id="wf-004",
        session_id="sess-004",
        model_locality="local",
        model_availability="available",
    )

    artifact_refs = ["art-xyz-001", "art-xyz-002"]
    nav = mgr.build_cross_trace_navigator(binding=binding, artifact_refs=artifact_refs)

    assert nav.selection_id == sel.selection_id
    assert nav.workflow_id == "wf-004"
    assert nav.session_id == "sess-004"
    assert "eval-001" in nav.evidence_refs
    assert "eval-002" in nav.evidence_refs
    assert "art-xyz-001" in nav.artifact_refs
    assert "art-xyz-002" in nav.artifact_refs
    assert nav.navigator_id.startswith("nav-")


# ---------------------------------------------------------------------------
# Test 5: Workspace isolation — multiple workflows have independent bindings
# ---------------------------------------------------------------------------

def test_v100_workspace_isolation_bindings_are_independent():
    """Multiple workflows receive isolated binding records; no cross-contamination."""
    mgr = WorkflowModelBindingManager()
    sel_a = make_selection(project_ctx="proj-a", model_id="llama3-8b")
    sel_b = make_selection(project_ctx="proj-b", model_id="mistral-7b")

    bind_a = mgr.bind_selection_to_workflow(
        selection=sel_a, workflow_id="wf-a", session_id="sess-a",
        model_locality="local", model_availability="available",
    )
    bind_b = mgr.bind_selection_to_workflow(
        selection=sel_b, workflow_id="wf-b", session_id="sess-b",
        model_locality="local", model_availability="available",
    )

    bindings_a = mgr.list_bindings_for_workflow("wf-a")
    bindings_b = mgr.list_bindings_for_workflow("wf-b")

    assert len(bindings_a) == 1
    assert len(bindings_b) == 1
    assert bindings_a[0].model_id == "llama3-8b"
    assert bindings_b[0].model_id == "mistral-7b"
    # Cross contamination check
    assert bindings_a[0].binding_id != bindings_b[0].binding_id
    # Model Router unchanged — binding_state is MANUAL for both
    assert bindings_a[0].binding_state == WorkflowModelBindingState.MANUAL_OPERATOR_SELECTION
    assert bindings_b[0].binding_state == WorkflowModelBindingState.MANUAL_OPERATOR_SELECTION
