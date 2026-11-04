// ZCODE-AMCA-STUDIO-V0.8.0-REVIEW-CENTER-WORKFLOW-UX-ENHANCEMENT-01 — pure model.
//
// Connects existing records (approvals, failed runs, completed runs with
// verification evidence, artifacts) into the Review Center as classified,
// navigable review groups. Rules recorded by the order:
// - classification is derived ONLY from recorded state — no AI priority,
//   no urgency prediction, no severity inference;
// - detail views quote the source record (timestamp, project, workflow id,
//   artifacts, governance state); missing data renders
//   "No recorded detail available.";
// - navigation targets existing views only, with NO mutation actions —
//   approve/reject stays wherever it already exists.



export const REVIEW_BOUNDARY = "Review items are derived from recorded events only.";
export const NO_DETAIL = "No recorded detail available.";

// ---------- 1. Classification (recorded state only) ----------

export type ReviewGroup =
  | "approval_required"
  | "workflow_failed"
  | "verification_available"
  | "artifact_available";

export const REVIEW_GROUP_LABELS: Record<ReviewGroup, string> = {
  approval_required: "Approval required",
  workflow_failed: "Workflow failed",
  verification_available: "Verification available",
  artifact_available: "Artifact available",
};

export interface ReviewSourceRecord {
  kind: "approval" | "workflow";
  id: string;
  status: string;
  projectId: string | null;
  timestamp: string | null;
  detail: string | null;
  artifactIds: string[];
}

/**
 * Classify recorded items into the four groups. An approval record with a
 * pending state classifies as "Approval required"; a failed run as
 * "Workflow failed"; a completed run as "Verification available" when it
 * carries verification evidence (result record); an artifact record as
 * "Artifact available". Nothing is scored, ranked, or prioritized — the
 * order within each group is the record's own order.
 */
export function classifyReviewItems(input: {
  approvals: Array<{ id: string; status: string; created_at: string | null; action?: string }>;
  runs: Array<{
    run_id: string;
    status: string;
    project_id: string | null;
    created_at?: string | null;
    finished_at?: string | null;
    error?: string | null;
    result?: Record<string, unknown> | null;
  }>;
  artifactIds: Array<{ id: string; title?: string }>;
}): Array<{ group: ReviewGroup; label: string; items: ReviewSourceRecord[] }> {
  const items: Array<{ group: ReviewGroup; label: string; items: ReviewSourceRecord[] }> = [
    { group: "approval_required", label: REVIEW_GROUP_LABELS.approval_required, items: [] },
    { group: "workflow_failed", label: REVIEW_GROUP_LABELS.workflow_failed, items: [] },
    { group: "verification_available", label: REVIEW_GROUP_LABELS.verification_available, items: [] },
    { group: "artifact_available", label: REVIEW_GROUP_LABELS.artifact_available, items: [] },
  ];
  for (const approval of input.approvals) {
    if (approval.status !== "pending") continue;
    items[0].items.push({
      kind: "approval",
      id: approval.id,
      status: approval.status,
      projectId: null,
      timestamp: approval.created_at ?? null,
      detail: approval.action ?? null,
      artifactIds: [],
    });
  }
  for (const run of input.runs) {
    if (run.status === "failed" || run.status === "cancelled") {
      items[1].items.push({
        kind: "workflow",
        id: run.run_id,
        status: run.status,
        projectId: run.project_id,
        timestamp: run.finished_at ?? run.created_at ?? null,
        detail: run.error ?? null,
        artifactIds: [],
      });
      continue;
    }
    if (run.status === "succeeded" || run.status === "partial") {
      items[2].items.push({
        kind: "workflow",
        id: run.run_id,
        status: run.status,
        projectId: run.project_id,
        timestamp: run.finished_at ?? run.created_at ?? null,
        detail: run.result ? "Run result recorded" : null,
        // run.error may be undefined per the structural input; null it.
        artifactIds: [],
      });
    }
  }
  for (const artifact of input.artifactIds) {
    items[3].items.push({
      kind: "workflow",
      id: artifact.id,
      status: "recorded",
      projectId: null,
      timestamp: null,
      detail: artifact.title ?? null,
      artifactIds: [artifact.id],
    });
  }
  return items.filter((group) => group.items.length > 0);
}

// ---------- 2. Detail view (recorded fields, honest gaps) ----------

export interface ReviewDetail {
  sourceRecord: string;
  timestamp: string | null;
  relatedProject: string | null;
  workflowId: string | null;
  artifacts: string[];
  governanceState: string;
  missing: string[];
}

export function reviewDetail(record: {
  kind: "approval" | "workflow";
  id: string;
  status?: string | null;
  projectId?: string | null;
  timestamp?: string | null;
  artifactIds?: string[];
  governanceRecorded?: boolean;
}): ReviewDetail {
  const missing: string[] = [];
  const timestamp = record.timestamp ?? null;
  if (!timestamp) missing.push("timestamp");
  const relatedProject = record.projectId ?? null;
  if (!relatedProject) missing.push("related project");
  const workflowId = record.kind === "workflow" ? record.id : null;
  const artifacts = record.artifactIds ?? [];
  if (artifacts.length === 0) missing.push("artifacts");
  return {
    sourceRecord: `${record.kind} ${record.id}`,
    timestamp,
    relatedProject,
    workflowId,
    artifacts,
    governanceState:
      record.governanceRecorded === true
        ? "Governance state recorded"
        : record.governanceRecorded === false
          ? "Governance state not recorded"
          : "Not recorded",
    missing,
  };
}

// ---------- 3. Navigation (existing views only, no mutation) ----------

export type ReviewNavTarget = "agents" | "artifacts" | "validation" | "overview";

export interface ReviewNavLink {
  label: string;
  target: ReviewNavTarget | null;
  available: boolean;
  unavailableReason: string | null;
}

export function reviewNavigation(record: {
  kind: "approval" | "workflow";
  hasProject: boolean;
  hasArtifacts: boolean;
}): ReviewNavLink[] {
  const links: ReviewNavLink[] = [];
  links.push(
    record.kind === "workflow"
      ? { label: "Open in Agent Monitor", target: "agents", available: true, unavailableReason: null }
      : { label: "Open in Validation Center", target: "validation", available: true, unavailableReason: null },
  );
  links.push(
    record.hasArtifacts
      ? { label: "Open related artifacts", target: "artifacts", available: true, unavailableReason: null }
      : { label: "Open related artifacts", target: null, available: false, unavailableReason: "No recorded link available." },
  );
  links.push(
    record.hasProject
      ? { label: "Open project workspace", target: "overview", available: true, unavailableReason: null }
      : { label: "Open project workspace", target: null, available: false, unavailableReason: "No recorded link available." },
  );
  return links;
}

/** Approve/reject is NOT part of this enhancement — it lives where it already exists. */
export const NAVIGATION_MUTATION_NOTE =
  "Links navigate to existing views only. Approve/reject actions remain in their existing surfaces — this panel adds no execution controls.";
