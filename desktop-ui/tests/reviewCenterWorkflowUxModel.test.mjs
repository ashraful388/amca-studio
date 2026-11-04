// ZCODE-AMCA-STUDIO-V0.8.0-REVIEW-CENTER-WORKFLOW-UX-ENHANCEMENT-01 tests.
// Order minimum: record-only classification, no ranking, no urgency
// scoring, no inferred severity, navigation safety, canonical wording.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  REVIEW_BOUNDARY,
  NO_DETAIL,
  REVIEW_GROUP_LABELS,
  classifyReviewItems,
  reviewDetail,
  reviewNavigation,
  NAVIGATION_MUTATION_NOTE,
} from "../src/reviewCenterWorkflowUxModel.ts";

const run = (overrides = {}) => ({
  run_id: "run-1",
  status: "succeeded",
  project_id: "p1",
  created_at: "2026-09-03T10:00:00Z",
  finished_at: "2026-09-03T10:05:00Z",
  error: null,
  result: { ok: true },
  ...overrides,
});

// ---------- record-only classification ----------

test("classify groups recorded items into the four mandated groups", () => {
  const groups = classifyReviewItems({
    approvals: [{ id: "apr-1", status: "pending", created_at: "2026-09-01T10:00:00Z", action: "provider_call" }],
    runs: [
      run({ run_id: "r-fail", status: "failed", error: "converter crashed", project_id: "p2" }),
      run({ run_id: "r-ok", status: "succeeded" }),
      run({ run_id: "r-run", status: "running" }),
    ],
    artifactIds: [{ id: "art-1", title: "Report" }],
  });
  const byGroup = Object.fromEntries(groups.map((group) => [group.group, group.items]));
  assert.deepEqual(groups.map((group) => group.group), ["approval_required", "workflow_failed", "verification_available", "artifact_available"]);
  assert.equal(byGroup.approval_required.length, 1);
  assert.equal(byGroup.workflow_failed[0].id, "r-fail");
  assert.equal(byGroup.verification_available[0].id, "r-ok");
  assert.equal(byGroup.artifact_available[0].id, "art-1");
  // running runs are neither failed nor completed — not classified into either group
  assert.ok(!JSON.stringify(byGroup).includes("r-run"));
});

test("non-pending approvals are excluded from the approval group", () => {
  const groups = classifyReviewItems({
    approvals: [{ id: "apr-2", status: "approved", created_at: "2026-09-01T10:00:00Z" }],
    runs: [],
    artifactIds: [],
  });
  assert.equal(groups.length, 0);
});

// ---------- no ranking / no urgency / no severity inference ----------

test("classification output carries no priority, urgency, or severity fields", () => {
  const groups = classifyReviewItems({
    approvals: [{ id: "apr-1", status: "pending", created_at: "2026-09-01T10:00:00Z" }],
    runs: [run({ run_id: "r-fail", status: "failed" })],
    artifactIds: [{ id: "art-1" }],
  });
  const rendered = JSON.stringify(groups).toLowerCase();
  assert.ok(!/priority|urgency|severity|score|rank/.test(rendered));
  // group order is the fixed recorded order, not a ranking by "importance";
  // groups with no recorded items are omitted entirely
  assert.deepEqual(groups.map((group) => group.group), ["approval_required", "workflow_failed", "artifact_available"]);
});

// ---------- detail view (honest gaps) ----------

test("detail view quotes the source record and lists missing data", () => {
  const full = reviewDetail({ kind: "workflow", id: "run-1", status: "succeeded", projectId: "p1", timestamp: "2026-09-03T10:05:00Z", artifactIds: ["a1"], governanceRecorded: true });
  assert.equal(full.sourceRecord, "workflow run-1");
  assert.equal(full.timestamp, "2026-09-03T10:05:00Z");
  assert.equal(full.relatedProject, "p1");
  assert.deepEqual(full.artifacts, ["a1"]);
  assert.equal(full.governanceState, "Governance state recorded");

  const sparse = reviewDetail({ kind: "approval", id: "apr-1" });
  assert.equal(sparse.timestamp, null);
  assert.equal(sparse.relatedProject, null);
  assert.equal(sparse.workflowId, null);
  assert.deepEqual(sparse.artifacts, []);
  assert.deepEqual(sparse.missing, ["timestamp", "related project", "artifacts"]);
  assert.ok(!sparse.missing.includes(NO_DETAIL));
  assert.ok(JSON.stringify(sparse.missing).includes("related project"));
  assert.equal(NO_DETAIL, "No recorded detail available.");
});

// ---------- navigation safety ----------

test("navigation links target existing views; missing links degrade honestly", () => {
  const workflow = reviewNavigation({ kind: "workflow", hasProject: true, hasArtifacts: true });
  assert.equal(workflow[0].target, "agents");
  assert.equal(workflow[1].target, "artifacts");
  assert.equal(workflow[2].target, "overview");

  const approval = reviewNavigation({ kind: "approval", hasProject: false, hasArtifacts: false });
  assert.equal(approval[0].target, "validation");
  assert.equal(approval[1].available, false);
  assert.equal(approval[1].unavailableReason, "No recorded link available.");
  assert.equal(approval[2].unavailableReason, "No recorded link available.");

  const all = JSON.stringify([...workflow, ...approval]).toLowerCase();
  assert.ok(!/approve|reject|cancel|delete|retry/.test(all.replace(/approval_required|approval request/g, "")));
});

test("mutation note states approve/reject lives in existing surfaces", () => {
  assert.ok(NAVIGATION_MUTATION_NOTE.includes("Links navigate to existing views only"));
  assert.ok(NAVIGATION_MUTATION_NOTE.includes("no execution controls"));
});

// ---------- boundary ----------

test("review boundary is verbatim", () => {
  assert.equal(REVIEW_BOUNDARY, "Review items are derived from recorded events only.");
  assert.equal(REVIEW_GROUP_LABELS.approval_required, "Approval required");
  assert.equal(REVIEW_GROUP_LABELS.workflow_failed, "Workflow failed");
  assert.equal(REVIEW_GROUP_LABELS.verification_available, "Verification available");
  assert.equal(REVIEW_GROUP_LABELS.artifact_available, "Artifact available");
});
