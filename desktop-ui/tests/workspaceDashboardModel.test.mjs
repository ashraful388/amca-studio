// Z1 Workspace Dashboard — unit tests for the pure presentation model
// (no browser, no fetch, no backend). Run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  activeRuns,
  deriveCurrentTask,
  deriveAgentActivity,
  deriveDashboardMetrics,
  recentArtifacts,
} from "../src/workspaceDashboardModel.ts";

const RUN = (over = {}) => ({
  run_id: "r1", project_id: "p1", goal: "g", status: "queued",
  created_at: "2026-09-01T10:00:00Z", started_at: null, finished_at: null,
  result: null, error: null, log: "", provider: "ollama", model: "qwen3:1.7b",
  mode: "adaptive_hybrid", privacy: "local", conversation_id: null,
  ...over,
});

test("activeRuns keeps only live registry states", () => {
  const runs = [
    RUN({ run_id: "a", status: "running" }),
    RUN({ run_id: "b", status: "queued" }),
    RUN({ run_id: "c", status: "cancelling" }),
    RUN({ run_id: "d", status: "succeeded" }),
    RUN({ run_id: "e", status: "failed" }),
    RUN({ run_id: "f", status: "cancelled" }),
  ];
  const ids = activeRuns(runs).map((run) => run.run_id).sort();
  assert.deepEqual(ids, ["a", "b", "c"]);
});

test("deriveCurrentTask prefers live run, then recent, else none", () => {
  const recent = RUN({ run_id: "old", status: "succeeded", created_at: "2026-08-30T00:00:00Z" });
  const olderLive = RUN({ run_id: "live2", status: "running", created_at: "2026-08-31T00:00:00Z" });
  const live = RUN({ run_id: "live", status: "running", created_at: "2026-09-01T00:00:00Z" });

  const first = deriveCurrentTask([recent, olderLive, live]);
  assert.equal(first.run?.run_id, "live");
  assert.equal(first.source, "active_run");

  const second = deriveCurrentTask([recent, olderLive]);
  assert.equal(second.run?.run_id, "live2");
  assert.equal(second.source, "active_run");

  const third = deriveCurrentTask([recent]);
  assert.equal(third.run?.run_id, "old");
  assert.equal(third.source, "recent_run");

  const none = deriveCurrentTask([]);
  assert.equal(none.run, null);
  assert.equal(none.source, "none");
});

test("deriveAgentActivity counts nano live/completed states and live runs", () => {
  const nano = {
    assignments: [
      { nano_id: "n1", task_id: "t1", purpose: "p", depth: 1, expected_cost: null },
      { nano_id: "n2", task_id: "t2", purpose: "p", depth: 1, expected_cost: 0 },
      { nano_id: "n3", task_id: "t3", purpose: "p", depth: 1, expected_cost: 0.01 },
    ],
    statuses: { n1: "running", n2: "completed", n3: "cancelled" },
  };
  const counts = { core: 41, built_in_specialists: 250, custom: 2, total_available: 293, active: 4 };
  const summary = deriveAgentActivity(nano, counts, [RUN({ status: "running" }), RUN({ status: "failed", run_id: "r2" })]);
  assert.equal(summary.nanoLive, 1);
  assert.equal(summary.nanoCompleted, 1);
  assert.equal(summary.fabricActive, 4);
  assert.equal(summary.liveRuns, 1);
});

test("deriveDashboardMetrics sums nano live + fabric active for the agents card", () => {
  const metrics = deriveDashboardMetrics({
    studioProjects: 3,
    runs: [RUN({ status: "running" })],
    activity: { fabricActive: 2, nanoTotal: 5, nanoLive: 3, nanoCompleted: 1, liveRuns: 1 },
    artifactCount: 7,
  });
  assert.deepEqual(metrics, {
    studioProjects: 3,
    liveRuns: 1,
    totalRuns: 1,
    activeAgents: 5,
    artifactCount: 7,
  });
});

test("recentArtifacts returns newest-first tail without inventing timestamps", () => {
  const artifacts = [
    { artifact_id: "a1", project_id: "p", artifact_type: "markdown", title: "One", created_by: "u", current_version: 1 },
    { artifact_id: "a2", project_id: "p", artifact_type: "html", title: "Two", created_by: "u", current_version: 2 },
    { artifact_id: "a3", project_id: "p", artifact_type: "pdf", title: "Three", created_by: "u", current_version: 1 },
  ];
  const recent = recentArtifacts(artifacts, 2);
  assert.deepEqual(recent.map((item) => item.artifact_id), ["a3", "a2"]);
  assert.equal(recent[0].artifact_type, "pdf");
});
