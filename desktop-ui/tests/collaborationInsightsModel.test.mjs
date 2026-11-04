import { test } from "node:test";
import assert from "node:assert/strict";
import {
  architectureMode,
  architectureLabel,
  participantsFromRun,
  collaborationTimeline,
  decisionTrace,
  recordedTraceCount,
  costInsight,
  insightsAvailable,
} from "../src/collaborationInsightsModel.ts";

const BASE_RUN = {
  run_id: "run-1",
  project_id: "p1",
  goal: "Prove the boundary lemma and verify it.",
  status: "succeeded",
  created_at: "2026-09-02T00:00:00Z",
  started_at: "2026-09-02T00:00:05Z",
  finished_at: "2026-09-02T00:10:00Z",
  result: {},
  error: null,
  log: "",
  provider: "ollama",
  model: "qwen3:1.7b",
  mode: "solo",
  privacy: "offline",
  conversation_id: null,
  collaboration_rounds: 1,
  collaboration_decision: null,
};

// ---------- Architecture visibility ----------

test("architecture mode prefers the decided architecture over run.mode", () => {
  const run = { ...BASE_RUN, mode: "solo", collaboration_decision: { architecture: { mode: "adaptive_hybrid" } } };
  assert.equal(architectureMode(run), "adaptive_hybrid");
  assert.equal(architectureLabel(architectureMode(run)), "Adaptive Hybrid");
});

test("all five manager modes have human labels", () => {
  assert.equal(architectureLabel("solo"), "Solo");
  assert.equal(architectureLabel("joint"), "Joint");
  assert.equal(architectureLabel("independent_discuss"), "Independent → Discuss");
  assert.equal(architectureLabel("parallel"), "Parallel");
  assert.equal(architectureLabel("adaptive_hybrid"), "Adaptive Hybrid");
});

test("unknown modes degrade honestly instead of guessing", () => {
  assert.equal(architectureMode({ mode: "", collaboration_decision: null }), "unknown");
  assert.equal(architectureLabel("unknown"), "Unknown");
});

// ---------- Participants ----------

test("participants collapse measured calls per adapter", () => {
  const run = {
    ...BASE_RUN,
    result: {
      calls: [
        { adapter_id: "ollama=a", provider: "ollama", model: "qwen3:1.7b", status: "complete" },
        { adapter_id: "ollama=a", provider: "ollama", model: "qwen3:1.7b", status: "complete" },
        { adapter_id: "openai=b", provider: "openai", model: "gpt-4o-mini", status: "complete" },
      ],
    },
  };
  const rows = participantsFromRun(run);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].calls, 2);
  assert.equal(rows[1].calls, 1);
});

test("runs without calls fall back to the executed provider/model pair", () => {
  const rows = participantsFromRun(BASE_RUN);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].provider, "ollama");
  assert.equal(rows[0].model, "qwen3:1.7b");
  assert.equal(rows[0].calls, 0);
});

// ---------- Collaboration timeline ----------

test("timeline exposes the six manager-ordered stages", () => {
  const stages = collaborationTimeline(BASE_RUN);
  assert.deepEqual(
    stages.map((stage) => stage.key),
    ["request", "planner", "coordinator", "workers", "responses", "synthesis"],
  );
});

test("user request is always recorded with the goal", () => {
  const stages = collaborationTimeline(BASE_RUN);
  assert.equal(stages[0].state, "recorded");
  assert.equal(stages[0].detail, BASE_RUN.goal);
});

test("stages without evidence say not_recorded, never invented", () => {
  const stages = collaborationTimeline(BASE_RUN);
  for (const stage of stages.slice(1)) {
    assert.equal(stage.state, "not_recorded", stage.key);
  }
});

test("decision evidence marks planner and coordinator as recorded", () => {
  const run = {
    ...BASE_RUN,
    collaboration_decision: {
      model_set: { policy: "auto_intelligence", model_refs: ["ollama:qwen3:1.7b"] },
      eligible_model_count: 3,
      decomposition: { basis: "task_shape", work_unit_count: 2, levels: 1 },
      architecture: { mode: "parallel" },
    },
  };
  const stages = collaborationTimeline(run);
  assert.equal(stages[1].state, "recorded");
  assert.match(stages[1].detail, /auto_intelligence/);
  assert.match(stages[1].detail, /3 eligible models/);
  assert.equal(stages[2].state, "recorded");
  assert.match(stages[2].detail, /2 work units/);
});

test("measured calls and live organization mark work and responses", () => {
  const run = {
    ...BASE_RUN,
    result: {
      calls: [
        { adapter_id: "ollama=a", provider: "ollama", status: "complete", summary: "Lemma proven." },
        { adapter_id: "ollama=b", provider: "ollama", status: "failed", error: "timeout" },
      ],
      live_organization: { tasks: { "call-01": {}, "call-02": {} } },
    },
  };
  const stages = collaborationTimeline(run);
  assert.equal(stages[3].state, "recorded");
  assert.match(stages[3].detail, /2 tasks/);
  assert.equal(stages[4].state, "recorded");
  assert.match(stages[4].detail, /2 measured calls \(1 with summaries\)/);
  assert.equal(stages[5].state, "recorded");
  assert.match(stages[5].detail, /1 public summary/);
});

test("singular forms read grammatically in stage details", () => {
  const run = {
    ...BASE_RUN,
    result: {
      calls: [{ adapter_id: "a", provider: "ollama", status: "complete", summary: "done" }],
      live_organization: { tasks: { "call-01": {} } },
    },
    collaboration_decision: { decomposition: { basis: "single", work_unit_count: 1 } },
  };
  const stages = collaborationTimeline(run);
  assert.match(stages[2].detail, /1 work unit ·/);
  assert.match(stages[3].detail, /1 task in the measured/);
  assert.match(stages[4].detail, /1 measured call \(1 with summaries\)/);
  assert.match(stages[5].detail, /1 public summary/);
});

// ---------- Decision trace ----------

test("decision trace rows report only what the decision documents", () => {
  const run = {
    ...BASE_RUN,
    collaboration_decision: {
      model_set: { policy: "auto_intelligence", rationale: "lowest cost passes gates", model_refs: ["ollama:qwen3:1.7b"] },
      eligible_model_count: 3,
      architecture: { mode: "parallel", policy_rule: "decompose when coupled=false", triggers: ["parallelizable", "verification"], expected_rounds: 2 },
      task_shape: { complexity: "medium", coupled: false, decomposable: true, verification_heavy: true },
      decomposition: { basis: "task_shape", work_unit_count: 3, levels: 1 },
    },
  };
  const rows = decisionTrace(run);
  const byLabel = new Map(rows.map((row) => [row.label, row]));
  assert.equal(byLabel.get("Architecture")?.value, "parallel");
  assert.equal(byLabel.get("Architecture triggers")?.value, "parallelizable · verification");
  assert.equal(byLabel.get("Model choice rationale")?.value, "lowest cost passes gates");
  assert.equal(byLabel.get("Coupled task")?.value, "no");
  assert.equal(byLabel.get("Work units")?.value, "3");
  assert.ok(recordedTraceCount(rows) >= 10);
});

test("trace degrades honestly without a decision — only run.mode stays recorded", () => {
  const rows = decisionTrace(BASE_RUN);
  assert.ok(rows.length >= 15);
  const recorded = rows.filter((row) => row.recorded);
  // The only recorded line is the executed architecture (from run.mode);
  // every decision-specific row is explicitly not recorded.
  assert.deepEqual(recorded.map((row) => row.label), ["Architecture"]);
  assert.equal(recorded[0].value, "solo");
  assert.equal(recordedTraceCount(rows), 1);
});

test("degradation reason appears only when actually recorded", () => {
  const degraded = {
    ...BASE_RUN,
    collaboration_decision: { architecture: { mode: "joint", degradation_reason: "router fell back after gate failure" } },
  };
  const rows = decisionTrace(degraded);
  assert.equal(rows.find((row) => row.label === "Degradation")?.value, "router fell back after gate failure");
  const plain = decisionTrace(BASE_RUN);
  assert.equal(plain.find((row) => row.label === "Degradation"), undefined);
});

// ---------- Cost insight ----------

test("recorded task cost surfaces total, tokens, and breakdown", () => {
  const run = {
    ...BASE_RUN,
    provider: "openai",
    result: {
      task_cost: {
        total_external_api_cost: 0.01234,
        input_tokens: 2000,
        output_tokens: 800,
        breakdown: [{ model: "gpt-4o-mini", cost_usd: 0.01234 }],
      },
      calls: [{ adapter_id: "a", provider: "openai", status: "complete" }],
    },
  };
  const insight = costInsight(run);
  assert.equal(insight.basis, "recorded");
  assert.equal(insight.totalUsd, 0.01234);
  assert.equal(insight.inputTokens, 2000);
  assert.equal(insight.outputTokens, 800);
  assert.equal(insight.breakdown.length, 1);
  assert.match(insight.line, /1 cloud call/);
});

test("free-local runs state the no-charge guarantee", () => {
  const insight = costInsight(BASE_RUN);
  assert.equal(insight.basis, "free_local");
  assert.match(insight.line, /no external API charge is possible/);
});

test("non-local runs without cost evidence say so honestly", () => {
  const insight = costInsight({ ...BASE_RUN, provider: "openai" });
  assert.equal(insight.basis, "not_recorded");
  assert.equal(insight.line, "Cost evidence not recorded on this run.");
});

test("local vs cloud split counts providers honestly", () => {
  const run = {
    ...BASE_RUN,
    provider: "openai",
    result: {
      task_cost: { total_external_api_cost: 0.02, breakdown: [] },
      calls: [
        { adapter_id: "a", provider: "ollama", status: "complete" },
        { adapter_id: "b", provider: "openai", status: "complete" },
        { adapter_id: "c", provider: "openai", status: "complete" },
      ],
    },
  };
  const insight = costInsight(run);
  assert.equal(insight.localCalls, 1);
  assert.equal(insight.cloudCalls, 2);
  assert.match(insight.line, /2 cloud calls and 1 local call/);
});

test("zero-cost cloud-less recorded runs read as free", () => {
  const run = {
    ...BASE_RUN,
    result: { task_cost: { total_external_api_cost: 0, breakdown: [] }, calls: [] },
  };
  assert.match(costInsight(run).line, /no external API charge recorded/);
});

// ---------- Availability gate ----------

test("insights render only when run evidence exists", () => {
  assert.equal(insightsAvailable(BASE_RUN), false);
  assert.equal(insightsAvailable({ ...BASE_RUN, collaboration_decision: { architecture: { mode: "solo" } } }), true);
  assert.equal(insightsAvailable({ ...BASE_RUN, result: { calls: [] } }), true);
  assert.equal(insightsAvailable({ ...BASE_RUN, result: { task_cost: {} } }), true);
  assert.equal(insightsAvailable({ ...BASE_RUN, result: { live_organization: { tasks: {} } } }), true);
});

// ---------- Additional coverage: labels, empty states, gates ----------

test("decision architecture with unknown mode still labels honestly", () => {
  const run = { ...BASE_RUN, collaboration_decision: { architecture: { mode: "mystery_mode" } } };
  assert.equal(architectureMode(run), "unknown");
  assert.equal(architectureLabel(architectureMode(run)), "Unknown");
});

test("timeline synthesis without summaries stays not_recorded even with calls", () => {
  const run = {
    ...BASE_RUN,
    result: { calls: [{ adapter_id: "a", provider: "ollama", status: "failed", error: "boom" }] },
  };
  const stages = collaborationTimeline(run);
  assert.equal(stages[4].state, "recorded");
  assert.match(stages[4].detail, /0 with summaries/);
  assert.equal(stages[5].state, "not_recorded");
  assert.match(stages[5].detail, /No synthesis evidence recorded/);
});

test("cost insight breakdown filters non-finite entries", () => {
  const run = {
    ...BASE_RUN,
    provider: "openai",
    result: {
      task_cost: {
        total_external_api_cost: 0.5,
        breakdown: [{ model: "m1", cost_usd: 0.5 }, { model: "m2", cost_usd: "n/a" }, {}],
      },
    },
  };
  const insight = costInsight(run);
  assert.equal(insight.breakdown.length, 1);
  assert.equal(insight.breakdown[0].model, "m1");
});

test("participants dedupe by adapter+provider+model combination", () => {
  const run = {
    ...BASE_RUN,
    result: {
      calls: [
        { adapter_id: "x", provider: "ollama", model: "a" },
        { adapter_id: "x", provider: "ollama", model: "a" },
        { adapter_id: "x", provider: "ollama", model: "b" },
      ],
    },
  };
  const rows = participantsFromRun(run);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].calls, 2);
});

test("insights gate stays closed for bare runs", () => {
  assert.equal(insightsAvailable({ ...BASE_RUN, result: null }), false);
  assert.equal(insightsAvailable({ ...BASE_RUN, result: {} }), false);
});

test("empty model-set refs render as not recorded in trace", () => {
  const run = { ...BASE_RUN, collaboration_decision: { model_set: { policy: "auto_intelligence", model_refs: [] } } };
  const row = decisionTrace(run).find((item) => item.label === "Model set");
  assert.equal(row?.recorded, false);
});

test("expected rounds and levels render from decision when present", () => {
  const run = {
    ...BASE_RUN,
    collaboration_decision: {
      architecture: { mode: "adaptive_hybrid", expected_rounds: 3 },
      decomposition: { basis: "work_units", work_unit_count: 4, levels: 2 },
    },
  };
  const byLabel = new Map(decisionTrace(run).map((row) => [row.label, row]));
  assert.equal(byLabel.get("Expected rounds")?.value, "3");
  assert.equal(byLabel.get("Levels")?.value, "2");
  assert.equal(byLabel.get("Architecture")?.value, "adaptive_hybrid");
});

test("timeline detail for planner without policy still says recorded", () => {
  const run = { ...BASE_RUN, collaboration_decision: { eligible_model_count: 2 } };
  const planner = collaborationTimeline(run)[1];
  assert.equal(planner.state, "recorded");
  assert.equal(planner.detail, "Collaboration decision recorded");
});

test("cost insight singular cloud call reads grammatically", () => {
  const run = {
    ...BASE_RUN,
    provider: "openai",
    result: {
      task_cost: { total_external_api_cost: 0.01, breakdown: [] },
      calls: [{ adapter_id: "a", provider: "openai", status: "complete" }],
    },
  };
  assert.match(costInsight(run).line, /across 1 cloud call\./);
});

test("participants from runs with only provider show unrecorded model", () => {
  const rows = participantsFromRun({ ...BASE_RUN, provider: "openai", model: "" });
  assert.equal(rows[0].provider, "openai");
  assert.equal(rows[0].model, "unrecorded");
});

test("architecture label for joint and parallel exact", () => {
  assert.equal(architectureLabel(architectureMode({ mode: "joint", collaboration_decision: null })), "Joint");
  assert.equal(architectureLabel(architectureMode({ mode: "parallel", collaboration_decision: null })), "Parallel");
});
