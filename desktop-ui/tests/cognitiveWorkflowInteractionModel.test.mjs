// ZCODE-AMCA-STUDIO-V0.8.0-COGNITIVE-WORKFLOW-UX-INTERACTION-CONTROLS-01 tests.
// Minimum coverage per the order: navigation targets, missing-link handling,
// failed workflow interaction, artifact linking, knowledge linking, no
// execution mutation, no ranking/filter inference.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  EMPTY_FILTER,
  HISTORY_FILTER_NOTE,
  failureReview,
  filterHistory,
  knowledgeInteraction,
  recordedProjects,
  recordedStatuses,
  resourceNav,
  workflowActions,
} from "../src/cognitiveWorkflowInteractionModel.ts";

// ---------- navigation targets + missing-link handling ----------

test("actions navigate only to existing views; missing records render unavailable", () => {
  const full = workflowActions({ hasArtifacts: true, hasKnowledge: true, hasModels: true, hasAgents: true });
  assert.deepEqual(
    full.map((a) => a.target),
    ["artifacts", "knowledge", "settings", "agents"],
  );
  for (const action of full) {
    assert.equal(action.available, true);
    assert.equal(action.unavailableReason, null);
  }

  const sparse = workflowActions({ hasArtifacts: false, hasKnowledge: false, hasModels: true, hasAgents: false });
  const artifacts = sparse.find((a) => a.id === "open_artifacts");
  const knowledge = sparse.find((a) => a.id === "open_knowledge");
  const agents = sparse.find((a) => a.id === "open_agents");
  assert.equal(artifacts.available, false);
  assert.equal(artifacts.target, null);
  assert.ok(artifacts.unavailableReason.includes("No artifacts recorded"));
  assert.ok(knowledge.unavailableReason.includes("No knowledge was attached"));
  assert.ok(agents.unavailableReason.includes("No agents recorded"));
  // models always resolvable in this panel context
  assert.equal(sparse.find((a) => a.id === "open_models").available, true);
});

// ---------- history filters: recorded values only, no inference ----------

test("history filters apply project/status and date range to recorded timestamps", () => {
  const runs = [
    { run_id: "a", project_id: "p1", status: "succeeded", created_at: "2026-09-01T10:00:00Z" },
    { run_id: "b", project_id: "p1", status: "failed", created_at: "2026-09-02T10:00:00Z" },
    { run_id: "c", project_id: "p2", status: "succeeded", created_at: "2026-09-03T10:00:00Z" },
  ];
  assert.equal(filterHistory(runs, { ...EMPTY_FILTER, projectId: "p1" }).length, 2);
  assert.equal(filterHistory(runs, { ...EMPTY_FILTER, status: "succeeded" }).length, 2);
  assert.equal(filterHistory(runs, { ...EMPTY_FILTER, projectId: "p2", status: "succeeded" }).length, 1);
  assert.equal(filterHistory(runs, { ...EMPTY_FILTER, from: "2026-09-02", to: "2026-09-03" }).length, 2);
  // empty filter keeps everything
  assert.equal(filterHistory(runs, EMPTY_FILTER).length, 3);
  // recorded option lists, sorted
  assert.deepEqual(recordedStatuses(runs), ["failed", "succeeded"]);
  assert.deepEqual(recordedProjects(runs), ["p1", "p2"]);
});

test("undated records survive a date filter — never excluded by assumption", () => {
  const runs = [
    { run_id: "dated", project_id: "p1", status: "succeeded", created_at: "2026-09-01T10:00:00Z" },
    { run_id: "undated", project_id: "p1", status: "succeeded", created_at: null },
  ];
  const filtered = filterHistory(runs, { ...EMPTY_FILTER, from: "2026-09-02", to: "2026-09-09" });
  assert.deepEqual(filtered.map((r) => r.run_id), ["undated"]);
  assert.ok(HISTORY_FILTER_NOTE.includes("recorded timestamps only"));
  // forbidden claims must appear only inside negations
  assert.ok(!/^(?!.*no ).*(best workflow|performance score)/im.test(HISTORY_FILTER_NOTE));
  assert.ok(!/best workflow(?!.*selection)/i.test(HISTORY_FILTER_NOTE));
});

// ---------- failed workflow interaction: evidence, no diagnosis ----------

test("failure review quotes the recorded stage, error, artifacts, logs", () => {
  const review = failureReview({
    failedStageLabel: "Execution completed",
    error: "converter crashed",
    artifactIds: ["art-1", "art-2"],
    log: "line1\nline2\nline3",
  });
  assert.equal(review.headline, "View failure details");
  assert.equal(review.failureStage, "Execution completed");
  assert.equal(review.recordedError, "converter crashed");
  assert.deepEqual(review.relatedArtifacts, ["art-1", "art-2"]);
  assert.equal(review.relatedLogs, "line1\nline2\nline3");
  // no diagnosis language
  const rendered = JSON.stringify(review).toLowerCase();
  assert.ok(!/diagnos|cause:|probably|likely/i.test(rendered));
});

test("failure without recorded text says so; without logs yields no fabricated log", () => {
  const review = failureReview({ failedStageLabel: "Plan created", error: null, artifactIds: [], log: null });
  assert.equal(review.recordedError, "Recorded failure (no error text recorded)");
  assert.equal(review.relatedLogs, null);
  assert.deepEqual(review.relatedArtifacts, []);
});

// ---------- knowledge linking (view-only boundary) ----------

test("knowledge interaction is view-only and carries the permission record", () => {
  const interaction = knowledgeInteraction({
    artifactId: "art-9",
    title: "Catalyst report",
    provenanceLine: "Project p · agent-7 · art-9",
    shareability: "shareable",
    shareabilityReason: "Locally produced output.",
  });
  assert.equal(interaction.artifactId, "art-9");
  assert.equal(interaction.permissionRecord, "shareable — Locally produced output.");
  assert.ok(interaction.boundary.includes("View-only from this panel"));
  assert.ok(interaction.boundary.includes("never here"));
});

// ---------- resource navigation ----------

test("resource navigation opens the recorded details view with locality label", () => {
  const local = resourceNav("ollama:qwen", "model");
  assert.equal(local.target, "settings");
  assert.equal(local.localityLabel, "Local model");
  const provider = resourceNav("cred-7", "model");
  assert.equal(provider.localityLabel, "Provider model — approval required");
  const agent = resourceNav("agent-3", "agent");
  assert.equal(agent.target, "agents");
  assert.equal(agent.localityLabel, "Recorded agent participant");
});

// ---------- no execution mutation ----------

test("interaction surface contains no mutating verbs", () => {
  const actions = workflowActions({ hasArtifacts: true, hasKnowledge: true, hasModels: true, hasAgents: true });
  const rendered = JSON.stringify(actions).toLowerCase();
  for (const verb of ["cancel", "retry", "rerun", "re-run", "restart", "resume", "delete", "approve"]) {
    assert.ok(!rendered.includes(verb), `forbidden mutating verb: ${verb}`);
  }
  // action ids are navigation-only
  assert.deepEqual(
    actions.map((a) => a.id),
    ["open_artifacts", "open_knowledge", "open_models", "open_agents"],
  );
});
