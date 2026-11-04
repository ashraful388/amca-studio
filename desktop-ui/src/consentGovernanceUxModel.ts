// ZCODE-AMCA-STUDIO-V0.8.0-CONSENT-AND-GOVERNANCE-UX-ENHANCEMENT-01 — pure model.
//
// Governance transparency over recorded fields only:
// - the unified governance summary states knowledge permission state,
//   provider approval state, local/provider model status, and workflow
//   governance state from records the desktop already holds — no security
//   score, no compliance score;
// - approval context renders only existing approval records (what requires
//   approval, affected resource, current state, recorded timestamp); no
//   automation, prediction, or automatic decisions;
// - the user-control boundary panel states the recorded split: what users
//   control vs what AMCA never does;
// - cross-module privacy indicators use the canonical wording.

import type { RunJob } from "./types";

// ---------- 1. Unified governance summary (recorded fields only) ----------

export interface GovernanceSummary {
  knowledgePermissionState: string;
  providerApprovalState: string;
  localModelStatus: string;
  providerModelStatus: string;
  workflowGovernanceState: string;
  note: string;
}

/**
 * Summary over recorded fields: knowledge shareability verdicts (verbatim),
// provider credentials requiring approval, model locality from recorded
 * refs, and the recorded workflow governance restatement. No security or
 * compliance score exists anywhere in the output.
 */
export function governanceSummary(input: {
  knowledgeVerdicts: Array<{ shareability: string; shareabilityReason: string }>;
  providerApprovalState: string | null;
  localModelCount: number;
  providerModelCount: number;
}): GovernanceSummary {
  const shareable = input.knowledgeVerdicts.filter((v) => v.shareability === "shareable").length;
  const nonShareable = input.knowledgeVerdicts.length - shareable;
  return {
    knowledgePermissionState:
      input.knowledgeVerdicts.length === 0
        ? "Not recorded"
        : `${shareable} shareable · ${nonShareable} private/restricted (recorded verdicts)`,
    providerApprovalState:
      input.providerApprovalState && input.providerApprovalState.trim()
        ? input.providerApprovalState
        : "Not recorded",
    localModelStatus:
      input.localModelCount > 0
        ? `${input.localModelCount} local model(s) — no network call without approval`
        : "Not recorded",
    providerModelStatus:
      input.providerModelCount > 0
        ? `${input.providerModelCount} provider model(s) — approval required before any network call`
        : "Not recorded",
    workflowGovernanceState: "Explicit user selection; exact approval before any network call",
    note: "Recorded fields only — no security score, no compliance score.",
  };
}

// ---------- 2. Approval context visibility (existing records) ----------

export interface ApprovalContextItem {
  whatRequiresApproval: string;
  affectedResource: string;
  currentState: string;
  recordedTimestamp: string | null;
}

export const APPROVAL_EMPTY = {
  title: "No approval records available.",
  detail: "Approval records appear here when a provider call or governed action requests one — nothing is predicted or pre-decided.",
} as const;

/** Map existing approval records verbatim — no automation, no prediction. */
export function approvalContexts(
  records: Array<{
    approval_id?: string;
    resource?: string;
    status?: string;
    created_at?: string | null;
  }>,
): ApprovalContextItem[] {
  return records.map((record) => ({
    whatRequiresApproval: record.approval_id ? `Approval ${record.approval_id}` : "Recorded approval request",
    affectedResource: record.resource && record.resource.trim() ? record.resource : "Not recorded",
    currentState: record.status && record.status.trim() ? record.status : "Not recorded",
    recordedTimestamp: record.created_at ?? null,
  }));
}

// Map the recorded ApprovalRequest contract verbatim into the panel shape.
export function approvalContextFromRequest(record: {
  id: string;
  action: string;
  requested_by: string;
  status: string;
  created_at: string;
}): ApprovalContextItem {
  return {
    whatRequiresApproval: `${record.action} (approval ${record.id}) — requested by ${record.requested_by}`,
    affectedResource: record.action,
    currentState: record.status,
    recordedTimestamp: record.created_at,
  };
}

// ---------- 3. User control boundary panel ----------

export const USER_CONTROL_BOUNDARY = {
  usersControl: [
    "Knowledge attachment — items attach only by explicit user action in the composer.",
    "Execution initiation — runs start only when the user sends the task.",
    "Provider/network approval — exact approval before any network call.",
  ],
  amcaDoesNot: [
    "Retrieve hidden context — nothing is fetched automatically.",
    "Execute hidden actions — no background execution exists.",
    "Modify workflow state automatically — workflow records change only through recorded actions.",
  ],
  note: "Recorded behavior, not promises — each line reflects how the governed pipeline already works.",
} as const;

// ---------- 4. Cross-module privacy indicators (canonical wording) ----------

export type PrivacyIndicatorKind = "local_only" | "approval_required" | "explicitly_attached" | "not_recorded";

export interface PrivacyIndicator {
  kind: PrivacyIndicatorKind;
  label: string;
}

const INDICATOR_LABELS: Record<PrivacyIndicatorKind, string> = {
  local_only: "Local only",
  approval_required: "Approval required",
  explicitly_attached: "Explicitly attached",
  not_recorded: "Not recorded",
};

export function privacyIndicator(kind: PrivacyIndicatorKind): PrivacyIndicator {
  return { kind, label: INDICATOR_LABELS[kind] };
}

/** Map a recorded model ref to its canonical indicator. */
export function indicatorForModelRef(ref: string): PrivacyIndicator {
  if (!ref || !ref.trim()) return privacyIndicator("not_recorded");
  return privacyIndicator(ref.startsWith("ollama:") ? "local_only" : "approval_required");
}

/** Map a recorded knowledge item to its canonical indicator. */
export function indicatorForKnowledge(item: { shareability: string }): PrivacyIndicator {
  return privacyIndicator(item.shareability === "shareable" ? "explicitly_attached" : "not_recorded");
}

// ---------- 5. Run governance line (recorded fields only) ----------

export function runGovernanceLine(run: Pick<RunJob, "privacy" | "mode">): string {
  const privacy = run.privacy && run.privacy.trim() ? run.privacy : "Not recorded";
  const mode = run.mode && run.mode.trim() ? run.mode : "Not recorded";
  return `Recorded privacy: ${privacy} · recorded mode: ${mode} · exact approval before any network call`;
}
