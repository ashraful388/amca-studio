// AMCA Studio v1.0.0 Manual Model Selection to Workflow Binding — Frontend Tests.
// Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MANUAL-MODEL-SELECTION-TO-WORKFLOW-BINDING-01

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import {
  resolveBindingStateBadge,
  formatWorkflowBinding,
  formatBlockedModel,
  formatCrossTraceNavigation,
  auditNoSilentFallback,
} from "../src/manualModelSelectionWorkflowBindingModel.ts";

// ---------------------------------------------------------------------------
// Test 1: MANUAL_OPERATOR_SELECTION badge renders with success severity and no action required
// ---------------------------------------------------------------------------
describe("resolveBindingStateBadge — MANUAL_OPERATOR_SELECTION", () => {
  it("renders success badge with no action required", () => {
    const badge = resolveBindingStateBadge("MANUAL_OPERATOR_SELECTION");
    assert.equal(badge.severity, "success");
    assert.equal(badge.requiresAction, false);
    assert.ok(badge.label.includes("Manual"));
    assert.equal(badge.state, "MANUAL_OPERATOR_SELECTION");
  });
});

// ---------------------------------------------------------------------------
// Test 2: BLOCKED_UNAVAILABLE badge requires action and mentions no silent substitution
// ---------------------------------------------------------------------------
describe("resolveBindingStateBadge — BLOCKED_UNAVAILABLE", () => {
  it("renders error badge requiring action and mentions no automatic substitution", () => {
    const badge = resolveBindingStateBadge("BLOCKED_UNAVAILABLE");
    assert.equal(badge.severity, "error");
    assert.equal(badge.requiresAction, true);
    assert.ok(badge.description.toLowerCase().includes("substitution"));
  });
});

// ---------------------------------------------------------------------------
// Test 3: formatWorkflowBinding with full binding record
// ---------------------------------------------------------------------------
describe("formatWorkflowBinding — full record", () => {
  it("formats a binding record with correct fields and manual badge", () => {
    const binding = {
      binding_id: "bind-abc123",
      workflow_id: "wf-001",
      session_id: "sess-001",
      selection_id: "sel-def456",
      model_id: "llama3-8b",
      model_locality: "local",
      provider_approval_state: null,
      operator_rationale_notes: "Verified low latency by HumanEval benchmarks.",
      evidence_references_reviewed: ["eval-001", "eval-002"],
      binding_state: /** @type {"MANUAL_OPERATOR_SELECTION"} */ ("MANUAL_OPERATOR_SELECTION"),
      timestamp: "2026-09-03T00:00:00Z",
    };

    const pres = formatWorkflowBinding(binding);
    assert.equal(pres.hasBinding, true);
    assert.equal(pres.modelId, "llama3-8b");
    assert.equal(pres.badge.severity, "success");
    assert.deepEqual(pres.evidenceReferencesReviewed, ["eval-001", "eval-002"]);
    assert.ok(pres.operatorRationaleNotes.includes("HumanEval"));
  });
});

// ---------------------------------------------------------------------------
// Test 4: formatBlockedModel — confirms silent_fallback_performed is always false
// ---------------------------------------------------------------------------
describe("formatBlockedModel — unavailable model block", () => {
  it("renders blocked model correctly with silent_fallback_performed=false invariant", () => {
    const block = {
      block_id: "blk-xyz789",
      workflow_id: "wf-002",
      session_id: "sess-002",
      selection_id: "sel-ghi012",
      model_id: "codestral-7b",
      reason: "Model file not found on local disk",
      requires_explicit_user_action: true,
      silent_fallback_performed: false,
      timestamp: "2026-09-03T00:01:00Z",
    };

    const pres = formatBlockedModel(block);
    assert.equal(pres.isBlocked, true);
    assert.equal(pres.modelId, "codestral-7b");
    assert.equal(pres.silentFallbackPerformed, false); // invariant
    assert.equal(pres.requiresExplicitUserAction, true);
    assert.ok(pres.reason.toLowerCase().includes("model file"));
  });
});

// ---------------------------------------------------------------------------
// Test 5: Cross-trace navigation formats all hop links and auditNoSilentFallback passes
// ---------------------------------------------------------------------------
describe("formatCrossTraceNavigation and auditNoSilentFallback", () => {
  it("formats all navigation links and confirms anti-fallback invariant", () => {
    const nav = {
      navigator_id: "nav-abc001",
      selection_id: "sel-001",
      workflow_id: "wf-003",
      session_id: "sess-003",
      evidence_refs: ["eval-ref-001"],
      artifact_refs: ["art-001"],
    };

    const pres = formatCrossTraceNavigation(nav);
    assert.equal(pres.hasNavigator, true);
    assert.equal(pres.navigatorId, "nav-abc001");
    // Should have: selection, workflow, session, evidence, artifact = 5 links
    assert.equal(pres.links.length, 5);
    const kinds = pres.links.map((l) => l.kind);
    assert.ok(kinds.includes("selection"));
    assert.ok(kinds.includes("workflow"));
    assert.ok(kinds.includes("session"));
    assert.ok(kinds.includes("evidence"));
    assert.ok(kinds.includes("artifact"));

    // Anti-silent-fallback invariant
    const blocks = [
      {
        block_id: "blk-1",
        workflow_id: "wf-003",
        session_id: "sess-003",
        selection_id: "sel-001",
        model_id: "model-x",
        reason: "unavailable",
        requires_explicit_user_action: true,
        silent_fallback_performed: false,
        timestamp: "2026-09-03T00:02:00Z",
      },
    ];
    assert.equal(auditNoSilentFallback(blocks), true);
  });
});
