// Unit tests for the AMCA Studio Cognitive Workflow Model presentation logic.
// Run with: node --test tests/cognitiveWorkflowModel.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildCognitiveTimeline,
  formatKnowledgeUsage,
  formatLearningSignals,
  formatPlanningTransparency,
  formatResourceSelectionExplanation,
  formatTaskUnderstanding,
  formatVerificationResults,
  resolveCognitiveStateBadge,
} from "../src/cognitiveWorkflowModel.ts";

test("resolveCognitiveStateBadge returns truthful status and labels across all states", () => {
  assert.deepEqual(resolveCognitiveStateBadge("INITIALIZED"), {
    status: "neutral",
    label: "Initialized",
    isTerminal: false,
    isFailure: false,
  });
  assert.deepEqual(resolveCognitiveStateBadge("GOAL_UNDERSTOOD"), {
    status: "info",
    label: "Goal Understood",
    isTerminal: false,
    isFailure: false,
  });
  assert.deepEqual(resolveCognitiveStateBadge("COMPLETED"), {
    status: "success",
    label: "Completed",
    isTerminal: true,
    isFailure: false,
  });
  assert.deepEqual(resolveCognitiveStateBadge("FAILED_KNOWLEDGE_ACCESS"), {
    status: "error",
    label: "Knowledge Access Denied",
    isTerminal: true,
    isFailure: true,
  });
  assert.deepEqual(resolveCognitiveStateBadge("CANCELLED"), {
    status: "warning",
    label: "Cancelled",
    isTerminal: true,
    isFailure: false,
  });
});

test("buildCognitiveTimeline produces 6 ordered lifecycle steps", () => {
  const mockWf = {
    workflow_id: "wf-101",
    workspace_id: "ws-test",
    state: "RESOURCES_SELECTED",
    goal: {
      goal_id: "g1",
      raw_goal: "Analyze data",
      structured_intent: "DATA_ANALYSIS",
      constraints: ["offline"],
      domain: "data_science",
      required_outputs: ["report.md"],
      timestamp: "2026-09-02T12:00:00Z",
    },
    plan: {
      plan_id: "p1",
      planned_steps: ["step1", "step2"],
      required_capabilities: ["analysis"],
      strategy: "adaptive_hybrid",
      dependencies: [],
      timestamp: "2026-09-02T12:00:05Z",
    },
    selection: {
      selection_id: "s1",
      selected_models: ["llama3-8b-local"],
      selected_agents: ["analyst"],
      provider_health_references: ["ollama:HEALTHY"],
      decision_reason: "Capability match",
      timestamp: "2026-09-02T12:00:10Z",
    },
    created_at: "2026-09-02T12:00:00Z",
    updated_at: "2026-09-02T12:00:10Z",
  };

  const steps = buildCognitiveTimeline(mockWf);
  assert.equal(steps.length, 6);
  assert.deepEqual(
    steps.map((s) => s.id),
    ["goal", "plan", "resources", "knowledge", "execute", "verify"]
  );

  // Steps before RESOURCES_SELECTED are completed
  assert.equal(steps[0].status, "completed");
  assert.equal(steps[1].status, "completed");
  assert.equal(steps[2].status, "completed");

  // Step 3 (knowledge) is active
  assert.equal(steps[3].status, "active");

  // Later steps pending
  assert.equal(steps[4].status, "pending");
  assert.equal(steps[5].status, "pending");
});

test("buildCognitiveTimeline handles failure states truthfully", () => {
  const failedWf = {
    workflow_id: "wf-err",
    workspace_id: "ws-test",
    state: "FAILED_PLANNING",
    failure_reason: "No feasible plan found",
    created_at: "2026-09-02T12:00:00Z",
    updated_at: "2026-09-02T12:00:05Z",
  };

  const steps = buildCognitiveTimeline(failedWf);
  assert.equal(steps[1].status, "failed");
  assert.equal(steps[1].detail, "No feasible plan found");
  assert.equal(steps[2].status, "pending");
  assert.equal(steps[2].detail, "Skipped due to prior failure");
});

test("formatTaskUnderstanding formats structured intent and excludes hidden reasoning", () => {
  const goalRecord = {
    goal_id: "goal-99",
    raw_goal: "Evaluate network topologies",
    structured_intent: "TOPOLOGY_EVALUATION",
    domain: "networking",
    constraints: ["min_latency", "redundancy"],
    required_outputs: ["topology.json"],
    timestamp: "2026-09-02T10:00:00Z",
    // Simulating unexpected rogue property if injected
    hidden_reasoning: "Thinking internal thoughts...",
  };

  const formatted = formatTaskUnderstanding(goalRecord);
  assert.equal(formatted.hasData, true);
  assert.equal(formatted.goalId, "goal-99");
  assert.equal(formatted.structuredIntent, "TOPOLOGY_EVALUATION");
  assert.deepEqual(formatted.constraints, ["min_latency", "redundancy"]);
  assert.equal(Object.prototype.hasOwnProperty.call(formatted, "hidden_reasoning"), false);
});

test("formatPlanningTransparency exposes steps and strategy without confidence scores", () => {
  const planRecord = {
    plan_id: "plan-42",
    planned_steps: ["ingest", "filter", "aggregate"],
    required_capabilities: ["stream_processing"],
    strategy: "parallel_pipeline",
    dependencies: ["dep-1"],
    timestamp: "2026-09-02T10:01:00Z",
  };

  const formatted = formatPlanningTransparency(planRecord);
  assert.equal(formatted.hasData, true);
  assert.equal(formatted.strategy, "parallel_pipeline");
  assert.deepEqual(formatted.plannedSteps, ["ingest", "filter", "aggregate"]);
  assert.equal(Object.prototype.hasOwnProperty.call(formatted, "confidence"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(formatted, "ai_score"), false);
});

test("formatResourceSelectionExplanation classifies locality truthfully", () => {
  const selectionRecord = {
    selection_id: "sel-77",
    selected_models: ["llama3.1-8b-instruct", "claude-3-5-sonnet"],
    selected_agents: ["architect", "reviewer"],
    provider_health_references: ["ollama:HEALTHY", "anthropic:HEALTHY"],
    decision_reason: "High capability match",
    timestamp: "2026-09-02T10:02:00Z",
  };

  const formatted = formatResourceSelectionExplanation(selectionRecord);
  assert.equal(formatted.hasData, true);
  assert.equal(formatted.models.length, 2);
  assert.equal(formatted.models[0].name, "llama3.1-8b-instruct");
  assert.equal(formatted.models[0].locality, "Local Offline");
  assert.equal(formatted.models[1].name, "claude-3-5-sonnet");
  assert.equal(formatted.models[1].locality, "Cloud BYOK");
});

test("formatKnowledgeUsage exposes explicit provenance and audit references", () => {
  const knowledgeRecord = {
    binding_id: "kb-12",
    query_intent: "system architecture",
    retrieved_artifact_ids: ["art-101", "art-102"],
    permission_outcome: "AUTHORIZED",
    audit_event_ref: "aud-998877",
    timestamp: "2026-09-02T10:03:00Z",
  };

  const formatted = formatKnowledgeUsage(knowledgeRecord);
  assert.equal(formatted.hasData, true);
  assert.equal(formatted.isExplicit, true);
  assert.deepEqual(formatted.retrievedArtifactIds, ["art-101", "art-102"]);
  assert.equal(formatted.auditEventRef, "aud-998877");
});

test("formatVerificationResults presents execution status and artifacts cleanly", () => {
  const verifRecord = {
    verification_id: "v-88",
    execution_completed: true,
    artifacts_produced: ["output.csv", "metrics.json"],
    validation_result: "VERIFIED_SUCCESS",
    unresolved_issues: [],
    timestamp: "2026-09-02T10:04:00Z",
  };

  const formatted = formatVerificationResults(verifRecord);
  assert.equal(formatted.hasData, true);
  assert.equal(formatted.executionCompleted, true);
  assert.equal(formatted.validationResult, "VERIFIED_SUCCESS");
  assert.deepEqual(formatted.artifactsProduced, ["output.csv", "metrics.json"]);
});

test("formatLearningSignals captures metrics without subjective scores", () => {
  const learningRecord = {
    signal_id: "sig-5",
    workflow_id: "wf-1",
    execution_duration_ms: 320.5,
    failure_count: 0,
    retry_count: 0,
    resource_metrics: { peak_mem_mb: 256 },
    user_correction_count: 0,
    timestamp: "2026-09-02T10:05:00Z",
  };

  const formatted = formatLearningSignals(learningRecord);
  assert.equal(formatted.hasData, true);
  assert.equal(formatted.executionDurationMs, 320.5);
  assert.deepEqual(formatted.resourceMetrics, { peak_mem_mb: 256 });
});

test("presentation functions degrade gracefully on empty/null records", () => {
  assert.equal(formatTaskUnderstanding(null).hasData, false);
  assert.equal(formatPlanningTransparency(null).hasData, false);
  assert.equal(formatResourceSelectionExplanation(null).hasData, false);
  assert.equal(formatKnowledgeUsage(null).hasData, false);
  assert.equal(formatVerificationResults(null).hasData, false);
  assert.equal(formatLearningSignals(null).hasData, false);
});

