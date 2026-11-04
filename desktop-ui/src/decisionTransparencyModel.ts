/**
 * AMCA Studio v0.9.0 Decision Transparency UX Preparation Model.
 *
 * Milestone: ZCODE-AMCA-STUDIO-V0.9.0-DECISION-TRANSPARENCY-UX-PREPARATION-01
 *
 * Rules:
 * 1. UX preparation only (no adaptive decision engine, no backend changes).
 * 2. Displays decision status, evidence references, selected resources, approval references, override availability.
 * 3. Read-only evidence provenance viewer (IDs, category, timestamp, linked workflow/artifacts).
 * 4. Strictly forbids reasoning, AI-generated explanations, confidence scores, or rankings.
 * 5. Human control visibility placeholders are strictly disabled and informational (no actions implemented).
 */

export type DecisionStatus = "PENDING" | "APPROVED" | "REJECTED" | "OVERRIDDEN";

export interface DecisionEvidenceRecord {
  decision_id: string;
  status: DecisionStatus;
  evidence_reference_ids: string[];
  selected_resources: string[];
  approval_reference?: string | null;
  override_available: boolean;
  source_category: string;
  timestamp: string;
  linked_workflow_id: string;
  linked_artifact_ids: string[];
}

export interface DecisionTransparencyCard {
  hasRecord: boolean;
  decisionId: string | null;
  status: DecisionStatus | "NONE";
  statusBadge: {
    label: string;
    variant: "neutral" | "info" | "success" | "warning" | "error";
  };
  evidenceReferences: string[];
  selectedResources: string[];
  approvalReference: string | null;
  overrideAvailable: boolean;
  sourceCategory: string | null;
  timestamp: string | null;
  linkedWorkflowId: string | null;
  linkedArtifactIds: string[];
  emptyPlaceholder: string;
}

export interface EvidenceProvenanceEntry {
  evidenceId: string;
  sourceCategory: string;
  timestamp: string;
  linkedWorkflowId: string;
  linkedArtifactsCount: number;
}

export interface HumanControlPlaceholder {
  id: "approve" | "modify" | "override" | "reject";
  label: string;
  disabled: true;
  statusText: string;
}

export function formatDecisionTransparencyCard(
  record?: DecisionEvidenceRecord | null
): DecisionTransparencyCard {
  if (!record || !record.decision_id) {
    return {
      hasRecord: false,
      decisionId: null,
      status: "NONE",
      statusBadge: {
        label: "No Records",
        variant: "neutral",
      },
      evidenceReferences: [],
      selectedResources: [],
      approvalReference: null,
      overrideAvailable: false,
      sourceCategory: null,
      timestamp: null,
      linkedWorkflowId: null,
      linkedArtifactIds: [],
      emptyPlaceholder: "No decision records available",
    };
  }

  const badgeMap: Record<DecisionStatus, { label: string; variant: "neutral" | "info" | "success" | "warning" | "error" }> = {
    PENDING: { label: "Under Review", variant: "warning" },
    APPROVED: { label: "Approved", variant: "success" },
    REJECTED: { label: "Rejected", variant: "error" },
    OVERRIDDEN: { label: "Overridden", variant: "info" },
  };

  return {
    hasRecord: true,
    decisionId: record.decision_id,
    status: record.status,
    statusBadge: badgeMap[record.status] || { label: record.status, variant: "neutral" },
    evidenceReferences: Array.isArray(record.evidence_reference_ids) ? [...record.evidence_reference_ids] : [],
    selectedResources: Array.isArray(record.selected_resources) ? [...record.selected_resources] : [],
    approvalReference: record.approval_reference ?? null,
    overrideAvailable: Boolean(record.override_available),
    sourceCategory: record.source_category ?? "general",
    timestamp: record.timestamp,
    linkedWorkflowId: record.linked_workflow_id,
    linkedArtifactIds: Array.isArray(record.linked_artifact_ids) ? [...record.linked_artifact_ids] : [],
    emptyPlaceholder: "",
  };
}

export function formatEvidenceProvenanceViewer(
  record?: DecisionEvidenceRecord | null
): EvidenceProvenanceEntry[] {
  if (!record || !Array.isArray(record.evidence_reference_ids) || record.evidence_reference_ids.length === 0) {
    return [];
  }

  return record.evidence_reference_ids.map((id) => ({
    evidenceId: id,
    sourceCategory: record.source_category || "recorded_trace",
    timestamp: record.timestamp || new Date().toISOString(),
    linkedWorkflowId: record.linked_workflow_id || "unspecified",
    linkedArtifactsCount: Array.isArray(record.linked_artifact_ids) ? record.linked_artifact_ids.length : 0,
  }));
}

export function getHumanControlPlaceholders(): HumanControlPlaceholder[] {
  return [
    {
      id: "approve",
      label: "Approve Decision",
      disabled: true,
      statusText: "Informational (No action implemented)",
    },
    {
      id: "modify",
      label: "Modify Parameters",
      disabled: true,
      statusText: "Informational (No action implemented)",
    },
    {
      id: "override",
      label: "Operator Override",
      disabled: true,
      statusText: "Informational (No action implemented)",
    },
    {
      id: "reject",
      label: "Reject Decision",
      disabled: true,
      statusText: "Informational (No action implemented)",
    },
  ];
}

