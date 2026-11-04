// ZCODE-AMCA-STUDIO-V0.8.0-INTELLIGENT-DECISION-TRANSPARENCY-UX-DESIGN-01 — pure design model.
//
// DESIGN ONLY: the UX contract for FUTURE AMCA adaptive decisions.
// Nothing here is rendered in the app yet and no adaptive decision exists
// in this build. The contract defines how a future decision trace,
// explainability surface, human control panel, and audit view would
// display recorded data — under the recorded constraints:
// - no generated explanations, no hidden reasoning;
// - explicit user control: approve / modify / override / reject are
//   DESIGNED surfaces, deliberately NOT implemented actions;
// - every display field maps to a recorded backend field name so the
//   future implementation is mechanical, not inventive.

// ---------- 1. Future decision trace UX design ----------

export const DECISION_TRACE_BOUNDARY =
  "Decision transparency design only — no adaptive decision exists in this build.";

/** Sections of the future decision-trace panel, in display order. */
export interface DecisionTraceSection {
  sectionId: string;
  title: string;
  /** Recorded backend fields the section would render (contract field names). */
  contractFields: string[];
  description: string;
}

export const DECISION_TRACE_SECTIONS: DecisionTraceSection[] = [
  {
    sectionId: "selected_strategy",
    title: "Selected strategy",
    contractFields: ["decision.strategy", "decision.strategy_reason"],
    description: "The recorded collaboration strategy the decision chose, with the recorded reason string — quoted, never generated.",
  },
  {
    sectionId: "selected_models",
    title: "Selected models",
    contractFields: ["decision.selected_model_refs", "selection_trace.provider_health_references"],
    description: "Recorded model references with their locality and provider-health references as recorded.",
  },
  {
    sectionId: "selected_agents",
    title: "Selected agents",
    contractFields: ["decision.selected_agent_ids"],
    description: "Recorded agent participants; agents without recorded participation are simply absent.",
  },
  {
    sectionId: "selected_knowledge",
    title: "Selected knowledge sources",
    contractFields: ["decision.knowledge_artifact_ids", "binding.audit_event_ref"],
    description: "Recorded knowledge bindings with their audit reference — each item traceable to an audit event.",
  },
  {
    sectionId: "approval_checkpoints",
    title: "Approval checkpoints",
    contractFields: ["decision.approval_checkpoint_ids", "checkpoint.state"],
    description: "Approval gates the decision will pass, each with its current recorded state (pending/granted/denied).",
  },
];

// ---------- 2. Explainability contract (UI fields) ----------

export interface ExplainabilityField {
  uiField: string;
  contractSource: string;
  required: boolean;
  /** How a missing value renders — never invented. */
  missingRendering: string;
}

export const EXPLAINABILITY_FIELDS: ExplainabilityField[] = [
  { uiField: "Decision input records", contractSource: "decision.input_record_ids", required: true, missingRendering: "No recorded decision input available." },
  { uiField: "Evidence used", contractSource: "decision.evidence_refs", required: true, missingRendering: "No recorded evidence used." },
  { uiField: "Approval state", contractSource: "decision.approval_state", required: true, missingRendering: "Approval state not recorded." },
  { uiField: "User override option", contractSource: "decision.override_available", required: true, missingRendering: "Override availability not recorded." },
  { uiField: "Final execution record", contractSource: "decision.execution_record_id", required: false, missingRendering: "Not executed yet." },
];

/**
 * The explainability rendering rule: every field renders the recorded
 * value or its mandated missing rendering. No generated explanations and
 * no hidden reasoning are ever inserted.
 */
export const EXPLAINABILITY_RULE =
  "Each field renders its recorded value or the mandated missing rendering — no generated explanations, no hidden reasoning.";

// ---------- 3. Human control surface design (NOT implemented actions) ----------

export interface DesignedControl {
  control: "approve" | "modify" | "override" | "reject";
  description: string;
  /** What the control would touch — always a recorded decision, never a live engine call. */
  target: string;
  implementedInThisBuild: false;
}

export const DESIGNED_CONTROLS: DesignedControl[] = [
  {
    control: "approve",
    description: "Confirm the recorded decision so execution may proceed through the existing approval pipeline.",
    target: "the recorded decision's approval checkpoint",
    implementedInThisBuild: false,
  },
  {
    control: "modify",
    description: "Change a decision parameter (e.g. swap a model ref) before approving; the modification is recorded alongside the decision.",
    target: "the recorded decision fields",
    implementedInThisBuild: false,
  },
  {
    control: "override",
    description: "Replace the recorded decision with the user's own choice; the override and its reason are recorded.",
    target: "the recorded decision outcome",
    implementedInThisBuild: false,
  },
  {
    control: "reject",
    description: "Decline the recorded decision; the rejection and reason are recorded and execution does not proceed.",
    target: "the recorded decision outcome",
    implementedInThisBuild: false,
  },
];

export const CONTROL_SURFACE_NOTE =
  "Designed control surfaces only — none are implemented in this build. Approve/reject of EXISTING approvals remains in the existing approval surfaces.";

// ---------- 4. Decision audit model (UX only) ----------

export interface DecisionAuditDesign {
  uiField: string;
  contractSource: string;
  missingRendering: string;
}

export const DECISION_AUDIT_FIELDS: DecisionAuditDesign[] = [
  { uiField: "Initiated by", contractSource: "decision.initiator", missingRendering: "Initiator not recorded." },
  { uiField: "Evidence references", contractSource: "decision.evidence_refs", missingRendering: "No recorded evidence references." },
  { uiField: "Timestamp", contractSource: "decision.created_at", missingRendering: "No recorded timestamp." },
  { uiField: "Approval state", contractSource: "decision.approval_state", missingRendering: "Approval state not recorded." },
];

export const DECISION_AUDIT_NOTE =
  "Audit display mirrors the recorded decision record one-to-one — no derived judgment.";

// ---------- 5. Design validation helpers ----------

/** Forbidden language for this design: current adaptive claims, autonomous execution. */
export const FORBIDDEN_PATTERNS: ReadonlyArray<RegExp> = [
  /decides? automatically/i,
  /autonomous(ly)? (execut|decid|select)/i,
  /will (?:automatically )?(?:choose|select|optimize) (?:the )?(?:best|optimal)/i,
  /intelligent (?:auto-)?routing (?:is )?(?:now |already )?(?:active|implemented)/i,
];

/** True when a design text passes the forbidden-language scan. */
export function passesDesignLanguageScan(text: string): boolean {
  return !FORBIDDEN_PATTERNS.some((pattern) => pattern.test(text));
}

/** All designed surfaces concatenated — for the audit's blanket scan. */
export function fullDesignText(): string {
  return [
    DECISION_TRACE_BOUNDARY,
    ...DECISION_TRACE_SECTIONS.map((section) => `${section.title} ${section.description} ${section.contractFields.join(" ")}`),
    ...EXPLAINABILITY_FIELDS.map((field) => `${field.uiField} ${field.missingRendering}`),
    EXPLAINABILITY_RULE,
    ...DESIGNED_CONTROLS.map((control) => `${control.control} ${control.description} ${control.target}`),
    CONTROL_SURFACE_NOTE,
    ...DECISION_AUDIT_FIELDS.map((field) => `${field.uiField} ${field.missingRendering}`),
    DECISION_AUDIT_NOTE,
  ].join("\n");
}
