// BETA-B11 Professional Workspace UX — pure presentation model (no fetch,
// no DOM). Exposes the unified command center from EXISTING workspace
// records only: projects, runs, agents (fabric + nano), models, artifacts,
// automations, governance approvals. Memory has no endpoint in this build,
// so its card reports "Not recorded" instead of an inferred value. No
// telemetry, no analytics service, no productivity/efficiency/quality
// scores, no payload contents in the timeline.

import type { RunJob, ApprovalRequest, ArtifactMeta } from "./types";

// ---------- 1. Workspace Command Center overview cards ----------

export interface OverviewCard {
  id: "projects" | "active_runs" | "agents" | "models" | "artifacts" | "memory" | "automations" | "governance";
  label: string;
  value: number | null;
  displayValue: string;
  evidenceSource: string;
  reason: string | null;
  navView: string | null;
}

export interface CommandCenterInput {
  studioProjects: number;
  runs: RunJob[];
  // Distinct agent participants recorded across the run registry
  // (participantsFromRun); null when no runs exist, so the card shows
  // "Not recorded" rather than a false zero.
  agentParticipants: number | null;
  availableModels: number;
  artifacts: ArtifactMeta[];
  automations: { total: number; enabled: number };
  approvals: ApprovalRequest[];
}

const ACTIVE_RUN_STATES = ["queued", "running", "cancelling"];

export function commandCenterCards(input: CommandCenterInput): OverviewCard[] {
  const activeRuns = input.runs.filter((run) => ACTIVE_RUN_STATES.includes(run.status));
  return [
    {
      id: "projects",
      label: "Projects",
      value: input.studioProjects,
      displayValue: String(input.studioProjects),
      evidenceSource: "Studio project registry",
      reason: null,
      navView: "overview",
    },
    {
      id: "active_runs",
      label: "Active runs",
      value: activeRuns.length,
      displayValue: String(activeRuns.length),
      evidenceSource: "Run registry (queued/running/cancelling)",
      reason: null,
      navView: "dashboard",
    },
    {
      id: "agents",
      label: "Agents",
      value: input.agentParticipants,
      displayValue: input.agentParticipants === null ? "Not recorded" : String(input.agentParticipants),
      evidenceSource: "Run registry — recorded agent participants",
      reason: input.agentParticipants === null ? "No runs recorded yet — agent participation appears with the first run." : null,
      navView: "agents",
    },
    {
      id: "models",
      label: "Models",
      value: input.availableModels,
      displayValue: String(input.availableModels),
      evidenceSource: "Model Hub available models (local + configured BYOK + direct)",
      reason: null,
      navView: "settings",
    },
    {
      id: "artifacts",
      label: "Artifacts",
      value: input.artifacts.length,
      displayValue: String(input.artifacts.length),
      evidenceSource: "Artifact registry (loaded project scope)",
      reason: input.artifacts.length ? null : "Artifacts appear after completed tasks.",
      navView: "artifacts",
    },
    {
      id: "memory",
      label: "Memory",
      value: null,
      displayValue: "Not recorded",
      evidenceSource: "No memory activity summary endpoint available.",
      reason: "No memory activity summary endpoint available.",
      navView: null,
    },
    {
      id: "automations",
      label: "Automations",
      value: input.automations.total,
      displayValue: String(input.automations.total),
      evidenceSource: "Automation definitions registry",
      reason: null,
      navView: "automations",
    },
    {
      id: "governance",
      label: "Governance events",
      value: input.approvals.length,
      displayValue: String(input.approvals.length),
      evidenceSource: "Approval requests (project-scoped records)",
      reason: input.approvals.length
        ? null
        : "No approval requests recorded for the selected project.",
      navView: "approvals",
    },
  ];
}

// ---------- 2. Unified activity timeline ----------

export type ActivityKind = "run" | "artifact" | "governance" | "automation";

export interface TimelineEntry {
  key: string;
  kind: ActivityKind;
  label: string;
  createdAt: string | null;
  detail: string;
  navView: string | null;
}

export function unifiedTimeline(input: {
  runs: RunJob[];
  artifacts: ArtifactMeta[];
  approvals: ApprovalRequest[];
  automations: Array<{ id: string; name: string; last_run_at: string | null }>;
  limit?: number;
}): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  for (const run of input.runs) {
    entries.push({
      key: `run:${run.run_id}`,
      kind: "run",
      label: run.goal ? `Run — ${run.goal.slice(0, 80)}` : `Run ${run.run_id.slice(0, 8)}`,
      createdAt: run.created_at,
      detail: `Run registry · ${run.status} · ${run.mode}${run.project_id ? ` · project ${run.project_id.slice(0, 8)}` : ""}`,
      navView: "dashboard",
    });
  }
  for (const artifact of input.artifacts) {
    entries.push({
      key: `artifact:${artifact.artifact_id}`,
      kind: "artifact",
      label: artifact.title || `Artifact ${artifact.artifact_id.slice(0, 8)}`,
      createdAt: null,
      detail: `Artifact registry · ${artifact.artifact_type} · v${artifact.current_version}${artifact.project_id ? ` · project ${artifact.project_id.slice(0, 8)}` : ""}`,
      navView: "artifacts",
    });
  }
  for (const approval of input.approvals) {
    entries.push({
      key: `governance:${approval.id}`,
      kind: "governance",
      label: approval.action,
      createdAt: approval.created_at,
      detail: `Approvals · ${approval.status} · risk ${approval.risk}`,
      navView: "approvals",
    });
  }
  for (const automation of input.automations) {
    entries.push({
      key: `automation:${automation.id}`,
      kind: "automation",
      label: automation.name,
      createdAt: automation.last_run_at,
      detail: "Automations · definition record",
      navView: "automations",
    });
  }
  const dated = entries.filter((entry) => entry.createdAt !== null);
  const undated = entries.filter((entry) => entry.createdAt === null);
  const sortKey = (value: string | null) => {
    const parsed = value ? Date.parse(value) : NaN;
    return Number.isNaN(parsed) ? 0 : parsed;
  };
  dated.sort((a, b) => sortKey(b.createdAt) - sortKey(a.createdAt));
  undated.sort((a, b) => a.label.localeCompare(b.label));
  const merged = [...dated, ...undated];
  return merged.slice(0, input.limit ?? 50);
}

// ---------- 3. Cross-module trace navigation ----------

export interface TraceStep {
  stage: string;
  targetView: string | null;
  state: "recorded" | "not_recorded";
  detail: string;
}

export function crossModuleTrace(input: {
  project: { project_id: string; name: string } | null;
  run: RunJob | null;
  artifact: ArtifactMeta | null;
  agentParticipants: Array<{ ref: string; provider: string; model: string; calls: number }>;
}): TraceStep[] {
  const steps: TraceStep[] = [];
  if (input.project) {
    steps.push({
      stage: "Project",
      targetView: "overview",
      state: "recorded",
      detail: `${input.project.name} · ${input.project.project_id.slice(0, 8)}`,
    });
  } else {
    steps.push({ stage: "Project", targetView: null, state: "not_recorded", detail: "No project selected." });
  }
  if (input.run) {
    steps.push({
      stage: "Workflow run",
      targetView: "dashboard",
      state: "recorded",
      detail: `${input.run.run_id.slice(0, 8)} · ${input.run.status}${input.run.conversation_id ? ` · chat ${input.run.conversation_id.slice(0, 8)}` : ""}`,
    });
  } else {
    steps.push({ stage: "Workflow run", targetView: null, state: "not_recorded", detail: "No run recorded." });
  }
  if (input.agentParticipants.length) {
    steps.push({
      stage: "Agent activity",
      targetView: "agents",
      state: "recorded",
      detail: input.agentParticipants
        .map((participant) => `${participant.ref}:${participant.provider}/${participant.model}${participant.calls ? ` (${participant.calls} call${participant.calls === 1 ? "" : "s"})` : ""}`)
        .join(" · "),
    });
  } else {
    steps.push({ stage: "Agent activity", targetView: null, state: "not_recorded", detail: "No agent participation recorded." });
  }
  if (input.artifact) {
    steps.push({
      stage: "Artifact",
      targetView: "artifacts",
      state: "recorded",
      detail: `${input.artifact.title || input.artifact.artifact_id.slice(0, 8)} · v${input.artifact.current_version}`,
    });
  } else {
    steps.push({ stage: "Artifact", targetView: null, state: "not_recorded", detail: "No artifact recorded for the selected project." });
  }
  steps.push({
    stage: "Knowledge / memory",
    targetView: null,
    state: "not_recorded",
    detail: "No memory activity summary endpoint available.",
  });
  return steps;
}

// ---------- 4. Workspace health summary ----------

export interface WorkspaceHealth {
  activeProjects: number;
  runningTasks: number;
  completedWorkflows: number;
  availableModels: number;
  artifactsGenerated: number;
  line: string;
}

export function workspaceHealth(input: CommandCenterInput): WorkspaceHealth {
  const activeRuns = input.runs.filter((run) => ACTIVE_RUN_STATES.includes(run.status));
  const completed = input.runs.filter((run) => run.status === "succeeded");
  return {
    activeProjects: input.studioProjects,
    runningTasks: activeRuns.length,
    completedWorkflows: completed.length,
    availableModels: input.availableModels,
    artifactsGenerated: input.artifacts.length,
    line: `${input.studioProjects} active project${input.studioProjects === 1 ? "" : "s"} · ${activeRuns.length} running · ${completed.length} completed workflow${completed.length === 1 ? "" : "s"} · ${input.availableModels} available model${input.availableModels === 1 ? "" : "s"} · ${input.artifacts.length} artifact${input.artifacts.length === 1 ? "" : "s"} generated`,
  };
}

// ---------- 5. Empty states (mandated wording) ----------

export const COMMAND_CENTER_EMPTY_STATES = {
  workspace: "No workspace activity yet. Create a project to begin.",
  models: "No models configured. Connect a provider or enable local models.",
  artifacts: "Artifacts appear after completed tasks.",
} as const;
