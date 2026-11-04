// ZCODE-AMCA-STUDIO-V0.8.0-CROSS-MODULE-WORKFLOW-CONTINUITY-01 — pure model.
//
// Cross-module continuity traces built ONLY from existing records: the
// desktop run registry (projects/workflows), the artifact registry, the
// composer's explicit knowledge selection, and recorded participant refs.
// Rules recorded by the order:
// - only recorded links appear; a link without a record degrades to the
//   honest "not available" line instead of being inferred;
// - no ranking, no performance scores, no usage analytics;
// - knowledge→workflow traces exist ONLY where the user explicitly
//   attached the item (the composer selection) — nothing else counts as a
//   workflow attachment record.

import type { RunJob } from "./types";
import type { KnowledgeContextItem } from "./knowledgeContextModel";

// ---------- 1. Project cognitive history (records only) ----------

export interface ProjectWorkflowEntry {
  workflowId: string;
  status: string;
  createdAt: string | null;
  modelRefs: string[];
  agentCount: number;
  artifactCount: number;
  knowledgeCount: number;
  recordedFailure: string | null;
}

export interface ProjectCognitiveHistory {
  projectId: string;
  entries: ProjectWorkflowEntry[];
  totals: {
    workflows: number;
    artifacts: number;
    knowledgeAttachments: number;
    recordedFailures: number;
  };
  note: string;
}

/**
 * Group recorded runs of one project into a cognitive history. Artifacts
 * are attributed to the run that produced them via the record's
 * `result.artifact_ids` when present; knowledge via the explicit selection
 * passed by the caller. No cross-record inference.
 */
export function projectCognitiveHistory(
  projectId: string,
  runs: Array<RunJob & { artifact_ids?: string[]; knowledgeItems?: KnowledgeContextItem[]; agentIds?: string[] }>,
): ProjectCognitiveHistory {
  const entries = runs
    .filter((record) => record.project_id === projectId || projectId === "unassigned")
    .map((record) => {
      const failure =
        record.status === "failed" || record.status === "cancelled"
          ? record.error && record.error.trim()
            ? record.error
            : "Recorded failure (no error text recorded)"
          : null;
      return {
        workflowId: record.run_id,
        status: record.status,
        createdAt: record.created_at ?? null,
        modelRefs: [record.model].filter((ref): ref is string => Boolean(ref)),
        agentCount: record.agentIds?.length ?? 0,
        artifactCount: record.artifact_ids?.length ?? 0,
        knowledgeCount: record.knowledgeItems?.length ?? 0,
        recordedFailure: failure,
      };
    });
  return {
    projectId,
    entries,
    totals: {
      workflows: entries.length,
      artifacts: entries.reduce((sum, entry) => sum + entry.artifactCount, 0),
      knowledgeAttachments: entries.reduce((sum, entry) => sum + entry.knowledgeCount, 0),
      recordedFailures: entries.filter((entry) => entry.recordedFailure !== null).length,
    },
    note: "Records only — statuses and counts; no ranking, no performance score.",
  };
}

// ---------- 2. Artifact → workflow trace ----------

export interface ArtifactWorkflowTrace {
  artifactId: string;
  originatingWorkflowId: string | null;
  projectId: string | null;
  recordedCreator: string | null;
  relatedKnowledge: string[];
  unavailableReason: string | null;
}

/**
 * Trace an artifact back to its originating workflow through the recorded
 * `result.artifact_ids` linkage. When no recorded linkage exists the trace
 * degrades honestly — no inferred relationships.
 */
export function artifactWorkflowTrace(
  artifactId: string,
  runs: Array<RunJob & { artifact_ids?: string[]; knowledgeItems?: KnowledgeContextItem[] }>,
  projectId: string | null,
  recordedCreator: string | null,
): ArtifactWorkflowTrace {
  const record = runs.find((candidate) => candidate.artifact_ids?.includes(artifactId));
  if (!record) {
    return {
      artifactId,
      originatingWorkflowId: null,
      projectId,
      recordedCreator,
      relatedKnowledge: [],
      unavailableReason: "No workflow attachment record available.",
    };
  }
  return {
    artifactId,
    originatingWorkflowId: record.run_id,
    projectId: record.project_id,
    recordedCreator,
    relatedKnowledge: (record.knowledgeItems ?? []).map((item) => item.artifactId),
    unavailableReason: null,
  };
}

// ---------- 3. Knowledge → workflow trace (explicit attachments only) ----------

export interface KnowledgeWorkflowTrace {
  artifactId: string;
  attachedToWorkflows: string[];
  provenance: string;
  permissionStatus: string;
  unavailableReason: string | null;
}

/**
 * Workflows where this knowledge item was EXPLICITLY attached by the user.
 * Attachment comes only from the recorded composer selection; surfaces
 * without such records render the mandated honest line.
 */
export function knowledgeWorkflowTrace(
  item: KnowledgeContextItem,
  attachmentIndex: Record<string, string[]>,
): KnowledgeWorkflowTrace {
  const workflowIds = attachmentIndex[item.artifactId] ?? [];
  return {
    artifactId: item.artifactId,
    attachedToWorkflows: workflowIds,
    provenance: item.provenanceLine,
    permissionStatus: `${item.shareability} — ${item.shareabilityReason}`,
    unavailableReason:
      workflowIds.length > 0 ? null : "No workflow attachment record available.",
  };
}

// ---------- 4. Model / agent usage references (records, no ranking) ----------

export interface ModelUsageReference {
  ref: string;
  workflowsParticipated: string[];
  locality: string;
  governanceStatus: string;
}

export interface AgentUsageReference {
  agentId: string;
  workflowsParticipated: string[];
}

/** Recorded participation from the run registry — a list, never a ranking. */
export function modelUsageReference(
  ref: string,
  runs: Array<Pick<RunJob, "run_id" | "model">>,
): ModelUsageReference {
  const workflowsParticipated = runs
    .filter((record) => record.model === ref)
    .map((record) => record.run_id);
  return {
    ref,
    workflowsParticipated,
    locality: ref.startsWith("ollama:") ? "Local model" : "Provider model — approval required",
    governanceStatus: "Exact approval before any network call",
  };
}

export function agentUsageReference(
  agentId: string,
  runs: Array<Pick<RunJob, "run_id"> & { agentIds?: string[] }>,
): AgentUsageReference {
  return {
    agentId,
    workflowsParticipated: runs
      .filter((record) => record.agentIds?.includes(agentId))
      .map((record) => record.run_id),
  };
}

// ---------- 5. Honest degradation ----------

export const CONTINUITY_NOTE =
  "Traces use existing records only — links appear when a record carries them and degrade honestly when it does not; no inferred relationships, no analytics scoring.";
