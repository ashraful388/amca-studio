// Z1 Model Hub routing candidates — unit tests for the pure presentation
// model additions (no browser, no fetch, no backend). Run with:
// node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  buildRoutingCandidates,
  AUTO_ROUTING_OBJECTIVE,
} from "../src/modelHubModel.ts";

test("routing candidates cover all four existing sources and skip unconfigured ones", () => {
  const candidates = buildRoutingCandidates({
    localModels: [{ model: "qwen3:1.7b" }, { model: "llama3.2:3b" }],
    byokProviders: [
      { id: "prov1", name: "DeepSeek", model_ids: ["deepseek-chat", "deepseek-reasoner"], enabled_model_ids: ["deepseek-chat", "deepseek-reasoner"], configured: true },
      { id: "prov2", name: "Unconfigured", model_ids: ["hidden-model"], enabled_model_ids: [], configured: false },
      { id: "prov3", name: "Disabled", model_ids: ["still-published"], enabled_model_ids: ["still-published"], configured: true, enabled: false },
    ],
    directModels: [
      { credential_id: "cred1", label: "Claude Opus", provider: "anthropic", configured: true },
      { credential_id: "cred2", label: "Ghost", provider: "openai", configured: false },
    ],
    amcaApiModels: [{ logical_model_id: "amca-mini" }, {}],
  });

  const byRef = new Map(candidates.map((candidate) => [candidate.ref, candidate]));
  assert.ok(byRef.has("ollama:qwen3:1.7b"));
  assert.ok(byRef.has("prov1:deepseek-chat"));
  assert.ok(byRef.has("cred1"));
  assert.ok(byRef.has("amca-mini"));
  assert.ok(!byRef.has("prov2:hidden-model"));
  assert.ok(!byRef.has("prov3:still-published"));
  assert.ok(!byRef.has("cred2"));

  const local = byRef.get("ollama:qwen3:1.7b");
  assert.equal(local.source, "local");
  assert.equal(local.providerLabel, "Ollama · local");

  const byok = byRef.get("prov1:deepseek-chat");
  assert.equal(byok.source, "byok");
  assert.equal(byok.providerLabel, "DeepSeek");

  const direct = byRef.get("cred1");
  assert.equal(direct.source, "direct");
  assert.equal(direct.label, "Claude Opus");

  const managed = byRef.get("amca-mini");
  assert.equal(managed.source, "amca_api");
  assert.equal(managed.providerLabel, "AMCA API");
});

test("routing candidates deduplicate identical refs", () => {
  const candidates = buildRoutingCandidates({
    localModels: [{ model: "qwen3:1.7b" }],
    byokProviders: [],
    directModels: [],
    amcaApiModels: [],
  });
  const again = buildRoutingCandidates({
    localModels: [{ model: "qwen3:1.7b" }],
    byokProviders: [],
    directModels: [],
    amcaApiModels: [],
  });
  const merged = [...candidates, ...again];
  const refs = new Set(merged.map((candidate) => candidate.ref));
  assert.equal(refs.size, 1);
  assert.equal(merged.length, 2);
});

test("auto routing objective is honest about the existing gate order", () => {
  assert.match(AUTO_ROUTING_OBJECTIVE, /quality and privacy gates/i);
  assert.match(AUTO_ROUTING_OBJECTIVE, /lowest cost/i);
});
