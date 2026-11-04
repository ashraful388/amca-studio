// ZCODE-AMCA-STUDIO-V0.8.0-PROJECT-COLLABORATION-WORKSPACE-UX-01 tests.
// Order minimum: record-only data, no collaboration claims, no ranking,
// no inferred relationships, empty states, canonical wording.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  COLLABORATION_BOUNDARIES,
  COLLABORATION_EMPTY_STATES,
  COLLABORATION_NO_WORKFLOWS,
  COLLABORATION_NO_ARTIFACTS,
  COLLABORATION_NO_REVIEW,
  projectCollaborationOverview,
  projectActivityFeed,
  reusableWorkflowPatterns,
  projectReviewSummary,
  recordedTaskType,
} from "../src/projectCollaborationWorkspaceUxModel.ts";

const run = (overrides = {}) => ({
  run_id: "run-1",
  project_id: "p1",
  status: "succeeded",
  created_at: "2026-09-03T10:00:00Z",
  goal: "[Task type: Research] Compare catalysts",
  model: "ollama:qwen",
  error: null,
  ...overrides,
});

const knowledge = (id) => ({
  artifactId: id,
  title: `Report ${id}`,
  typeLabel: "Reports & notes",
  currentVersion: 1,
  shareability: "shareable",
  shareabilityReason: "Locally produced output.",
  provenanceLine: `Project p · agent-7 · ${id}`,
});

// ---------- overview ----------

test("overview reports recorded counts and participants; boundary verbatim", () => {
  const overview = projectCollaborationOverview({
    purpose: "Catalyst research",
    runs: [run({ model: "ollama:qwen" }), run({ run_id: "r2", model: "ollama:qwen" }), run({ run_id: "r3", model: "cred-7" })],
    artifactCount: 3,
    knowledgeAttachmentCount: 2,
    agentIds: ["agent-1", "agent-2"],
  });
  assert.equal(overview.workflowsExecuted, 3);
  assert.equal(overview.artifactsGenerated, 3);
  assert.equal(overview.knowledgeUsed, 2);
  assert.deepEqual(overview.participatingModels, ["ollama:qwen", "cred-7"]);
  assert.deepEqual(overview.participatingAgents, ["agent-1", "agent-2"]);
  assert.equal(overview.boundary, "Recorded project activity only — no productivity score or project quality ranking.");
});

test("null purpose renders as null (caller shows Not recorded) — no invention", () => {
  assert.equal(projectCollaborationOverview({ purpose: null, runs: [], artifactCount: 0, knowledgeAttachmentCount: 0, agentIds: [] }).purpose, null);
  assert.equal(projectCollaborationOverview({ purpose: "  ", runs: [], artifactCount: 0, knowledgeAttachmentCount: 0, agentIds: [] }).purpose, null);
});

// ---------- activity feed ----------

test("activity feed merges all five event kinds newest first, undated last", () => {
  const feed = projectActivityFeed({
    runs: [run({ run_id: "r1", created_at: "2026-09-02T10:00:00Z" })],
    artifacts: [{ artifact_id: "a1abcdef1234", title: "Report", created_at: "2026-09-03T09:00:00Z" }],
    approvals: [{ id: "apr-1", action: "provider_call", status: "pending", created_at: "2026-09-01T10:00:00Z" }],
    automationOccurrences: [{ automationId: "auto-1", status: "succeeded", scheduled_for: "2026-09-04T09:00:00Z" }],
  });
  assert.deepEqual(feed.map((e) => e.kind), ["automation", "artifact", "workflow", "approval"]);
  assert.equal(feed[0].detail.includes("auto-1"), true);
});

test("knowledge attachment events without timestamps are preserved as undated", () => {
  const feed = projectActivityFeed({
    runs: [run({ run_id: "r1", knowledgeCount: 2 })],
    artifacts: [],
    approvals: [],
    automationOccurrences: [],
  });
  const knowledgeEvents = feed.filter((event) => event.kind === "knowledge");
  assert.equal(knowledgeEvents.length, 2);
  for (const event of knowledgeEvents) {
    assert.equal(event.timestamp, null);
    assert.ok(event.detail.includes("explicitly attached by the user"));
  }
});

// ---------- reusable workflow patterns (visibility only) ----------

test("patterns group completed workflows by recorded task type", () => {
  const summary = reusableWorkflowPatterns([
    run({ run_id: "r1", goal: "[Task type: Research] A", artifactCount: 2, agentCount: 1 }),
    run({ run_id: "r2", goal: "[Task type: Research] B", artifactCount: 1, agentCount: 0 }),
    run({ run_id: "r3", status: "failed", goal: "[Task type: Research] C" }),
    run({ run_id: "r4", goal: "Untagged goal", status: "succeeded" }),
  ]);
  assert.equal(summary.patterns.length, 2);
  const research = summary.patterns.find((pattern) => pattern.taskType === "Research");
  assert.equal(research.workflows.length, 2);
  assert.equal(research.artifactCount, 3);
  const untyped = summary.patterns.find((pattern) => pattern.taskType === "Untyped");
  assert.ok(untyped);
  // failed workflows are not patterns — only recorded completed evidence
  assert.ok(!summary.patterns.some((pattern) => pattern.workflows.includes("r3")));
  assert.equal(summary.note, "Visibility only — no cloning engine, no automatic recommendations, no optimization ranking.");
});

test("no completed workflows yields the mandated patterns empty state", () => {
  assert.equal(reusableWorkflowPatterns([]).emptyReason, COLLABORATION_EMPTY_STATES.no_patterns);
  assert.equal(COLLABORATION_EMPTY_STATES.no_patterns, "No reusable workflow patterns recorded yet — patterns appear after workflows complete.");
});

// ---------- review summary ----------

test("review summary counts recorded evidence and carries the boundary", () => {
  const summary = projectReviewSummary({
    runs: [run({ run_id: "r1", status: "succeeded" }), run({ run_id: "r2", status: "failed" }), run({ run_id: "r3", status: "running" })],
    pendingGovernanceActions: 2,
    availableArtifacts: 4,
    availableKnowledgeReferences: 1,
  });
  assert.equal(summary.completedWorkflows, 1);
  assert.equal(summary.failedWorkflows, 1);
  assert.equal(summary.pendingGovernanceActions, 2);
  assert.equal(summary.availableArtifacts, 4);
  assert.equal(summary.availableKnowledgeReferences, 1);
  assert.equal(summary.boundary, "Summary of recorded project evidence only.");
});

test("empty project review yields the mandated no-review state", () => {
  const summary = projectReviewSummary({ runs: [], pendingGovernanceActions: 0, availableArtifacts: 0, availableKnowledgeReferences: 0 });
  assert.equal(COLLABORATION_NO_REVIEW, "No recorded project evidence to summarize yet.");
  assert.equal(summary.completedWorkflows + summary.failedWorkflows, 0);
});

// ---------- task type helper + canonical wording ----------

test("recorded task type parses only the existing goal tag", () => {
  assert.equal(recordedTaskType("[Task type: Coding] build"), "Coding");
  assert.equal(recordedTaskType("no tag"), null);
  assert.equal(recordedTaskType(null), null);
});

test("boundaries carry canonical wording with no collaboration claims", () => {
  const rendered = JSON.stringify(COLLABORATION_BOUNDARIES).toLowerCase();
  assert.ok(rendered.includes("no productivity score"));
  assert.ok(rendered.includes("project quality ranking"));
  assert.ok(rendered.includes("single-operator"));
  assert.ok(rendered.includes("no collaboration service"));
  assert.ok(!/team score|collaboration score|productivity score: \d/.test(rendered));
  assert.equal(COLLABORATION_NO_WORKFLOWS, "No workflow records available yet.");
  assert.equal(COLLABORATION_NO_ARTIFACTS, "No artifacts recorded for this project yet.");
});
