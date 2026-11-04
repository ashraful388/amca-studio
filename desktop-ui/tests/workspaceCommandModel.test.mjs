import test from "node:test";
import assert from "node:assert/strict";
import {
  commandCenterCards,
  unifiedTimeline,
  crossModuleTrace,
  workspaceHealth,
  COMMAND_CENTER_EMPTY_STATES,
} from "../src/workspaceCommandModel.ts";

function run(overrides = {}) {
  return {
    run_id: "run-12345678",
    project_id: "proj-11111111",
    goal: "Ship the release notes",
    status: "succeeded",
    created_at: "2026-09-02T10:00:00Z",
    started_at: null,
    finished_at: null,
    result: null,
    error: null,
    log: "",
    provider: "ollama",
    model: "qwen3",
    mode: "solo",
    privacy: "local",
    conversation_id: "conv-22222222",
    ...overrides,
  };
}

const BASE_INPUT = {
  studioProjects: 2,
  runs: [run()],
  agentParticipants: 3,
  availableModels: 4,
  artifacts: [
    { artifact_id: "art-33333333", project_id: "proj-11111111", artifact_type: "report", title: "Release notes", created_by: "run", current_version: 2 },
  ],
  automations: { total: 3, enabled: 2 },
  approvals: [
    {
      id: "appr-44444444", project_id: "proj-11111111", action: "file.write", scope: {}, scope_sha256: "x",
      risk: "low", reason: "needed", requested_by: "agent", status: "approved",
      created_at: "2026-09-02T11:00:00Z", expires_at: "2026-09-03T11:00:00Z",
      decided_at: null, decided_by: null, decision_reason: null,
    },
  ],
};

test("command center exposes all eight overview cards in order", () => {
  const cards = commandCenterCards(BASE_INPUT);
  assert.deepEqual(
    cards.map((card) => card.id),
    ["projects", "active_runs", "agents", "models", "artifacts", "memory", "automations", "governance"],
  );
});

test("each card carries a value and an evidence source", () => {
  for (const card of commandCenterCards(BASE_INPUT)) {
    assert.ok(card.evidenceSource.length > 0, `${card.id} missing evidence source`);
    assert.ok(card.displayValue.length > 0, `${card.id} missing display value`);
  }
});

test("memory card reports not recorded with the endpoint reason", () => {
  const memory = commandCenterCards(BASE_INPUT).find((card) => card.id === "memory");
  assert.equal(memory.value, null);
  assert.equal(memory.displayValue, "Not recorded");
  assert.match(memory.reason, /No memory activity summary endpoint available/);
  assert.equal(memory.navView, null);
});

test("active runs count only queued/running/cancelling states", () => {
  const cards = commandCenterCards({
    ...BASE_INPUT,
    runs: [run({ run_id: "a", status: "succeeded" }), run({ run_id: "b", status: "running" }), run({ run_id: "c", status: "queued" }), run({ run_id: "d", status: "failed" })],
  });
  const active = cards.find((card) => card.id === "active_runs");
  assert.equal(active.value, 2);
});

test("agents card reports participants and degrades to not recorded without runs", () => {
  const agents = commandCenterCards(BASE_INPUT).find((card) => card.id === "agents");
  assert.equal(agents.value, 3);
  const noRuns = commandCenterCards({ ...BASE_INPUT, runs: [], agentParticipants: null }).find((card) => card.id === "agents");
  assert.equal(noRuns.value, null);
  assert.equal(noRuns.displayValue, "Not recorded");
  assert.match(noRuns.reason, /No runs recorded yet/);
});

test("governance card counts approval requests and discloses scope", () => {
  const governance = commandCenterCards(BASE_INPUT).find((card) => card.id === "governance");
  assert.equal(governance.value, 1);
  assert.match(governance.evidenceSource, /project-scoped/);
});

test("no card invents a value without a record (models zero shows unavailable wording)", () => {
  const cards = commandCenterCards({ ...BASE_INPUT, availableModels: 0 });
  const models = cards.find((card) => card.id === "models");
  assert.equal(models.value, 0);
});

test("unified timeline merges all four sources newest first", () => {
  const entries = unifiedTimeline({
    runs: [run({ created_at: "2026-09-02T10:00:00Z" })],
    artifacts: BASE_INPUT.artifacts,
    approvals: BASE_INPUT.approvals,
    automations: [{ id: "auto-1", name: "Nightly digest", last_run_at: "2026-09-02T09:00:00Z" }],
  });
  assert.equal(entries.length, 4);
  const kinds = entries.map((entry) => entry.kind);
  for (const kind of ["run", "artifact", "governance", "automation"]) {
    assert.ok(kinds.includes(kind), `missing ${kind}`);
  }
  assert.equal(entries[0].kind, "governance"); // 11:00 latest
  const dated = entries.filter((entry) => entry.createdAt !== null);
  assert.equal(dated[dated.length - 1].createdAt, "2026-09-02T09:00:00Z"); // automation, oldest dated
  const undated = entries.filter((entry) => entry.createdAt === null);
  assert.equal(undated.length, 1); // artifact kept, never given an invented timestamp
});

test("timeline entries never include payload contents or execution metadata", () => {
  const entries = unifiedTimeline({
    runs: [run({ result: { calls: [{ summary: "SECRET-PAYLOAD" }], task_cost: { usd: 9 } } })],
    artifacts: [],
    approvals: [],
    automations: [],
  });
  const text = JSON.stringify(entries);
  assert.ok(!text.includes("SECRET-PAYLOAD"));
  assert.ok(!text.includes("task_cost"));
  assert.match(entries[0].detail, /Run registry · succeeded · solo/);
});

test("timeline keeps undated artifact records instead of dropping them", () => {
  const entries = unifiedTimeline({
    runs: [],
    artifacts: BASE_INPUT.artifacts,
    approvals: [],
    automations: [],
  });
  assert.equal(entries.length, 1);
  assert.equal(entries[0].createdAt, null);
  assert.match(entries[0].detail, /Artifact registry/);
});

test("cross-module trace renders the full chain when every id exists", () => {
  const steps = crossModuleTrace({
    project: { project_id: "proj-11111111", name: "Alpha" },
    run: run(),
    artifact: BASE_INPUT.artifacts[0],
    agentParticipants: [{ ref: "run", provider: "ollama", model: "qwen3", calls: 3 }],
  });
  assert.deepEqual(steps.map((step) => step.stage), ["Project", "Workflow run", "Agent activity", "Artifact", "Knowledge / memory"]);
  const recorded = steps.filter((step) => step.state === "recorded");
  assert.equal(recorded.length, 4);
  for (const step of recorded) assert.ok(step.targetView, `${step.stage} missing nav link`);
  assert.match(steps[1].detail, /conv-2/);
});

test("cross-module trace degrades missing references to not recorded", () => {
  const steps = crossModuleTrace({
    project: null,
    run: null,
    artifact: null,
    agentParticipants: [],
  });
  for (const step of steps) {
    assert.equal(step.state, "not_recorded");
    assert.equal(step.targetView, null);
    assert.ok(step.detail.length > 0);
  }
  assert.match(steps[4].detail, /No memory activity summary endpoint available/);
});

test("trace never infers agent participation beyond recorded calls", () => {
  const steps = crossModuleTrace({
    project: { project_id: "p", name: "Alpha" },
    run: run({ result: null }),
    artifact: null,
    agentParticipants: [],
  });
  const agents = steps.find((step) => step.stage === "Agent activity");
  assert.equal(agents.state, "not_recorded");
});

test("workspace health summary uses evidence counts only", () => {
  const health = workspaceHealth({
    ...BASE_INPUT,
    runs: [run({ status: "succeeded" }), run({ run_id: "b", status: "running" }), run({ run_id: "c", status: "succeeded" })],
  });
  assert.equal(health.activeProjects, 2);
  assert.equal(health.runningTasks, 1);
  assert.equal(health.completedWorkflows, 2);
  assert.equal(health.availableModels, 4);
  assert.equal(health.artifactsGenerated, 1);
  assert.match(health.line, /2 active projects · 1 running · 2 completed workflows/);
});

test("health summary carries no score fields", () => {
  const health = workspaceHealth(BASE_INPUT);
  const keys = Object.keys(health);
  for (const forbidden of ["score", "productivity", "efficiency", "ranking", "quality"]) {
    assert.ok(!keys.some((key) => key.toLowerCase().includes(forbidden)), `forbidden key ${forbidden}`);
  }
});

test("mandated empty state wording is exposed verbatim", () => {
  assert.equal(COMMAND_CENTER_EMPTY_STATES.workspace, "No workspace activity yet. Create a project to begin.");
  assert.equal(COMMAND_CENTER_EMPTY_STATES.models, "No models configured. Connect a provider or enable local models.");
  assert.equal(COMMAND_CENTER_EMPTY_STATES.artifacts, "Artifacts appear after completed tasks.");
});
