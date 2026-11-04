// ZCODE-AMCA-STUDIO-V0.8.0-WORKFLOW-TEMPLATE-AND-REUSE-UX-FOUNDATION-01 tests.
// Order minimum: recorded-only examples, no recommendation claims, no
// ranking, no automatic creation, safe navigation, honest empty states.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  TEMPLATE_BOUNDARY,
  REUSE_BOUNDARY,
  TEMPLATE_EMPTY_STATES,
  COMPARISON_NOTE,
  workflowTemplates,
  recordedTemplateTaskType,
  templateDetail,
  reusePreparation,
  templateComparison,
} from "../src/workflowTemplateReuseUxModel.ts";

const run = (overrides = {}) => ({
  run_id: "run-1",
  project_id: "p1",
  status: "succeeded",
  created_at: "2026-09-03T10:00:00Z",
  finished_at: "2026-09-03T10:05:00Z",
  goal: "[Task type: Research] Compare catalysts",
  model: "ollama:qwen",
  error: null,
  artifactCount: 3,
  knowledgeCount: 1,
  agentCount: 1,
  ...overrides,
});

// ---------- template visibility (recorded examples) ----------

test("templates derive from completed workflows only, boundary verbatim", () => {
  const templates = workflowTemplates([
    run({ run_id: "r-ok" }),
    run({ run_id: "r-fail", status: "failed" }),
    run({ run_id: "r-cancel", status: "cancelled" }),
    run({ run_id: "r-run", status: "running" }),
  ]);
  assert.equal(templates.length, 1);
  assert.equal(templates[0].sourceWorkflowId, "r-ok");
  assert.equal(templates[0].taskType, "Research");
  assert.equal(templates[0].boundary, "Recorded workflow examples only — not recommendations or rankings.");
});

test("failed and running workflows are never templates", () => {
  for (const status of ["failed", "cancelled", "running", "queued", "cancelling"]) {
    assert.equal(workflowTemplates([run({ status })]).length, 0, status);
  }
});

// ---------- detail view (recorded fields, honest gaps) ----------

test("detail quotes recorded stages/resources/outputs and lists missing", () => {
  const detail = templateDetail(run());
  assert.equal(detail.sourceWorkflowId, "run-1");
  assert.equal(detail.taskType, "Research");
  assert.deepEqual(detail.recordedStages, ["Run registry status: succeeded"]);
  assert.deepEqual(detail.resources, ["ollama:qwen"]);
  assert.equal(detail.knowledgeAttachments, 1);
  assert.deepEqual(detail.outputs, ["3 recorded artifact(s)"]);
  assert.equal(detail.missing.length, 0);

  const sparse = templateDetail(run({ artifactCount: 0, knowledgeCount: 0, agentCount: 0, model: "" }));
  assert.ok(sparse.missing.includes("agents"));
  assert.ok(sparse.missing.includes("knowledge"));
  assert.ok(sparse.missing.includes("outputs"));
  assert.equal(TEMPLATE_EMPTY_STATES.no_detail, "No recorded detail available.");
});

// ---------- reuse preparation (no automatic creation) ----------

test("reuse preparation maps an exact recorded task type; no fuzzy inference", () => {
  const ids = ["research", "coding", "analysis"];
  const exact = reusePreparation(workflowTemplates([run()])[0], ids);
  assert.equal(exact.taskTypeId, "research");
  assert.equal(exact.workspaceProjectId, "p1");
  assert.equal(exact.boundary, REUSE_BOUNDARY);

  // no exact id match -> null; the user picks the type themselves
  const noMatch = reusePreparation(workflowTemplates([run({ goal: "[Task type: Astro physics] x" })])[0], ids);
  assert.equal(noMatch.taskTypeId, null);
  assert.ok(REUSE_BOUNDARY.includes("Nothing is created or executed automatically"));
  // scan the data fields; the boundary note is the negation statement itself
  const prep = reusePreparation(workflowTemplates([run()])[0], ids);
  const dataFields = JSON.stringify({ taskTypeId: prep.taskTypeId, workspaceProjectId: prep.workspaceProjectId }).toLowerCase();
  assert.ok(!/auto|clone|create workflow/.test(dataFields));
});

test("untyped templates record the Untyped label", () => {
  assert.equal(recordedTemplateTaskType("no tag"), "Untyped");
  assert.equal(recordedTemplateTaskType(null), "Untyped");
  assert.equal(recordedTemplateTaskType("[Task type: Coding] x"), "Coding");
});

// ---------- comparison (display only) ----------

test("comparison shows recorded fields without any best/fastest calculation", () => {
  const rows = templateComparison([
    workflowTemplates([run({ run_id: "rA", finished_at: "2026-09-01T10:00:00Z" })])[0],
    workflowTemplates([run({ run_id: "rB", artifactCount: 0, knowledgeCount: 0, agentCount: 0, finished_at: null })])[0],
  ]);
  const byField = Object.fromEntries(rows.map((row) => [row.field, row.values]));
  assert.equal(byField["Task type"].length, 2);
  assert.equal(byField["Knowledge attachments"][1], "Not recorded");
  assert.equal(byField["Outputs"][1], "Not recorded");
  assert.equal(byField["Completed at"][0], "2026-09-01T10:00:00Z");
  const rendered = JSON.stringify(rows).toLowerCase();
  assert.ok(!/better|fastest|best model|efficiency score|winner/.test(rendered));
  assert.ok(COMPARISON_NOTE.includes("no better-workflow, fastest-workflow, best-model, or efficiency calculation"));
});

// ---------- honest empty states ----------

test("no completed workflows yields the mandated empty state", () => {
  assert.equal(workflowTemplates([run({ status: "failed" })]).length, 0);
  assert.equal(
    TEMPLATE_EMPTY_STATES.no_templates,
    "No completed workflows recorded yet — templates appear after workflows complete.",
  );
});
