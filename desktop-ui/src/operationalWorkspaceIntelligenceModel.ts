// ZCODE-AMCA-STUDIO-V0.8.0-OPERATIONAL-WORKSPACE-INTELLIGENCE-ENHANCEMENT-01 — pure model.
//
// Connects EXISTING operational records (run registry, artifact registry,
// approval records, composer knowledge selection, model refs) into a
// unified operational view: command-center metrics, a project operational
// timeline, health evidence cards, and drill-down navigation.
// Rules recorded by the order:
// - all values come from existing records;
// - newest first; recorded timestamps only; undated records preserved
//   honestly (they render without a timestamp and are never dropped);
// - explicit statement: "Recorded operational state only — no productivity
//   or quality score.";
// - drill-down targets are existing views; missing records render
//   "No recorded link available." with no inference;
// - no ranking, no scoring, no inferred relationships.

import type { RunJob } from "./types";
import type { KnowledgeContextItem } from "./knowledgeContextModel";

export const OPERATIONAL_STATE_NOTE =
  "Recorded operational state only — no productivity or quality score.";

export const OPERATIONAL_EMPTY_STATES = {
  no_workflows: "No workflow records available yet.",
  no_timeline: "No recorded operational events for this project yet.",
  no_link: "No recorded link available.",
} as const;

// ---------- 1. Command-center operational metrics (existing records) ----------

export interface OperationalMetrics {
  activeWorkflows: number;
  completedWorkflows: number;
  pendingApprovals: number;
  artifactsGenerated: number;
  knowledgeAttachments: number;
  localExecutionVisible: string;
  providerExecutionVisible: string;
}

const ACTIVE_RUN_STATES = ["queued", "running", "cancelling"];
const DONE_RUN_STATES = ["succeeded", "partial"];

export function operationalMetrics(input: {
  runs: RunJob[];
  pendingApprovals: number;
  artifactCount: number;
  knowledgeItems: KnowledgeContextItem[];
  localModels: number;
  providerModels: number;
}): OperationalMetrics {
  const activeWorkflows = input.runs.filter((run) => ACTIVE_RUN_STATES.includes(run.status)).length;
  const completedWorkflows = input.runs.filter((run) => DONE_RUN_STATES.includes(run.status)).length;
  return {
    activeWorkflows,
    completedWorkflows,
    pendingApprovals: input.pendingApprovals,
    artifactsGenerated: input.artifactCount,
    knowledgeAttachments: input.knowledgeItems.length,
    localExecutionVisible:
      input.localModels > 0
        ? `${input.localModels} local model(s) recorded`
        : "Not recorded",
    providerExecutionVisible:
      input.providerModels > 0
        ? `${input.providerModels} provider model(s) recorded — approval required`
        : "Not recorded",
  };
}

// ---------- 2. Project operational timeline (newest first) ----------

export interface OperationalTimelineEvent {
  kind: "workflow" | "artifact" | "approval" | "knowledge";
  label: string;
  /** Recorded timestamp or null — undated events render "undated". */
  timestamp: string | null;
  detail: string;
}

/**
 * Newest-first merge over recorded events; undated records sort LAST
 * (after all dated events, in their original relative order) — never
 * first, and never dropped.
 */
export function operationalTimeline(events: OperationalTimelineEvent[]): OperationalTimelineEvent[] {
  const dated = events.filter((event) => event.timestamp !== null);
  const undated = events.filter((event) => event.timestamp === null);
  dated.sort((a, b) => {
    const at = a.timestamp ?? "";
    const bt = b.timestamp ?? "";
    if (at === bt) return 0;
    return at < bt ? 1 : -1;
  });
  return [...dated, ...undated];
}

/** Build the event list for one project from existing records. */
export function projectOperationalEvents(input: {
  projectId: string;
  runs: RunJob[];
  artifacts: Array<{ artifact_id: string; title: string; created_at?: string | null }>;
  approvals: Array<{ id: string; action: string; status: string; created_at: string | null }>;
  knowledgeEvents: Array<{ artifactId: string; timestamp: string | null }>;
}): OperationalTimelineEvent[] {
  const events: OperationalTimelineEvent[] = [];
  for (const run of input.runs.filter((record) => record.project_id === input.projectId)) {
    events.push({
      kind: "workflow",
      label: "Workflow event",
      timestamp: run.created_at ?? null,
      detail: `${run.run_id.slice(0, 12)}… · ${run.status}`,
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
  for (const approval of input.approvals) {
    events.push({
      kind: "approval",
      label: "Approval event",
      timestamp: approval.created_at,
      detail: `${approval.action} · ${approval.status}`,
    });
  }
  for (const knowledge of input.knowledgeEvents) {
    events.push({
      kind: "knowledge",
      label: "Knowledge attached",
      timestamp: knowledge.timestamp,
      detail: `${knowledge.artifactId.slice(0, 12)}… · explicitly attached by the user`,
    });
  }
  return operationalTimeline(events);
}

// ---------- 3. Operational health summary (evidence cards) ----------

export interface HealthCard {
  label: string;
  value: number;
  evidence: string;
}

export function operationalHealthCards(input: {
  runs: RunJob[];
  pendingApprovals: number;
  availableResources: number;
}): HealthCard[] {
  return [
    { label: "Running workflows", value: input.runs.filter((run) => ACTIVE_RUN_STATES.includes(run.status)).length, evidence: "Run registry states" },
    { label: "Completed workflows", value: input.runs.filter((run) => DONE_RUN_STATES.includes(run.status)).length, evidence: "Run registry states" },
    { label: "Pending governance actions", value: input.pendingApprovals, evidence: "Recorded approval requests" },
    { label: "Failed workflows", value: input.runs.filter((run) => run.status === "failed").length, evidence: "Run registry states" },
    { label: "Available resources", value: input.availableResources, evidence: "Recorded model and agent refs" },
  ];
}

// ---------- 4. Drill-down navigation (existing views only) ----------

export type DrillTarget = "agents" | "artifacts" | "settings" | "validation";

export interface DrillLink {
  label: string;
  target: DrillTarget | null;
  available: boolean;
  unavailableReason: string | null;
}

export function drillLink(kind: "workflow" | "artifact" | "model" | "approval", linkedRecordExists: boolean): DrillLink {
  const targets: Record<typeof kind, DrillTarget> = {
    workflow: "agents",
    artifact: "artifacts",
    model: "settings",
    approval: "validation",
  };
  return linkedRecordExists
    ? { label: `Open ${kind} details`, target: targets[kind], available: true, unavailableReason: null }
    : { label: `Open ${kind} details`, target: null, available: false, unavailableReason: "No recorded link available." };
}
