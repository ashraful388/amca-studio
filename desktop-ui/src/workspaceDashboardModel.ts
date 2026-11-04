// Z1 Workspace Dashboard — pure presentation model (no fetch, no DOM).
// Aggregates EXISTING backend documents (dashboard runs, conversations,
// nano agents, agent fabric, artifacts) into the dashboard's Current Task,
// Active Agents, and Recent Artifacts summaries. No new backend semantics.

import type { RunJob, ArtifactMeta, AgentFabricState } from "./types";

export const ACTIVE_RUN_STATES = ["queued", "running", "cancelling"];

export function activeRuns(runs: RunJob[]): RunJob[] {
  return runs.filter((run) => ACTIVE_RUN_STATES.includes(run.status));
}

function newestFirst(runs: RunJob[]): RunJob[] {
  return runs.slice().sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
}

export type CurrentTaskSource = "active_run" | "recent_run" | "none";

export interface CurrentTaskView {
  run: RunJob | null;
  source: CurrentTaskSource;
}

// Conversation active_run_id values reference runs inside the same
// /api/dashboard document, so the live branch covers them; no separate
// conversation-linked lookup is needed.
export function deriveCurrentTask(runs: RunJob[]): CurrentTaskView {
  const live = newestFirst(activeRuns(runs));
  if (live.length) return { run: live[0], source: "active_run" };
  const recent = newestFirst(runs)[0];
  return recent ? { run: recent, source: "recent_run" } : { run: null, source: "none" };
}

export interface NanoActivityLike {
  assignments: Array<{ nano_id: string; task_id: string; purpose: string; depth: number; expected_cost: number | null }>;
  statuses: Record<string, string>;
}

const NANO_LIVE_STATES = new Set(["proposed", "authorized", "queued", "running", "waiting", "verifying"]);

export interface AgentActivitySummary {
  fabricActive: number;
  nanoTotal: number;
  nanoLive: number;
  nanoCompleted: number;
  liveRuns: number;
}

export function deriveAgentActivity(
  nano: NanoActivityLike,
  fabricCounts: AgentFabricState["counts"],
  runs: RunJob[],
): AgentActivitySummary {
  let nanoLive = 0;
  let nanoCompleted = 0;
  for (const assignment of nano.assignments) {
    const status = nano.statuses[assignment.nano_id] ?? "";
    if (NANO_LIVE_STATES.has(status)) nanoLive += 1;
    if (status === "completed" || status === "retired") nanoCompleted += 1;
  }
  return {
    fabricActive: fabricCounts.active,
    nanoTotal: nano.assignments.length,
    nanoLive,
    nanoCompleted,
    liveRuns: activeRuns(runs).length,
  };
}

export interface ArtifactSummaryItem {
  artifact_id: string;
  title: string;
  artifact_type: string;
  current_version: number;
}

// ArtifactMeta carries no timestamps; the backend list order is the
// authoritative recency signal, so "recent" is the tail of the list.
export function recentArtifacts(artifacts: ArtifactMeta[], limit = 6): ArtifactSummaryItem[] {
  return artifacts
    .slice(-limit)
    .reverse()
    .map((artifact) => ({
      artifact_id: artifact.artifact_id,
      title: artifact.title,
      artifact_type: artifact.artifact_type,
      current_version: artifact.current_version,
    }));
}

export interface DashboardMetrics {
  studioProjects: number;
  liveRuns: number;
  totalRuns: number;
  activeAgents: number;
  artifactCount: number;
}

export function deriveDashboardMetrics(input: {
  studioProjects: number;
  runs: RunJob[];
  activity: AgentActivitySummary;
  artifactCount: number;
}): DashboardMetrics {
  return {
    studioProjects: input.studioProjects,
    liveRuns: input.activity.liveRuns,
    totalRuns: input.runs.length,
    activeAgents: input.activity.nanoLive + input.activity.fabricActive,
    artifactCount: input.artifactCount,
  };
}
