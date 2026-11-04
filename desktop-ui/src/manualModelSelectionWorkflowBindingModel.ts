// AMCA Studio v1.0.0 Manual Model Selection to Workflow Binding — Presentation Model.
//
// Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MANUAL-MODEL-SELECTION-TO-WORKFLOW-BINDING-01
//
// Responsibilities:
// 1. Typed representation of WorkflowModelBindingRecord and WorkflowModelBindingState.
// 2. State badge resolution: distinguishes MANUAL_OPERATOR_SELECTION from ROUTER_SELECTED
//    and UNRECORDED in the Workflow/Cognitive Workflow surface.
// 3. Blocked/unavailable model formatting: clearly surfaces BLOCKED_UNAVAILABLE state
//    requiring explicit operator re-selection — NO silent substitution labeling.
// 4. Cross-trace navigation links: Model Selection → Workflow → Session → Evidence.
// 5. Anti-fallback audit: verifies no binding record carries silent substitution.
//
// RESTRICTIONS:
//   - MUST NOT change Model Router algorithms, Agent Fabric, or Decision Engine.
//   - Only: presentation model, state formatting, trace navigation, UI display helpers.

export type WorkflowModelBindingState =
  | "MANUAL_OPERATOR_SELECTION"
  | "ROUTER_SELECTED"
  | "UNRECORDED"
  | "BLOCKED_UNAVAILABLE"
  | "PENDING_PROVIDER_APPROVAL";

export interface WorkflowModelBindingRecord {
  binding_id: string;
  workflow_id: string;
  session_id: string;
  selection_id: string;
  model_id: string;
  model_locality: string; // "local" | "cloud"
  provider_approval_state: string | null;
  operator_rationale_notes: string;
  evidence_references_reviewed: string[];
  binding_state: WorkflowModelBindingState;
  timestamp: string;
}

export interface UnavailableModelBlockRecord {
  block_id: string;
  workflow_id: string;
  session_id: string;
  selection_id: string;
  model_id: string;
  reason: string;
  requires_explicit_user_action: boolean;
  silent_fallback_performed: boolean; // ALWAYS false — invariant
  timestamp: string;
}

export interface WorkflowCrossTraceNavigator {
  navigator_id: string;
  selection_id: string;
  workflow_id: string;
  session_id: string;
  evidence_refs: string[];
  artifact_refs: string[];
}

// ---------------------------------------------------------------------------
// State Badge Presentation
// ---------------------------------------------------------------------------

export interface WorkflowModelBindingStateBadge {
  state: WorkflowModelBindingState;
  label: string;
  severity: "info" | "success" | "warning" | "error" | "neutral";
  requiresAction: boolean;
  description: string;
}

export function resolveBindingStateBadge(
  state: WorkflowModelBindingState
): WorkflowModelBindingStateBadge {
  switch (state) {
    case "MANUAL_OPERATOR_SELECTION":
      return {
        state,
        label: "Manual Operator Selection",
        severity: "success",
        requiresAction: false,
        description: "Model was explicitly chosen by the operator with documented rationale.",
      };
    case "ROUTER_SELECTED":
      return {
        state,
        label: "Router Selected",
        severity: "info",
        requiresAction: false,
        description: "Model was selected by the routing system without explicit operator input.",
      };
    case "UNRECORDED":
      return {
        state,
        label: "Unrecorded",
        severity: "neutral",
        requiresAction: false,
        description: "No model selection state recorded for this workflow.",
      };
    case "BLOCKED_UNAVAILABLE":
      return {
        state,
        label: "Blocked — Model Unavailable",
        severity: "error",
        requiresAction: true,
        description:
          "Manually selected model is currently unavailable. Explicit operator re-selection is required. No automatic substitution has been performed.",
      };
    case "PENDING_PROVIDER_APPROVAL":
      return {
        state,
        label: "Pending Provider Approval",
        severity: "warning",
        requiresAction: true,
        description:
          "Provider model awaiting approval gate. Workflow cannot proceed until provider approval is confirmed by the operator.",
      };
    default:
      return {
        state: "UNRECORDED",
        label: String(state) || "Unknown",
        severity: "neutral",
        requiresAction: false,
        description: "Binding state is unrecognized.",
      };
  }
}

// ---------------------------------------------------------------------------
// Binding Presentation Formatters
// ---------------------------------------------------------------------------

export interface WorkflowBindingPresentation {
  hasBinding: boolean;
  bindingId: string;
  workflowId: string;
  sessionId: string;
  selectionId: string;
  modelId: string;
  modelLocality: string;
  providerApprovalState: string | null;
  operatorRationaleNotes: string;
  evidenceReferencesReviewed: string[];
  badge: WorkflowModelBindingStateBadge;
  timestamp: string | null;
}

export function formatWorkflowBinding(
  binding?: WorkflowModelBindingRecord | null
): WorkflowBindingPresentation {
  if (!binding) {
    return {
      hasBinding: false,
      bindingId: "—",
      workflowId: "—",
      sessionId: "—",
      selectionId: "—",
      modelId: "—",
      modelLocality: "—",
      providerApprovalState: null,
      operatorRationaleNotes: "No manual model selection recorded for this workflow.",
      evidenceReferencesReviewed: [],
      badge: resolveBindingStateBadge("UNRECORDED"),
      timestamp: null,
    };
  }

  return {
    hasBinding: true,
    bindingId: binding.binding_id,
    workflowId: binding.workflow_id,
    sessionId: binding.session_id,
    selectionId: binding.selection_id,
    modelId: binding.model_id,
    modelLocality: binding.model_locality,
    providerApprovalState: binding.provider_approval_state ?? null,
    operatorRationaleNotes: binding.operator_rationale_notes || "—",
    evidenceReferencesReviewed: Array.isArray(binding.evidence_references_reviewed)
      ? [...binding.evidence_references_reviewed]
      : [],
    badge: resolveBindingStateBadge(binding.binding_state),
    timestamp: binding.timestamp || null,
  };
}

// ---------------------------------------------------------------------------
// Blocked Model Alert Formatter
// ---------------------------------------------------------------------------

export interface BlockedModelPresentation {
  isBlocked: boolean;
  blockId: string;
  modelId: string;
  reason: string;
  requiresExplicitUserAction: boolean;
  silentFallbackPerformed: boolean; // MUST always be false
  timestamp: string | null;
}

export function formatBlockedModel(
  block?: UnavailableModelBlockRecord | null
): BlockedModelPresentation {
  if (!block) {
    return {
      isBlocked: false,
      blockId: "—",
      modelId: "—",
      reason: "No model availability issue recorded.",
      requiresExplicitUserAction: false,
      silentFallbackPerformed: false,
      timestamp: null,
    };
  }

  return {
    isBlocked: true,
    blockId: block.block_id,
    modelId: block.model_id,
    reason: block.reason,
    requiresExplicitUserAction: block.requires_explicit_user_action,
    silentFallbackPerformed: block.silent_fallback_performed, // always false
    timestamp: block.timestamp || null,
  };
}

// ---------------------------------------------------------------------------
// Cross-Trace Navigation Presenter
// ---------------------------------------------------------------------------

export interface CrossTraceNavigationPresentation {
  hasNavigator: boolean;
  navigatorId: string;
  links: {
    label: string;
    refId: string;
    kind: "selection" | "workflow" | "session" | "evidence" | "artifact";
  }[];
}

export function formatCrossTraceNavigation(
  nav?: WorkflowCrossTraceNavigator | null
): CrossTraceNavigationPresentation {
  if (!nav) {
    return { hasNavigator: false, navigatorId: "—", links: [] };
  }

  const links: CrossTraceNavigationPresentation["links"] = [
    { label: "Model Selection", refId: nav.selection_id, kind: "selection" },
    { label: "Workflow", refId: nav.workflow_id, kind: "workflow" },
    { label: "Runtime Session", refId: nav.session_id, kind: "session" },
    ...nav.evidence_refs.map((ref) => ({
      label: `Evidence: ${ref}`,
      refId: ref,
      kind: "evidence" as const,
    })),
    ...nav.artifact_refs.map((ref) => ({
      label: `Artifact: ${ref}`,
      refId: ref,
      kind: "artifact" as const,
    })),
  ];

  return { hasNavigator: true, navigatorId: nav.navigator_id, links };
}

// ---------------------------------------------------------------------------
// Anti-Silent-Fallback Audit
// ---------------------------------------------------------------------------

export function auditNoSilentFallback(blocks: UnavailableModelBlockRecord[]): boolean {
  return blocks.every((b) => b.silent_fallback_performed === false);
}
