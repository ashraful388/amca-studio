// Z2 Studio Task Composer + live execution views — pure presentation model
// (no fetch, no DOM). Builds governed run-creation payloads from EXISTING
// backend contracts only (/api/conversations + /api/chat-runs), maps the
// EXISTING run-registry statuses onto an honest execution timeline, and
// groups existing artifact types for the task-output view. No new backend
// semantics, no execution bypass.

import type { RunJob, CollaborationArchitecture } from "./types";

export interface TaskTypeDescriptor {
  id: string;
  label: string;
  hint: string;
}

// Task type is a user-facing classification; it is carried as a visible
// tag in the goal text because the backend run contract has no task-type
// field. Nothing is hidden from the governed payload.
export const TASK_TYPES: TaskTypeDescriptor[] = [
  { id: "general_reasoning", label: "General Reasoning", hint: "Think through a problem with evidence" },
  { id: "research", label: "Research", hint: "Derive, verify, and synthesize findings" },
  { id: "coding", label: "Coding", hint: "Build or repair software" },
  { id: "analysis", label: "Analysis", hint: "Analyze data, documents, or systems" },
  { id: "document_creation", label: "Document Creation", hint: "Produce a report or document artifact" },
  { id: "agent_collaboration", label: "Agent Collaboration", hint: "Multi-agent organization on a shared goal" },
];

export function taskTypeById(id: string): TaskTypeDescriptor | null {
  return TASK_TYPES.find((type) => type.id === id) ?? null;
}

export interface ComposerModeDescriptor {
  id: CollaborationArchitecture;
  label: string;
  hint: string;
}

export const COMPOSER_MODES: ComposerModeDescriptor[] = [
  { id: "solo", label: "Solo", hint: "One participant works the task" },
  { id: "joint", label: "Joint", hint: "Participants work together on one thread" },
  { id: "independent_discuss", label: "Independent → Discuss", hint: "Work separately, then synchronize" },
  { id: "parallel", label: "Parallel", hint: "Decomposed work units in parallel" },
  { id: "adaptive_hybrid", label: "Adaptive Hybrid", hint: "Backend supervisor chooses as it executes" },
];

const MODE_IDS = new Set(COMPOSER_MODES.map((mode) => mode.id));

export function toggleComposerSelection(current: string[], ref: string, mode: "auto" | "manual"): string[] {
  if (mode === "auto") return [ref];
  return current.includes(ref) ? current.filter((item) => item !== ref) : [...current, ref];
}

export type SelectionPolicy = "auto_intelligence" | "manual_model_set";

export interface ComposerDraft {
  goal: string;
  taskTypeId: string;
  mode: CollaborationArchitecture;
  selectionPolicy: SelectionPolicy;
  modelRefs: string[];
  workspaceProjectId: string | null;
}

export interface ComposerPayload {
  goal: string;
  participant_ids: string[];
  mode: CollaborationArchitecture;
  selection_policy: SelectionPolicy;
  workspace_project_id: string | null;
  conversation_mode: CollaborationArchitecture;
}

export interface ComposerPayloadOk {
  ok: true;
  payload: ComposerPayload;
}
export interface ComposerPayloadInvalid {
  ok: false;
  errors: string[];
}

export function goalWithTag(goal: string, taskTypeId: string): string {
  const type = taskTypeById(taskTypeId);
  const text = goal.trim();
  if (!type) return text;
  return `[Task type: ${type.label}] ${text}`;
}

// Mirrors the existing chat flow semantics: auto strategy requests
// backend-controlled selection (no participant refs); manual requires at
// least one ref. The mode field is always sent because the request contract
// carries it, but only a manual selection honors it - Auto Intelligence owns
// the collaboration architecture and ignores the requested mode.
export function buildComposerPayload(draft: ComposerDraft): ComposerPayloadOk | ComposerPayloadInvalid {
  const errors: string[] = [];
  const text = draft.goal.trim();
  if (!text) errors.push("Describe the task goal before starting.");
  if (!taskTypeById(draft.taskTypeId)) errors.push("Choose a valid task type.");
  if (!MODE_IDS.has(draft.mode)) errors.push("Choose a valid collaboration mode.");
  if (draft.selectionPolicy === "manual_model_set" && draft.modelRefs.length === 0) {
    errors.push("Manual model strategy needs at least one selected model — or switch to Auto Intelligence.");
  }
  if (errors.length) return { ok: false, errors };

  const participantIds = draft.selectionPolicy === "manual_model_set" ? [...draft.modelRefs] : [];
  return {
    ok: true,
    payload: {
      goal: goalWithTag(text, draft.taskTypeId),
      participant_ids: participantIds,
      mode: draft.mode,
      selection_policy: draft.selectionPolicy,
      workspace_project_id: draft.workspaceProjectId?.trim() || null,
      conversation_mode: draft.mode,
    },
  };
}

export type TimelinePhase = "done" | "active" | "pending" | "failed" | "cancelled";

export interface TimelineStep {
  key: string;
  label: string;
  phase: TimelinePhase;
}

// Honest timeline over the EXISTING registry states only: queued →
// running → terminal. Terminal states appear exactly as the registry
// reports them (succeeded, partial, failed, cancelled) — nothing invented.
export const TERMINAL_RUN_STATES = ["succeeded", "partial", "failed", "cancelled"];

export function executionTimeline(run: Pick<RunJob, "status">): TimelineStep[] {
  const steps: TimelineStep[] = [
    { key: "queued", label: "Queued", phase: "done" },
    { key: "running", label: "Running", phase: "pending" },
  ];
  if (TERMINAL_RUN_STATES.includes(run.status)) {
    steps[0].phase = "done";
    steps[1].phase = "done";
    steps.push({
      key: run.status,
      label: run.status === "succeeded" || run.status === "partial" ? "Completed" : run.status === "failed" ? "Failed" : "Cancelled",
      phase: run.status === "failed" ? "failed" : run.status === "cancelled" ? "cancelled" : "done",
    });
    return steps;
  }
  steps[1].phase = run.status === "running" || run.status === "cancelling" ? "active" : "pending";
  return steps;
}

export interface ArtifactLike {
  artifact_id: string;
  artifact_type: string;
  title: string;
  current_version: number;
}

export interface ArtifactGroup {
  type: string;
  label: string;
  items: ArtifactLike[];
}

// Groups over the EXISTING artifact types (markdown, docx, pdf, tex,
// xlsx, pptx, image, html, zip). No invented categories.
const ARTIFACT_TYPE_LABELS: Record<string, string> = {
  markdown: "Reports & notes",
  docx: "Documents",
  pdf: "PDF outputs",
  tex: "LaTeX sources",
  xlsx: "Spreadsheets",
  pptx: "Presentations",
  image: "Images",
  html: "Web outputs",
  zip: "Archives",
};

export function groupArtifacts(artifacts: ArtifactLike[]): ArtifactGroup[] {
  const groups = new Map<string, ArtifactLike[]>();
  for (const artifact of artifacts) {
    const list = groups.get(artifact.artifact_type) ?? [];
    list.push(artifact);
    groups.set(artifact.artifact_type, list);
  }
  return [...groups.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([type, items]) => ({ type, label: ARTIFACT_TYPE_LABELS[type] ?? type, items }));
}

export interface NanoAssignmentLike {
  nano_id: string;
  task_id: string;
  parent_task_id: string;
  run_ref: string;
  purpose: string;
  task_type: string;
  depth: number;
}

export interface TaskAgentGroup {
  taskKey: string;
  goal: string;
  runId: string | null;
  runState: string | null;
  agents: Array<{ id: string; role: string; kind: "run" | "nano"; state: string }>;
}

// Task-centric read-only grouping over the EXISTING registries: each run
// is the task's coordinator; nano assignments attach via their run_ref.
// Assignments without a matching run stay visible under an explicit
// background group. Roles are not invented: the registry has runs and
// nano workers, nothing else.
export function groupAgentsByTask(
  runs: Array<Pick<RunJob, "run_id" | "goal" | "status">>,
  nano: { assignments: NanoAssignmentLike[]; statuses: Record<string, string> },
  limit = 8,
): TaskAgentGroup[] {
  const groups: TaskAgentGroup[] = [];
  const claimed = new Set<string>();
  for (const run of runs.slice(0, limit)) {
    const workers = nano.assignments.filter((assignment) => assignment.run_ref === run.run_id);
    workers.forEach((assignment) => claimed.add(assignment.nano_id));
    groups.push({
      taskKey: run.run_id,
      goal: run.goal,
      runId: run.run_id,
      runState: run.status,
      agents: [
        { id: run.run_id, role: "Coordinator (run)", kind: "run", state: run.status },
        ...workers.map((assignment) => ({
          id: assignment.nano_id,
          role: `Worker · ${assignment.task_type || assignment.purpose || "task"}`,
          kind: "nano" as const,
          state: nano.statuses[assignment.nano_id] ?? "unknown",
        })),
      ],
    });
  }
  const background = nano.assignments.filter((assignment) => !claimed.has(assignment.nano_id));
  if (background.length) {
    groups.push({
      taskKey: "background",
      goal: "Background & unattached nano tasks",
      runId: null,
      runState: null,
      agents: background.map((assignment) => ({
        id: assignment.nano_id,
        role: `Worker · ${assignment.task_type || assignment.purpose || "task"}`,
        kind: "nano" as const,
        state: nano.statuses[assignment.nano_id] ?? "unknown",
      })),
    });
  }
  return groups;
}
