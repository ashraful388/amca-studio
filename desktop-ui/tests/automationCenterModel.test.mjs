// AMCA Studio Milestone B2: Automation Center UI model tests
// Pure presentation logic verification (card formatting, status badges, parameter extraction, draft validation, filtering)
// Run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  extractGoalPlaceholders,
  filterWorkflows,
  formatWorkflowCard,
  resolveWorkflowStatusBadge,
  validateWorkflowDraft,
} from "../src/automationCenterModel.ts";

const SAMPLE_WORKFLOWS = [
  {
    id: "wf-1",
    name: "Nightly Security Audit",
    project_id: "prj-alpha",
    goal: "Inspect repository for vulnerable dependencies and outdated lockfiles.",
    automation_type: "recurring",
    schedule: "0 2 * * *",
    enabled: true,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    last_run_at: "2026-09-02T02:00:00Z",
    last_run_id: "run-sec-01",
    revision: 1,
    metadata: {
      template: "security_audit",
      mode: "joint",
      participant_ids: ["ollama:llama3.2"],
    },
    execution_history_count: 5,
    latest_occurrence: {
      id: "occ-1",
      automation_id: "wf-1",
      scheduled_for: "2026-09-02T02:00:00Z",
      status: "succeeded",
      claimed_at: "2026-09-02T02:00:01Z",
      run_id: "run-sec-01",
      completed_at: "2026-09-02T02:01:30Z",
      error: null,
    },
  },
  {
    id: "wf-2",
    name: "Dataset Schema Ingestion",
    project_id: "prj-alpha",
    goal: "Verify {dataset_file} exists and validate schema columns against {target_table}.",
    automation_type: "conditional",
    schedule: "*/15 * * * *",
    enabled: true,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    last_run_at: null,
    last_run_id: null,
    revision: 2,
    metadata: {
      template: "dataset_validation",
      mode: "solo",
    },
    execution_history_count: 0,
    latest_occurrence: null,
  },
  {
    id: "wf-3",
    name: "Ad-hoc Literature Review",
    project_id: "prj-alpha",
    goal: "Synthesize findings across research corpus on topic: {topic_name}",
    automation_type: "once",
    schedule: "manual",
    enabled: true,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    last_run_at: "2026-09-01T12:00:00Z",
    last_run_id: "run-lit-01",
    revision: 1,
    metadata: {
      template: "research_synthesis",
      mode: "adaptive_hybrid",
    },
    execution_history_count: 2,
    latest_occurrence: {
      id: "occ-3",
      automation_id: "wf-3",
      scheduled_for: "2026-09-01T12:00:00Z",
      status: "failed",
      claimed_at: "2026-09-01T12:00:01Z",
      run_id: "run-lit-01",
      completed_at: "2026-09-01T12:00:15Z",
      error: "Ollama model timeout during synthesis pass",
    },
  },
];

test("extractGoalPlaceholders identifies variable parameters in goal prompt", () => {
  const goal = "Analyze {query_term} on branch {target_branch} with mode {mode_flag} and check {query_term} again.";
  const params = extractGoalPlaceholders(goal);
  assert.deepEqual(params, ["query_term", "target_branch", "mode_flag"]);
});

test("extractGoalPlaceholders returns empty array for goals without placeholders", () => {
  assert.deepEqual(extractGoalPlaceholders("Run static analysis on current workspace."), []);
  assert.deepEqual(extractGoalPlaceholders(""), []);
});

test("resolveWorkflowStatusBadge maps latest occurrence states truthfully", () => {
  assert.deepEqual(resolveWorkflowStatusBadge(null), { status: "ready", label: "Ready" });
  assert.deepEqual(resolveWorkflowStatusBadge(undefined), { status: "ready", label: "Ready" });
  assert.deepEqual(resolveWorkflowStatusBadge({ status: "running" }), { status: "running", label: "Running" });
  assert.deepEqual(resolveWorkflowStatusBadge({ status: "succeeded" }), { status: "succeeded", label: "Success" });
  assert.deepEqual(resolveWorkflowStatusBadge({ status: "failed" }), { status: "failed", label: "Failed" });
  assert.deepEqual(resolveWorkflowStatusBadge({ status: "claimed" }), { status: "claimed", label: "Claimed" });
});

test("formatWorkflowCard formats workflow card presentation accurately", () => {
  const card1 = formatWorkflowCard(SAMPLE_WORKFLOWS[0]);
  assert.equal(card1.id, "wf-1");
  assert.equal(card1.name, "Nightly Security Audit");
  assert.equal(card1.automationType, "recurring");
  assert.equal(card1.schedule, "0 2 * * *");
  assert.equal(card1.templateName, "security_audit");
  assert.equal(card1.modeName, "joint");
  assert.equal(card1.runsCount, 5);
  assert.equal(card1.statusBadge.status, "succeeded");
  assert.equal(card1.statusBadge.label, "Success");

  const card2 = formatWorkflowCard(SAMPLE_WORKFLOWS[1]);
  assert.equal(card2.runsCount, 0);
  assert.equal(card2.statusBadge.status, "ready");
});

test("validateWorkflowDraft validates required fields for all automation types", () => {
  // Empty draft
  const res1 = validateWorkflowDraft({
    name: "",
    goal: "",
    automation_type: "once",
  });
  assert.equal(res1.valid, false);
  assert.equal(res1.errors.length, 2);

  // Valid once draft
  const res2 = validateWorkflowDraft({
    name: "Adhoc Run",
    goal: "Run verification checks",
    automation_type: "once",
  });
  assert.equal(res2.valid, true);
  assert.equal(res2.errors.length, 0);

  // Invalid recurring draft (missing/malformed cron)
  const res3 = validateWorkflowDraft({
    name: "Nightly Run",
    goal: "Run test suite",
    automation_type: "recurring",
    schedule: "invalid cron",
  });
  assert.equal(res3.valid, false);
  assert.match(res3.errors[0], /5 parts/);

  // Valid recurring draft
  const res4 = validateWorkflowDraft({
    name: "Nightly Run",
    goal: "Run test suite",
    automation_type: "recurring",
    schedule: "0 0 * * 1-5",
  });
  assert.equal(res4.valid, true);

  // Invalid conditional draft (missing conditionPath)
  const res5 = validateWorkflowDraft({
    name: "Trigger On File",
    goal: "Process new file",
    automation_type: "conditional",
    conditionPath: "",
  });
  assert.equal(res5.valid, false);
  assert.match(res5.errors[0], /file path is required/);

  // Valid conditional draft
  const res6 = validateWorkflowDraft({
    name: "Trigger On File",
    goal: "Process new file",
    automation_type: "conditional",
    conditionPath: "input/data.csv",
  });
  assert.equal(res6.valid, true);
});

test("filterWorkflows filters by search query and automation type", () => {
  // Filter by query "security"
  const qRes = filterWorkflows(SAMPLE_WORKFLOWS, "security", "all");
  assert.equal(qRes.length, 1);
  assert.equal(qRes[0].id, "wf-1");

  // Filter by type "conditional"
  const typeRes = filterWorkflows(SAMPLE_WORKFLOWS, "", "conditional");
  assert.equal(typeRes.length, 1);
  assert.equal(typeRes[0].id, "wf-2");

  // Filter by query and type
  const bothRes = filterWorkflows(SAMPLE_WORKFLOWS, "topic", "once");
  assert.equal(bothRes.length, 1);
  assert.equal(bothRes[0].id, "wf-3");

  // Filter no matches
  const emptyRes = filterWorkflows(SAMPLE_WORKFLOWS, "nonexistent", "all");
  assert.equal(emptyRes.length, 0);
});

