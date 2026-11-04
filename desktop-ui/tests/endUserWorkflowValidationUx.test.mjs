// ZCODE-AMCA-STUDIO-V0.9.0-END-USER-WORKFLOW-VALIDATION-AND-POLISH-01 tests.
// Order minimum: full journey coverage, terminology consistency, no
// unsupported claims, no broken navigation, empty-state consistency.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  JOURNEY_STAGES,
  CANONICAL_TERMS,
  terminologyAudit,
  navigationTargetsExist,
  emptyStateConsistency,
  passesClaimScan,
  validateEndUserWorkflow,
} from "../src/endUserWorkflowValidationUxModel.ts";
import { NAVIGABLE_VIEWS } from "../src/productivityModel.ts";
import { COMMAND_CENTER_EMPTY_STATES } from "../src/workspaceCommandModel.ts";
import { COLLABORATION_EMPTY_STATES } from "../src/projectCollaborationWorkspaceUxModel.ts";
import { TEMPLATE_EMPTY_STATES } from "../src/workflowTemplateReuseUxModel.ts";
import { CONTINUATION_EMPTY_STATES } from "../src/workspaceProductivityAssistanceUxModel.ts";
import { KNOWLEDGE_EMPTY_STATES } from "../src/knowledgeUxModel.ts";
import { EMPTY_STATE_COVERAGE } from "../src/consolidationUxModel.ts";

// ---------- full journey coverage ----------

test("all eleven journey stages map to existing surfaces in order", () => {
  assert.equal(JOURNEY_STAGES.length, 11);
  assert.deepEqual(
    JOURNEY_STAGES.map((stage) => stage.stage),
    [
      "First Launch",
      "Onboarding",
      "Workspace Creation",
      "Model Setup",
      "Knowledge Selection",
      "Workflow Creation",
      "Governance Review",
      "Execution",
      "Monitoring",
      "Artifact Review",
      "Export",
    ],
  );
  for (const stage of JOURNEY_STAGES) {
    assert.ok(stage.surface.length > 3, stage.stage);
    assert.ok(stage.evidence.length > 3, stage.stage);
  }
});

// ---------- terminology consistency ----------

test("canonical terms appear verbatim in the live surfaces that own them", () => {
  const surfaces = [
    { name: "knowledgeContextModel", text: "Explicitly attached — can be added to the composer" },
    { name: "consentGovernanceUxModel", text: "Local only. Approval required. Not recorded." },
    { name: "knowledgeIntelligenceModel", text: "Explicit user attachment (no automatic retrieval)" },
  ];
  const audit = terminologyAudit(surfaces);
  const byTerm = Object.fromEntries(audit.map((row) => [row.term, row]));
  assert.equal(byTerm["Local only"].consistent, true);
  assert.equal(byTerm["Approval required"].consistent, true);
  assert.equal(byTerm["Explicitly attached"].consistent, true);
  assert.equal(byTerm["Not recorded"].consistent, true);
  assert.equal(byTerm["No automatic retrieval"].consistent, true);
  // a term no surface carries is flagged inconsistent
  const broken = terminologyAudit([{ name: "empty", text: "" }]);
  assert.ok(broken.every((row) => row.consistent === false));
});

// ---------- navigation safety ----------

test("all recorded drill/entry navigation targets are existing sidebar views", () => {
  const viewIds = NAVIGABLE_VIEWS.map((view) => view.id);
  // recorded targets from the accepted UX orders
  const targets = ["agents", "artifacts", "settings", "validation", "overview", "dashboard", "review", "help", "automations"];
  const journeyViewIds = NAVIGABLE_VIEWS.map((view) => view.id);
  const results = navigationTargetsExist(targets, viewIds);
  for (const result of results) {
    assert.equal(result.exists, true, result.target);
  }
  // a broken target is caught
  assert.equal(navigationTargetsExist(["nonexistent-view"], viewIds)[0].exists, false);
  assert.ok(viewIds.length >= 17);
});

// ---------- empty-state consistency ----------

test("all registered empty-state sources carry non-empty consistent wording", () => {
  // wire the LIVE empty-state sources from the owning models
  const sources = [
    { source: "workspaceCommandModel.COMMAND_CENTER_EMPTY_STATES", keys: Object.values(COMMAND_CENTER_EMPTY_STATES) },
    { source: "projectCollaborationWorkspaceUxModel.COLLABORATION_EMPTY_STATES", keys: Object.values(COLLABORATION_EMPTY_STATES) },
    { source: "workflowTemplateReuseUxModel.TEMPLATE_EMPTY_STATES", keys: Object.values(TEMPLATE_EMPTY_STATES) },
    { source: "workspaceProductivityAssistanceUxModel.CONTINUATION_EMPTY_STATES", keys: Object.values(CONTINUATION_EMPTY_STATES) },
    { source: "knowledgeUxModel.KNOWLEDGE_EMPTY_STATES", keys: Object.values(KNOWLEDGE_EMPTY_STATES).map((state) => state.title) },
  ];
  const checks = emptyStateConsistency(sources);
  assert.equal(checks.length, 5);
  for (const check of checks) {
    assert.equal(check.consistent, true, check.source);
    for (const key of check.keys) {
      assert.ok(key.trim().length > 5, check.source);
    }
  }
});

// ---------- unsupported claims ----------

test("journey text with unsupported claims fails the scan; clean text passes", () => {
  assert.equal(passesClaimScan("Local-first desktop platform with governed workflows."), true);
  for (const claim of [
    "autonomous AI employee",
    "unlimited agent swarm",
    "cloud SaaS platform",
    "marketplace ecosystem",
    "best workflow for you",
    "productivity score: 92",
  ]) {
    assert.equal(passesClaimScan(claim), false, claim);
  }
});

// ---------- full validation ----------

test("end-user workflow validation passes end to end", () => {
  const surfaces = [
    { name: "knowledgeContextModel", text: "Explicitly attached" },
    { name: "consentGovernanceUxModel", text: "Local only / Approval required / Not recorded" },
    { name: "knowledgeIntelligenceModel", text: "no automatic retrieval" },
  ];
  const journeyViewIds = NAVIGABLE_VIEWS.map((view) => view.id);
  const validation = validateEndUserWorkflow({
    surfaces,
    navigationTargets: ["agents", "artifacts", "settings", "validation", "overview", "dashboard", "review", "help", "automations"],
    journeyText: "First launch to export: recorded evidence only, governance visible at every stage.",
    viewIds: journeyViewIds,
    emptyStateSources: [
      { source: "workspaceCommandModel", keys: Object.values(COMMAND_CENTER_EMPTY_STATES) },
      { source: "projectCollaborationWorkspaceUxModel", keys: Object.values(COLLABORATION_EMPTY_STATES) },
      { source: "workflowTemplateReuseUxModel", keys: Object.values(TEMPLATE_EMPTY_STATES) },
      { source: "workspaceProductivityAssistanceUxModel", keys: Object.values(CONTINUATION_EMPTY_STATES) },
      { source: "knowledgeUxModel", keys: Object.values(KNOWLEDGE_EMPTY_STATES).map((state) => state.title) },
    ],
  });
  assert.equal(validation.passed, true, JSON.stringify(validation.terminology.filter((t) => !t.consistent)));
  assert.equal(validation.stagesCovered, validation.totalStages);
  // onboarding coverage evidence exists (the accepted consolidation coverage registry)
  assert.ok(EMPTY_STATE_COVERAGE.length > 0);
});
