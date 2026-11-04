// BETA-B14 Operational Intelligence UX — pure presentation model
// (no fetch, no DOM). Renders the EXISTING operational documents: the run
// registry (dashboard), studio projects, automation definitions +
// occurrences + scheduler status, artifact records, cost state
// (local/per-model split), and cognitive events. Privacy rules: no
// analytics, no productivity scores, no user-behavior inference; unknown
// counts stay "not recorded"; every card names its evidence source.

import type { RunJob, StudioProjectSummary, AutomationDefinition, AutomationOccurrence, ArtifactMeta, CognitiveEvent } from "./types";

// ---------- 1. Operational dashboard cards (order scope 1) ----------

export interface OperationalCard {
  key: string;
  label: string;
  value: number | null;
  detail: string;
  source: string;
}

export interface AutomationSnapshot {
  definitions: number;
  enabled: number;
  occurrences: AutomationOccurrence[];
  schedulerRunning: boolean | null;
}

export interface OperationalEvidence {
  projects: StudioProjectSummary[];
  runs: RunJob[];
  automations: AutomationDefinition[];
  occurrences: AutomationOccurrence[];
  schedulerRunning: boolean | null;
  artifacts: ArtifactMeta[];
}

const RUN_TERMINAL_SUCCEEDED = new Set(["succeeded", "partial"]);

export function operationalCards(evidence: OperationalEvidence): OperationalCard[] {
  const activeProjects = evidence.projects.filter((project) => !project.archived).length;
  const completedWorkflows = evidence.runs.filter((run) => RUN_TERMINAL_SUCCEEDED.has(run.status)).length;
  const automationActivity = evidence.occurrences.filter(
    (occurrence) => occurrence.status === "running" || occurrence.status === "succeeded",
  ).length;
  return [
    {
      key: "active_projects",
      label: "Active projects",
      value: activeProjects,
      detail: `${evidence.projects.length - activeProjects} archived`,
      source: "/api/studio-projects",
    },
    {
      key: "completed_workflows",
      label: "Completed workflows",
      value: completedWorkflows,
      detail: `${evidence.runs.filter((run) => run.status === "failed").length} failed · ${evidence.runs.filter((run) => run.status === "cancelled").length} cancelled`,
      source: "/api/dashboard run registry",
    },
    {
      key: "automation_activity",
      label: "Automation activity",
      value: automationActivity,
      detail: `${evidence.automations.length} defined · ${evidence.automations.filter((automation) => automation.enabled).length} enabled`,
      source: "/api/automations + occurrences",
    },
    {
      key: "artifact_production",
      label: "Artifact production",
      value: evidence.artifacts.length,
      detail: `${new Set(evidence.artifacts.map((asset) => asset.project_id)).size} project(s)`,
      source: "artifact registry",
    },    {
      key: "memory_activity",
      label: "Memory activity",
      value: null,
      detail: "Not recorded — this build exposes no memory-count endpoint",
      source: "none available",
    },
    {
      key: "scheduler",
      label: "Scheduler",
      value: evidence.schedulerRunning === null ? null : evidence.schedulerRunning ? 1 : 0,
      detail: evidence.schedulerRunning === null ? "Unknown" : evidence.schedulerRunning ? "Running" : "Not running",
      source: "/api/automations/status",
    },
  ];
}

// ---------- 2. Operational report document (order scope 2) ----------

export interface ReportMetricRow {
  label: string;
  value: string;
  recorded: boolean;
  source: string;
}

export interface OperationalReport {
  generatedAt: string;
  title: string;
  metrics: ReportMetricRow[];
  privacyNote: string;
}

function fmtUsd(value: number): string {
  return `$${value.toFixed(4)}`;
}

export function buildOperationalReport(evidence: OperationalEvidence, generatedAtIso: string): OperationalReport {
  const cards = operationalCards(evidence);
  const metrics: ReportMetricRow[] = cards.map((card) => ({
    label: card.label,
    value: card.value === null ? "not recorded" : String(card.value),
    recorded: card.value !== null,
    source: card.source,
  }));
  return {
    generatedAt: generatedAtIso,
    title: "AMCA Studio Operational Report",
    metrics,
    privacyNote: "All figures come from the local run/automation/artifact registries. No analytics tracking, no user-behavior inference, nothing leaves this device.",
  };
}

export function reportToText(report: OperationalReport): string {
  return JSON.stringify(report, null, 2);
}

// ---------- 3. Resource visibility (order scope 3) ----------

export interface CostStateLike {
  total_known_cost_usd: number;
  total_unknown_calls: number;
  total_calls: number;
  total_local_calls: number;
  per_model_cost_usd: Record<string, number>;
  known: boolean;
}

export interface ResourceVisibility {
  localModelRatio: string | null;
  recordedCostUsd: number | null;
  unknownCalls: number | null;
  workflowExecutionCount: number;
  perModel: Array<{ model: string; costUsd: number }>;
  line: string;
}

export function resourceVisibility(costState: CostStateLike | null, runs: RunJob[]): ResourceVisibility {
  const visibility: ResourceVisibility = {
    localModelRatio: null,
    recordedCostUsd: null,
    unknownCalls: null,
    workflowExecutionCount: runs.length,
    perModel: [],
    line: "",
  };
  if (!costState || !costState.known) {
    visibility.line = runs.length
      ? "Cost state not recorded for the visible runs — no estimates are shown."
      : "No runs yet — cost and usage appear after the first governed task.";
    return visibility;
  }
  const totalCalls = Math.max(0, costState.total_calls);
  visibility.localModelRatio = totalCalls > 0
    ? `${costState.total_local_calls}/${totalCalls} recorded calls ran locally`
    : "no calls recorded";
  visibility.recordedCostUsd = costState.total_known_cost_usd;
  visibility.unknownCalls = costState.total_unknown_calls;
  visibility.perModel = Object.entries(costState.per_model_cost_usd)
    .map(([model, costUsd]) => ({ model, costUsd }))
    .sort((a, b) => b.costUsd - a.costUsd);
  visibility.line = totalCalls > 0
    ? `${visibility.localModelRatio}; recorded cost ${fmtUsd(costState.total_known_cost_usd)}`
      + (costState.total_unknown_calls > 0 ? ` · ${costState.total_unknown_calls} call(s) without recorded pricing` : "")
    : "no model calls recorded yet";
  return visibility;
}

// ---------- 4. Workspace operational timeline (order scope 4) ----------

export type TimelineEntryKind = "workflow" | "automation" | "governance";

export interface TimelineEntry {
  id: string;
  kind: TimelineEntryKind;
  label: string;
  at: string | null;
  state: string;
}

const RUN_KIND_LABELS: Record<string, string> = {
  queued: "queued",
  running: "executing",
  succeeded: "completed",
  partial: "completed (partial)",
  failed: "failed",
  cancelled: "cancelled",
  cancelling: "cancelling",
};

export function operationalTimeline(
  runs: RunJob[],
  occurrences: AutomationOccurrence[],
  events: CognitiveEvent[],
  limit = 24,
): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  for (const run of runs) {
    entries.push({
      id: `run:${run.run_id}`,
      kind: "workflow",
      label: run.goal.length > 70 ? `${run.goal.slice(0, 70)}…` : run.goal,
      at: run.finished_at ?? run.started_at ?? run.created_at,
      state: RUN_KIND_LABELS[run.status] ?? run.status,
    });
  }
  for (const occurrence of occurrences) {
    entries.push({
      id: `occ:${occurrence.id}`,
      kind: "automation",
      label: `Automation occurrence ${occurrence.automation_id.slice(0, 12)}`,
      at: occurrence.completed_at ?? occurrence.claimed_at ?? occurrence.scheduled_for,
      state: occurrence.status,
    });
  }
  for (const event of events.slice(0, limit)) {
    entries.push({
      id: `evt:${event.id}`,
      kind: "governance",
      label: `${event.event_type.replaceAll("_", " ")} · ${event.source}`,
      at: event.created_at,
      state: event.severity,
    });
  }
  return entries
    .filter((entry) => entry.at)
    .sort((a, b) => Date.parse(b.at ?? "") - Date.parse(a.at ?? ""))
    .slice(0, limit);
}
