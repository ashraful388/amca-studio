// Unit tests for the AMCA Studio Decision Transparency UX Preparation Model.
// Run with: node --test tests/decisionTransparencyModel.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import {
  formatDecisionTransparencyCard,
  formatEvidenceProvenanceViewer,
  getHumanControlPlaceholders,
} from "../src/decisionTransparencyModel.ts";

test("missing decision record handling displays truthful empty state", () => {
  const nullCard = formatDecisionTransparencyCard(null);
  assert.equal(nullCard.hasRecord, false);
  assert.equal(nullCard.decisionId, null);
  assert.equal(nullCard.emptyPlaceholder, "No decision records available");
  assert.equal(nullCard.status, "NONE");
  assert.equal(nullCard.statusBadge.label, "No Records");

  const undefinedCard = formatDecisionTransparencyCard(undefined);
  assert.equal(undefinedCard.hasRecord, false);
  assert.equal(undefinedCard.emptyPlaceholder, "No decision records available");

  const emptyViewer = formatEvidenceProvenanceViewer(null);
  assert.deepEqual(emptyViewer, []);
});

test("evidence reference rendering extracts and formats provenance correctly", () => {
  const mockRecord = {
    decision_id: "dec-001",
    status: "APPROVED",
    evidence_reference_ids: ["ev-101", "ev-102"],
    selected_resources: ["qwen2.5-coder-32b", "agent-coder"],
    approval_reference: "appr-dr-smith-01",
    override_available: true,
    source_category: "clinical_oncology",
    timestamp: "2026-09-02T22:00:00Z",
    linked_workflow_id: "wf-res-001",
    linked_artifact_ids: ["art-lung-ct"],
  };

  const card = formatDecisionTransparencyCard(mockRecord);
  assert.equal(card.hasRecord, true);
  assert.equal(card.decisionId, "dec-001");
  assert.equal(card.status, "APPROVED");
  assert.equal(card.statusBadge.label, "Approved");
  assert.equal(card.statusBadge.variant, "success");
  assert.deepEqual(card.evidenceReferences, ["ev-101", "ev-102"]);
  assert.deepEqual(card.selectedResources, ["qwen2.5-coder-32b", "agent-coder"]);
  assert.equal(card.approvalReference, "appr-dr-smith-01");
  assert.equal(card.overrideAvailable, true);

  const viewer = formatEvidenceProvenanceViewer(mockRecord);
  assert.equal(viewer.length, 2);
  assert.equal(viewer[0].evidenceId, "ev-101");
  assert.equal(viewer[0].sourceCategory, "clinical_oncology");
  assert.equal(viewer[0].linkedWorkflowId, "wf-res-001");
  assert.equal(viewer[0].linkedArtifactsCount, 1);
});

test("no hidden reasoning fields exist in presentation models", () => {
  const mockWithForbiddenKeys = {
    decision_id: "dec-002",
    status: "PENDING",
    evidence_reference_ids: ["ev-201"],
    selected_resources: [],
    override_available: false,
    source_category: "review",
    timestamp: "2026-09-02T22:00:00Z",
    linked_workflow_id: "wf-002",
    linked_artifact_ids: [],
    // Forbidden fields that an untrusted API might send:
    chain_of_thought: "internal thinking",
    hidden_reasoning: "private contemplation",
    raw_thoughts: "unfiltered thoughts",
  };

  const card = formatDecisionTransparencyCard(mockWithForbiddenKeys);
  assert.equal("chain_of_thought" in card, false);
  assert.equal("hidden_reasoning" in card, false);
  assert.equal("raw_thoughts" in card, false);

  const viewer = formatEvidenceProvenanceViewer(mockWithForbiddenKeys);
  assert.equal("chain_of_thought" in viewer[0], false);
  assert.equal("hidden_reasoning" in viewer[0], false);
});

test("no confidence scores or subjective rankings in models", () => {
  const mockRecord = {
    decision_id: "dec-003",
    status: "REJECTED",
    evidence_reference_ids: ["ev-301"],
    selected_resources: [],
    override_available: false,
    source_category: "triage",
    timestamp: "2026-09-02T22:00:00Z",
    linked_workflow_id: "wf-003",
    linked_artifact_ids: [],
    confidence: 0.98,
    ranking: 1,
    score: 95,
  };

  const card = formatDecisionTransparencyCard(mockRecord);
  assert.equal("confidence" in card, false);
  assert.equal("ranking" in card, false);
  assert.equal("score" in card, false);
});

test("no active decision controls - all placeholders are strictly disabled", () => {
  const controls = getHumanControlPlaceholders();
  assert.equal(controls.length, 4);
  for (const ctrl of controls) {
    assert.equal(ctrl.disabled, true);
    assert.match(ctrl.statusText, /Informational.*No action implemented/);
  }
});

test("governance wording consistency across status variants", () => {
  const statuses = ["PENDING", "APPROVED", "REJECTED", "OVERRIDDEN"];
  for (const st of statuses) {
    const card = formatDecisionTransparencyCard({
      decision_id: `dec-${st}`,
      status: st,
      evidence_reference_ids: [],
      selected_resources: [],
      override_available: false,
      source_category: "test",
      timestamp: "2026-09-02T22:00:00Z",
      linked_workflow_id: "wf-test",
      linked_artifact_ids: [],
    });
    assert.ok(card.statusBadge.label.length > 0);
    assert.ok(["neutral", "info", "success", "warning", "error"].includes(card.statusBadge.variant));
  }
});

