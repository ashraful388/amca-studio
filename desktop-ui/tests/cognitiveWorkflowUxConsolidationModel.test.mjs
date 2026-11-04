// ZCODE-AMCA-STUDIO-V0.8.0-COGNITIVE-WORKFLOW-UX-FINAL-CONSOLIDATION-01 tests.
// Minimum coverage per the order: audit passes on live model data, wording
// consistency, empty-state consistency, documentation links, no execution
// controls, no reasoning exposure.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  auditCognitiveWorkflowUx,
  CANONICAL_CWF_TERMS,
  CONSOLIDATED_EMPTY_STATES,
  COGNITIVE_WORKFLOW_DOCS,
  COGNITIVE_WORKFLOW_HELP_NOTE,
  wordingChecks,
} from "../src/cognitiveWorkflowUxConsolidationModel.ts";
import {
  workflowStages,
  WORKFLOW_STAGE_LABELS,
  knowledgeUsage,
  workflowPanelView,
} from "../src/cognitiveWorkflowUxModel.ts";
import { workflowSummaryCard } from "../src/cognitiveWorkflowInsightsModel.ts";
import { stageDetail as insightsStageDetail } from "../src/cognitiveWorkflowInsightsModel.ts";
import { workflowActions } from "../src/cognitiveWorkflowInteractionModel.ts";
import { HELP_RESOURCES } from "../src/consolidationUxModel.ts";

const run = (overrides = {}) => ({
  run_id: "run-consolidation",
  status: "succeeded",
  created_at: "2026-09-03T09:00:00Z",
  finished_at: "2026-09-03T09:03:00Z",
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

// ---------- audit passes on live model data ----------

test("consolidation audit passes against live model outputs", () => {
  const liveRun = run();
  const knowledgeItems = [knowledge("a1"), knowledge("a2")];
  // wire the LIVE models exactly as the panel does
  const stages = workflowStages(liveRun, knowledgeItems);
  const summary = workflowSummaryCard({ stages, knowledgeItems, resourceCount: 2, artifactCount: 1 });
  const actions = workflowActions({ hasArtifacts: true, hasKnowledge: true, hasModels: true, hasAgents: true });
  const usage = knowledgeUsage(knowledgeItems, null);
  const audit = auditCognitiveWorkflowUx({
    stages,
    summary,
    firstStageDetail: insightsStageDetail(stages[0]),
    actions,
    usage,
    localOnlySurfaces: ["Local models only — no network call without approval"],
    artifactTitles: ["Catalyst report"],
    agentIds: ["agent-7"],
    mandatedStageLabels: WORKFLOW_STAGE_LABELS,
  });
  assert.equal(audit.passed, true, JSON.stringify(audit.findings.filter((f) => f.severity === "issue")));
  const areas = audit.findings.map((f) => f.area);
  for (const area of ["timeline", "summary", "stage-expansion", "interaction", "knowledge", "resource-navigation", "wording", "empty-states", "documentation", "reasoning"]) {
    assert.ok(areas.includes(area), `missing audit area: ${area}`);
  }
});

test("live derivation never produces evidence on a not_recorded stage", () => {
  const live = workflowStages(null, []);
  const unrecorded = live.filter((stage) => stage.state === "not_recorded");
  assert.equal(unrecorded.length, 9);
  for (const stage of unrecorded) {
    assert.equal(stage.timestamp, null);
    assert.equal(stage.detail, null);
  }
});

// ---------- wording consistency ----------

test("canonical terms are the mandated four and verified on live surfaces", () => {
  assert.deepEqual(CANONICAL_CWF_TERMS, ["Not recorded", "Local only", "Unavailable", "No automatic retrieval"]);
  const usage = [{ retrievalMode: "Explicit user attachment (no automatic retrieval)" }];
  const checks = wordingChecks({ knowledgeUsage: usage, localOnlySurfaces: ["Local models only"] });
  const byTerm = Object.fromEntries(checks.map((check) => [check.term, check]));
  assert.equal(byTerm["Not recorded"].consistent, true);
  assert.equal(byTerm["Local only"].consistent, true);
  assert.equal(byTerm["Unavailable"].consistent, true);
  assert.equal(byTerm["No automatic retrieval"].consistent, true);
  // a retrieval mode that omits the canonical phrase breaks consistency
  const broken = wordingChecks({ knowledgeUsage: [{ retrievalMode: "auto-retrieved" }], localOnlySurfaces: [] });
  assert.equal(broken.find((c) => c.term === "No automatic retrieval").consistent, false);
});

// ---------- empty-state consistency ----------

test("mandated empty states are consolidated verbatim, no invented alternatives", () => {
  assert.deepEqual(
    CONSOLIDATED_EMPTY_STATES.map((state) => state.text),
    [
      "No workflow records available yet.",
      "Stage details are unavailable because this run does not contain lifecycle records.",
      "No derived insights are available from recorded data.",
      "No knowledge attached to this run — nothing retrieved automatically.",
    ],
  );
});

// ---------- documentation links ----------

test("cognitive workflow Help entries link to the recorded report paths", () => {
  assert.equal(COGNITIVE_WORKFLOW_DOCS.length, 3);
  for (const doc of COGNITIVE_WORKFLOW_DOCS) {
    assert.ok(doc.docsPath.startsWith("software/docs/validation/AMCA-Studio-v0.8.0-Cognitive-Workflow-"), doc.docsPath);
  }
  // the entries are registered in the existing Help/Resources surface
  const helpTitles = HELP_RESOURCES.map((resource) => resource.title);
  for (const doc of COGNITIVE_WORKFLOW_DOCS) {
    assert.ok(helpTitles.includes(doc.title), `missing Help entry: ${doc.title}`);
  }
  const helpPaths = HELP_RESOURCES.map((resource) => resource.docsPath);
  for (const doc of COGNITIVE_WORKFLOW_DOCS) {
    assert.ok(helpPaths.includes(doc.docsPath));
  }
  assert.ok(COGNITIVE_WORKFLOW_HELP_NOTE.includes("no in-app documentation reader"));
});

// ---------- no execution controls / no reasoning exposure ----------

test("audit interaction area asserts the absence of execution controls", () => {
  const failedRun = run({ status: "failed", error: "boom" });
  const stages = workflowStages(failedRun, []);
  const audit = auditCognitiveWorkflowUx({
    stages,
    summary: workflowSummaryCard({ stages, knowledgeItems: [], resourceCount: 0, artifactCount: 0 }),
    firstStageDetail: insightsStageDetail(stages[0]),
    actions: workflowActions({ hasArtifacts: false, hasKnowledge: false, hasModels: true, hasAgents: false }),
    usage: knowledgeUsage([], null),
    localOnlySurfaces: [],
    artifactTitles: [],
    agentIds: [],
    mandatedStageLabels: WORKFLOW_STAGE_LABELS,
  });
  const interaction = audit.findings.find((f) => f.area === "interaction");
  assert.equal(interaction.severity, "ok");
  assert.ok(interaction.detail.includes("no execution controls"));
  const reasoning = audit.findings.find((f) => f.area === "reasoning");
  assert.equal(reasoning.severity, "ok");
  // the whole audit output carries no reasoning exposure
  const rendered = JSON.stringify(audit).toLowerCase();
  assert.ok(!/chain-of-thought|hidden reasoning|internal monologue/.test(rendered));
});
