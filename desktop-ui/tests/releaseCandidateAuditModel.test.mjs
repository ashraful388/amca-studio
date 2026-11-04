// Unit tests for the AMCA Studio Release Candidate UX and Documentation Audit Model.
// Run with: node --test tests/releaseCandidateAuditModel.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  getAuditedSurfaces,
  getCapabilityMatrix,
  verifyReleaseNotesWording,
} from "../src/releaseCandidateAuditModel.ts";
import { validateGuidanceClaims } from "../src/onboardingAndGuidanceModel.ts";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test("capability boundary wording - available vs unclaimed capability matrix", () => {
  const matrix = getCapabilityMatrix();
  assert.equal(matrix.available.length, 5);
  assert.equal(matrix.unclaimed.length, 4);

  const availableIds = matrix.available.map((c) => c.id);
  assert.deepEqual(availableIds, [
    "cap-local-first",
    "cap-multi-model",
    "cap-governed-exec",
    "cap-knowledge-assisted",
    "cap-evidence-tracking",
  ]);

  const unclaimedIds = matrix.unclaimed.map((c) => c.id);
  assert.deepEqual(unclaimedIds, [
    "unclaimed-auto-employee",
    "unclaimed-unrestricted-auto",
    "unclaimed-hidden-decisions",
    "unclaimed-cloud-dependency",
  ]);

  for (const c of [...matrix.available, ...matrix.unclaimed]) {
    assert.ok(c.name.length > 0);
    assert.ok(c.description.length > 0);
    assert.ok(c.governanceContractRef.length > 0);
  }
});

test("navigation completeness across all 7 audited platform surfaces", () => {
  const surfaces = getAuditedSurfaces();
  assert.equal(surfaces.length, 7);

  const surfaceIds = surfaces.map((s) => s.surfaceId);
  assert.deepEqual(surfaceIds, [
    "dashboard",
    "model_hub",
    "knowledge_center",
    "cognitive_workflow",
    "review_center",
    "artifact_center",
    "automation_center",
  ]);

  for (const s of surfaces) {
    assert.equal(s.status, "AUDITED_CLEAN");
    assert.equal(s.navigationVerified, true);
    assert.equal(s.emptyStateVerified, true);
    assert.equal(s.guidanceAttached, true);
  }
});

test("no unsupported claims - strict anti-hype checks across capability matrix", () => {
  const matrix = getCapabilityMatrix();
  for (const cap of matrix.available) {
    const text = `${cap.name} ${cap.description}`;
    const check = validateGuidanceClaims(text);
    assert.equal(check.hasHype, false, `Forbidden hype term found in available capability ${cap.id}`);
  }
});

test("release notes wording and canonical terminology verification", () => {
  const releaseNotesPath = path.resolve(__dirname, "../../docs/release/AMCA-Studio-v0.9.0-Release-Notes.md");
  assert.equal(fs.existsSync(releaseNotesPath), true, "Release notes file must exist");

  const releaseNotesContent = fs.readFileSync(releaseNotesPath, "utf-8");
  const verification = verifyReleaseNotesWording(releaseNotesContent);
  assert.equal(verification.verified, true, `Issues in release notes: ${verification.issues.join("; ")}`);
  assert.equal(verification.issues.length, 0);
});

test("terminology consistency across audited surfaces and governance references", () => {
  const matrix = getCapabilityMatrix();
  const descText = matrix.available.map((c) => c.description).join(" ");
  assert.match(descText, /local/i);
  assert.match(descText, /governed/i);
  assert.match(descText, /approval/i);
  assert.match(descText, /provenance/i);
});

