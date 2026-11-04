// Unit tests for the AMCA Studio End-User Workflow Validation and Polish Model.
// Run with: node --test tests/endUserWorkflowValidationModel.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import {
  CANONICAL_TERMINOLOGY,
  auditTerminologyConsistency,
  getReleaseCandidateChecklist,
  getUserJourneyStages,
} from "../src/userWorkflowPolishModel.ts";
import { validateGuidanceClaims } from "../src/onboardingAndGuidanceModel.ts";

test("full journey coverage - 6 ordered stages from first launch to artifact export", () => {
  const stages = getUserJourneyStages();
  assert.equal(stages.length, 6);

  const expectedStages = [
    "stage-1-launch",
    "stage-2-models",
    "stage-3-knowledge",
    "stage-4-workflow",
    "stage-5-review",
    "stage-6-artifacts",
  ];
  assert.deepEqual(stages.map((s) => s.stageId), expectedStages);

  for (const st of stages) {
    assert.ok(st.name.length > 0);
    assert.ok(st.surface.length > 0);
    assert.ok(st.userObjective.length > 0);
    assert.ok(st.nextActionLabel.length > 0);
    assert.ok(st.safetyBoundaryNote.length > 0);
  }
});

test("cross-surface terminology consistency - canonical terms audit", () => {
  const allTerms = Object.values(CANONICAL_TERMINOLOGY);
  assert.equal(allTerms.length, 5);
  assert.ok(allTerms.includes("Local only"));
  assert.ok(allTerms.includes("Approval required"));
  assert.ok(allTerms.includes("Explicitly attached"));
  assert.ok(allTerms.includes("Not recorded"));
  assert.ok(allTerms.includes("No automatic retrieval"));

  // Audit combined text across user journey stages
  const combinedText = getUserJourneyStages()
    .map((s) => `${s.name} ${s.userObjective} ${s.canonicalTerms.join(" ")} ${s.safetyBoundaryNote}`)
    .join(" ");

  const audit = auditTerminologyConsistency(combinedText);
  assert.equal(audit.missingTerms.length, 0, `Missing canonical terms: ${audit.missingTerms.join(", ")}`);
  assert.equal(audit.verifiedTerms.length, 5);
});

test("no unsupported claims - strict anti-hype verification across user journey descriptions", () => {
  const stages = getUserJourneyStages();
  for (const st of stages) {
    const text = `${st.name} ${st.userObjective} ${st.safetyBoundaryNote}`;
    const check = validateGuidanceClaims(text);
    assert.equal(check.hasHype, false, `Found hype terms in stage ${st.stageId}: ${check.forbiddenTerms.join(", ")}`);
  }
});

test("no broken navigation - every stage provides explicit, clear next action label", () => {
  const stages = getUserJourneyStages();
  for (const st of stages) {
    assert.ok(st.nextActionLabel.length > 0);
    assert.notEqual(st.nextActionLabel, "Click here");
    assert.notEqual(st.nextActionLabel, "Submit");
  }
});

test("release candidate checklist completeness and verification", () => {
  const checklist = getReleaseCandidateChecklist();
  assert.equal(checklist.length, 3);

  const totalChecks = checklist.reduce((acc, cat) => acc + cat.checks.length, 0);
  assert.equal(totalChecks, 9);

  for (const cat of checklist) {
    assert.ok(cat.category.length > 0);
    for (const check of cat.checks) {
      assert.ok(check.id.length > 0);
      assert.ok(check.title.length > 0);
      assert.ok(check.description.length > 0);
      assert.equal(check.verified, true);
    }
  }
});

