// ZCODE-AMCA-STUDIO-V0.8.0-WORKSPACE-PRODUCTIVITY-ASSISTANCE-UX-FOUNDATION-01 — pure model.
//
// Transparent productivity assistance assembled ONLY from existing
// records (run registry, artifact registry, approval records, recorded
// composer selections). Rules recorded by the order:
// - activity summary states the boundary "Workspace activity summary from
//   recorded events only.";
// - the "Continue work" surface lists recorded facts (recently active
//   projects, unfinished workflows, available artifacts, pending reviews)
//   with NO recommended next action, NO AI prioritization, NO urgency
//   ranking — entries render in recorded recency order;
// - context resume visibility is preparation only: it shows what a
//   previous workflow carried and never loads context automatically;
// - search integration points at existing search over recorded records —
//   no semantic recommendation, no hidden retrieval;
// - canonical wording and honest empty states throughout.

import type { RunJob } from "./types";

export const ACTIVITY_SUMMARY_BOUNDARY =
  "Workspace activity summary from recorded events only.";

export const CONTINUATION_NOTE =
  "Listed in recorded recency order — no recommended next action, no AI prioritization, no urgency ranking.";

export const RESUME_PREPARATION_NOTE =
  "Preparation only — nothing is loaded automatically. Open the recorded items yourself.";

export const SEARCH_INTEGRATION_NOTE =
  "Entry points search existing records only — no semantic recommendation, no hidden retrieval.";

export const PRODUCTIVITY_EMPTY_STATES = {
  no_workflows: "No workflow records available yet.",
  no_projects: "No recently active projects recorded.",
  no_unfinished: "No unfinished workflows recorded.",
  no_pending_reviews: "No pending reviews recorded.",
} as const;

// ---------- 1. Workspace activity summary (recorded events) ----------

export interface WorkspaceActivitySummary {
  recentWorkflows: Array<{ id: string; status: string; timestamp: string | null }>;
  recentArtifacts: Array<{ id: string; title: string; timestamp: string | null }>;
  recentKnowledgeAttachments: number;
  pendingReviews: number;
  reusableWorkflowExamples: number;
  boundary: string;
}

const RECENCY_CUTOFF = (iso: string | null | undefined): boolean =>
  Boolean(iso && String(iso).trim());

/** Most recent recorded items first; items without timestamps excluded from "recent" (honest). */
export function workspaceActivitySummary(input: {
  runs: RunJob[];
  artifacts: Array<{ artifact_id: string; title: string; created_at?: string | null }>;
  knowledgeAttachmentCount: number;
  pendingReviews: number;
  completedWorkflowCount: number;
  limit?: number;
}): WorkspaceActivitySummary {
  const limit = input.limit ?? 5;
  const recentWorkflows = input.runs
    .filter((run) => RECENCY_CUTOFF(run.created_at))
    .sort((a, b) => (a.created_at! < b.created_at! ? 1 : -1))
    .slice(0, limit)
    .map((run) => ({ id: run.run_id, status: run.status, timestamp: run.created_at }));
  const recentArtifacts = input.artifacts
    .filter((artifact) => RECENCY_CUTOFF(artifact.created_at))
    .sort((a, b) => ((a.created_at ?? "") < (b.created_at ?? "") ? 1 : -1))
    .slice(0, limit)
    .map((artifact) => ({ id: artifact.artifact_id, title: artifact.title, timestamp: artifact.created_at ?? null }));
  return {
    recentWorkflows,
    recentArtifacts,
    recentKnowledgeAttachments: input.knowledgeAttachmentCount,
    pendingReviews: input.pendingReviews,
    reusableWorkflowExamples: input.completedWorkflowCount,
    boundary: ACTIVITY_SUMMARY_BOUNDARY,
  };
}

// ---------- 2. Continue work surface (recorded facts, no ranking) ----------

export interface ContinuationEntry {
  projectId: string | null;
  workflowId: string;
  status: string;
  taskType: string | null;
  timestamp: string | null;
  hasArtifacts: boolean;
}

/** Unfinished workflows in recorded recency order — never prioritized. */
export function continuationEntries(
  runs: RunJob[],
): ContinuationEntry[] {
  const unfinished = runs
    .filter((run) => run.status === "queued" || run.status === "running" || run.status === "cancelling" || run.status === "partial")
    .map((run) => ({
      projectId: run.project_id,
      workflowId: run.run_id,
      status: run.status,
      taskType: taskTypeFromGoal(run.goal),
      timestamp: run.created_at ?? null,
      hasArtifacts: false,
    }));
  return [...unfinished].sort((a, b) => {
    const at = a.timestamp ?? "9999-12-31T23:59:59.999Z";
    const bt = b.timestamp ?? "9999-12-31T23:59:59.999Z";
    if (at === bt) return 0;
    return at < bt ? 1 : -1;
  });
}

/** Task type parsed from the recorded composer goal tag, or null. */
export function taskTypeFromGoal(goal: string | null | undefined): string | null {
  const match = /\[Task type:\s*([^\]]+)\]/.exec(goal ?? "");
  return match ? match[1].trim() : null;
}

export const CONTINUATION_EMPTY_STATES = {
  no_projects: "No recently active projects recorded.",
  no_unfinished: "No unfinished workflows recorded.",
  no_pending_reviews: "No pending reviews recorded.",
} as const;

// ---------- 3. Context resume visibility (preparation only) ----------

export interface ResumeContext {
  projectId: string | null;
  workflowId: string;
  taskType: string | null;
  attachedKnowledge: number;
  artifacts: string[];
  modelsAgents: string[];
  note: string;
}

/** What a previous workflow carried — displayed, never auto-loaded. */
export function resumeContext(
  run: RunJob,
  knowledgeItems: KnowledgeCountLike[],
  agentIds: string[],
): ResumeContext {
  return {
    projectId: run.project_id,
    workflowId: run.run_id,
    taskType: taskTypeFromGoal(run.goal),
    attachedKnowledge: knowledgeItems.length,
    artifacts: [],
    modelsAgents: [run.model].filter((ref): ref is string => Boolean(ref)).concat(agentIds),
    note: RESUME_PREPARATION_NOTE,
  };
}

type KnowledgeCountLike = unknown;

// ---------- 4. Workspace search integration (existing records) ----------

export interface SearchEntryPoint {
  label: string;
  target: "overview" | "artifacts" | "agents" | "settings" | "validation";
  recordKind: string;
}

/** Entry points into existing search/list surfaces — no new search backend. */
export const SEARCH_ENTRY_POINTS: SearchEntryPoint[] = [
  { label: "Search projects", target: "overview", recordKind: "recorded studio projects" },
  { label: "Search workflows", target: "agents", recordKind: "recorded run registry entries" },
  { label: "Search artifacts", target: "artifacts", recordKind: "recorded artifact registry entries" },
  { label: "Search knowledge", target: "artifacts", recordKind: "recorded knowledge surface entries" },
  { label: "Search models", target: "settings", recordKind: "recorded model hub entries" },
];
