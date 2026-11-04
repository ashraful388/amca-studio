// Z2 groupAgentsByTask — unit tests for the task-centric read-only
// grouping over the run + nano-agent registries (pure model, no browser,
// no backend). Run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import { groupAgentsByTask } from "../src/composerModel.ts";

const RUN = (over = {}) => ({
  run_id: "r1",
  goal: "Research the topic",
  status: "running",
  ...over,
});

const NANO = (over = {}) => ({
  nano_id: "n1",
  task_id: "t1",
  parent_task_id: "t0",
  run_ref: "r1",
  purpose: "gather sources",
  task_type: "research",
  depth: 1,
  ...over,
});

test("each run becomes a task group with itself as coordinator", () => {
  const groups = groupAgentsByTask([RUN()], { assignments: [], statuses: {} });
  assert.equal(groups.length, 1);
  assert.equal(groups[0].taskKey, "r1");
  assert.equal(groups[0].goal, "Research the topic");
  assert.equal(groups[0].runId, "r1");
  assert.equal(groups[0].runState, "running");
  assert.equal(groups[0].agents.length, 1);
  assert.equal(groups[0].agents[0].role, "Coordinator (run)");
  assert.equal(groups[0].agents[0].kind, "run");
  assert.equal(groups[0].agents[0].state, "running");
});

test("nano assignments attach to their run via run_ref as workers", () => {
  const groups = groupAgentsByTask(
    [RUN()],
    { assignments: [NANO({ nano_id: "n1" }), NANO({ nano_id: "n2", task_type: "coding" })], statuses: { n1: "running", n2: "completed" } },
  );
  assert.equal(groups.length, 1);
  const workers = groups[0].agents.filter((agent) => agent.kind === "nano");
  assert.equal(workers.length, 2);
  assert.equal(workers[0].role, "Worker · research");
  assert.equal(workers[0].state, "running");
  assert.equal(workers[1].role, "Worker · coding");
  assert.equal(workers[1].state, "completed");
});

test("worker role falls back to purpose when task_type is blank", () => {
  const groups = groupAgentsByTask(
    [RUN()],
    { assignments: [NANO({ task_type: "", purpose: "summarize pages" })], statuses: {} },
  );
  assert.equal(groups[0].agents[1].role, "Worker · summarize pages");
});

test("worker role falls back to the generic label when both type and purpose are blank", () => {
  const groups = groupAgentsByTask(
    [RUN()],
    { assignments: [NANO({ task_type: "", purpose: "" })], statuses: {} },
  );
  assert.equal(groups[0].agents[1].role, "Worker · task");
});

test("assignments without a matching run stay visible in the background group", () => {
  const groups = groupAgentsByTask(
    [RUN({ run_id: "r1" })],
    { assignments: [NANO({ nano_id: "attached", run_ref: "r1" }), NANO({ nano_id: "orphan", run_ref: "r-gone" })], statuses: { orphan: "running" } },
  );
  assert.equal(groups.length, 2);
  const background = groups.find((group) => group.taskKey === "background");
  assert.ok(background, "background group exists");
  assert.equal(background.runId, null);
  assert.equal(background.runState, null);
  assert.deepEqual(background.agents.map((agent) => agent.id), ["orphan"]);
  assert.equal(background.agents[0].state, "running");
  assert.equal(background.agents[0].kind, "nano");
});

test("background group appears alone when there are no runs at all", () => {
  const groups = groupAgentsByTask([], { assignments: [NANO()], statuses: {} });
  assert.equal(groups.length, 1);
  assert.equal(groups[0].taskKey, "background");
});

test("no runs and no assignments yields no groups", () => {
  assert.deepEqual(groupAgentsByTask([], { assignments: [], statuses: {} }), []);
});

test("limit caps the number of run groups (most recent first by input order)", () => {
  const runs = ["a", "b", "c", "d"].map((id) => RUN({ run_id: id }));
  const groups = groupAgentsByTask(runs, { assignments: [], statuses: {} }, 2);
  assert.deepEqual(groups.map((group) => group.taskKey), ["a", "b"]);
});

test("nano statuses missing from the registry read as unknown, not invented states", () => {
  const groups = groupAgentsByTask(
    [RUN()],
    { assignments: [NANO({ nano_id: "n1" })], statuses: {} },
  );
  assert.equal(groups[0].agents[1].state, "unknown");
});

test("worker with multiple parents only attaches to the matching run_ref", () => {
  const groups = groupAgentsByTask(
    [RUN({ run_id: "r1" }), RUN({ run_id: "r2", goal: "Other task", status: "queued" })],
    { assignments: [NANO({ nano_id: "n1", run_ref: "r2" })], statuses: { n1: "queued" } },
  );
  assert.equal(groups.length, 2);
  assert.equal(groups[0].agents.length, 1, "r1 has only its coordinator");
  assert.equal(groups[1].agents.length, 2, "r2 has coordinator + its worker");
  assert.equal(groups[1].agents[1].id, "n1");
});

test("groups follow the input run order", () => {
  const groups = groupAgentsByTask(
    [RUN({ run_id: "later" }), RUN({ run_id: "earlier" })],
    { assignments: [], statuses: {} },
  );
  assert.deepEqual(groups.map((group) => group.taskKey), ["later", "earlier"]);
});
