import { test } from "node:test";
import assert from "node:assert/strict";
import {
  operationalCards,
  buildOperationalReport,
  reportToText,
  resourceVisibility,
  operationalTimeline,
} from "../src/operationalIntelligenceModel.ts";
function makeRun(overrides) {
  return {
    run_id: "r", project_id: "p", goal: "g", status: "succeeded", created_at: "2026-09-02T00:00:00Z",
    started_at: null, finished_at: null, result: {}, error: null, log: "", provider: "ollama",
    model: "m", mode: "solo", privacy: "offline", conversation_id: null,
    collaboration_rounds: 1, collaboration_decision: null, ...overrides,
  };
}

const PROJECT = (overrides = {}) => ({
  project_id: "p1", name: "P", description: "", instructions: "", template: "general",
  created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-02T00:00:00Z", archived: false, chat_count: 0, ...overrides,
});

const AUTOMATION = (overrides = {}) => ({
  id: "a1", name: "A", automation_type: "interval", schedule: "*/5 * * * *", enabled: true,
  created_at: "2026-09-01T00:00:00Z", next_scheduled_check_at: null, last_result: null, description: "", ...overrides,
});

const OCCURRENCE = (overrides) => ({
  id: "o1", automation_id: "a1", scheduled_for: "2026-09-02T01:00:00Z", status: "succeeded",
  claimed_at: "2026-09-02T01:00:05Z", ...overrides,
});

const ASSET = (overrides) => ({
  asset_id: "as1", project_id: "p1", conversation_id: null, version: 1, media_type: "text/markdown",
  filename: "out.md", size_bytes: 10, sha256: "x".repeat(64), source: "model", modality: "document",
  extraction_status: "ready", extraction_method: "none", extraction_note: "", created_at: "2026-09-02T00:00:00Z",
  updated_at: "2026-09-02T00:00:00Z", chunks: [], ...overrides,
});

const EVENT = (overrides) => ({
  id: "e1", event_type: "approval_granted", source: "governor", payload: {}, state_version: 1,
  severity: "info", created_at: "2026-09-02T01:30:00Z", ...overrides,
});

const BASE_EVIDENCE = {
  projects: [PROJECT()],
  runs: [makeRun({ run_id: "r1" }), makeRun({ run_id: "r2", status: "failed" })],
  automations: [AUTOMATION()],
  occurrences: [OCCURRENCE({ status: "succeeded" })],
  schedulerRunning: true,
  artifacts: [ASSET({})],
};

// ---------- Cards ----------

test("cards count active projects excluding archived ones", () => {
  const cards = operationalCards({ ...BASE_EVIDENCE, projects: [PROJECT(), PROJECT({ project_id: "p2", archived: true })] });
  const card = cards.find((item) => item.key === "active_projects");
  assert.equal(card?.value, 1);
  assert.match(card?.detail ?? "", /1 archived/);
});

test("cards split completed/failed/cancelled workflows", () => {
  const cards = operationalCards({
    ...BASE_EVIDENCE,
    runs: [
      makeRun({ run_id: "a", status: "succeeded" }),
      makeRun({ run_id: "b", status: "partial" }),
      makeRun({ run_id: "c", status: "failed" }),
      makeRun({ run_id: "d", status: "cancelled" }),
      makeRun({ run_id: "e", status: "running" }),
    ],
  });
  const workflow = cards.find((item) => item.key === "completed_workflows");
  assert.equal(workflow?.value, 2);
  assert.match(workflow?.detail ?? "", /1 failed · 1 cancelled/);
});

test("automation card counts running+succeeded occurrences and definitions", () => {
  const cards = operationalCards({
    ...BASE_EVIDENCE,
    automations: [AUTOMATION(), AUTOMATION({ id: "a2", enabled: false })],
    occurrences: [OCCURRENCE({ id: "o1", status: "succeeded" }), OCCURRENCE({ id: "o2", status: "failed" }), OCCURRENCE({ id: "o3", status: "running" })],
  });
  const card = cards.find((item) => item.key === "automation_activity");
  assert.equal(card?.value, 2);
  assert.match(card?.detail ?? "", /2 defined · 1 enabled/);
});

test("memory card stays honestly not recorded", () => {
  const card = operationalCards(BASE_EVIDENCE).find((item) => item.key === "memory_activity");
  assert.equal(card?.value, null);
  assert.match(card?.detail ?? "", /Not recorded/);
  assert.equal(card?.source, "none available");
});

test("scheduler card reflects the real scheduler state", () => {
  assert.equal(operationalCards(BASE_EVIDENCE).find((item) => item.key === "scheduler")?.detail, "Running");
  assert.equal(operationalCards({ ...BASE_EVIDENCE, schedulerRunning: null }).find((item) => item.key === "scheduler")?.detail, "Unknown");
});

test("every card names its evidence source", () => {
  for (const card of operationalCards(BASE_EVIDENCE)) {
    assert.ok(card.source.length > 0, card.key);
    assert.ok(card.label.length > 0, card.key);
  }
});

// ---------- Report ----------

test("operational report renders recorded and not-recorded metrics with sources", () => {
  const report = buildOperationalReport(BASE_EVIDENCE, "2026-09-02T09:00:00Z");
  assert.equal(report.title, "AMCA Studio Operational Report");
  assert.equal(report.generatedAt, "2026-09-02T09:00:00Z");
  const memory = report.metrics.find((metric) => metric.label === "Memory activity");
  assert.equal(memory?.value, "not recorded");
  assert.equal(memory?.recorded, false);
  assert.match(report.privacyNote, /No analytics tracking/);
});

test("report serialization is a stable local document", () => {
  const report = buildOperationalReport(BASE_EVIDENCE, "2026-09-02T09:00:00Z");
  assert.match(reportToText(report), /"privacyNote"/);
  assert.match(reportToText(report), /AMCA Studio Operational Report/);
});

// ---------- Resource visibility ----------

const COST_STATE = {
  total_known_cost_usd: 0.05,
  total_unknown_calls: 1,
  total_calls: 5,
  total_local_calls: 4,
  per_model_cost_usd: { "gpt-4o-mini": 0.05 },
  known: true,
};

test("resource visibility computes the local ratio and recorded cost", () => {
  const visibility = resourceVisibility(COST_STATE, [makeRun({ run_id: "r" })]);
  assert.equal(visibility.localModelRatio, "4/5 recorded calls ran locally");
  assert.equal(visibility.recordedCostUsd, 0.05);
  assert.equal(visibility.unknownCalls, 1);
  assert.match(visibility.line, /recorded cost \$0\.0500/);
  assert.match(visibility.line, /1 call\(s\) without recorded pricing/);
  assert.deepEqual(visibility.perModel, [{ model: "gpt-4o-mini", costUsd: 0.05 }]);
});

test("unknown cost state shows no estimates", () => {
  const visibility = resourceVisibility(null, [makeRun({ run_id: "r" })]);
  assert.equal(visibility.recordedCostUsd, null);
  assert.match(visibility.line, /not recorded/);
  assert.match(visibility.line, /no estimates/);
});

test("cost state flagged not-known degrades the same way", () => {
  const visibility = resourceVisibility({ ...COST_STATE, known: false }, []);
  assert.equal(visibility.recordedCostUsd, null);
  assert.match(visibility.line, /No runs yet|not recorded/);
});

test("workflow execution count comes from the run registry", () => {
  assert.equal(resourceVisibility(COST_STATE, [makeRun({ run_id: "1" }), makeRun({ run_id: "2" })]).workflowExecutionCount, 2);
});

// ---------- Timeline ----------

test("timeline merges runs, occurrences, and events newest-first", () => {
  const entries = operationalTimeline(
    [makeRun({ run_id: "r1", goal: "Long goal ".repeat(20), created_at: "2026-09-02T02:00:00Z", finished_at: "2026-09-02T02:05:00Z" })],
    [OCCURRENCE({ id: "o1", status: "succeeded", completed_at: "2026-09-02T03:00:00Z" })],
    [EVENT({ id: "e1", created_at: "2026-09-02T04:00:00Z" })],
  );
  assert.deepEqual(entries.map((entry) => entry.kind), ["governance", "automation", "workflow"]);
  assert.match(entries[2].label, /Long goal/);
  assert.ok(entries[2].label.length <= 75);
  assert.equal(entries[2].state, "completed");
});

test("timeline respects the limit and drops undated entries", () => {
  const runs = Array.from({ length: 30 }, (_, index) => makeRun({ run_id: `r${index}`, created_at: `2026-09-0${(index % 2) + 1}T00:00:0${index % 10}Z` }));
  const entries = operationalTimeline(runs, [], [EVENT({ id: "e1", created_at: "" })], 10);
  assert.equal(entries.length, 10);
});

test("timeline kind labels map run states honestly", () => {
  const entries = operationalTimeline(
    [makeRun({ run_id: "x", status: "partial", created_at: "2026-09-02T01:00:00Z" })],
    [], [],
  );
  assert.equal(entries[0].state, "completed (partial)");
});

test("governance events show type and source without payload details", () => {
  const entries = operationalTimeline([], [], [EVENT({ id: "e9", event_type: "permission_requested", source: "governor", created_at: "2026-09-02T05:00:00Z", payload: { secret: "value" } })]);
  assert.equal(entries[0].label, "permission requested · governor");
  assert.ok(!JSON.stringify(entries).includes("secret"));
});
