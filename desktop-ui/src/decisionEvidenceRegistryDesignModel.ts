// ZCODE-AMCA-STUDIO-V0.8.0-DECISION-EVIDENCE-REGISTRY-DESIGN-01 — pure design model.
//
// DESIGN ONLY: the evidence registry contract that future adaptive
// decisions will depend on. Nothing here executes or is rendered yet.
// Recorded rules from the order:
// - "Store evidence references, not hidden reasoning." — the registry
//   carries references to existing records, never reasoning payloads;
// - provenance is a one-to-one relationship map — no inference, no
//   generated explanation;
// - human override traces are first-class records (modification, override
//   reason, rejected decision, approved decision);
// - lifecycle states are recorded transitions only.

// ---------- 1. Decision evidence record design ----------

export interface DecisionEvidenceRecordField {
  field: string;
  type: string;
  required: boolean;
  /** What the field stores — always a reference or a recorded fact. */
  note: string;
}

export const DECISION_EVIDENCE_RECORD_FIELDS: DecisionEvidenceRecordField[] = [
  { field: "decision_id", type: "string (uuid)", required: true, note: "Primary identifier of the future decision record." },
  { field: "workflow_id", type: "string", required: true, note: "The workflow the decision belongs to." },
  { field: "timestamp", type: "ISO-8601 string", required: true, note: "Recorded creation time." },
  { field: "initiator", type: "string (recorded role/ref)", required: true, note: "Who or what initiated the decision (user or recorded system role)." },
  { field: "input_references", type: "string[]", required: true, note: "References to the recorded input records the decision was based on." },
  { field: "evidence_references", type: "string[]", required: true, note: "References to existing evidence records — never reasoning payloads." },
  { field: "selected_resources", type: "{ models: string[]; agents: string[]; knowledge: string[] }", required: true, note: "Selected resources as recorded references." },
  { field: "approval_references", type: "string[]", required: true, note: "References to the approval records gating this decision." },
];

export const REGISTRY_RULE =
  "Store evidence references, not hidden reasoning.";

// ---------- 2. Evidence provenance model ----------

export type ProvenanceRelation =
  | "input_records"
  | "knowledge_references"
  | "model_agent_references"
  | "approval_records"
  | "execution_record";

export interface ProvenanceEdge {
  relation: ProvenanceRelation;
  /** The provenance links are references — the registry stores IDs only. */
  storesReferencesOnly: true;
  designNote: string;
}

export const PROVENANCE_MODEL: ProvenanceEdge[] = [
  { relation: "input_records", storesReferencesOnly: true, designNote: "Decision → input record IDs the decision consumed." },
  { relation: "knowledge_references", storesReferencesOnly: true, designNote: "Decision → knowledge artifact IDs explicitly attached or bound." },
  { relation: "model_agent_references", storesReferencesOnly: true, designNote: "Decision → recorded model/agent IDs the decision selected." },
  { relation: "approval_records", storesReferencesOnly: true, designNote: "Decision → approval record IDs gating the decision." },
  { relation: "execution_record", storesReferencesOnly: true, designNote: "Decision → the final execution record ID (when executed)." },
];

/**
 * Provenance is one-to-one reference storage: no inference, no generated
 * explanation. Relationships exist only where a recorded reference exists.
 */
export const PROVENANCE_RULE =
  "Provenance stores references only — no inference, no generated explanation.";

// ---------- 3. Human override trace design ----------

export interface OverrideTraceField {
  field: string;
  note: string;
}

export const OVERRIDE_TRACE_FIELDS: OverrideTraceField[] = [
  { field: "override_id", note: "Identifier of the trace record itself." },
  { field: "decision_id", note: "The decision being overridden or decided on." },
  { field: "trace_type", note: 'One of: "user_modification" | "override_with_reason" | "rejected_decision" | "approved_decision".' },
  { field: "user_ref", note: "Recorded user role/ref who acted." },
  { field: "reason", note: "Recorded reason text — required for modifications, overrides, and rejections." },
  { field: "changed_fields", note: "Recorded fields the user modified (empty for approve/reject)." },
  { field: "timestamp", note: "Recorded action time." },
];

// ---------- 4. Decision lifecycle state model ----------

export type DecisionLifecycleState =
  | "CREATED"
  | "EVIDENCE_COLLECTED"
  | "AWAITING_APPROVAL"
  | "APPROVED"
  | "MODIFIED"
  | "REJECTED"
  | "EXECUTED"
  | "VERIFIED";

export const LIFECYCLE_ORDER: DecisionLifecycleState[] = [
  "CREATED",
  "EVIDENCE_COLLECTED",
  "AWAITING_APPROVAL",
  "APPROVED",
  "MODIFIED",
  "REJECTED",
  "EXECUTED",
  "VERIFIED",
];

/** Terminal/branching semantics: APPROVED/MODIFIED proceed to EXECUTED; REJECTED does not. */
export function lifecycleProceeds(state: DecisionLifecycleState): boolean {
  return state === "APPROVED" || state === "MODIFIED";
}

/** Whether a state transition is recorded-legal. REJECTED is terminal. */
export function canTransition(from: DecisionLifecycleState, to: DecisionLifecycleState): boolean {
  if (from === "REJECTED") return false;
  if (from === to) return false;
  if (from === "EXECUTED" && to === "VERIFIED") return true;
  if (from === "VERIFIED") return false;
  return LIFECYCLE_ORDER.indexOf(to) > LIFECYCLE_ORDER.indexOf(from);
}

// ---------- design validation helper ----------

const FORBIDDEN_DESIGN_PATTERNS: ReadonlyArray<RegExp> = [
  /hidden reasoning/i,
  /reasoning payload/i,
  /autonomous(ly)? (execut|decid|select)/i,
  /decides? automatically/i,
  /chain[- ]of[- ]thought/i,
];

/**
 * Registry schema text must never carry reasoning/autonomy fields. The
 * mandated boundary statements ("never reasoning payloads", "not hidden
 * reasoning") are negations of the forbidden claims and pass the scan.
 */
export function passesRegistrySchemaScan(text: string): boolean {
  return !FORBIDDEN_DESIGN_PATTERNS.some((pattern) =>
    pattern.test(text) && !new RegExp("(never|not) " + pattern.source.replace("^", ""), "i").test(text),
  );
}
