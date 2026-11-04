// ZCODE-AMCA-STUDIO-V0.9.0-DECISION-TRANSPARENCY-UX-PREPARATION-01 tests.
// Order minimum: missing decision record handling, evidence reference
// rendering, no hidden reasoning fields, no confidence/ranking fields,
// no active decision controls, governance wording consistency.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DECISION_RECORDS_UNAVAILABLE,
  DESIGNED_CONTROL_LABEL,
  PROVENANCE_VIEWER_NOTE,
  DECISION_PANEL_SECTIONS,
  CONTROL_PLACEHOLDERS,
  INTEGRATION_NOTE,
  INTEGRATION_SURFACES,
  decisionPanelForAbsentRecords,
  provenanceViewerRows,
} from "../src/decisionTransparencyUxPreparationModel.ts";
import { DECISION_TRACE_BOUNDARY } from "../src/intelligentDecisionTransparencyUxDesignModel.ts";
import { DECISION_TRACE_BOUNDARY as PREP_BOUNDARY } from "../src/decisionTransparencyUxPreparationModel.ts";

// ---------- missing decision record handling ----------

test("absent records render the mandated unavailable wording in every section", () => {
  assert.equal(DECISION_RECORDS_UNAVAILABLE, "Decision records unavailable. No adaptive decision exists in this build.");
  const panel = decisionPanelForAbsentRecords();
  assert.equal(panel.sections.length, 5);
  for (const section of panel.sections) {
    assert.equal(section.currentRendering, DECISION_RECORDS_UNAVAILABLE);
  }
});

// ---------- evidence reference rendering ----------

test("provenance rows render recorded references with honest gaps", () => {
  const rows = provenanceViewerRows([
    { referenceId: "ev-1", sourceCategory: "approval_records", timestamp: "2026-09-03T10:00:00Z", workflowId: "run-1", artifactIds: ["a1"] },
    { referenceId: "ev-2", sourceCategory: "knowledge_references", timestamp: null, workflowId: null, artifactIds: [] },
  ]);
  assert.equal(rows[0].evidenceReferenceId, "ev-1");
  assert.equal(rows[0].sourceCategory, "approval_records");
  assert.equal(rows[0].timestamp, "2026-09-03T10:00:00Z");
  assert.equal(rows[0].linkedWorkflow, "run-1");
  assert.deepEqual(rows[0].linkedArtifacts, ["a1"]);
  // gaps stay null — never invented
  assert.equal(rows[1].timestamp, null);
  assert.equal(rows[1].linkedWorkflow, null);
  assert.deepEqual(rows[1].linkedArtifacts, []);
});

test("empty evidence list renders an empty read-only viewer", () => {
  assert.deepEqual(provenanceViewerRows([]), []);
  assert.ok(PROVENANCE_VIEWER_NOTE.includes("no reasoning"));
  assert.ok(PROVENANCE_VIEWER_NOTE.includes("no confidence scores"));
  assert.ok(PROVENANCE_VIEWER_NOTE.includes("no rankings"));
});

// ---------- no hidden reasoning / no confidence / no ranking fields ----------

test("panel sections and controls carry no hidden reasoning, confidence, or ranking fields", () => {
  const rendered = JSON.stringify({ sections: DECISION_PANEL_SECTIONS, controls: CONTROL_PLACEHOLDERS }).toLowerCase();
  assert.ok(!/hidden reasoning|chain[- ]of[- ]thought|reasoning payload/.test(rendered));
  assert.ok(!/confidence|ranking|priority|urgency/.test(rendered));
});

// ---------- no active decision controls ----------

test("all four control placeholders are designed-only and inactive", () => {
  assert.deepEqual(CONTROL_PLACEHOLDERS.map((placeholder) => placeholder.control), ["approve", "modify", "override", "reject"]);
  for (const placeholder of CONTROL_PLACEHOLDERS) {
    assert.equal(placeholder.implemented, false);
    assert.equal(placeholder.inactiveReason, DESIGNED_CONTROL_LABEL);
    assert.ok(placeholder.inactiveReason.includes("No decision engine is active"));
  }
  // the absent-records panel keeps placeholders inactive
  const panel = decisionPanelForAbsentRecords();
  for (const control of panel.controls) {
    assert.equal(control.implemented, false);
  }
});

// ---------- governance wording consistency ----------

test("wording stays consistent with the accepted decision-trace design", () => {
  assert.equal(DECISION_TRACE_BOUNDARY, "Decision transparency design only — no adaptive decision exists in this build.");
  // cross-check: the preparation model duplicates the boundary verbatim
  assert.equal(PREP_BOUNDARY, DECISION_TRACE_BOUNDARY);
  assert.ok(DECISION_RECORDS_UNAVAILABLE.includes("No adaptive decision exists in this build"));
  // integration stays on existing surfaces
  assert.deepEqual([...INTEGRATION_SURFACES], ["Review Center", "Cognitive Workflow Panel", "Validation Center"]);
  assert.ok(INTEGRATION_NOTE.includes("no new navigation"));
});
