// ZCODE-AMCA-STUDIO-V0.8.0-PROFESSIONAL-WORKFLOW-AUTOMATION-READINESS-UX-01 — pure model.
//
// Readiness UX over recorded automation records only:
// - continuity: Automation → Workflow Run → Artifacts links exist ONLY when
//   the recorded `last_run_id` / run records carry them; missing links
//   degrade to "No recorded workflow link available." — nothing inferred;
// - governance: enabled state, trigger capability status (manual-only for
//   monitor/event triggers until the scheduler supports them), approval
//   requirements, and local-only/provider boundaries — verbatim records;
// - occurrence history: counts of recorded statuses; empty history renders
//   "Automation execution history is not recorded.";
// - NO automatic enabling, automatic execution, trigger recommendations,
//   prediction, or scheduling intelligence anywhere.

export const AUTOMATION_EMPTY_STATES = {
  no_records: "No automation records available yet.",
  no_history: "Automation execution history is not recorded.",
  scheduler_unavailable: "Scheduler capability is unavailable for this trigger.",
} as const;

export const CONTINUITY_UNAVAILABLE = "No recorded workflow link available.";

export interface AutomationContinuity {
  lastRunId: string | null;
  hasWorkflowLink: boolean;
  unavailableReason: string | null;
  /** Where the artifacts of that run live — navigation, not execution. */
  artifactsNote: string;
}

/** Continuity from the recorded `last_run_id` — nothing inferred. */
export function automationContinuity(automation: { last_run_id: string | null }): AutomationContinuity {
  const lastRunId = automation.last_run_id && automation.last_run_id.trim() ? automation.last_run_id : null;
  return {
    lastRunId,
    hasWorkflowLink: lastRunId !== null,
    unavailableReason: lastRunId === null ? CONTINUITY_UNAVAILABLE : null,
    artifactsNote: "Artifacts of recorded runs appear in the Artifact Center for their project.",
  };
}

// ---------- governance fields (recorded, verbatim) ----------

export interface AutomationGovernanceFields {
  enabledState: "enabled" | "disabled";
  triggerCapability: string;
  approvalRequirement: string;
  executionBoundary: string;
}

export const AUTOMATION_APPROVAL_REQUIREMENT =
  "Enabling records intent; execution still passes exact provider and privacy authorization. Cost is recorded as telemetry only.";

export const AUTOMATION_EXECUTION_BOUNDARY =
  "Runs execute only through explicit user action or the recorded scheduler — never automatically beyond the recorded trigger type.";

export function automationGovernanceFields(input: {
  enabled: boolean;
  triggerType: string;
  schedulerRunning: boolean | null;
}): AutomationGovernanceFields {
  const manualOnly = input.triggerType === "monitor" || input.triggerType === "event_triggered";
  return {
    enabledState: input.enabled ? "enabled" : "disabled",
    triggerCapability: manualOnly
      ? "Manual execution only — the scheduler does not support this trigger type yet."
      : input.schedulerRunning === false
        ? "Scheduler stopped — this trigger waits until the scheduler runs."
        : "Scheduler-supported trigger.",
    approvalRequirement: AUTOMATION_APPROVAL_REQUIREMENT,
    executionBoundary: AUTOMATION_EXECUTION_BOUNDARY,
  };
}

// ---------- occurrence history (recorded statuses, counts only) ----------

export interface OccurrenceSummary {
  total: number;
  byStatus: Record<string, number>;
  emptyReason: string | null;
}

export function occurrenceSummary(
  occurrences: Array<{ status?: string }> | null,
): OccurrenceSummary {
  if (occurrences === null) {
    return { total: 0, byStatus: {}, emptyReason: AUTOMATION_EMPTY_STATES.no_history };
  }
  if (occurrences.length === 0) {
    return { total: 0, byStatus: {}, emptyReason: "No recorded occurrences yet." };
  }
  const byStatus: Record<string, number> = {};
  for (const occurrence of occurrences) {
    const status = occurrence.status && occurrence.status.trim() ? occurrence.status : "unknown";
    byStatus[status] = (byStatus[status] ?? 0) + 1;
  }
  return { total: occurrences.length, byStatus, emptyReason: null };
}

// ---------- readiness note ----------

export const AUTOMATION_READINESS_NOTE =
  "Automation records are shown as recorded — no prediction, no scheduling intelligence, no automatic enabling or execution. This surface prepares for future automation capability; it does not add any.";
