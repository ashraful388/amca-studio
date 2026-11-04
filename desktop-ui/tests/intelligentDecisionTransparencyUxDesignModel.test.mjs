// ZCODE-AMCA-STUDIO-V0.8.0-INTELLIGENT-DECISION-TRANSPARENCY-UX-DESIGN-01 tests.
// Design validation: no current adaptive claims, no hidden reasoning
// exposure, no autonomous execution language, explicit user control.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DECISION_TRACE_BOUNDARY,
  DECISION_TRACE_SECTIONS,
  EXPLAINABILITY_FIELDS,
  EXPLAINABILITY_RULE,
  DESIGNED_CONTROLS,
  CONTROL_SURFACE_NOTE,
  DECISION_AUDIT_FIELDS,
  DECISION_AUDIT_NOTE,
  FORBIDDEN_PATTERNS,
  passesDesignLanguageScan,
  fullDesignText,
} from "../src/intelligentDecisionTransparencyUxDesignModel.ts";

// ---------- decision trace design ----------

test("decision trace covers the five mandated sections with named contract fields", () => {
  assert.deepEqual(
    DECISION_TRACE_SECTIONS.map((section) => section.sectionId),
    ["selected_strategy", "selected_models", "selected_agents", "selected_knowledge", "approval_checkpoints"],
  );
  for (const section of DECISION_TRACE_SECTIONS) {
    assert.ok(section.contractFields.every((field) => field.startsWith("decision.") || field.startsWith("checkpoint.") || field.includes("selection_trace") || field.includes("binding.")), section.sectionId);
    assert.ok(section.description.length > 20, section.sectionId);
  }
  assert.equal(DECISION_TRACE_BOUNDARY, "Decision transparency design only — no adaptive decision exists in this build.");
});

// ---------- explainability contract ----------

test("explainability contract covers all five required UI fields", () => {
  const fields = EXPLAINABILITY_FIELDS.map((field) => field.uiField);
  assert.deepEqual(fields, [
    "Decision input records",
    "Evidence used",
    "Approval state",
    "User override option",
    "Final execution record",
  ]);
  for (const field of EXPLAINABILITY_FIELDS) {
    assert.ok(field.contractSource.startsWith("decision."), field.uiField);
    assert.ok(field.missingRendering.length > 5, field.uiField);
  }
  assert.ok(EXPLAINABILITY_RULE.includes("no generated explanations"));
  assert.ok(EXPLAINABILITY_RULE.includes("no hidden reasoning"));
});

// ---------- human control surface (designed, not implemented) ----------

test("human control surface designs approve/modify/override/reject without implementing them", () => {
  assert.deepEqual(
    DESIGNED_CONTROLS.map((control) => control.control),
    ["approve", "modify", "override", "reject"],
  );
  for (const control of DESIGNED_CONTROLS) {
    assert.equal(control.implementedInThisBuild, false);
    assert.ok(control.description.length > 20, control.control);
  }
  assert.ok(CONTROL_SURFACE_NOTE.includes("none are implemented in this build"));
  assert.ok(CONTROL_SURFACE_NOTE.includes("existing approval surfaces"));
});

// ---------- decision audit (UX only) ----------

test("decision audit design covers initiator, evidence, timestamp, approval state", () => {
  const fields = DECISION_AUDIT_FIELDS.map((field) => field.uiField);
  assert.deepEqual(fields, ["Initiated by", "Evidence references", "Timestamp", "Approval state"]);
  for (const field of DECISION_AUDIT_FIELDS) {
    assert.ok(field.missingRendering.length > 5, field.uiField);
  }
  assert.ok(DECISION_AUDIT_NOTE.includes("no derived judgment"));
});

// ---------- design language validation ----------

test("full design text passes the forbidden-language scan", () => {
  const text = fullDesignText();
  assert.equal(passesDesignLanguageScan(text), true);
  for (const pattern of FORBIDDEN_PATTERNS) {
    assert.ok(!pattern.test(text), `forbidden pattern matched: ${pattern}`);
  }
});

test("forbidden patterns catch current-adaptive and autonomous-execution claims", () => {
  const claims = [
    "AMCA decides automatically which model to use",
    "autonomous execution of workflows",
    "will automatically choose the best model",
    "intelligent auto-routing is now active",
  ];
  for (const claim of claims) {
    assert.equal(passesDesignLanguageScan(claim), false, `scan must flag: ${claim}`);
    // i.e. FORBIDDEN_PATTERNS must match each claim
    assert.ok(FORBIDDEN_PATTERNS.some((pattern) => pattern.test(claim)), claim);
  }
});

// ---------- explicit user control ----------

test("design includes explicit user control fields and override option", () => {
  const override = EXPLAINABILITY_FIELDS.find((field) => field.uiField === "User override option");
  assert.ok(override, "user override option must exist in the explainability contract");
  const reject = DESIGNED_CONTROLS.find((control) => control.control === "reject");
  assert.ok(reject.description.includes("execution does not proceed"));
  const modify = DESIGNED_CONTROLS.find((control) => control.control === "modify");
  assert.ok(modify.description.includes("recorded alongside the decision"));
});
