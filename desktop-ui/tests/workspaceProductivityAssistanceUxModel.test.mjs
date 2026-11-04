// ZCODE-AMCA-STUDIO-V0.8.0-WORKSPACE-PRODUCTIVITY-ASSISTANCE-UX-FOUNDATION-01 tests.
// Order minimum: record-only summaries, no recommendation claims, no
// ranking, no urgency scoring, no automatic resume, canonical wording,
// empty states.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ACTIVITY_SUMMARY_BOUNDARY,
  CONTINUATION_NOTE,
  CONTINUATION_EMPTY_STATES,
  RESUME_PREPARATION_NOTE,
  SEARCH_INTEGRATION_NOTE,
  SEARCH_ENTRY_POINTS,
  workspaceActivitySummary,
  continuationEntries,
  resumeContext,
  taskTypeFromGoal,
} from "../src/workspaceProductivityAssistanceUxModel.ts";

const run = (overrides = {}) => ({
  run_id: "run-1",
  project_id: "p1",
  status: "succeeded",
  created_at: "2026-09-03T10:00:00Z",
  finished_at: "2026-09-03T10:05:00Z",
  goal: "[Task type: Research] Compare catalysts",
  model: "ollama:qwen",
  error: null,
  ...overrides,
});

// ---------- activity summary (recorded events only) ----------

test("activity summary lists recorded recency with the boundary verbatim", () => {
  const summary = workspaceActivitySummary({
    runs: [
      run({ run_id: "r1", created_at: "2026-09-02T10:00:00Z" }),
      run({ run_id: "r2", created_at: "2026-09-03T10:00:00Z" }),
      run({ run_id: "r-undated", created_at: null }),
    ],
    artifacts: [{ artifact_id: "a1", title: "Report", created_at: "2026-09-03T11:00:00Z" }],
    knowledgeAttachmentCount: 2,
    pendingReviews: 1,
    completedWorkflowCount: 3,
  });
  // undated runs are excluded from "recent" honestly — not invented timestamps
  assert.deepEqual(summary.recentWorkflows.map((w) => w.id), ["r2", "r1"]);
  assert.deepEqual(summary.recentArtifacts.map((a) => a.id), ["a1"]);
  assert.equal(summary.recentKnowledgeAttachments, 2);
  assert.equal(summary.pendingReviews, 1);
  assert.equal(summary.reusableWorkflowExamples, 3);
  assert.equal(summary.boundary, "Workspace activity summary from recorded events only.");
});

// ---------- continue work surface (recorded facts, no ranking) ----------

test("continue work lists unfinished workflows in recorded recency order", () => {
  const entries = continuationEntries([
    run({ run_id: "r-old", status: "running", created_at: "2026-09-01T10:00:00Z" }),
    run({ run_id: "r-new", status: "partial", created_at: "2026-09-03T10:00:00Z" }),
    run({ run_id: "r-done", status: "succeeded", created_at: "2026-09-03T11:00:00Z" }),
  ]);
  // succeeded run is finished — not in the continuation list
  assert.deepEqual(entries.map((entry) => entry.workflowId), ["r-new", "r-old"]);
  assert.equal(entries[0].taskType, "Research");
  const rendered = JSON.stringify(entries).toLowerCase();
  assert.ok(!/recommended next|priorit|urgency/.test(rendered));
});

test("continuation empty state is the mandated wording", () => {
  assert.equal(continuationEntries([run({ run_id: "r-done", status: "succeeded" })]).length, 0);
  assert.equal(CONTINUATION_EMPTY_STATES.no_unfinished, "No unfinished workflows recorded.");
  assert.equal(CONTINUATION_EMPTY_STATES.no_pending_reviews, "No pending reviews recorded.");
  assert.equal(CONTINUATION_EMPTY_STATES.no_projects, "No recently active projects recorded.");
});

// ---------- context resume (preparation only) ----------

test("resume context is displayed preparation, never automatic loading", () => {
  const context = resumeContext(run({ goal: "[Task type: Coding] fix" }), [knowledge("k1"), knowledge("k2")], ["agent-1"]);
  assert.equal(context.taskType, "Coding");
  assert.equal(context.attachedKnowledge, 2);
  assert.deepEqual(context.modelsAgents, ["ollama:qwen", "agent-1"]);
  assert.equal(context.note, RESUME_PREPARATION_NOTE);
  assert.ok(RESUME_PREPARATION_NOTE.includes("nothing is loaded automatically"));
  const untyped = resumeContext(run({ goal: "no tag" }), [], []);
  assert.equal(untyped.taskType, null);
});

test("recorded task type parses only the existing goal tag", () => {
  assert.equal(taskTypeFromGoal("[Task type: Coding] build"), "Coding");
  assert.equal(taskTypeFromGoal("no tag"), null);
  assert.equal(taskTypeFromGoal(null), null);
});

function knowledge(id) {
  return {
    artifactId: id,
    title: `Report ${id}`,
    typeLabel: "Reports & notes",
    currentVersion: 1,
    shareability: "shareable",
    shareabilityReason: "Locally produced output.",
    provenanceLine: `Project p · agent-7 · ${id}`,
  };
}

// ---------- search integration ----------

test("search entry points target existing record surfaces with honest notes", () => {
  assert.equal(SEARCH_ENTRY_POINTS.length, 5);
  for (const entry of SEARCH_ENTRY_POINTS) {
    assert.ok(entry.recordKind.includes("recorded"), entry.label);
  }
  assert.ok(SEARCH_INTEGRATION_NOTE.includes("no semantic recommendation"));
  assert.ok(SEARCH_INTEGRATION_NOTE.includes("no hidden retrieval"));
});

// ---------- canonical boundary ----------

test("activity summary boundary is verbatim", () => {
  assert.equal(ACTIVITY_SUMMARY_BOUNDARY, "Workspace activity summary from recorded events only.");
  assert.ok(CONTINUATION_NOTE.includes("no recommended next action"));
  assert.ok(CONTINUATION_NOTE.includes("no AI prioritization"));
  assert.ok(CONTINUATION_NOTE.includes("no urgency ranking"));
});
