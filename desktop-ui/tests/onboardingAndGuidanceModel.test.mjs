// Unit tests for the AMCA Studio Professional Workflow Onboarding and Guidance Model.
// Run with: node --test tests/onboardingAndGuidanceModel.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import {
  getContextualHelp,
  getEmptyStateGuidance,
  getOnboardingSteps,
  validateGuidanceClaims,
} from "../src/onboardingAndGuidanceModel.ts";

test("onboarding flow completeness - all 5 steps configured with valid navigation labels", () => {
  const steps = getOnboardingSteps();
  assert.equal(steps.length, 5);

  const categories = steps.map((s) => s.category);
  assert.deepEqual(categories, [
    "intro",
    "local_first",
    "model_selection",
    "approval_boundaries",
    "workflow_creation",
  ]);

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    assert.equal(step.stepIndex, i + 1);
    assert.ok(step.title.length > 0);
    assert.ok(step.summary.length > 0);
    assert.ok(step.details.length >= 3);
    assert.ok(step.userActionLabel.length > 0);
  }
});

test("no unsupported claims - strict anti-hype verification across all guidance text", () => {
  const steps = getOnboardingSteps();
  for (const step of steps) {
    const allText = `${step.title} ${step.summary} ${step.details.join(" ")}`;
    const check = validateGuidanceClaims(allText);
    assert.equal(check.hasHype, false, `Found forbidden terms in step ${step.stepId}: ${check.forbiddenTerms.join(", ")}`);
  }

  const surfaces = [
    "model_hub",
    "knowledge_center",
    "cognitive_workflow",
    "review_center",
    "artifact_center",
    "automation_center",
  ];
  for (const surf of surfaces) {
    const help = getContextualHelp(surf);
    const text = `${help.title} ${help.summary} ${help.guidancePoints.join(" ")} ${help.whatAmcaDoesNotDo}`;
    const check = validateGuidanceClaims(text);
    assert.equal(check.hasHype, false, `Found forbidden terms in help for ${surf}: ${check.forbiddenTerms.join(", ")}`);
  }
});

test("governance explanation present across onboarding and contextual help", () => {
  const steps = getOnboardingSteps();
  const step4 = steps.find((s) => s.category === "approval_boundaries");
  assert.ok(step4);
  assert.match(step4.title, /Approval Boundaries/i);
  assert.match(step4.summary, /operator authorization/i);

  const reviewHelp = getContextualHelp("review_center");
  assert.match(reviewHelp.title, /Operator Sign-Off/i);
  assert.match(reviewHelp.whatAmcaDoesNotDo, /does not bypass human operator review/i);
});

test("contextual help coverage across all 6 required surfaces", () => {
  const requiredSurfaces = [
    "model_hub",
    "knowledge_center",
    "cognitive_workflow",
    "review_center",
    "artifact_center",
    "automation_center",
  ];

  for (const surf of requiredSurfaces) {
    const help = getContextualHelp(surf);
    assert.equal(help.surfaceId, surf);
    assert.ok(help.title.length > 0);
    assert.ok(help.summary.length > 0);
    assert.ok(help.guidancePoints.length >= 3);
    assert.match(help.whatAmcaDoesNotDo, /AMCA does not/i);
  }
});

test("empty-state consistency across all 5 workspace surfaces", () => {
  const emptySurfaces = ["projects", "workflows", "models", "knowledge", "artifacts"];

  for (const surf of emptySurfaces) {
    const guidance = getEmptyStateGuidance(surf);
    assert.equal(guidance.surface, surf);
    assert.ok(guidance.title.length > 0);
    assert.ok(guidance.whatIsMissing.length > 0);
    assert.ok(guidance.nextActionPrompt.length > 0);
    assert.match(guidance.whatAmcaDoesNotDo, /AMCA does not/i);
  }
});

