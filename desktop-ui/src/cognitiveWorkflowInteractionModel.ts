// ZCODE-AMCA-STUDIO-V0.8.0-COGNITIVE-WORKFLOW-UX-INTERACTION-CONTROLS-01 — pure model.
//
// Safe interaction controls around the cognitive workflow transparency
// layer. Every action is navigation-only — to existing views over existing
// records. Rules recorded by the order:
// - no execution bypass, no hidden state modification, no mutation of any
//   workflow state (the panel never cancels, retries, or re-runs);
// - history filters: project / status / date range over RECORDED
//   timestamps only — no quality/performance ranking, no "best workflow";
// - failure review: failure stage + recorded error + related artifacts +
//   logs if already available + "View failure details" — NO automatic
//   diagnosis;
// - knowledge interaction: open item / view provenance / view permission —
//   never attach, retrieve, or modify from this panel;
// - missing links render an honest "unavailable" action, never a guessed
//   target.


// ---------- 1. Navigation targets (existing views only) ----------

/** The app's existing views an action may navigate to — no others exist. */
export type WorkflowNavTarget =
  | "artifacts"
  | "overview"
  | "settings"
  | "agents"
  | "knowledge";

export interface WorkflowAction {
  id: string;
  label: string;
  target: WorkflowNavTarget | null;
  /** True when the record needed for the target exists. */
  available: boolean;
  unavailableReason: string | null;
}

/**
 * Actions for one workflow. Every target is an existing view; an action
 * whose record is missing renders as honestly unavailable instead of
 * pointing at a guessed destination.
 */
export function workflowActions(input: {
  hasArtifacts: boolean;
  hasKnowledge: boolean;
  hasModels: boolean;
  hasAgents: boolean;
}): WorkflowAction[] {
  return [
    action("open_artifacts", "Open related artifacts", input.hasArtifacts, "No artifacts recorded for this workflow."),
    action("open_knowledge", "Open knowledge references", input.hasKnowledge, "No knowledge was attached to this workflow."),
    action("open_models", "Open model details", input.hasModels, "No model selection recorded for this workflow."),
    action("open_agents", "Open agent details", input.hasAgents, "No agents recorded for this workflow."),
  ];
}

function action(id: string, label: string, available: boolean, reason: string): WorkflowAction {
  return available
    ? { id, label, target: targetFor(id), available: true, unavailableReason: null }
    : { id, label, target: null, available: false, unavailableReason: reason };
}

function targetFor(id: string): WorkflowNavTarget {
  switch (id) {
    case "open_artifacts": return "artifacts";
    case "open_knowledge": return "knowledge";
    case "open_models": return "settings";
    case "open_agents": return "agents";
    default: return "overview";
  }
}

// ---------- 2. History filters (recorded timestamps only) ----------

export interface HistoryFilter {
  projectId: string | null;
  status: string | null;
  /** Inclusive ISO bounds applied ONLY to recorded timestamps. */
  from: string | null;
  to: string | null;
}

export const EMPTY_FILTER: HistoryFilter = { projectId: null, status: null, from: null, to: null };

export function filterHistory<
  T extends { project_id?: string; status: string; created_at: string | null },
>(runs: T[], filter: HistoryFilter): T[] {
  return runs.filter((record) => {
    if (filter.projectId && (record.project_id ?? "unassigned") !== filter.projectId) return false;
    if (filter.status && record.status !== filter.status) return false;
    // Date range applies only when the record carries a recorded timestamp —
    // undated records are shown regardless of the range (never excluded by
    // an assumption). Bounds arrive as date-only strings from the date
    // inputs: "from" covers the whole from-day, "to" through the whole
    // to-day, so end-of-day records are not silently dropped.
    if ((filter.from || filter.to) && record.created_at) {
      if (filter.from && record.created_at < filter.from) return false;
      if (filter.to && record.created_at > `${filter.to}T23:59:59.999Z` && !record.created_at.startsWith(filter.to)) return false;
    }
    return true;
  });
}

/** Recorded statuses present in the history — the only filter options. */
export function recordedStatuses(runs: Array<{ status: string }>): string[] {
  return [...new Set(runs.map((record) => record.status))].sort();
}

/** Recorded projects present in the history — the only filter options. */
export function recordedProjects(runs: Array<{ project_id?: string }>): string[] {
  return [...new Set(runs.map((record) => record.project_id ?? "unassigned"))].sort();
}

export const HISTORY_FILTER_NOTE =
  "Filters apply to recorded values only — project, status, and recorded timestamps only. No quality or performance ranking exists.";

// ---------- 3. Failure review (recorded evidence, no diagnosis) ----------

export interface FailureReview {
  failureStage: string;
  recordedError: string | null;
  relatedArtifacts: string[];
  relatedLogs: string | null;
  headline: string;
}

/**
 * Failure review for a failed workflow. Everything quotes records: the
 * failed stage label, the recorded error, artifact ids the record carries,
 * and the run log tail if the record already includes it. No diagnosis,
 * no cause attribution beyond the recorded text.
 */
export function failureReview(input: {
  failedStageLabel: string;
  error: string | null;
  artifactIds: string[];
  log?: string | null;
}): FailureReview | null {
  return {
    failureStage: input.failedStageLabel,
    recordedError: input.error && input.error.trim() ? input.error : "Recorded failure (no error text recorded)",
    relatedArtifacts: input.artifactIds,
    relatedLogs: input.log && input.log.trim() ? input.log.split("\n").slice(-14).join("\n") : null,
    headline: "View failure details",
  };
}

// ---------- 4. Knowledge interaction (view-only) ----------

export interface KnowledgeInteraction {
  artifactId: string;
  title: string;
  provenance: string;
  permissionRecord: string;
  /** What the panel will NOT do — rendered so the boundary is visible. */
  boundary: string;
}

export function knowledgeInteraction(item: {
  artifactId: string;
  title: string;
  provenanceLine: string;
  shareability: string;
  shareabilityReason: string;
}): KnowledgeInteraction {
  return {
    artifactId: item.artifactId,
    title: item.title,
    provenance: item.provenanceLine,
    permissionRecord: `${item.shareability} — ${item.shareabilityReason}`,
    boundary: "View-only from this panel — attaching, retrieval, and workflow modification happen in the composer, never here.",
  };
}

// ---------- 5. Resource navigation (existing details views) ----------

export interface ResourceNav {
  ref: string;
  target: "settings" | "agents";
  /** Recorded locality label shown next to the link. */
  localityLabel: string;
}

/** Model refs open the existing Model Hub (settings); agents open Agents. */
export function resourceNav(ref: string, kind: "model" | "agent"): ResourceNav {
  return kind === "model"
    ? { ref, target: "settings", localityLabel: ref.startsWith("ollama:") ? "Local model" : "Provider model — approval required" }
    : { ref, target: "agents", localityLabel: "Recorded agent participant" };
}
