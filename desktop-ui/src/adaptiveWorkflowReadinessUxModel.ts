// ZCODE-AMCA-STUDIO-V0.8.0-ADAPTIVE-WORKFLOW-READINESS-UX-FOUNDATION-01 — pure model.
//
// Readiness panel for future adaptive workflow capabilities — WITHOUT
// implementing adaptive decisions. Everything is derived from recorded
// configuration fields (recorded task type, mode, model refs, knowledge
// attachment count, recorded goal):
// - complexity indicators count recorded facts (they are NOT a quality or
//   capability score);
// - the capability matrix restates recorded model fields (locality,
//   governance) — no best-model selection, recommendation, or routing;
// - workflow strategy shows the recorded collaboration mode; absent →
//   "Workflow strategy not recorded.";
// - the boundary explanation states what future adaptive capability could
//   add and what THIS build does not do (no autonomous decisions, no
//   automatic optimization, no execution-strategy change).

import type { RunJob } from "./types";

export const ADAPTIVE_BOUNDARY = {
  headline: "Readiness information from recorded configuration only — no adaptive decision is made.",
  futureCapabilities: [
    "Adaptive planning — AMCA could later plan workflows adaptively.",
    "Model selection — AMCA could later choose models per recorded capability needs.",
    "Agent coordination — AMCA could later coordinate agents across steps.",
  ],
  thisBuildDoesNot: [
    "does not make autonomous decisions",
    "does not optimize automatically",
    "does not change execution strategy",
  ],
} as const;

export const STRATEGY_NOT_RECORDED = "Workflow strategy not recorded.";

// ---------- 1. Complexity indicators (recorded facts, counts only) ----------

export interface ComplexityIndicators {
  recordedTaskType: string | null;
  recordedMode: string | null;
  participatingModelCount: number;
  participatingAgentCount: number;
  knowledgeAttachmentCount: number;
  goalLength: number;
  note: string;
}

/**
 * Counts of recorded configuration fields for one workflow. These indicate
 * how much configuration exists — they are NOT a complexity score, and
 * nothing ranks or compares workflows by them.
 */
export function complexityIndicators(input: {
  goal: string | null | undefined;
  mode: string | null | undefined;
  modelRefs: string[];
  agentIds: string[];
  knowledgeAttachmentCount: number;
}): ComplexityIndicators {
  const taskType = taskTypeFromGoal(input.goal);
  const goalLength = (input.goal ?? "").trim().length;
  return {
    recordedTaskType: taskType,
    recordedMode: input.mode && input.mode.trim() ? input.mode : null,
    participatingModelCount: input.modelRefs.length,
    participatingAgentCount: input.agentIds.length,
    knowledgeAttachmentCount: input.knowledgeAttachmentCount,
    goalLength,
    note: "Counts of recorded configuration fields — indicators, not a complexity score.",
  };
}

/** Task type parsed from the recorded composer goal tag, or null. */
export function taskTypeFromGoal(goal: string | null | undefined): string | null {
  const match = /\[Task type:\s*([^\]]+)\]/.exec(goal ?? "");
  return match ? match[1].trim() : null;
}

// ---------- 2. Capability visibility matrix (recorded fields) ----------

export interface CapabilityMatrixRow {
  ref: string;
  kind: "model" | "agent";
  locality: string;
  governanceStatus: string;
  recordedCapability: string | null;
}

/**
 * One row per recorded resource: locality from the recorded ref prefix,
 * governance restatement, and any recorded capability note verbatim.
 * No best-model selection, no recommendation, no automatic routing.
 */
export function capabilityMatrix(resources: Array<{
  ref: string;
  kind: "model" | "agent";
  governanceRecorded?: boolean;
  recordedCapability?: string | null;
}>): CapabilityMatrixRow[] {
  return resources.map((resource) => ({
    ref: resource.ref,
    kind: resource.kind,
    locality: resource.ref.startsWith("ollama:")
      ? "Local — no network call without approval"
      : "Provider — approval required before any network call",
    governanceStatus: resource.governanceRecorded === false ? "Governance not recorded" : "Recorded: exact approval before any network call",
    recordedCapability: resource.recordedCapability && resource.recordedCapability.trim() ? resource.recordedCapability : null,
  }));
}

// ---------- 3. Workflow strategy visibility ----------

/**
 * Recorded strategy line for a run: the recorded collaboration mode, or
 * the canonical not-recorded line.
 */
export function workflowStrategy(run: Pick<RunJob, "mode"> | null): string {
  const mode = run?.mode && run.mode.trim() ? run.mode : null;
  return mode ?? STRATEGY_NOT_RECORDED;
}

// ---------- panel assembly ----------

export interface AdaptiveReadinessView {
  indicators: ComplexityIndicators;
  capabilityRows: CapabilityMatrixRow[];
  strategy: string;
  boundary: typeof ADAPTIVE_BOUNDARY;
  note: string;
}

export function adaptiveReadinessView(input: {
  run: RunJob | null;
  modelRefs: string[];
  agentIds: string[];
  knowledgeAttachmentCount: number;
}): AdaptiveReadinessView {
  const run = input.run;
  const indicators = complexityIndicators({
    goal: run?.goal ?? null,
    mode: run?.mode ?? null,
    modelRefs: input.modelRefs,
    agentIds: input.agentIds,
    knowledgeAttachmentCount: input.knowledgeAttachmentCount,
  });
  const capabilityRows = capabilityMatrix([
    ...input.modelRefs.map((ref) => ({ ref, kind: "model" as const, governanceRecorded: true })),
    ...input.agentIds.map((ref) => ({ ref, kind: "agent" as const, governanceRecorded: true })),
  ]);
  return {
    indicators,
    capabilityRows,
    strategy: workflowStrategy(run),
    boundary: ADAPTIVE_BOUNDARY,
    note: "Assembled from recorded configuration only — no adaptive decision is made.",
  };
}
