// ZCODE-AMCA-STUDIO-V0.8.0-COGNITIVE-WORKFLOW-UX-ADVANCED-INSIGHTS-01 tests.
// Minimum coverage per the order: summary rendering, stage expansion,
// history grouping, missing data handling, forbidden score detection,
// no inferred explanations.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  EMPTY_NO_INSIGHTS,
  EMPTY_NO_STAGE_DETAILS,
  EMPTY_NO_WORKFLOWS,
  stageDetail,
  whyThisResource,
  workflowHistory,
  historyNote,
  workflowSummaryCard,
} from "../src/cognitiveWorkflowInsightsModel.ts";
import { workflowStages } from "../src/cognitiveWorkflowUxModel.ts";

const run = (overrides = {}) => ({
  run_id: "run-1",
  status: "succeeded",
  created_at: "2026-09-03T10:00:00Z",
  finished_at: "2026-09-03T10:05:00Z",
  ...overrides,
});

const knowledge = (id) => ({
  artifactId: id,
  title: `Report ${id}`,
  typeLabel: "Reports & notes",
  currentVersion: 2,
  shareability: "shareable",
  shareabilityReason: "Locally produced output.",
  provenanceLine: `Project p · agent-7 · ${id}`,
});

// ---------- summary rendering ----------

test("summary card counts only recorded facts — no scores anywhere", () => {
  const stages = workflowStages(run(), [knowledge("a1")]);
  const card = workflowSummaryCard({ stages, knowledgeItems: [knowledge("a1")], resourceCount: 2, artifactCount: 3 });
  assert.equal(card.completedStages, 6);
  assert.equal(card.totalStages, 9);
  assert.equal(card.currentStage.includes("recorded"), true);
  assert.equal(card.knowledgeCount, 1);
  assert.equal(card.resourceCount, 2);
  assert.equal(card.artifactCount, 3);
  assert.equal(card.blockedState, null);
  const rendered = JSON.stringify(card);
  assert.ok(!/score|probability|percent|%|rating|rank/i.test(rendered));
});

test("failed workflow surfaces the blocked stage in the summary", () => {
  const stages = workflowStages(run({ status: "failed", error: "boom" }), []);
  const card = workflowSummaryCard({ stages, knowledgeItems: [], resourceCount: 1, artifactCount: 0 });
  assert.equal(card.blockedState, "Execution completed");
  assert.ok(card.currentStage.includes("failed"));
});

// ---------- stage expansion ----------

test("stage expansion exposes evidence source, timestamp, error — no explanations", () => {
  const stages = workflowStages(run({ status: "failed", error: "converter crashed" }), []);
  const done = stageDetail(stages.find((s) => s.id === "task_created"));
  assert.equal(done.status, "done");
  assert.equal(done.evidenceSource, "Desktop run registry record");
  assert.equal(done.timestamp, "2026-09-03T10:00:00Z");
  assert.equal(done.error, null);

  const failed = stageDetail(stages.find((s) => s.id === "execution_completed"));
  assert.equal(failed.error, "converter crashed");

  const missing = stageDetail(stages.find((s) => s.id === "plan_created"));
  assert.equal(missing.status, "Not recorded");
  assert.ok(missing.evidenceSource.includes("not exposed yet"));
  assert.equal(missing.timestamp, null);
  const allText = JSON.stringify(stageDetail(missing)) + JSON.stringify(done);
  assert.ok(!/because the system decided|the model thinks|inferred/i.test(allText));
});

test("all stages not recorded yields the mandated stage-details empty state", () => {
  const stages = workflowStages(null, []);
  const anyRecorded = stages.some((s) => s.state !== "not_recorded");
  assert.equal(anyRecorded, false);
  assert.equal(EMPTY_NO_STAGE_DETAILS, "Stage details are unavailable because this run does not contain lifecycle records.");
});

// ---------- history grouping ----------

test("history groups per project with statuses and counts — no ranking", () => {
  const groups = workflowHistory([
    { ...run({ run_id: "r1", status: "succeeded" }), project_id: "p1", artifact_count: 2 },
    { ...run({ run_id: "r2", status: "failed", error: "boom" }), project_id: "p1", artifact_count: 0 },
    { ...run({ run_id: "r3", status: "running" }), project_id: "p2", artifact_count: 0 },
  ]);
  assert.equal(groups.length, 2);
  const p1 = groups.find((g) => g.projectId === "p1");
  assert.equal(p1.entries.length, 2);
  assert.equal(p1.failureCount, 1);
  const p2 = groups.find((g) => g.projectId === "p2");
  assert.equal(p2.entries[0].recordedFailure, null);
  const rendered = JSON.stringify(groups);
  assert.ok(!/best|rank|score|top workflow/i.test(rendered));
  assert.ok(historyNote().includes("no ranking, no best-workflow selection, no performance score"));
});

test("runs without a project fall into the unassigned group; empty history yields the mandated empty state", () => {
  const groups = workflowHistory([{ ...run({ run_id: "r9" }) }]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].projectId, "unassigned");
  assert.equal(EMPTY_NO_WORKFLOWS, "No workflow records available yet.");
});

// ---------- decision transparency: recorded fields only ----------

test("why-lines restate recorded fields and never generate explanations", () => {
  const local = whyThisResource({ ref: "ollama:qwen", kind: "model" });
  assert.ok(local.reasons[0].includes("Local model selected (recorded ref prefix)"));

  const provider = whyThisResource({ ref: "cred-7", kind: "model" });
  assert.ok(provider.reasons[0].includes("Governance-approved provider model"));

  const withDecision = whyThisResource({ ref: "ollama:qwen", kind: "model", decisionReason: "matched required_outputs" });
  assert.ok(withDecision.reasons.some((r) => r === "Recorded decision: matched required_outputs"));

  const withCapability = whyThisResource({ ref: "agent-3", kind: "agent", capabilityNote: "retrieval" });
  assert.ok(withCapability.reasons.some((r) => r === "Recorded capability match: retrieval"));
  assert.ok(withCapability.reasons[0].includes("Recorded agent participant"));

  const governed = whyThisResource({ ref: "cred-7", kind: "model", governanceRecorded: true });
  assert.ok(governed.reasons.some((r) => r.includes("exact approval before any network call")));

  const ungoverned = whyThisResource({ ref: "cred-7", kind: "model", governanceRecorded: false });
  assert.ok(!ungoverned.reasons.some((r) => r.includes("Governance status recorded")));

  // blank recorded fields are not quoted — nothing is generated
  const blank = whyThisResource({ ref: "cred-7", kind: "model", decisionReason: "  ", capabilityNote: "" });
  assert.equal(blank.reasons.length, 1);
});

// ---------- insights empty state ----------

test("mandated no-insights empty state is exact", () => {
  assert.equal(EMPTY_NO_INSIGHTS, "No derived insights are available from recorded data.");
});
