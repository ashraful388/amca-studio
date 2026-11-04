// BETA-B13 Project Lifecycle UX — pure presentation model (no fetch, no
// DOM). This build exposes NO dedicated lifecycle/milestone backend, so
// milestones are EMPIRICAL stages computed strictly from existing records
// (project registry, run registry, artifact registry, chat counts) — the
// UI discloses that. No AI confidence, no subjective scoring, no
// predicted completion dates, no inference.

import type { RunJob, StudioProjectSummary, ArtifactMeta } from "./types";

// ---------- 1. Empirical milestones (order scope 2) ----------

export interface MilestoneEvidence {
  source: string;
  detail: string;
  at: string | null;
}

export interface LifecycleStage {
  key: string;
  label: string;
  status: "done" | "pending";
  evidence: MilestoneEvidence | null;
}

const TERMINAL_SUCCEEDED = new Set(["succeeded", "partial"]);

export interface ProjectLifecycleInput {
  project: StudioProjectSummary;
  runs: RunJob[];
  artifacts: ArtifactMeta[];
  artifactsComplete: boolean;
}

function firstTimestamp(runs: RunJob[]): string | null {
  let earliest: string | null = null;
  for (const run of runs) {
    const at = run.started_at ?? run.created_at;
    if (!earliest || at < earliest) earliest = at;
  }
  return earliest;
}

export function projectLifecycle(input: ProjectLifecycleInput): {
  stages: LifecycleStage[];
  completed: number;
  total: number;
  percent: number;
  disclosure: string;
} {
  const { project, runs, artifacts, artifactsComplete } = input;
  const projectRuns = runs.filter((run) => run.project_id === project.project_id);
  const projectArtifacts = artifacts.filter((asset) => asset.project_id === project.project_id);
  const firstRunAt = firstTimestamp(projectRuns);
  const firstCompletedRun = projectRuns.find((run) => TERMINAL_SUCCEEDED.has(run.status));
  const stages: LifecycleStage[] = [
    {
      key: "project_created",
      label: "Project created",
      status: "done",
      evidence: { source: "project registry", detail: project.name, at: project.created_at },
    },
    {
      key: "chat_started",
      label: "First chat started",
      status: project.chat_count > 0 ? "done" : "pending",
      evidence: project.chat_count > 0
        ? { source: "project registry", detail: `${project.chat_count} chat${project.chat_count === 1 ? "" : "s"} recorded`, at: null }
        : null,
    },
    {
      key: "first_run",
      label: "First governed run",
      status: firstRunAt ? "done" : "pending",
      evidence: firstRunAt ? { source: "run registry", detail: `${projectRuns.length} run${projectRuns.length === 1 ? "" : "s"} recorded`, at: firstRunAt } : null,
    },
    {
      key: "workflow_completed",
      label: "Workflow completed",
      status: firstCompletedRun ? "done" : "pending",
      evidence: firstCompletedRun
        ? { source: "run registry", detail: `run ${firstCompletedRun.run_id.slice(0, 12)} finished ${firstCompletedRun.status}`, at: firstCompletedRun.finished_at ?? firstCompletedRun.created_at }
        : null,
    },
    {
      key: "artifact_produced",
      label: "Artifact produced",
      status: artifactsComplete && projectArtifacts.length > 0 ? "done" : "pending",
      evidence: artifactsComplete
        ? projectArtifacts.length > 0
          ? { source: "artifact registry", detail: `${projectArtifacts.length} artifact${projectArtifacts.length === 1 ? "" : "s"}`, at: null }
          : null
        : { source: "artifact registry", detail: "artifact list not loaded for this project", at: null },
    },
  ];
  const completed = stages.filter((stage) => stage.status === "done" && !(stage.key === "artifact_produced" && !artifactsComplete)).length;
  const evaluated = artifactsComplete ? stages.length : stages.length - 1;
  return {
    stages,
    completed,
    total: evaluated,
    percent: evaluated > 0 ? Math.round((completed / evaluated) * 100) : 0,
    disclosure: "Milestones are empirical stages computed from existing records — this build has no dedicated lifecycle/milestone backend, so progress is evidence-backed counts only.",
  };
}

// ---------- 2. Health states (order scope 5) ----------

export type ProjectHealth = "not_started" | "active" | "completed" | "blocked";

export interface HealthView {
  state: ProjectHealth;
  reason: string;
}

export function projectHealth(lifecycle: {
  stages: LifecycleStage[];
  completed: number;
  total: number;
}, runs: RunJob[]): HealthView {
  const projectRuns = runs;
  const workEvidence = lifecycle.stages.some(
    (stage) => stage.status === "done" && stage.key !== "project_created",
  );
  const allDone = lifecycle.total > 0 && lifecycle.completed === lifecycle.total;
  const failedRuns = projectRuns.filter((run) => run.status === "failed");
  const latestFailed = failedRuns
    .slice()
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0];
  if (!workEvidence) {
    return { state: "not_started", reason: "No lifecycle records for this project yet — no chats, runs, or artifacts recorded." };
  }
  if (latestFailed) {
    return {
      state: "blocked",
      reason: `Recorded issue: run ${latestFailed.run_id.slice(0, 12)} failed${latestFailed.error ? ` — ${latestFailed.error.slice(0, 80)}` : ""}.`,
    };
  }
  if (allDone) {
    return { state: "completed", reason: "All recorded milestones are complete." };
  }
  return { state: "active", reason: "Milestones in progress." };
}

// ---------- 3. Lifecycle evidence panel (order scope 4) ----------

export interface EvidenceLine {
  label: string;
  recorded: boolean;
  detail: string | null;
}

export function completionEvidence(input: ProjectLifecycleInput): EvidenceLine[] {
  const { runs, artifacts, artifactsComplete } = input;
  const projectRuns = runs.filter((run) => run.project_id === input.project.project_id);
  const succeeded = projectRuns.filter((run) => TERMINAL_SUCCEEDED.has(run.status));
  const projectArtifacts = artifacts.filter((asset) => asset.project_id === input.project.project_id);
  return [
    {
      label: "Workflow completed",
      recorded: succeeded.length > 0,
      detail: succeeded.length > 0 ? `${succeeded.length} succeeded run${succeeded.length === 1 ? "" : "s"}` : null,
    },
    {
      label: "Artifact created",
      recorded: artifactsComplete && projectArtifacts.length > 0,
      detail: artifactsComplete
        ? projectArtifacts.length > 0 ? `${projectArtifacts.length} artifact${projectArtifacts.length === 1 ? "" : "s"}` : null
        : "artifact list not loaded",
    },
    {
      label: "Validation passed",
      recorded: succeeded.some((run) => run.status === "succeeded"),
      detail: succeeded.some((run) => run.status === "succeeded")
        ? "run finished with full completion status"
        : null,
    },
    {
      label: "User confirmation recorded",
      recorded: false,
      detail: null,
    },
  ];
}
