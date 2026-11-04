// ZCODE-AMCA-STUDIO-V0.8.0-WORKFLOW-TEMPLATE-AND-REUSE-UX-FOUNDATION-01 — pure model.
//
// Transparent workflow reuse foundation built ONLY from previously
// completed, recorded workflows. Rules recorded by the order:
// - templates are recorded examples, NOT recommendations or rankings —
//   the boundary is stated verbatim wherever templates appear;
// - detail views quote the recorded stages/resources/knowledge/outputs
//   with "No recorded detail available." for gaps;
// - reuse preparation creates NO workflow automatically, changes NO
//   execution state: "Start from this workflow structure" only opens the
//   existing composer with the recorded task type and project prefilled —
//   the user still writes the goal and sends it themselves;
// - comparison is side-by-side display of recorded fields — no
//   better/fastest/best-model/efficiency calculation.

import type { RunJob } from "./types";

export const TEMPLATE_BOUNDARY =
  "Recorded workflow examples only — not recommendations or rankings.";

export const REUSE_BOUNDARY =
  "Preparation only: opens the composer with the recorded task type and project. Nothing is created or executed automatically.";

export const TEMPLATE_EMPTY_STATES = {
  no_templates: "No completed workflows recorded yet — templates appear after workflows complete.",
  no_detail: "No recorded detail available.",
  no_comparison: "Select two recorded workflows to compare.",
} as const;

// ---------- 1. Template visibility (recorded examples) ----------

export interface WorkflowTemplate {
  sourceWorkflowId: string;
  taskType: string;
  projectId: string | null;
  models: string[];
  agentCount: number;
  knowledgeCount: number;
  artifactCount: number;
  completedAt: string | null;
  boundary: string;
}

const TEMPLATE_SOURCE_STATES = ["succeeded", "partial"];

/** Templates derive from completed recorded workflows only. */
export function workflowTemplates(
  runs: Array<RunJob & { artifactCount?: number; knowledgeCount?: number; agentCount?: number }>,
): WorkflowTemplate[] {
  return (runs ?? [])
    .filter((run) => TEMPLATE_SOURCE_STATES.includes(run.status))
    .map((run) => ({
      sourceWorkflowId: run.run_id,
      taskType: recordedTemplateTaskType(run.goal),
      projectId: run.project_id,
      models: [run.model].filter((ref): ref is string => Boolean(ref)),
      agentCount: run.agentCount ?? 0,
      knowledgeCount: run.knowledgeCount ?? 0,
      artifactCount: run.artifactCount ?? 0,
      completedAt: run.finished_at ?? run.created_at ?? null,
      boundary: TEMPLATE_BOUNDARY,
    }));
}

/** Task type from the recorded composer goal tag; "Untyped" when absent. */
export function recordedTemplateTaskType(goal: string | null | undefined): string {
  const match = /\[Task type:\s*([^\]]+)\]/.exec(goal ?? "");
  return match ? match[1].trim() : "Untyped";
}

// ---------- 2. Template detail view (recorded fields, honest gaps) ----------

export interface TemplateDetail {
  sourceWorkflowId: string;
  taskType: string;
  recordedStages: string[];
  resources: string[];
  knowledgeAttachments: number;
  outputs: string[];
  missing: string[];
}

export function templateDetail(
  run: RunJob & { artifactCount?: number; knowledgeCount?: number; agentCount?: number },
): TemplateDetail {
  const missing: string[] = [];
  const resources = [run.model].filter((ref): ref is string => Boolean(ref));
  const knowledgeAttachments = run.knowledgeCount ?? 0;
  const artifactCount = run.artifactCount ?? 0;
  const agentCount = run.agentCount ?? 0;
  if (agentCount === 0) missing.push("agents");
  if (knowledgeAttachments === 0) missing.push("knowledge");
  if (artifactCount === 0) missing.push("outputs");
  // The desktop record carries the run's registry state; orchestrator-side
  // stage records are not exposed yet — the recorded stage list is the
  // registry status line only.
  const recordedStages = [`Run registry status: ${run.status}`];
  return {
    sourceWorkflowId: run.run_id,
    taskType: recordedTemplateTaskType(run.goal),
    recordedStages,
    resources,
    knowledgeAttachments,
    outputs: artifactCount > 0 ? [`${artifactCount} recorded artifact(s)`] : [],
    missing,
  };
}

// ---------- 3. Reuse preparation (opens existing composer only) ----------

export interface ReusePreparation {
  taskTypeId: string | null;
  workspaceProjectId: string | null;
  boundary: string;
}

/**
 * Compute the composer prefill from a recorded template. The action
 * returns a draft patch — the caller opens the existing composer; nothing
 * is created or executed.
 */
export function reusePreparation(
  template: WorkflowTemplate,
  knownTaskTypeIds: readonly string[],
): ReusePreparation {
  // The recorded goal tag holds a display label; map it to a composer task
  // type id only when an exact case-insensitive id match exists. Otherwise
  // null — the user picks the type themselves. No fuzzy inference.
  const lowered = template.taskType.toLowerCase();
  const match = knownTaskTypeIds.find((id) => id.toLowerCase() === lowered);
  return {
    taskTypeId: match ?? null,
    workspaceProjectId: template.projectId,
    boundary: REUSE_BOUNDARY,
  };
}

// ---------- 4. Side-by-side comparison (display only) ----------

export interface ComparisonRow {
  field: string;
  values: string[];
}

export function templateComparison(
  templates: WorkflowTemplate[],
): ComparisonRow[] {
  const pick = (value: string | null | undefined, fallback = "Not recorded") =>
    value && value.trim() ? value : fallback;
  return [
    { field: "Task type", values: templates.map((t) => t.taskType) },
    { field: "Recorded stage evidence", values: templates.map(() => "Run registry status only (orchestrator stages not exposed)") },
    { field: "Resources", values: templates.map((t) => t.models.join(", ") || "Not recorded") },
    { field: "Agent participation", values: templates.map((t) => (t.agentCount > 0 ? `${t.agentCount} recorded` : "Not recorded")) },
    { field: "Knowledge attachments", values: templates.map((t) => (t.knowledgeCount > 0 ? `${t.knowledgeCount} recorded` : "Not recorded")) },
    { field: "Outputs", values: templates.map((t) => (t.artifactCount > 0 ? `${t.artifactCount} recorded` : "Not recorded")) },
    { field: "Completed at", values: templates.map((t) => pick(t.completedAt)) },
  ];
}

export const COMPARISON_NOTE =
  "Side-by-side display of recorded fields — no better-workflow, fastest-workflow, best-model, or efficiency calculation exists.";
