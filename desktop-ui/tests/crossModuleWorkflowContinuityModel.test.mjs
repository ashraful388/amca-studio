// ZCODE-AMCA-STUDIO-V0.8.0-CROSS-MODULE-WORKFLOW-CONTINUITY-01 tests.
// Order minimum: only recorded links appear, missing links degrade
// honestly, no inferred relationships, no analytics scoring.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  projectCognitiveHistory,
  artifactWorkflowTrace,
  knowledgeWorkflowTrace,
  modelUsageReference,
  agentUsageReference,
  CONTINUITY_NOTE,
} from "../src/crossModuleWorkflowContinuityModel.ts";

const run = (overrides = {}) => ({
  run_id: "run-1",
  project_id: "p1",
  status: "succeeded",
  created_at: "2026-09-03T10:00:00Z",
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

// ---------- 1. project cognitive history ----------

test("project history groups recorded runs with counts only", () => {
  const history = projectCognitiveHistory("p1", [
    run({ run_id: "r1", artifact_ids: ["a1", "a2"], knowledgeItems: [knowledge("k1")], agentIds: ["agent-1"] }),
    run({ run_id: "r2", project_id: "p2" }),
  ]);
  assert.equal(history.projectId, "p1");
  assert.equal(history.entries.length, 1);
  assert.equal(history.totals.workflows, 1);
  assert.equal(history.totals.artifacts, 2);
  assert.equal(history.totals.knowledgeAttachments, 1);
  assert.equal(history.totals.recordedFailures, 0);
  assert.ok(history.note.includes("no ranking, no performance score"));
  const { note, ...rest } = history;
  // the note is the boundary statement itself; the data carries no scores
  const rendered = JSON.stringify(rest).toLowerCase();
  assert.ok(!/score|probability|rating|best workflow/.test(rendered));
});

test("failed runs record the failure; empty history yields the mandated empty state", () => {
  const withFailure = projectCognitiveHistory("p1", [
    run({ run_id: "r3", status: "failed", error: "converter crashed" }),
  ]);
  assert.equal(withFailure.totals.recordedFailures, 1);
  assert.equal(withFailure.entries[0].recordedFailure, "converter crashed");
  const silent = projectCognitiveHistory("p1", [run({ run_id: "r4", status: "cancelled", error: null })]);
  assert.equal(silent.entries[0].recordedFailure, "Recorded failure (no error text recorded)");
  const empty = projectCognitiveHistory("p1", []);
  assert.equal(empty.totals.workflows, 0);
});

// ---------- 2. artifact → workflow trace ----------

test("artifact trace follows recorded linkage only; no linkage degrades honestly", () => {
  const runs = [run({ run_id: "run-42", artifact_ids: ["art-1"], knowledgeItems: [knowledge("k9")] })];
  const linked = artifactWorkflowTrace("art-1", runs, "p1", "agent-7");
  assert.equal(linked.originatingWorkflowId, "run-42");
  assert.equal(linked.projectId, "p1");
  assert.equal(linked.recordedCreator, "agent-7");
  assert.deepEqual(linked.relatedKnowledge, ["k9"]);
  assert.equal(linked.unavailableReason, null);

  const unlinked = artifactWorkflowTrace("art-404", runs, "p1", null);
  assert.equal(unlinked.originatingWorkflowId, null);
  assert.equal(unlinked.unavailableReason, "No workflow attachment record available.");
  assert.deepEqual(unlinked.relatedKnowledge, []);
  // no inferred relationships: unknown artifact still shows nothing else
  const rendered = JSON.stringify(unlinked);
  assert.ok(!rendered.includes("run-42"));
});

// ---------- 3. knowledge → workflow trace ----------

test("knowledge trace lists explicit attachments or the mandated unavailable line", () => {
  const item = knowledge("k1");
  const attached = knowledgeWorkflowTrace(item, { k1: ["run-1", "run-5"] });
  assert.deepEqual(attached.attachedToWorkflows, ["run-1", "run-5"]);
  assert.equal(attached.provenance, item.provenanceLine);
  assert.equal(attached.unavailableReason, null);

  const unattached = knowledgeWorkflowTrace(item, {});
  assert.deepEqual(unattached.attachedToWorkflows, []);
  assert.equal(unattached.unavailableReason, "No workflow attachment record available.");
  const rendered = JSON.stringify(unattached);
  assert.ok(!rendered.includes("run-1"));
});

// ---------- 4. model/agent usage references (no ranking) ----------

test("model usage references participation and locality; agents list workflows", () => {
  const runs = [
    { run_id: "r1", model: "ollama:qwen" },
    { run_id: "r2", model: "cred-7" },
    { run_id: "r3", model: "ollama:qwen" },
  ];
  const local = modelUsageReference("ollama:qwen", runs);
  assert.deepEqual(local.workflowsParticipated, ["r1", "r3"]);
  assert.equal(local.locality, "Local model");
  const provider = modelUsageReference("cred-7", runs);
  assert.equal(provider.locality, "Provider model — approval required");
  assert.equal(provider.governanceStatus, "Exact approval before any network call");

  const agentRuns = [
    { run_id: "r1", agentIds: ["agent-1"] },
    { run_id: "r9", agentIds: ["agent-2"] },
  ];
  const agent = agentUsageReference("agent-1", agentRuns);
  assert.deepEqual(agent.workflowsParticipated, ["r1"]);
  const rendered = JSON.stringify([local, provider, agent]).toLowerCase();
  assert.ok(!/top model|most used|rank|preferred/.test(rendered));
});

test("agent with no recorded participation degrades to an empty list", () => {
  const agent = agentUsageReference("agent-9", []);
  assert.deepEqual(agent.workflowsParticipated, []);
});

// ---------- honest degradation note ----------

test("continuity note states the records-only boundary", () => {
  assert.ok(CONTINUITY_NOTE.includes("existing records only"));
  assert.ok(CONTINUITY_NOTE.includes("no inferred relationships"));
  assert.ok(CONTINUITY_NOTE.includes("no analytics scoring"));
});
