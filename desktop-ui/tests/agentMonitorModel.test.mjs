// Z1 Agent Control Panel — unit tests for the pure presentation model
// (no browser, no fetch, no backend). Run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  runMonitorState,
  nanoMonitorState,
  canCancel,
  buildMonitorRows,
  buildNanoTree,
  countStates,
} from "../src/agentMonitorModel.ts";

const RUN = (over = {}) => ({
  run_id: "r1", project_id: "p1", goal: "Analyze dataset", status: "queued",
  created_at: "2026-09-01T10:00:00Z", started_at: null, finished_at: null,
  result: null, error: null, log: "", provider: "ollama", model: "qwen3:1.7b",
  mode: "adaptive_hybrid", privacy: "local", conversation_id: null,
  ...over,
});

test("run statuses map onto the six monitor states", () => {
  assert.equal(runMonitorState("queued"), "queued");
  assert.equal(runMonitorState("running"), "running");
  assert.equal(runMonitorState("cancelling"), "running");
  assert.equal(runMonitorState("succeeded"), "completed");
  assert.equal(runMonitorState("partial"), "completed");
  assert.equal(runMonitorState("failed"), "failed");
  assert.equal(runMonitorState("cancelled"), "cancelled");
  assert.equal(runMonitorState("mystery"), "created");
});

test("nano statuses map onto the six monitor states", () => {
  assert.equal(nanoMonitorState("proposed"), "created");
  assert.equal(nanoMonitorState("authorized"), "created");
  assert.equal(nanoMonitorState("queued"), "queued");
  assert.equal(nanoMonitorState("running"), "running");
  assert.equal(nanoMonitorState("waiting"), "running");
  assert.equal(nanoMonitorState("verifying"), "running");
  assert.equal(nanoMonitorState("completed"), "completed");
  assert.equal(nanoMonitorState("retired"), "completed");
  assert.equal(nanoMonitorState("failed"), "failed");
  assert.equal(nanoMonitorState("cancelled"), "cancelled");
  assert.equal(nanoMonitorState(undefined), "created");
});

test("cancel is offered only for queued/running states", () => {
  assert.ok(canCancel("queued"));
  assert.ok(canCancel("running"));
  assert.ok(!canCancel("created"));
  assert.ok(!canCancel("completed"));
  assert.ok(!canCancel("failed"));
  assert.ok(!canCancel("cancelled"));
});

test("buildMonitorRows maps runs and nano assignments with safe model labels", () => {
  const runs = [RUN({ run_id: "r1", status: "running" }), RUN({ run_id: "r2", status: "failed", provider: "", model: "" })];
  const nano = {
    assignments: [
      { nano_id: "n1", task_id: "t1", parent_task_id: "m0", purpose: "Research", task_type: "search", depth: 1, expected_cost: null },
    ],
    statuses: { n1: "running" },
  };
  const rows = buildMonitorRows(runs, nano);
  assert.equal(rows.length, 3);
  const runRow = rows[0];
  assert.equal(runRow.kind, "run");
  assert.equal(runRow.state, "running");
  assert.equal(runRow.model, "ollama / qwen3:1.7b");
  assert.equal(runRow.runId, "r1");
  const failedRow = rows[1];
  assert.equal(failedRow.model, "unassigned");
  const nanoRow = rows[2];
  assert.equal(nanoRow.kind, "nano");
  assert.equal(nanoRow.state, "running");
  assert.equal(nanoRow.model, "router-selected");
  assert.equal(nanoRow.runId, null);
  const counts = countStates(rows);
  assert.equal(counts.running, 2);
  assert.equal(counts.failed, 1);
});

test("buildNanoTree nests children under parent by task linkage and sorts roots", () => {
  const nano = {
    assignments: [
      { nano_id: "b", task_id: "t2", parent_task_id: "t1", purpose: "Beta", task_type: "w", depth: 1, expected_cost: null },
      { nano_id: "a", task_id: "t1", parent_task_id: "root", purpose: "Alpha", task_type: "w", depth: 0, expected_cost: null },
      { nano_id: "c", task_id: "t3", parent_task_id: "t1", purpose: "Gamma", task_type: "w", depth: 1, expected_cost: null },
    ],
    statuses: { a: "running", b: "queued", c: "completed" },
  };
  const tree = buildNanoTree(nano);
  assert.equal(tree.length, 1);
  assert.equal(tree[0].nanoId, "a");
  assert.deepEqual(tree[0].children.map((child) => child.nanoId), ["b", "c"]);
  assert.equal(tree[0].children[0].state, "queued");
});

test("buildNanoTree degrades malformed parent chains to visible roots", () => {
  const nano = {
    assignments: [
      { nano_id: "x", task_id: "t9", parent_task_id: "missing-parent", purpose: "Orphan", task_type: "w", depth: 1, expected_cost: null },
    ],
    statuses: {},
  };
  const tree = buildNanoTree(nano);
  assert.equal(tree.length, 1);
  assert.equal(tree[0].nanoId, "x");
});
