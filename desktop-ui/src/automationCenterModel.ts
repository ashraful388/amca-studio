import type {
  StudioWorkflowOccurrence,
  StudioWorkflowSummary,
} from "./types";

export interface WorkflowCardPresentation {
  id: string;
  name: string;
  goalSummary: string;
  automationType: string;
  schedule: string;
  templateName: string;
  modeName: string;
  runsCount: number;
  statusBadge: {
    status: string;
    label: string;
  };
}

export function extractGoalPlaceholders(goalText: string): string[] {
  if (!goalText) return [];
  const matches = goalText.match(/\{([a-zA-Z0-9_-]+)\}/g);
  if (!matches) return [];
  return Array.from(new Set(matches.map((m) => m.slice(1, -1))));
}

export function resolveWorkflowStatusBadge(
  latestOccurrence?: StudioWorkflowOccurrence | null
): { status: "ready" | "running" | "succeeded" | "failed" | "claimed"; label: string } {
  if (!latestOccurrence) {
    return { status: "ready", label: "Ready" };
  }
  switch (latestOccurrence.status) {
    case "running":
      return { status: "running", label: "Running" };
    case "succeeded":
      return { status: "succeeded", label: "Success" };
    case "failed":
      return { status: "failed", label: "Failed" };
    case "claimed":
    default:
      return { status: "claimed", label: "Claimed" };
  }
}

export function formatWorkflowCard(workflow: StudioWorkflowSummary): WorkflowCardPresentation {
  const badge = resolveWorkflowStatusBadge(workflow.latest_occurrence);
  const goalSummary =
    workflow.goal && workflow.goal.length > 130
      ? `${workflow.goal.slice(0, 130)}…`
      : workflow.goal || "";

  return {
    id: workflow.id,
    name: workflow.name || "Untitled Workflow",
    goalSummary,
    automationType: workflow.automation_type,
    schedule: workflow.schedule || "manual",
    templateName: String(workflow.metadata?.template || "custom"),
    modeName: String(workflow.metadata?.mode || "solo"),
    runsCount: workflow.execution_history_count || 0,
    statusBadge: {
      status: badge.status,
      label: badge.label,
    },
  };
}

export function validateWorkflowDraft(draft: {
  name: string;
  goal: string;
  automation_type: "once" | "recurring" | "conditional";
  schedule?: string;
  conditionPath?: string;
}): { valid: boolean; errors: string[] } {
  const errors: string[] = [];
  if (!draft.name || !draft.name.trim()) {
    errors.push("Workflow name is required.");
  }
  if (!draft.goal || !draft.goal.trim()) {
    errors.push("Workflow objective/goal prompt is required.");
  }
  if (draft.automation_type === "recurring") {
    if (!draft.schedule || !draft.schedule.trim()) {
      errors.push("Cron schedule expression is required for recurring workflows.");
    } else {
      const parts = draft.schedule.trim().split(/\s+/);
      if (parts.length !== 5) {
        errors.push("Cron schedule expression must have exactly 5 parts (minute hour day month day-of-week).");
      }
    }
  }
  if (draft.automation_type === "conditional") {
    if (!draft.conditionPath || !draft.conditionPath.trim()) {
      errors.push("Condition trigger file path is required for conditional workflows.");
    }
  }
  return {
    valid: errors.length === 0,
    errors,
  };
}

export function filterWorkflows(
  workflows: StudioWorkflowSummary[],
  searchQuery: string,
  typeFilter: "all" | "once" | "recurring" | "conditional"
): StudioWorkflowSummary[] {
  const query = searchQuery.trim().toLowerCase();
  return workflows.filter((wf) => {
    const matchesSearch =
      !query ||
      (wf.name && wf.name.toLowerCase().includes(query)) ||
      (wf.goal && wf.goal.toLowerCase().includes(query)) ||
      (wf.metadata?.template && String(wf.metadata.template).toLowerCase().includes(query));
    const matchesType = typeFilter === "all" || wf.automation_type === typeFilter;
    return Boolean(matchesSearch && matchesType);
  });
}

