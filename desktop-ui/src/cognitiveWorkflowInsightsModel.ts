// ZCODE-AMCA-STUDIO-V0.8.0-COGNITIVE-WORKFLOW-UX-ADVANCED-INSIGHTS-01 — pure model.
//
// Record-based insights layered on the accepted Cognitive Workflow
// transparency panel. Everything counts or quotes existing records:
// - summary card: counts only — no productivity score, no success probability;
// - stage expansion: name/status/evidence source/timestamp/error — no
//   inferred explanations;
// - decision transparency: "why this resource appears" lines restating
//   recorded fields (ref prefix, recorded capability/decision fields);
//   nothing is generated;
// - history: per-project grouping of recorded runs — no ranking, no
//   "best workflow", no performance score;
// - the order's three honest empty states verbatim.

import type { WorkflowStage, WorkflowRunRecord } from "./cognitiveWorkflowUxModel";
import type { KnowledgeContextItem } from "./knowledgeContextModel";

export const EMPTY_NO_WORKFLOWS = "No workflow records available yet.";
export const EMPTY_NO_STAGE_DETAILS =
  "Stage details are unavailable because this run does not contain lifecycle records.";
export const EMPTY_NO_INSIGHTS = "No derived insights are available from recorded data.";

// ---------- 1. Workflow summary card (counts only) ----------

export interface WorkflowSummaryCard {
  currentStage: string;
  completedStages: number;
  totalStages: number;
  blockedState: string | null;
  knowledgeCount: number;
  resourceCount: number;
  artifactCount: number;
}

export function workflowSummaryCard(input: {
  stages: WorkflowStage[];
  knowledgeItems: KnowledgeContextItem[];
  resourceCount: number;
  artifactCount: number;
}): WorkflowSummaryCard {
  const { stages, knowledgeItems, resourceCount, artifactCount } = input;
  const completed = stages.filter((stage) => stage.state === "done").length;
  const failedStage = stages.find((stage) => stage.state === "failed");
  const activeStage = stages.find((stage) => stage.state === "active");
  const lastDone = [...stages].reverse().find((stage) => stage.state === "done");
  return {
    currentStage: failedStage ? `${failedStage.label} — failed` : activeStage ? `${activeStage.label} — active` : lastDone ? `${lastDone.label} — recorded` : "Not recorded",
    completedStages: completed,
    totalStages: stages.length,
    blockedState: failedStage ? failedStage.label : null,
    knowledgeCount: knowledgeItems.length,
    resourceCount,
    artifactCount,
  };
}

// ---------- 2. Stage detail expansion (evidence only) ----------

export interface StageDetail {
  name: string;
  status: string;
  evidenceSource: string;
  timestamp: string | null;
  error: string | null;
}

/**
 * Expansion payload for one stage. The evidence source names WHERE the
 * state came from (desktop run registry vs not recorded); explanations are
 * never generated.
 */
export function stageDetail(stage: WorkflowStage): StageDetail {
  return {
    name: stage.label,
    status: stage.state === "not_recorded" ? "Not recorded" : stage.state,
    evidenceSource:
      stage.state === "not_recorded"
        ? "No desktop record carries this stage; the orchestrator-side record is not exposed yet."
        : "Desktop run registry record",
    timestamp: stage.timestamp,
    error: stage.detail && stage.state === "failed" ? stage.detail : null,
  };
}

// ---------- 3. Decision transparency (recorded fields only) ----------

export interface ResourceWhyLine {
  ref: string;
  reasons: string[];
}

export interface RecordedResourceFields {
  ref: string;
  kind: "model" | "agent";
  /** Recorded decision/reason field from the run or selection trace, if present. */
  decisionReason?: string | null;
  /** Recorded capability/collaboration note, if present. */
  capabilityNote?: string | null;
  /** Whether the recorded governance note exists (exact approval before network). */
  governanceRecorded?: boolean;
}

/**
 * "Why this resource appears" — each line restates a recorded field.
 * A ref's own prefix is recorded; a decision/capability note is included
 * verbatim when the record carries one; the governance line is included
 * only when governance is actually recorded. No explanation is generated.
 */
export function whyThisResource(resource: RecordedResourceFields): ResourceWhyLine {
  const reasons: string[] = [];
  if (resource.kind === "model") {
    reasons.push(resource.ref.startsWith("ollama:") ? "Local model selected (recorded ref prefix)" : "Governance-approved provider model (exact approval before any network call)");
  } else {
    reasons.push("Recorded agent participant for this run");
  }
  if (resource.decisionReason && resource.decisionReason.trim()) {
    reasons.push(`Recorded decision: ${resource.decisionReason.trim()}`);
  }
  if (resource.capabilityNote && resource.capabilityNote.trim()) {
    reasons.push(`Recorded capability match: ${resource.capabilityNote.trim()}`);
  }
  if (resource.governanceRecorded) {
    reasons.push("Governance status recorded: exact approval before any network call");
  }
  return { ref: resource.ref, reasons };
}

// ---------- 4. Workflow history (grouping, no ranking) ----------

export interface HistoryEntry {
  workflowId: string;
  status: string;
  createdAt: string | null;
  artifactsCreated: number;
  recordedFailure: string | null;
}

export interface WorkflowHistoryGroup {
  projectId: string;
  entries: HistoryEntry[];
  /** Recorded failure count — a count, not a score. */
  failureCount: number;
}

export function workflowHistory(
  runs: Array<WorkflowRunRecord & { project_id?: string; artifact_count?: number }>,
): WorkflowHistoryGroup[] {
  const groups = new Map<string, WorkflowHistoryGroup>();
  for (const record of runs) {
    const key = record.project_id ?? "unassigned";
    let group = groups.get(key);
    if (!group) {
      group = { projectId: key, entries: [], failureCount: 0 };
      groups.set(key, group);
    }
    const failure = record.status === "failed" ? record.error ?? "Recorded failure (no error text recorded)" : null;
    if (failure) group.failureCount += 1;
    group.entries.push({
      workflowId: record.run_id,
      status: record.status,
      createdAt: record.created_at ?? null,
      artifactsCreated: typeof record.artifact_count === "number" ? record.artifact_count : 0,
      recordedFailure: failure,
    });
  }
  return [...groups.values()];
}

/** Render guard: history lines are counts and statuses — no ranking ever. */
export function historyNote(): string {
  return "Grouped by project from existing run records — statuses and counts only; no ranking, no best-workflow selection, no performance score.";
}
