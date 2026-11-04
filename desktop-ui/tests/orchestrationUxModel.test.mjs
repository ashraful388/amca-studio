import { test } from "node:test";
import assert from "node:assert/strict";
import {
  refLocality,
  decisionLocality,
  decisionPanel,
  routingExplanations,
  capabilityRows,
  costTransparencyLines,
} from "../src/orchestrationUxModel.ts";

const BASE_RUN = {
  run_id: "run-1",
  project_id: "p1",
  goal: "Prove the lemma.",
  status: "succeeded",
  created_at: "2026-09-02T00:00:00Z",
  started_at: null,
  finished_at: null,
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

// ---------- Locality ----------

test("locality detection reads provider hints inside refs", () => {
  assert.equal(refLocality("ollama:qwen3:1.7b"), "local");
  assert.equal(refLocality("cred-local-1"), "local");
  assert.equal(refLocality("openai:gpt-4o"), "unknown");
});

test("mixed reference sets report mixed locality; empty stays unknown", () => {
  assert.equal(decisionLocality(["ollama:a", "openai:b"]), "mixed");
  assert.equal(decisionLocality(["ollama:a", "ollama:b"]), "local");
  assert.equal(decisionLocality([]), "unknown");
  assert.equal(decisionLocality(["openai:a"]), "unknown");
});

// ---------- Decision panel ----------

test("panel degrades honestly without a decision document", () => {
  const view = decisionPanel(BASE_RUN);
  assert.equal(view.hasDecision, false);
  assert.deepEqual(view.selectedRefs, []);
  assert.equal(view.locality, "unknown");
  assert.deepEqual(view.factors, []);
  assert.equal(view.expectedCostUsd, null);
  assert.equal(view.rationale, null);
});

test("panel renders selected refs, locality, and recorded factors", () => {
  const run = {
    ...BASE_RUN,
    collaboration_decision: {
      model_set: { policy: "auto_intelligence", model_refs: ["ollama:qwen3:1.7b"], rationale: "lowest cost passes gates", expected_cost_usd: 0 },
      eligible_model_count: 3,
      task_shape: { complexity: "medium", coupled: false, decomposable: true, verification_heavy: false },
      signals: { has_critical_objection: false, parallel_conflict: true },
    },
  };
  const view = decisionPanel(run);
  assert.equal(view.hasDecision, true);
  assert.deepEqual(view.selectedRefs, ["ollama:qwen3:1.7b"]);
  assert.equal(view.locality, "local");
  assert.equal(view.expectedCostUsd, 0);
  assert.equal(view.rationale, "lowest cost passes gates");
  assert.equal(view.alternativesCount, 3);
  const byLabel = new Map(view.factors.map((factor) => [factor.label, factor.value]));
  assert.equal(byLabel.get("Task complexity"), "medium");
  assert.equal(byLabel.get("Coupled task"), "no");
  assert.equal(byLabel.get("Parallel conflict flag"), "yes");
});

test("panel factors skip unrecorded dimensions entirely", () => {
  const run = { ...BASE_RUN, collaboration_decision: { eligible_model_count: 2 } };
  const view = decisionPanel(run);
  assert.equal(view.factors.length, 1);
  assert.equal(view.factors[0].label, "Eligible models");
});

// ---------- Routing explanations ----------

test("auto policy explains capability+cost selection without exposing reasoning", () => {
  const run = { ...BASE_RUN, collaboration_decision: { model_set: { policy: "auto_intelligence", model_refs: ["ollama:a"] } } };
  const explanations = routingExplanations(run);
  assert.equal(explanations[0].sentence, "Selected because it matched the required capability at the lowest cost among eligible models.");
  assert.equal(explanations[0].evidence, "model_set.policy");
});

test("local-preferred policy with all-local refs cites cost reduction", () => {
  const run = { ...BASE_RUN, collaboration_decision: { model_set: { policy: "local_preferred", model_refs: ["ollama:a"] } } };
  assert.match(routingExplanations(run)[0].sentence, /Local model selected to reduce cost/);
});

test("degradation reason becomes the fallback explanation with its evidence", () => {
  const run = {
    ...BASE_RUN,
    collaboration_decision: {
      model_set: { policy: "auto_intelligence", model_refs: [] },
      architecture: { degradation_reason: "preferred model unavailable" },
    },
  };
  const explanation = routingExplanations(run).find((item) => item.evidence === "architecture.degradation_reason");
  assert.equal(explanation?.sentence, "Fallback used because: preferred model unavailable");
});

test("runs without decisions get one honest not-recorded explanation", () => {
  const explanations = routingExplanations(BASE_RUN);
  assert.equal(explanations.length, 1);
  assert.match(explanations[0].sentence, /were not recorded/);
  assert.equal(explanations[0].evidence, "none");
});

test("recorded rationale is surfaced verbatim with its evidence field", () => {
  const run = { ...BASE_RUN, collaboration_decision: { model_set: { policy: "manual_only", model_refs: ["x"], rationale: "user pinned the local model" } } };
  const explanation = routingExplanations(run).find((item) => item.evidence === "model_set.rationale");
  assert.equal(explanation?.sentence, "user pinned the local model");
});

// ---------- Capability rows ----------

test("capability rows keep unknown dimensions unknown", () => {
  const rows = capabilityRows([
    { ref: "ollama:qwen3:1.7b", provider: "ollama", parameterSize: "1.7B", quantization: "Q4_K_M", tokensPerSecond: 45 },
  ]);
  const row = rows[0];
  assert.equal(row.locality, "local");
  assert.equal(row.speedTier, "fast (≥40 tok/s measured)");
  assert.equal(row.parameters, "1.7B");
  assert.equal(row.contextWindow, "unknown");
  assert.equal(row.toolSupport, "unknown");
  assert.equal(row.visionSupport, "unknown");
});

test("models without measurements render unknown speed, never invented tiers", () => {
  const rows = capabilityRows([
    { ref: "ollama:big", provider: "ollama", parameterSize: null, quantization: null, tokensPerSecond: null },
  ]);
  assert.equal(rows[0].speedTier, "unknown");
  assert.equal(rows[0].parameters, "unknown");
});

test("measured speed tiers map by real tok/s thresholds", () => {
  const rows = capabilityRows([
    { ref: "a", provider: "ollama", parameterSize: null, quantization: null, tokensPerSecond: 20 },
    { ref: "b", provider: "ollama", parameterSize: null, quantization: null, tokensPerSecond: 5 },
    { ref: "c", provider: "ollama", parameterSize: null, quantization: null, tokensPerSecond: Number.NaN },
  ]);
  assert.match(rows[0].speedTier, /moderate/);
  assert.match(rows[1].speedTier, /slow/);
  assert.equal(rows[2].speedTier, "unknown");
});

// ---------- Cost transparency ----------

test("local refs state the $0 no-network guarantee", () => {
  const lines = costTransparencyLines(["ollama:qwen3:1.7b"]);
  assert.match(lines[0].line, /\$0 external API charge/);
  assert.match(lines[0].line, /no network call/);
});

test("cloud refs stay honest about unknown pricing", () => {
  const lines = costTransparencyLines(["openai:gpt-4o"]);
  assert.match(lines[0].line, /unknown pricing is never estimated/);
});

test("mixed sets produce one honest line per ref", () => {
  const lines = costTransparencyLines(["ollama:a", "openai:b"]);
  assert.equal(lines.length, 2);
});
