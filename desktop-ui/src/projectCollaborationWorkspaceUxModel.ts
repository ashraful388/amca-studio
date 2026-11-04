// ZCODE-AMCA-STUDIO-V0.8.0-PROJECT-COLLABORATION-WORKSPACE-UX-01 — pure model.
//
// Project-level collaboration and reusable-workflow visibility over
// EXISTING records only (run registry, artifact registry, approval
// records, recorded composer selections). Rules recorded by the order:
// - overview, activity feed, reusable-pattern visibility, and review
//   summary all derive from records; nothing is inferred;
// - boundary statements rendered verbatim: "Recorded project activity
//   only — no productivity score or project quality ranking." and
//   "Summary of recorded project evidence only.";
// - reusable workflow visibility shows previously executed patterns
//   (task types, model/agent participation, artifact outputs) — NO
//   cloning engine, automatic recommendations, or optimization ranking;
// - activity feed is newest first with recorded timestamps only;
//   undated records preserved honestly;
// - no multi-user/collaboration-service claims anywhere.

import type { RunJob } from "./types";

export const COLLABORATION_BOUNDARIES = {
  overview: "Recorded project activity only — no productivity score or project quality ranking.",
  review: "Summary of recorded project evidence only.",
  noMultiUser: "Single-operator workspace — no team management, no collaboration service, nothing multi-user.",
} as const;

export const COLLABORATION_EMPTY_STATES = {
  no_workflows: "No workflow records available yet.",
  no_patterns: "No reusable workflow patterns recorded yet — patterns appear after workflows complete.",
  no_artifacts: "No artifacts recorded for this project yet.",
  no_review: "No recorded project evidence to summarize yet.",
} as const;

// ---------- 1. Project collaboration overview ----------

export interface ProjectCollaborationOverview {
  purpose: string | null;
  workflowsExecuted: number;
  artifactsGenerated: number;
  knowledgeUsed: number;
  participatingModels: string[];
  participatingAgents: string[];
  boundary: string;
}

/** Task type parsed from the recorded composer goal tag, or null. */
export function recordedTaskType(goal: string | null | undefined): string | null {
  const match = /\[Task type:\s*([^\]]+)\]/.exec(goal ?? "");
  return match ? match[1].trim() : null;
}

export function projectCollaborationOverview(input: {
  purpose: string | null;
  runs: RunJob[];
  artifactCount: number;
  knowledgeAttachmentCount: number;
  agentIds: string[];
}): ProjectCollaborationOverview {
  const models = [...new Set(input.runs.map((run) => run.model).filter((ref) => Boolean(ref)))];
  const agents = [...new Set(input.agentIds)];
  return {
    purpose: input.purpose && input.purpose.trim() ? input.purpose.trim() : null,
    workflowsExecuted: input.runs.length,
    artifactsGenerated: input.artifactCount,
    knowledgeUsed: input.knowledgeAttachmentCount,
    participatingModels: models,
    participatingAgents: agents,
    boundary: COLLABORATION_BOUNDARIES.overview,
  };
}

// ---------- 2. Project activity feed (unified, newest first) ----------

export interface ProjectActivityEvent {
  kind: "workflow" | "artifact" | "knowledge" | "approval" | "automation";
  label: string;
  timestamp: string | null;
  detail: string;
}

export function projectActivityFeed(input: {
  runs: Array<RunJob & { agentIds?: string[]; knowledgeCount?: number }>;
  artifacts: Array<{ artifact_id: string; title: string; created_at?: string | null }>;
  approvals: Array<{ id: string; action: string; status: string; created_at: string | null }>;
  automationOccurrences: Array<{ automationId: string; status: string; scheduled_for: string | null }>;
}): ProjectActivityEvent[] {
  const events: ProjectActivityEvent[] = [];
  for (const run of input.runs) {
    events.push({
      kind: "workflow",
      label: "Workflow",
      timestamp: run.created_at ?? null,
      detail: `${run.run_id.slice(0, 12)}… · ${run.status}${run.error ? ` — ${run.error}` : ""}`,
    });
  }
  for (const artifact of input.artifacts) {
    events.push({
      kind: "artifact",
      label: "Artifact created",
      timestamp: artifact.created_at ?? null,
      detail: `${artifact.title} (${artifact.artifact_id.slice(0, 12)}…)`,
    });
  }
  for (const knowledge of input.runs.flatMap((run) =>
    Array.from({ length: run.knowledgeCount ?? 0 }, (_, index) => `run ${run.run_id.slice(0, 8)}… #${index + 1}`),
  )) {
    events.push({ kind: "knowledge", label: "Knowledge attached", timestamp: null, detail: `${knowledge} — explicitly attached by the user` });
  }
  for (const approval of input.approvals) {
    events.push({
      kind: "approval",
      label: "Approval event",
      timestamp: approval.created_at,
      detail: `${approval.action} · ${approval.status}`,
    });
  }
  for (const occurrence of input.automationOccurrences) {
    events.push({
      kind: "automation",
      label: "Automation occurrence",
      timestamp: occurrence.scheduled_for,
      detail: `${occurrence.automationId.slice(0, 12)}… · ${occurrence.status}`,
    });
  }
  return [...events].sort((a, b) => {
    const at = a.timestamp ?? "9999-12-31T23:59:59.999Z";
    const bt = b.timestamp ?? "9999-12-31T23:59:59.999Z";
    if (at === bt) return 0;
    return at < bt ? 1 : -1;
  });
}

// ---------- 3. Reusable workflow visibility (recorded patterns) ----------

export interface ReusableWorkflowPattern {
  taskType: string;
  workflows: string[];
  models: string[];
  agentCount: number;
  artifactCount: number;
}

export interface ReusablePatternSummary {
  patterns: ReusableWorkflowPattern[];
  emptyReason: string | null;
  note: string;
}

/**
 * Group completed workflows by their recorded task type. A pattern is a
 * recorded fact (task type + which workflows used it + recorded
 * participants/outputs) — visibility only; the surface offers no cloning,
 * recommendations, or ranking.
 */
export function reusableWorkflowPatterns(
  runs: Array<(RunJob & { artifactCount?: number; agentCount?: number }) | null>,
): ReusablePatternSummary {
  const completed = (runs ?? []).filter(
    (run): run is RunJob & { artifactCount?: number; agentCount?: number } =>
      Boolean(run) && Boolean(run?.status) && (run!.status === "succeeded" || run!.status === "partial"),
  );
  if (completed.length === 0) {
    return { patterns: [], emptyReason: COLLABORATION_EMPTY_STATES.no_patterns, note: "Visibility only — no cloning engine, no automatic recommendations, no optimization ranking." };
  }
  const byType = new Map<string, ReusableWorkflowPattern>();
  for (const run of completed) {
    const taskType = recordedTaskType(run.goal) ?? "Untyped";
    let pattern = byType.get(taskType);
    if (!pattern) {
      pattern = { taskType, workflows: [], models: [], agentCount: 0, artifactCount: 0 };
      byType.set(taskType, pattern);
    }
    pattern.workflows.push(run.run_id);
    if (run.model && !pattern.models.includes(run.model)) pattern.models.push(run.model);
    pattern.artifactCount += run.artifactCount ?? 0;
    pattern.agentCount += run.agentCount ?? 0;
  }
  return {
    patterns: [...byType.values()],
    emptyReason: null,
    note: "Visibility only — no cloning engine, no automatic recommendations, no optimization ranking.",
  };
}

// ---------- 4. Project review summary (recorded evidence) ----------

export interface ProjectReviewSummary {
  completedWorkflows: number;
  failedWorkflows: number;
  pendingGovernanceActions: number;
  availableArtifacts: number;
  availableKnowledgeReferences: number;
  boundary: string;
}

export function projectReviewSummary(input: {
  runs: RunJob[];
  pendingGovernanceActions: number;
  availableArtifacts: number;
  availableKnowledgeReferences: number;
}): ProjectReviewSummary {
  return {
    completedWorkflows: input.runs.filter((run) => run.status === "succeeded" || run.status === "partial").length,
    failedWorkflows: input.runs.filter((run) => run.status === "failed" || run.status === "cancelled").length,
    pendingGovernanceActions: input.pendingGovernanceActions,
    availableArtifacts: input.availableArtifacts,
    availableKnowledgeReferences: input.availableKnowledgeReferences,
    boundary: COLLABORATION_BOUNDARIES.review,
  };
}

// ---------- empty-state passthrough for the surface ----------

export const COLLABORATION_NO_WORKFLOWS = COLLABORATION_EMPTY_STATES.no_workflows;
export const COLLABORATION_NO_ARTIFACTS = COLLABORATION_EMPTY_STATES.no_artifacts;
export const COLLABORATION_NO_REVIEW = COLLABORATION_EMPTY_STATES.no_review;
