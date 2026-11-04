// ZCODE-AMCA-STUDIO-V0.8.0-OPERATIONAL-WORKSPACE-INTELLIGENCE-ENHANCEMENT-01 tests.
// Order minimum: record-only metrics, no ranking, no scoring, no inferred
// relationships, canonical wording, empty states.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  operationalMetrics,
  operationalTimeline,
  projectOperationalEvents,
  operationalHealthCards,
  drillLink,
  OPERATIONAL_STATE_NOTE,
  OPERATIONAL_EMPTY_STATES,
} from "../src/operationalWorkspaceIntelligenceModel.ts";

const run = (overrides = {}) => ({
  run_id: "run-1",
  project_id: "p1",
  status: "succeeded",
  created_at: "2026-09-03T10:00:00Z",
  model: "ollama:qwen",
  error: null,
  ...overrides,
});

// ---------- command-center metrics (record-only) ----------

test("operational metrics count recorded states; visibility lines stay honest", () => {
  const metrics = operationalMetrics({
    runs: [
      run({ run_id: "r1", status: "running" }),
      run({ run_id: "r2", status: "succeeded" }),
      run({ run_id: "r3", status: "failed" }),
    ],
    pendingApprovals: 2,
    artifactCount: 4,
    knowledgeItems: [knowledge("k1"), knowledge("k2"), knowledge("k3")],
    localModels: 2,
    providerModels: 1,
  });
  assert.equal(metrics.activeWorkflows, 1);
  assert.equal(metrics.completedWorkflows, 1);
  assert.equal(metrics.pendingApprovals, 2);
  assert.equal(metrics.artifactsGenerated, 4);
  assert.equal(metrics.knowledgeAttachments, 3);
  assert.ok(metrics.localExecutionVisible.includes("2 local model(s)"));
  assert.ok(metrics.providerExecutionVisible.includes("approval required"));
  const rendered = JSON.stringify(metrics).toLowerCase();
  assert.ok(!/score|productivity|rank/.test(rendered));
});

test("unrecorded metrics render Not recorded, never a false zero", () => {
  const metrics = operationalMetrics({
    runs: [],
    pendingApprovals: 0,
    artifactCount: 0,
    knowledgeItems: [],
    localModels: 0,
    providerModels: 0,
  });
  assert.equal(metrics.localExecutionVisible, "Not recorded");
  assert.equal(metrics.providerExecutionVisible, "Not recorded");
});

// ---------- project operational timeline (newest first, undated preserved) ----------

test("timeline merges event kinds newest first and preserves undated records", () => {
  const events = projectOperationalEvents({
    projectId: "p1",
    runs: [run({ run_id: "r1", created_at: "2026-09-02T10:00:00Z" })],
    artifacts: [{ artifact_id: "a1abcdef1234", title: "Catalyst report", created_at: "2026-09-03T09:00:00Z" }],
    approvals: [{ id: "apr-1", action: "provider_call", status: "pending", created_at: "2026-09-01T10:00:00Z" }],
    knowledgeEvents: [{ artifactId: "k1abcdef1234", timestamp: null }],
  });
  assert.equal(events.length, 4);
  // newest first: artifact (09-03) → workflow (09-02) → approval (09-01) → knowledge (undated last)
  assert.equal(events[0].kind, "artifact");
  assert.equal(events[1].kind, "workflow");
  assert.equal(events[2].kind, "approval");
  assert.equal(events[3].kind, "knowledge");
  assert.equal(events[3].timestamp, null);
});

test("operationalTimeline sorts a plain list newest first with undated last", () => {
  const sorted = operationalTimeline([
    { kind: "workflow", label: "w", timestamp: "2026-09-01", detail: "d" },
    { kind: "artifact", label: "a", timestamp: "2026-09-05", detail: "d" },
    { kind: "approval", label: "p", timestamp: null, detail: "d" },
  ]);
  assert.deepEqual(sorted.map((event) => event.kind), ["artifact", "workflow", "approval"]);
});

test("no events yields the mandated empty state", () => {
  const events = projectOperationalEvents({ projectId: "p-empty", runs: [], artifacts: [], approvals: [], knowledgeEvents: [] });
  assert.equal(events.length, 0);
  assert.equal(OPERATIONAL_EMPTY_STATES.no_timeline, "No recorded operational events for this project yet.");
});

// ---------- health cards + drill-down ----------

test("health cards are counts with named evidence sources", () => {
  const cards = operationalHealthCards({
    runs: [run({ run_id: "r1", status: "running" }), run({ run_id: "r2", status: "failed" })],
    pendingApprovals: 3,
    availableResources: 5,
  });
  const byLabel = Object.fromEntries(cards.map((card) => [card.label, card]));
  assert.equal(byLabel["Running workflows"].value, 1);
  assert.equal(byLabel["Completed workflows"].value, 0);
  assert.equal(byLabel["Pending governance actions"].value, 3);
  assert.equal(byLabel["Failed workflows"].value, 1);
  assert.equal(byLabel["Available resources"].value, 5);
  assert.ok(OPERATIONAL_STATE_NOTE.includes("Recorded operational state only"));
  assert.ok(OPERATIONAL_STATE_NOTE.includes("no productivity or quality score"));
  const rendered = JSON.stringify(cards).toLowerCase();
  assert.ok(!/score|probability|rating|rank/.test(rendered));
});

test("drill-down links target existing views; missing links degrade honestly", () => {
  const linked = drillLink("artifact", true);
  assert.equal(linked.target, "artifacts");
  assert.equal(linked.available, true);
  const unlinked = drillLink("model", false);
  assert.equal(unlinked.target, null);
  assert.equal(unlinked.unavailableReason, "No recorded link available.");
  for (const kind of ["workflow", "artifact", "model", "approval"]) {
    const link = drillLink(kind, true);
    assert.ok(["agents", "artifacts", "settings", "validation"].includes(link.target));
  }
  const rendered = JSON.stringify([drillLink("workflow", true)]).toLowerCase();
  assert.ok(!/rank|score|best/.test(rendered));
});

// ---------- knowledge fixture helper (matches composer item shape) ----------

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
