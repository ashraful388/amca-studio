import { test } from "node:test";
import assert from "node:assert/strict";
import {
  projectLifecycle,
  projectHealth,
  completionEvidence,
} from "../src/projectLifecycleModel.ts";

const PROJECT = (overrides = {}) => ({
  project_id: "p1", name: "Lemma Research", description: "", instructions: "", template: "research",
  created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-02T00:00:00Z", archived: false, chat_count: 0, ...overrides,
});

const RUN = (overrides = {}) => ({
  run_id: "r1", project_id: "p1", goal: "g", status: "succeeded", created_at: "2026-09-01T10:00:00Z",
  started_at: "2026-09-01T10:00:05Z", finished_at: "2026-09-01T11:00:00Z", result: {}, error: null, log: "",
  provider: "ollama", model: "m", mode: "solo", privacy: "offline", conversation_id: null,
  collaboration_rounds: 1, collaboration_decision: null, ...overrides,
});

const ARTIFACT = (overrides = {}) => ({
  artifact_id: "a1", project_id: "p1", artifact_type: "markdown", title: "Proof",
  created_by: "model:m", current_version: 1, ...overrides,
});

const COMPLETE = { artifactsComplete: true };

test("fresh project: only creation milestone done, health not started", () => {
  const lifecycle = projectLifecycle({ project: PROJECT(), runs: [], artifacts: [], ...COMPLETE });
  assert.equal(lifecycle.stages[0].status, "done");
  assert.ok(lifecycle.stages.slice(1).every((stage) => stage.status === "pending"));
  assert.equal(lifecycle.completed, 1);
  const health = projectHealth({ ...lifecycle }, []);
  assert.equal(health.state, "not_started");
  assert.match(health.reason, /No lifecycle records/);
});
test("full journey: chat, run, completion, artifact → 100% and completed", () => {
  const lifecycle = projectLifecycle({
    project: PROJECT({ chat_count: 2 }),
    runs: [RUN({ run_id: "r1" })],
    artifacts: [ARTIFACT()],
    ...COMPLETE,
  });
  assert.equal(lifecycle.completed, 5);
  assert.equal(lifecycle.total, 5);
  assert.equal(lifecycle.percent, 100);
  assert.equal(projectHealth({ ...lifecycle }, []).state, "completed");
});

test("partial journey: 60% empirical progress, health active", () => {
  const lifecycle = projectLifecycle({
    project: PROJECT({ chat_count: 1 }),
    runs: [RUN({ run_id: "r1", status: "running", finished_at: null })],
    artifacts: [],
    ...COMPLETE,
  });
  assert.equal(lifecycle.completed, 3);
  assert.equal(lifecycle.percent, 60);
  assert.equal(projectHealth({ ...lifecycle }, []).state, "active");
});

test("workflow milestone cites the succeeded run with its timestamp", () => {
  const lifecycle = projectLifecycle({
    project: PROJECT({ chat_count: 1 }),
    runs: [RUN({ run_id: "abc123def456" })],
    artifacts: [ARTIFACT()],
    ...COMPLETE,
  });
  const stage = lifecycle.stages.find((item) => item.key === "workflow_completed");
  assert.match(stage?.evidence?.detail ?? "", /run abc123def456 finished succeeded/);
  assert.equal(stage?.evidence?.at, "2026-09-01T11:00:00Z");
});

test("latest failed run makes health blocked with the recorded error", () => {
  const runs = [
    RUN({ run_id: "old-success", status: "succeeded", created_at: "2026-09-01T10:00:00Z" }),
    RUN({ run_id: "new-fail", status: "failed", created_at: "2026-09-02T10:00:00Z", error: "provider timeout" }),
  ];
  const lifecycle = projectLifecycle({
    project: PROJECT({ chat_count: 1 }),
    runs,
    artifacts: [ARTIFACT()],
    ...COMPLETE,
  });
  // All milestones are empirically complete (the earlier run succeeded);
  // the later failure still surfaces as blocked.
  assert.equal(lifecycle.completed, 5);
  const health = projectHealth({ ...lifecycle }, runs);
  assert.equal(health.state, "blocked");
  assert.match(health.reason, /new-fail/);
  assert.match(health.reason, /provider timeout/);
});

test("disclosure states the empirical-only scope", () => {
  const lifecycle = projectLifecycle({ project: PROJECT(), runs: [], artifacts: [], ...COMPLETE });
  assert.match(lifecycle.disclosure, /no dedicated lifecycle\/milestone backend/);
  assert.match(lifecycle.disclosure, /evidence-backed counts only/);
});

test("percentage is integer-rounded and never exceeds 100", () => {
  const lifecycle = projectLifecycle({
    project: PROJECT({ chat_count: 1 }),
    runs: [RUN({ run_id: "r1" })],
    artifacts: [ARTIFACT()],
    ...COMPLETE,
  });
  assert.ok(Number.isInteger(lifecycle.percent));
  assert.ok(lifecycle.percent >= 0 && lifecycle.percent <= 100);
});

// ---------- Evidence panel ----------

test("completion evidence lists recorded types and omits unrecorded ones", () => {
  const evidence = completionEvidence({
    project: PROJECT({ chat_count: 1 }),
    runs: [RUN({ run_id: "r1", status: "succeeded" })],
    artifacts: [ARTIFACT()],
    ...COMPLETE,
  });
  const byLabel = new Map(evidence.map((line) => [line.label, line]));
  assert.equal(byLabel.get("Workflow completed")?.recorded, true);
  assert.equal(byLabel.get("Artifact created")?.recorded, true);
  assert.equal(byLabel.get("Validation passed")?.recorded, true);
  assert.equal(byLabel.get("User confirmation recorded")?.recorded, false);
  assert.equal(byLabel.get("User confirmation recorded")?.detail, null);
});

test("partial run completes workflow but not the validation line", () => {
  const evidence = completionEvidence({
    project: PROJECT({ chat_count: 1 }),
    runs: [RUN({ run_id: "r1", status: "partial" })],
    artifacts: [],
    ...COMPLETE,
  });
  const byLabel = new Map(evidence.map((line) => [line.label, line]));
  assert.equal(byLabel.get("Workflow completed")?.recorded, true);
  assert.equal(byLabel.get("Validation passed")?.recorded, false);
});

test("unloaded artifact list is disclosed, never guessed", () => {
  const evidence = completionEvidence({
    project: PROJECT({ chat_count: 1 }),
    runs: [RUN({ run_id: "r1" })],
    artifacts: [],
    artifactsComplete: false,
  });
  const artifactLine = evidence.find((line) => line.label === "Artifact created");
  assert.equal(artifactLine?.recorded, false);
  assert.match(artifactLine?.detail ?? "", /not loaded/);
});

test("milestones are scoped to the project's own runs", () => {
  const lifecycle = projectLifecycle({
    project: PROJECT({ project_id: "p1", chat_count: 1 }),
    runs: [RUN({ run_id: "r1", project_id: "p2", status: "succeeded" })],
    artifacts: [],
    ...COMPLETE,
  });
  const firstRun = lifecycle.stages.find((stage) => stage.key === "first_run");
  assert.equal(firstRun?.status, "pending");
});
