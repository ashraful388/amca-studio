// ZCODE-AMCA-STUDIO-V0.8.0-ADAPTIVE-WORKFLOW-READINESS-UX-FOUNDATION-01 tests.
// Order minimum: no recommendation claims, no optimization claims, no
// hidden decisions, recorded-only rendering, canonical wording.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ADAPTIVE_BOUNDARY,
  STRATEGY_NOT_RECORDED,
  complexityIndicators,
  capabilityMatrix,
  workflowStrategy,
  adaptiveReadinessView,
} from "../src/adaptiveWorkflowReadinessUxModel.ts";

const run = (overrides = {}) => ({
  run_id: "run-1",
  project_id: "p1",
  status: "succeeded",
  created_at: "2026-09-03T10:00:00Z",
  finished_at: "2026-09-03T10:05:00Z",
  goal: "[Task type: Research] Compare catalysts",
  mode: "joint",
  model: "ollama:qwen",
  error: null,
  ...overrides,
});

// ---------- complexity indicators (recorded counts, no score) ----------

test("complexity indicators count recorded configuration fields only", () => {
  const indicators = complexityIndicators({
    goal: "[Task type: Research] Compare catalysts",
    mode: "joint",
    modelRefs: ["ollama:qwen"],
    agentIds: ["agent-1", "agent-2"],
    knowledgeAttachmentCount: 2,
  });
  assert.equal(indicators.recordedTaskType, "Research");
  assert.equal(indicators.recordedMode, "joint");
  assert.equal(indicators.participatingModelCount, 1);
  assert.equal(indicators.participatingAgentCount, 2);
  assert.equal(indicators.knowledgeAttachmentCount, 2);
  // goalLength counts the recorded goal text as-is (including the tag)
  assert.equal(indicators.goalLength, "[Task type: Research] Compare catalysts".length);
  assert.ok(indicators.note.includes("indicators, not a complexity score"));
  // the note is the boundary statement itself; scan only the data fields
  const { note, ...dataFields } = indicators;
  const rendered = JSON.stringify(dataFields).toLowerCase();
  assert.ok(!/complexity score|capability score|rating|rank/.test(rendered));
});

// ---------- capability matrix (recorded fields) ----------

test("capability matrix restates recorded fields without selection or routing", () => {
  const rows = capabilityMatrix([
    { ref: "ollama:qwen", kind: "model", governanceRecorded: true },
    { ref: "cred-7", kind: "model", recordedCapability: "vision" },
    { ref: "agent-1", kind: "agent" },
  ]);
  assert.equal(rows[0].locality, "Local — no network call without approval");
  assert.equal(rows[1].locality, "Provider — approval required before any network call");
  assert.equal(rows[1].recordedCapability, "vision");
  assert.equal(rows[2].governanceStatus, "Recorded: exact approval before any network call");
  const rendered = JSON.stringify(rows).toLowerCase();
  assert.ok(!/best model|recommended|routing decision|selected automatically/.test(rendered));
});

// ---------- strategy visibility ----------

test("workflow strategy shows the recorded mode or the canonical not-recorded line", () => {
  assert.equal(workflowStrategy(run({ mode: "joint" })), "joint");
  assert.equal(workflowStrategy(run({ mode: "" })), STRATEGY_NOT_RECORDED);
  assert.equal(workflowStrategy(null), STRATEGY_NOT_RECORDED);
  assert.equal(STRATEGY_NOT_RECORDED, "Workflow strategy not recorded.");
});

// ---------- boundary explanation ----------

test("boundary states future capability and this build's exclusions", () => {
  assert.equal(
    ADAPTIVE_BOUNDARY.headline,
    "Readiness information from recorded configuration only — no adaptive decision is made.",
  );
  assert.equal(ADAPTIVE_BOUNDARY.futureCapabilities.length, 3);
  assert.equal(ADAPTIVE_BOUNDARY.thisBuildDoesNot.length, 3);
  const exclusions = ADAPTIVE_BOUNDARY.thisBuildDoesNot.join(" ").toLowerCase();
  assert.ok(exclusions.includes("autonomous decisions"));
  assert.ok(exclusions.includes("optimize automatically"));
  assert.ok(exclusions.includes("execution strategy"));
});

// ---------- panel assembly ----------

test("readiness view assembles recorded fields; unrecorded inputs stay honest", () => {
  const view = adaptiveReadinessView({
    run: run(),
    modelRefs: ["ollama:qwen"],
    agentIds: ["agent-1"],
    knowledgeAttachmentCount: 2,
  });
  assert.equal(view.strategy, "joint");
  assert.equal(view.capabilityRows.length, 2);
  assert.equal(view.note.includes("no adaptive decision is made"), true);

  const empty = adaptiveReadinessView({ run: null, modelRefs: [], agentIds: [], knowledgeAttachmentCount: 0 });
  assert.equal(empty.indicators.recordedTaskType, null);
  assert.equal(empty.indicators.recordedMode, null);
  assert.equal(empty.strategy, STRATEGY_NOT_RECORDED);
  assert.equal(empty.indicators.participatingModelCount, 0);
});
