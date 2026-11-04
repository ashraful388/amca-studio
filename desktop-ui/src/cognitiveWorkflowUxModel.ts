// ZCODE-AMCA-STUDIO-V0.8.0-COGNITIVE-WORKFLOW-UX-FOUNDATION-01 — pure model.
//
// First frontend transparency layer for the Cognitive Workflow Loop, driven
// ONLY by existing recorded records (the desktop run registry record + the
// knowledge context selection the user attached). The backend Cognitive
// Workflow store exists on the architecture track but no HTTP route exposes
// it, so every stage of the 9-step loop derives from what the desktop
// already records: run status, timestamps, model refs, artifacts, and the
// explicit knowledge annotation. Rules recorded by the order:
// - recorded states only; missing states render "Not recorded";
// - timestamps only when available;
// - NO inferred progress, no confidence scores, no AI quality ratings,
//   no subjective rankings, no hidden reasoning displayed.

import type { RunJob } from "./types";
import type { KnowledgeContextItem } from "./knowledgeContextModel";

// ---------- 1. The 9-stage Cognitive Workflow Loop ----------

export interface WorkflowStage {
  id: string;
  label: string;
  /** "Not recorded" when the desktop record carries no evidence. */
  state: "done" | "active" | "failed" | "not_recorded";
  /** Recorded timestamp when available, else null. */
  timestamp: string | null;
  /** Recorded evidence line (never invented). */
  detail: string | null;
}

const NOT_RECORDED = "Not recorded";

/** Run record fields the timeline reads (all existing desktop records). */
export interface WorkflowRunRecord extends Pick<RunJob, "run_id" | "status" | "created_at"> {
  finished_at?: string | null;
  started_at?: string | null;
  error?: string | null;
  updated_at?: string | null;
}

/** Derive the 9-stage timeline from a recorded run + explicit knowledge selection. */
export function workflowStages(
  run: WorkflowRunRecord | null,
  knowledgeItems: KnowledgeContextItem[],
): WorkflowStage[] {
  const status = run?.status ?? null;
  // Recorded RunStatus union: queued | running | cancelling | cancelled |
  // succeeded | partial | failed — no invented states.
  const failed = status === "failed" || status === "cancelled";
  const completed = status === "succeeded" || status === "partial";
  const running = status === "running" || status === "cancelling";

  const recorded = (value: string | null | undefined): string | null =>
    value && String(value).trim() ? String(value) : null;

  return [
    {
      id: "task_created",
      label: "Task created",
      state: run ? "done" : "not_recorded",
      timestamp: recorded(run?.created_at),
      detail: run ? `Run ${run.run_id}` : null,
    },
    {
      id: "understanding_complete",
      label: "Understanding complete",
      // The desktop record carries the goal text itself; the structured
      // understanding stage belongs to the orchestrator and is not exposed
      // to the UI yet — recorded as such, never inferred.
      state: run ? "done" : "not_recorded",
      timestamp: recorded(run?.created_at),
      detail: run ? "Recorded in the run goal text (structured understanding is orchestrator-side)." : null,
    },
    {
      id: "plan_created",
      label: "Plan created",
      state: "not_recorded",
      timestamp: null,
      detail: null,
    },
    {
      id: "resources_selected",
      label: "Resources selected",
      state: run ? "done" : "not_recorded",
      timestamp: recorded(run?.created_at),
      detail: null,
    },
    {
      id: "knowledge_attached",
      label: "Knowledge attached",
      state: knowledgeItems.length > 0 ? "done" : "not_recorded",
      timestamp: recorded(run?.created_at),
      detail: knowledgeItems.length
        ? `${knowledgeItems.length} item(s) attached explicitly by the user`
        : null,
    },
    {
      id: "execution_started",
      label: "Execution started",
      state: run ? (running ? "active" : completed || failed ? "done" : "not_recorded") : "not_recorded",
      timestamp: recorded(run?.started_at),
      detail: null,
    },
    {
      id: "execution_completed",
      label: "Execution completed",
      state: completed ? "done" : failed ? "failed" : "not_recorded",
      timestamp: recorded(run?.finished_at),
      detail: failed ? recorded(run?.error) : null,
    },
    {
      id: "verification_complete",
      label: "Verification complete",
      state: "not_recorded",
      timestamp: null,
      detail: null,
    },
    {
      id: "learning_recorded",
      label: "Learning recorded",
      state: "not_recorded",
      timestamp: null,
      detail: null,
    },
  ];
}

/** The order's mandated 9 labels, in order — asserted by tests. */
export const WORKFLOW_STAGE_LABELS: ReadonlyArray<string> = [
  "Task created",
  "Understanding complete",
  "Plan created",
  "Resources selected",
  "Knowledge attached",
  "Execution started",
  "Execution completed",
  "Verification complete",
  "Learning recorded",
];

/** Overall loop position: last stage with recorded evidence, else "Not recorded". */
export function workflowPosition(stages: WorkflowStage[]): string {
  const doneIndex = stages.reduce((last, stage, index) => (stage.state === "done" ? index : last), -1);
  return doneIndex >= 0 ? stages[doneIndex].label : NOT_RECORDED;
}

// ---------- 2. Workflow panel fields (recorded only) ----------

export interface WorkflowPanelView {
  workflowId: string;
  userGoal: string;
  taskType: string | null;
  lifecycleState: string;
  failureReason: string | null;
}

/**
 * Panel header from the recorded run. Task type comes from the recorded
 * composer goal-tag pattern ("[Task type: X] …") when present — parsed, not
 * inferred; absent tags render as Not recorded.
 */
export function workflowPanelView(run: WorkflowRunRecord & { goal?: string | null }): WorkflowPanelView {
  const goal = run.goal && run.goal.trim() ? run.goal.trim() : NOT_RECORDED;
  const match = /\[Task type:\s*([^\]]+)\]/.exec(run.goal ?? "");
  const failure = run.error && run.error.trim() ? run.error.trim() : null;
  return {
    workflowId: run.run_id,
    userGoal: goal,
    taskType: match ? match[1].trim() : null,
    lifecycleState: run.status ?? NOT_RECORDED,
    failureReason: failure,
  };
}

// ---------- 3. Resource-selection transparency (existing records only) ----------

export interface ResourceTransparency {
  models: string[];
  agents: string[];
  /** Recorded locality line — from the model ref prefix, nothing computed. */
  locality: string;
  governanceStatus: string;
}

/**
 * Resource transparency from the recorded participant refs. Locality is the
 * recorded ref prefix ("ollama:" = local), not a computed score; governance
 * restates the recorded approval rule (exact approval before any network
 * call) rather than exposing hidden routing reasoning.
 */
export function resourceTransparency(modelRefs: string[], agentIds: string[]): ResourceTransparency {
  const localCount = modelRefs.filter((ref) => ref.startsWith("ollama:")).length;
  const locality = modelRefs.length === 0
    ? NOT_RECORDED
    : localCount === modelRefs.length
      ? "Local models only — no network call without approval"
      : localCount === 0
        ? "API/provider models — approval required before any network call"
        : `Mixed: ${localCount} local, ${modelRefs.length - localCount} provider`;
  return {
    models: modelRefs,
    agents: agentIds,
    locality,
    governanceStatus: "Explicit user selection; exact approval before any network call",
  };
}

// ---------- 4. Knowledge usage section (accepted Knowledge Workflow UX) ----------

export interface KnowledgeUsageLine {
  title: string;
  artifactId: string;
  provenance: string;
  permissionStatus: string;
  /** The recorded retrieval mode the selection used, or the composer default. */
  retrievalMode: string;
  auditReference: string | null;
}

export function knowledgeUsage(
  items: KnowledgeContextItem[],
  auditEventRef: string | null,
): KnowledgeUsageLine[] {
  return items.map((item) => ({
    title: item.title,
    artifactId: item.artifactId,
    provenance: item.provenanceLine,
    permissionStatus: item.shareability === "shareable" ? "Shareable — can be attached" : `${item.shareability} — ${item.shareabilityReason}`,
    retrievalMode: "Explicit user attachment (no automatic retrieval)",
    auditReference: auditEventRef,
  }));
}

// ---------- 5. Verification section (recorded only, no scores) ----------

export interface VerificationView {
  outcome: string;
  artifacts: string[];
  validationEvidence: string;
  unresolvedIssues: string[];
}

export function verificationView(run: WorkflowRunRecord | null, artifactTitles: string[]): VerificationView {
  const failed = run?.status === "failed" || run?.status === "cancelled";
  const completed = run?.status === "succeeded" || run?.status === "partial";
  return {
    outcome: completed ? "Execution completed — outcome recorded by the run registry" : failed ? "Execution did not complete — see recorded error" : NOT_RECORDED,
    artifacts: artifactTitles,
    validationEvidence: completed
      ? `Run registry status "${run?.status}" at ${run?.finished_at ?? run?.updated_at ?? "recorded time"}`
      : NOT_RECORDED,
    unresolvedIssues: failed && run?.error ? [run.error] : [],
  };
}

// ---------- Panel empty state ----------

export const WORKFLOW_PANEL_EMPTY = {
  title: "No cognitive workflow record for this run yet",
  detail: "The Cognitive Workflow orchestrator records its loop server-side and is not exposed to the desktop yet — this panel shows only what existing desktop records carry, marking everything else Not recorded.",
} as const;
