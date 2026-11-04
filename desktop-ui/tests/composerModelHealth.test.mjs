// Composer model-menu health presentation tests (pure logic — no browser,
// no fetch, no backend). Run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import { composerModelHealth } from "../src/composerModelHealth.ts";

test("a model with no recorded verdict is unverified, not blocked", () => {
  const health = composerModelHealth(null);
  assert.equal(health.blocked, false);
  assert.equal(health.unverified, true);
  assert.equal(health.label, "Unverified");
  // An untested model can be measured from the menu.
  assert.equal(health.verifiable, true);
});

test("a READY verdict keeps the model selectable", () => {
  const health = composerModelHealth({ state: "READY", runnable: true });
  assert.equal(health.blocked, false);
  assert.equal(health.unverified, false);
  assert.equal(health.label, "Runnable");
  assert.equal(health.verifiable, false);
});

test("a recorded failure blocks the model and says why in operator words", () => {
  const auth = composerModelHealth({ state: "AUTH_REQUIRED", runnable: false });
  assert.equal(auth.blocked, true);
  assert.equal(auth.label, "Auth required");
  assert.equal(auth.verifiable, true);

  const missing = composerModelHealth({ state: "MODEL_NOT_FOUND", runnable: false });
  assert.equal(missing.blocked, true);
  assert.equal(missing.label, "Not found");

  const down = composerModelHealth({ state: "PROVIDER_UNAVAILABLE", runnable: false });
  assert.equal(down.label, "Provider down");

  const offline = composerModelHealth({ state: "LOCAL_RUNTIME_OFFLINE", runnable: false });
  assert.equal(offline.label, "Runtime offline");
});

test("the raw state constant never reaches the operator", () => {
  const health = composerModelHealth({ state: "AUTH_REQUIRED", runnable: false });
  assert.equal(health.label.includes("_"), false);
  assert.equal(health.label, "Auth required");
});

test("a verdict without a runnable flag still blocks unless it is READY", () => {
  assert.equal(composerModelHealth({ state: "AUTH_REQUIRED" }).blocked, true);
  assert.equal(composerModelHealth({ state: "READY" }).blocked, false);
});

test("a recorded UNVERIFIED verdict is not confused with an untested model", () => {
  // An untested model is usable and says "Unverified".
  const untested = composerModelHealth(null);
  assert.equal(untested.unverified, true);
  assert.equal(untested.blocked, false);
  assert.equal(untested.label, "Unverified");

  // A model the backend measured and found unusable (no endpoint) must not
  // read like an untested one.
  const measured = composerModelHealth({ state: "UNVERIFIED", runnable: false });
  assert.equal(measured.unverified, false);
  assert.equal(measured.blocked, true);
  assert.equal(measured.label, "Not configured");
});

test("a transient failure does not lock the model out of the composer", () => {
  // The provider was down, rate limited, or did not answer in time: nothing
  // about the CONFIGURATION is known to be wrong, and the backend
  // re-verifies before it gives up on a run.
  for (const state of ["PROVIDER_UNAVAILABLE", "LOCAL_RUNTIME_OFFLINE"]) {
    const transient = composerModelHealth({ state, runnable: false });
    assert.equal(transient.blocked, false, state);
    // The label still says what happened.
    assert.notEqual(transient.label, "Runnable");
    // And the operator can measure it again.
    assert.equal(transient.verifiable, true);
  }
});

test("a configuration defect still locks the model out", () => {
  for (const state of ["AUTH_REQUIRED", "MODEL_NOT_FOUND", "QUOTA_EXHAUSTED"]) {
    const defect = composerModelHealth({ state, runnable: false });
    assert.equal(defect.blocked, true, state);
    assert.equal(defect.verifiable, true);
  }
  // "No credit" is the operator word for it, not a raw constant.
  assert.equal(
    composerModelHealth({ state: "QUOTA_EXHAUSTED", runnable: false }).label,
    "No credit",
  );
});
