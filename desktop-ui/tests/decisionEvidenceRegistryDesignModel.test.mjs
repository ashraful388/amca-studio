// ZCODE-AMCA-STUDIO-V0.8.0-DECISION-EVIDENCE-REGISTRY-DESIGN-01 tests.
// Order minimum: no hidden reasoning fields, no autonomous claims, evidence
// references only, human override support, audit completeness.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DECISION_EVIDENCE_RECORD_FIELDS,
  REGISTRY_RULE,
  PROVENANCE_MODEL,
  PROVENANCE_RULE,
  OVERRIDE_TRACE_FIELDS,
  LIFECYCLE_ORDER,
  lifecycleProceeds,
  canTransition,
  passesRegistrySchemaScan,
} from "../src/decisionEvidenceRegistryDesignModel.ts";

// ---------- evidence record (references only) ----------

test("evidence record carries the eight mandated reference/fact fields", () => {
  const fields = DECISION_EVIDENCE_RECORD_FIELDS.map((field) => field.field);
  assert.deepEqual(fields, [
    "decision_id",
    "workflow_id",
    "timestamp",
    "initiator",
    "input_references",
    "evidence_references",
    "selected_resources",
    "approval_references",
  ]);
  for (const field of DECISION_EVIDENCE_RECORD_FIELDS) {
    assert.ok(field.note.length > 5, field.field);
  }
  assert.equal(REGISTRY_RULE, "Store evidence references, not hidden reasoning.");
});

// ---------- provenance model ----------

test("provenance model stores references only — five recorded relations", () => {
  assert.deepEqual(
    PROVENANCE_MODEL.map((edge) => edge.relation),
    ["input_records", "knowledge_references", "model_agent_references", "approval_records", "execution_record"],
  );
  for (const edge of PROVENANCE_MODEL) {
    assert.equal(edge.storesReferencesOnly, true);
    assert.ok(edge.designNote.includes("ID"), edge.relation); // "ID"/"IDs" both count as reference storage
  }
  assert.ok(PROVENANCE_RULE.includes("no inference"));
  assert.ok(PROVENANCE_RULE.includes("no generated explanation"));
});

// ---------- override trace ----------

test("override trace supports modification, override, rejection, and approval", () => {
  const fields = OVERRIDE_TRACE_FIELDS.map((field) => field.field);
  assert.deepEqual(fields, [
    "override_id",
    "decision_id",
    "trace_type",
    "user_ref",
    "reason",
    "changed_fields",
    "timestamp",
  ]);
  const types = OVERRIDE_TRACE_FIELDS.find((field) => field.field === "trace_type").note;
  for (const type of ["user_modification", "override_with_reason", "rejected_decision", "approved_decision"]) {
    assert.ok(types.includes(type));
  }
  const reason = OVERRIDE_TRACE_FIELDS.find((field) => field.field === "reason").note;
  assert.ok(reason.includes("required for modifications, overrides, and rejections"));
});

// ---------- lifecycle state model ----------

test("lifecycle states follow the mandated order with legal transitions", () => {
  assert.deepEqual(LIFECYCLE_ORDER, [
    "CREATED",
    "EVIDENCE_COLLECTED",
    "AWAITING_APPROVAL",
    "APPROVED",
    "MODIFIED",
    "REJECTED",
    "EXECUTED",
    "VERIFIED",
  ]);
  assert.equal(lifecycleProceeds("APPROVED"), true);
  assert.equal(lifecycleProceeds("MODIFIED"), true);
  assert.equal(lifecycleProceeds("REJECTED"), false);
  // forward transitions are legal
  assert.equal(canTransition("CREATED", "EVIDENCE_COLLECTED"), true);
  assert.equal(canTransition("AWAITING_APPROVAL", "APPROVED"), true);
  assert.equal(canTransition("AWAITING_APPROVAL", "REJECTED"), true);
  assert.equal(canTransition("EXECUTED", "VERIFIED"), true);
  // backward or out-of-order transitions are not
  assert.equal(canTransition("EVIDENCE_COLLECTED", "CREATED"), false);
  assert.equal(canTransition("APPROVED", "EVIDENCE_COLLECTED"), false);
  // REJECTED and VERIFIED are terminal
  assert.equal(canTransition("REJECTED", "EXECUTED"), false);
  assert.equal(canTransition("VERIFIED", "CREATED"), false);
  assert.equal(canTransition("REJECTED", "REJECTED"), false);
});

// ---------- schema scan (no hidden reasoning / autonomy) ----------

test("registry schema text passes the hidden-reasoning and autonomy scan", () => {
  const allFields = DECISION_EVIDENCE_RECORD_FIELDS.map((field) => `${field.field}: ${field.note}`).join("\n");
  const provenance = PROVENANCE_MODEL.map((edge) => edge.designNote).join("\n");
  const overrides = OVERRIDE_TRACE_FIELDS.map((field) => `${field.field}: ${field.note}`).join("\n");
  for (const text of [allFields, provenance, overrides, REGISTRY_RULE, PROVENANCE_RULE]) {
    assert.equal(passesRegistrySchemaScan(text), true, text.slice(0, 60));
  }
  // forbidden claims are caught
  for (const bad of ["stores hidden reasoning", "reasoning payload included", "decides automatically", "autonomously selects models", "chain-of-thought recorded"]) {
    assert.equal(passesRegistrySchemaScan(bad), false, bad);
  }
});

// ---------- audit completeness ----------

test("audit completeness: every decision field is either a reference or a recorded fact", () => {
  for (const field of DECISION_EVIDENCE_RECORD_FIELDS) {
    const isReference = field.field.endsWith("_references") || field.field.endsWith("_id");
    const isRecordedFact = ["timestamp", "initiator", "selected_resources"].includes(field.field);
    assert.ok(isReference || isRecordedFact, field.field);
  }
});
