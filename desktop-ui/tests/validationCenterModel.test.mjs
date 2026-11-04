import { test } from "node:test";
import assert from "node:assert/strict";
import { APP_VERSION } from "../src/versionLabel.ts";
import {
  VALIDATION_SCENARIOS,
  scenarioById,
  RELEASE_RECORD,
  LIMITATION_RECORDS,
  betaHealthSummary,
  FEEDBACK_PATHS,
} from "../src/validationCenterModel.ts";

// ---------- Scenarios ----------

test("the three manager-named validation scenarios exist", () => {
  assert.deepEqual(
    VALIDATION_SCENARIOS.map((scenario) => scenario.id),
    ["research", "software", "productivity"],
  );
});

test("no scenario claims validated without a record", () => {
  for (const scenario of VALIDATION_SCENARIOS) {
    assert.equal(scenario.status, "not_recorded");
    assert.match(scenario.statusReason, /validation-record endpoint/i);
  }
});

test("every scenario has purpose, modules, and evidence references", () => {
  for (const scenario of VALIDATION_SCENARIOS) {
    assert.ok(scenario.purpose.length > 0, scenario.id);
    assert.ok(scenario.modules.length > 0, scenario.id);
    assert.ok(scenario.evidenceReferences.length > 0, scenario.id);
  }
});

test("scenario lookup finds known ids and rejects unknown", () => {
  assert.equal(scenarioById("software")?.name, "Software workflow");
  assert.equal(scenarioById("nope"), null);
});

// ---------- Release record ----------

test("release record shows the real app version, never a future label", () => {
  // 17H2: compare against the package.json-derived source of truth, never a literal.
  assert.equal(RELEASE_RECORD.appVersion, APP_VERSION);
});

// ---------- Limitations ----------

test("limitation records cover all eight accepted limitations with modules", () => {
  assert.equal(LIMITATION_RECORDS.length, 8);
  for (const record of LIMITATION_RECORDS) {
    assert.ok(record.module.length > 0, record.title);
    assert.ok(record.workaround.length > 0, record.title);
    assert.ok(record.targetRelease.length > 0, record.title);
  }
});

test("the most impactful limitations name their real modules", () => {
  const byTitle = new Map(LIMITATION_RECORDS.map((record) => [record.title, record]));
  assert.equal(byTitle.get("Provider health is discover-derived")?.module, "Model Hub");
  assert.equal(byTitle.get("Cost is a published-rate preview")?.module, "Cost & compute");
});

// ---------- Health summary ----------

test("health summary counts reflect the recorded scenario statuses", () => {
  const summary = betaHealthSummary({ feedbackRecords: null });
  assert.equal(summary.scenariosValidated, 0);
  assert.equal(summary.scenariosPending, 3);
  assert.equal(summary.scenariosFailed, 0);
  assert.equal(summary.openLimitations, 8);
  assert.equal(summary.feedbackRecords, null);
  assert.match(summary.line, /feedback records: not recorded/);
});

test("session feedback drafts appear only when actually composed", () => {
  const summary = betaHealthSummary({ feedbackRecords: 2 });
  assert.equal(summary.feedbackRecords, 2);
  assert.match(summary.line, /2 local feedback draft\(s\) this session/);
});

// ---------- Feedback paths ----------

test("feedback paths cover report, suggestion, and diagnostics", () => {
  assert.deepEqual(
    FEEDBACK_PATHS.map((path) => path.id),
    ["report", "suggest", "diagnostics"],
  );
});

test("every feedback path states local-only handling", () => {
  for (const path of FEEDBACK_PATHS) {
    assert.match(path.detail, /local|device|document/i, path.id);
    assert.ok(!/submit|upload|send to/i.test(path.detail), path.id);
  }
});
