// ZCODE-AMCA-STUDIO-V0.9.0-DECISION-TRANSPARENCY-UX-PREPARATION-01 — pure model.
//
// UX preparation for future decision evidence records, built on the
// accepted evidence registry design (decisionEvidenceRegistryDesignModel).
// Recorded rules from the order:
// - preparation only: no adaptive decision engine, no decision execution,
//   no backend changes — the surfaces show PLACEHOLDERS for records that
//   do not exist yet;
// - absent records render "Decision records unavailable." and "No
//   adaptive decision exists in this build.";
// - the provenance viewer is read-only: evidence reference IDs, source
//   category, timestamp, linked workflow, linked artifacts — NO
//   reasoning, NO AI-generated explanations, NO confidence scores, NO
//   rankings;
// - human control placeholders must state "Designed control only. No
//   decision engine is active." — no actions implemented.


// ---------- mandated wording ----------

/**
 * Same wording as the accepted decision-trace design boundary
 * (intelligentDecisionTransparencyUxDesignModel.DECISION_TRACE_BOUNDARY);
 * duplicated here verbatim so this model stays dependency-free — the test
 * cross-checks the two constants.
 */
export const DECISION_TRACE_BOUNDARY =
  "Decision transparency design only — no adaptive decision exists in this build.";

export const DECISION_RECORDS_UNAVAILABLE =
  "Decision records unavailable. No adaptive decision exists in this build.";

export const DESIGNED_CONTROL_LABEL = "Designed control only. No decision engine is active.";

export const PROVENANCE_VIEWER_NOTE =
  "Read-only provenance viewer — shows recorded references only; no reasoning, no AI-generated explanations, no confidence scores, no rankings.";

// ---------- 1. Future decision transparency panel (design shape) ----------

export interface DecisionPanelSection {
  sectionId: string;
  title: string;
  /** What the section will render once decision records exist. */
  plannedFields: string[];
  /** The honest rendering while records are absent. */
  currentRendering: string;
}

export const DECISION_PANEL_SECTIONS: DecisionPanelSection[] = [
  {
    sectionId: "decision_status",
    title: "Decision status",
    plannedFields: ["decision.approval_state", "decision.lifecycle_state"],
    currentRendering: DECISION_RECORDS_UNAVAILABLE,
  },
  {
    sectionId: "evidence_references",
    title: "Evidence references",
    plannedFields: ["decision.evidence_refs", "decision.input_record_ids"],
    currentRendering: DECISION_RECORDS_UNAVAILABLE,
  },
  {
    sectionId: "selected_resources",
    title: "Selected resources",
    plannedFields: ["decision.selected_model_refs", "decision.selected_agent_ids"],
    currentRendering: DECISION_RECORDS_UNAVAILABLE,
  },
  {
    sectionId: "approval_references",
    title: "Approval references",
    plannedFields: ["decision.approval_checkpoint_ids"],
    currentRendering: DECISION_RECORDS_UNAVAILABLE,
  },
  {
    sectionId: "override_availability",
    title: "Override availability",
    plannedFields: ["decision.override_available"],
    currentRendering: DECISION_RECORDS_UNAVAILABLE,
  },
];

// ---------- 2. Evidence provenance viewer (read-only rows) ----------

export interface ProvenanceViewerRow {
  evidenceReferenceId: string;
  sourceCategory: string;
  timestamp: string | null;
  linkedWorkflow: string | null;
  linkedArtifacts: string[];
}

/**
 * Build read-only provenance rows from recorded evidence references. The
 * source category mirrors the recorded registry relation; rows without a
 * recorded timestamp or workflow render those fields as null — never
 * invented.
 */
export function provenanceViewerRows(
  evidenceRefs: Array<{
    referenceId: string;
    sourceCategory: string;
    timestamp?: string | null;
    workflowId?: string | null;
    artifactIds?: string[];
  }>,
): ProvenanceViewerRow[] {
  return evidenceRefs.map((ref) => ({
    evidenceReferenceId: ref.referenceId,
    sourceCategory: ref.sourceCategory,
    timestamp: ref.timestamp ?? null,
    linkedWorkflow: ref.workflowId ?? null,
    linkedArtifacts: ref.artifactIds ?? [],
  }));
}

// ---------- 3. Human control placeholders (designed, inactive) ----------

export interface ControlPlaceholder {
  control: "approve" | "modify" | "override" | "reject";
  label: string;
  inactiveReason: string;
  implemented: false;
}

export const CONTROL_PLACEHOLDERS: ControlPlaceholder[] = (
  ["approve", "modify", "override", "reject"] as const
).map((control) => ({
  control,
  label: control.charAt(0).toUpperCase() + control.slice(1),
  inactiveReason: DESIGNED_CONTROL_LABEL,
  implemented: false as const,
}));

// ---------- 4. Integration location (existing surfaces only) ----------

export const INTEGRATION_SURFACES = ["Review Center", "Cognitive Workflow Panel", "Validation Center"] as const;

export const INTEGRATION_NOTE =
  "Panels integrate into existing surfaces only — no new navigation is created.";

// ---------- 5. Panel rendering from absent records ----------

/**
 * The honest render of the whole panel while no decision records exist:
 * every section shows the mandated unavailable wording and the control
 * placeholders stay inactive.
 */
export function decisionPanelForAbsentRecords(): {
  sections: DecisionPanelSection[];
  controls: ControlPlaceholder[];
  boundary: string;
} {
  return {
    sections: DECISION_PANEL_SECTIONS.map((section) => ({ ...section })),
    controls: CONTROL_PLACEHOLDERS.map((placeholder) => ({ ...placeholder })),
    boundary: DECISION_TRACE_BOUNDARY,
  };
}
