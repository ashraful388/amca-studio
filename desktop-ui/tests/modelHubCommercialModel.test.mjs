// Z3 Commercial Model Hub — unit tests for the provider directory, key
// masking, selection groups, cost visibility, and read-only health
// checks (pure model, no browser, no fetch, no backend). Run with:
// node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  PROVIDER_FAMILIES,
  buildProviderDirectory,
  estimateRunCost,
  familyForProviderName,
  findRateCard,
  formatUsdCost,
  healthChecksFromDiscover,
  localHealthChecks,
  maskSecret,
  selectionGroups,
} from "../src/modelHubCommercialModel.ts";

const HUB = (over = {}) => ({
  byok_providers: [],
  direct_models: [],
  local_models: { provider: "ollama", runtime_reachable: true, models: [] },
  amca_api: { models: [] },
  pricing: [],
  ...over,
});

// ---------- provider directory ----------

test("directory covers the nine commercial families plus local runtimes", () => {
  const dir = buildProviderDirectory(HUB());
  const ids = dir.providers.map((row) => row.familyId);
  for (const family of ["openai", "anthropic", "google", "moonshot", "deepseek", "qwen", "glm", "other"]) {
    assert.ok(ids.includes(family), `family ${family}`);
  }
  assert.deepEqual(dir.runtimes.map((row) => row.familyId), ["ollama", "llama_cpp", "vllm_sglang"]);
});

test("BYOK provider lands in its kind's family with model count and connected state", () => {
  const dir = buildProviderDirectory(HUB({
    byok_providers: [{ id: "prov1", provider_kind: "openai", name: "OpenAI", model_ids: ["gpt-4o", "gpt-4o-mini"], enabled_model_ids: ["gpt-4o", "gpt-4o-mini"], configured: true }],
  }));
  const openai = dir.providers.find((row) => row.familyId === "openai");
  assert.equal(openai.connected, true);
  assert.equal(openai.modelsAvailable, 2);
  assert.match(openai.detail, /1 BYOK connection/);
});

test("OpenAI-compatible Qwen/GLM connections match by name, not kind", () => {
  const dir = buildProviderDirectory(HUB({
    byok_providers: [
      { id: "p1", provider_kind: "openai_compatible", name: "Zhipu GLM", model_ids: ["glm-4.7"], enabled_model_ids: ["glm-4.7"], configured: true },
      { id: "p2", provider_kind: "openai_compatible", name: "Alibaba Qwen", model_ids: ["qwen3"], enabled_model_ids: ["qwen3"], configured: true },
    ],
  }));
  assert.equal(dir.providers.find((row) => row.familyId === "glm").connected, true);
  assert.equal(dir.providers.find((row) => row.familyId === "qwen").connected, true);
});

test("configured direct profiles count toward their family", () => {
  const dir = buildProviderDirectory(HUB({
    direct_models: [{ credential_id: "c1", provider: "openai", model: "gpt-4o", label: "GPT-4o", configured: true, input_cost_per_million: 2.5, output_cost_per_million: 10 }],
  }));
  const openai = dir.providers.find((row) => row.familyId === "openai");
  assert.equal(openai.connected, true);
  assert.equal(openai.modelsAvailable, 1);
});

test("unconfigured providers report no connection without inventing status", () => {
  const dir = buildProviderDirectory(HUB({
    byok_providers: [{ id: "p1", provider_kind: "deepseek", name: "DeepSeek", model_ids: [], enabled_model_ids: [], configured: false }],
  }));
  const deepseek = dir.providers.find((row) => row.familyId === "deepseek");
  assert.equal(deepseek.connected, false);
  assert.equal(deepseek.connectionLabel, "No connection yet");
});

test("ollama runtime reports real reachability and installed count", () => {
  const reachable = buildProviderDirectory(HUB({
    local_models: { provider: "ollama", runtime_reachable: true, models: [{ model: "a", size_bytes: 1, compatibility: { status: "green" } }, { model: "b", size_bytes: 1, compatibility: { status: "green" } }] },
  }));
  assert.equal(reachable.runtimes[0].connected, true);
  assert.equal(reachable.runtimes[0].connectionLabel, "Reachable");
  assert.equal(reachable.runtimes[0].modelsAvailable, 2);

  const unreachable = buildProviderDirectory(HUB({ local_models: { provider: "ollama", runtime_reachable: false, models: [] } }));
  assert.equal(unreachable.runtimes[0].connected, false);
  assert.equal(unreachable.runtimes[0].connectionLabel, "Unreachable");
});

test("llama.cpp and vLLM/SGLang rows say 'Not reported' instead of inventing status", () => {
  const dir = buildProviderDirectory(HUB());
  for (const runtime of dir.runtimes.slice(1)) {
    assert.equal(runtime.connected, false);
    assert.equal(runtime.connectionLabel, "Not reported");
    assert.match(runtime.detail, /no status document/);
  }
});

// ---------- key masking ----------

test("maskSecret hides the body and shows at most the last four chars", () => {
  assert.equal(maskSecret("sk-proj-abcdefgh12345678"), "••••••5678");
});

test("maskSecret reveals nothing for short secrets", () => {
  assert.equal(maskSecret("abc"), "••••••");
  assert.equal(maskSecret("1234567"), "••••••");
});

test("maskSecret returns empty for an empty secret", () => {
  assert.equal(maskSecret(""), "");
});

test("maskSecret output never contains the full secret", () => {
  const secret = "sk-verylongsecretkeyvalue123456";
  const masked = maskSecret(secret);
  assert.ok(!masked.includes("verylongsecretkeyvalue"));
  assert.ok(masked.startsWith("••••••"));
});

// ---------- selection groups ----------

test("selectionGroups cover local, byok, direct, and managed refs with unchanged refs", () => {
  const hub = HUB({
    byok_providers: [{ id: "prov1", provider_kind: "openai", name: "OpenAI", model_ids: ["gpt-4o"], enabled_model_ids: ["gpt-4o"], configured: true }],
    direct_models: [{ credential_id: "c1", provider: "anthropic", model: "claude", label: "Claude", configured: true, input_cost_per_million: 3, output_cost_per_million: 15 }],
    amca_api: { models: [{ logical_model_id: "amca.standard" }] },
    local_models: { provider: "ollama", runtime_reachable: true, models: [{ model: "qwen3:1.7b", size_bytes: 1, compatibility: { status: "green" } }] },
  });
  const groups = selectionGroups(hub);
  const all = groups.flatMap((group) => group.options);
  assert.deepEqual(all.map((option) => option.ref).sort(), ["amca.standard", "c1", "ollama:qwen3:1.7b", "prov1:gpt-4o"]);
  assert.equal(groups.find((group) => group.familyId === "openai").options[0].ref, "prov1:gpt-4o");
  assert.equal(groups.find((group) => group.familyId === "anthropic").options[0].ref, "c1");
  assert.equal(groups.find((group) => group.familyId === "ollama").options[0].source, "local");
});

test("selectionGroups skip unconfigured providers and deduplicate refs", () => {
  const hub = HUB({
    byok_providers: [
      { id: "p1", provider_kind: "openai", name: "OpenAI", model_ids: ["gpt-4o"], enabled_model_ids: ["gpt-4o"], configured: false },
      { id: "p2", provider_kind: "openai", name: "OpenAI 2", model_ids: ["gpt-4o"], enabled_model_ids: ["gpt-4o"], configured: true },
      { id: "p3", provider_kind: "openai", name: "OpenAI 3", model_ids: ["gpt-4o-mini"], enabled_model_ids: ["gpt-4o-mini"], configured: true, enabled: false },
    ],
  });
  const groups = selectionGroups(hub);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].options.length, 1);
  assert.ok(!groups.flatMap((group) => group.options).some((option) => option.ref === "p3:gpt-4o-mini"));
});

test("selectionGroups order puts commercial families before managed and local", () => {
  const hub = HUB({
    byok_providers: [{ id: "p1", provider_kind: "openai", name: "OpenAI", model_ids: ["gpt-4o"], enabled_model_ids: ["gpt-4o"], configured: true }],
    amca_api: { models: [{ logical_model_id: "amca.standard" }] },
    local_models: { provider: "ollama", runtime_reachable: true, models: [{ model: "m", size_bytes: 1, compatibility: { status: "green" } }] },
  });
  const ids = selectionGroups(hub).map((group) => group.familyId);
  assert.ok(ids.indexOf("openai") < ids.indexOf("amca_api"));
  assert.ok(ids.indexOf("amca_api") < ids.indexOf("ollama"));
});

// ---------- cost visibility ----------

test("findRateCard: local models are free by definition", () => {
  const card = findRateCard("ollama:qwen3:1.7b", HUB());
  assert.equal(card.basis, "free_local");
  assert.equal(card.inputPer1m, 0);
  assert.equal(card.outputPer1m, 0);
});

test("findRateCard: configured direct profiles expose their per-million rates", () => {
  const hub = HUB({
    direct_models: [{ credential_id: "c1", provider: "openai", model: "gpt-4o", label: "GPT-4o", configured: true, input_cost_per_million: 2.5, output_cost_per_million: 10 }],
  });
  const card = findRateCard("c1", hub);
  assert.equal(card.basis, "direct_profile");
  assert.equal(card.inputPer1m, 2.5);
  assert.equal(card.outputPer1m, 10);
  assert.equal(card.provider, "openai");
});

test("findRateCard: unconfigured direct profiles do not leak rates", () => {
  const hub = HUB({
    direct_models: [{ credential_id: "c1", provider: "openai", model: "gpt-4o", label: "GPT-4o", configured: false, input_cost_per_million: 2.5, output_cost_per_million: 10 }],
  });
  assert.equal(findRateCard("c1", hub).basis, "unknown");
});

test("findRateCard: pricing registry record is used with newest verified_at first", () => {
  const hub = HUB({
    pricing: [
      { access_source: "byok", provider: "openai", logical_model_id: "gpt-4o", input_per_1m: 2.5, output_per_1m: 10, verified_at: "2026-01-01T00:00:00Z", status: "verified" },
      { access_source: "byok", provider: "openai", logical_model_id: "gpt-4o", input_per_1m: 5, output_per_1m: 20, verified_at: "2025-06-01T00:00:00Z", status: "verified" },
    ],
  });
  const card = findRateCard("prov1:gpt-4o", hub);
  assert.equal(card.basis, "pricing_registry");
  assert.equal(card.inputPer1m, 2.5);
  assert.equal(card.outputPer1m, 10);
});

test("findRateCard: unknown pricing stays unknown, never invented", () => {
  assert.equal(findRateCard("prov1:unknown-model", HUB()).basis, "unknown");
});

test("estimateRunCost multiplies tokens by per-million rates", () => {
  const estimate = estimateRunCost(1_000_000, 500_000, { inputPer1m: 2, outputPer1m: 10 });
  assert.ok(Math.abs(estimate.totalUsd - 7) < 1e-9);
  assert.ok(Math.abs(estimate.inputUsd - 2) < 1e-9);
  assert.ok(Math.abs(estimate.outputUsd - 5) < 1e-9);
});

test("estimateRunCost clamps negative token counts to zero", () => {
  const estimate = estimateRunCost(-100, 0, { inputPer1m: 2, outputPer1m: 10 });
  assert.equal(estimate.totalUsd, 0);
});

test("estimateRunCost returns null when rates are unknown", () => {
  assert.equal(estimateRunCost(100, 100, { inputPer1m: null, outputPer1m: null }), null);
});

test("estimateRunCost free local model costs zero regardless of tokens", () => {
  const estimate = estimateRunCost(5_000_000, 5_000_000, { inputPer1m: 0, outputPer1m: 0 });
  assert.equal(estimate.totalUsd, 0);
});

test("formatUsdCost keeps precision for tiny estimates and rounds large ones", () => {
  assert.equal(formatUsdCost(0.0000123), "$0.000012");
  assert.equal(formatUsdCost(0.42), "$0.4200");
  assert.equal(formatUsdCost(12.5), "$12.50");
  assert.equal(formatUsdCost(0), "$0.00");
  assert.equal(formatUsdCost(Number.NaN), "—");
});

// ---------- health testing ----------

test("healthChecksFromDiscover pass produces four passing checks with latency and catalog", () => {
  const checks = healthChecksFromDiscover({ ok: true, catalogCount: 12, elapsedMs: 431, failureKind: null });
  assert.deepEqual(checks.map((check) => check.label), ["Reachable", "Authentication", "Response latency", "Context available"]);
  assert.ok(checks.every((check) => check.state === "pass"));
  assert.match(checks.find((check) => check.key === "latency").detail, /431 ms/);
  assert.match(checks.find((check) => check.key === "context").detail, /12 models/);
});

test("healthChecksFromDiscover auth failure marks reachable+auth failed, rest not assessed", () => {
  const checks = healthChecksFromDiscover({ ok: false, catalogCount: 0, elapsedMs: 250, failureKind: "auth_failed" });
  const byKey = Object.fromEntries(checks.map((check) => [check.key, check.state]));
  assert.equal(byKey.reachable, "fail");
  assert.equal(byKey.authentication, "fail");
  assert.equal(byKey.latency, "not_assessed");
  assert.equal(byKey.context, "not_assessed");
});

test("healthChecksFromDiscover unreachable leaves auth not assessed (no guess)", () => {
  const checks = healthChecksFromDiscover({ ok: false, catalogCount: 0, elapsedMs: 0, failureKind: "unreachable" });
  const byKey = Object.fromEntries(checks.map((check) => [check.key, check.state]));
  assert.equal(byKey.reachable, "fail");
  assert.equal(byKey.authentication, "not_assessed");
});

test("healthChecksFromDiscover empty catalog fails the context check", () => {
  const checks = healthChecksFromDiscover({ ok: true, catalogCount: 0, elapsedMs: 100, failureKind: null });
  assert.equal(checks.find((check) => check.key === "context").state, "fail");
});

test("healthChecksFromDiscover clamps negative latency detail", () => {
  const checks = healthChecksFromDiscover({ ok: true, catalogCount: 1, elapsedMs: -5, failureKind: null });
  assert.match(checks.find((check) => check.key === "latency").detail, /0 ms/);
});

test("localHealthChecks: reachable runtime with models passes reachability and context", () => {
  const checks = localHealthChecks(true, 3);
  const byKey = Object.fromEntries(checks.map((check) => [check.key, check.state]));
  assert.equal(byKey.reachable, "pass");
  assert.equal(byKey.authentication, "not_assessed");
  assert.equal(byKey.latency, "not_assessed");
  assert.equal(byKey.context, "pass");
  assert.match(checks.find((check) => check.key === "context").detail, /3 models/);
});

test("localHealthChecks: unreachable runtime fails reachability and context", () => {
  const checks = localHealthChecks(false, 0);
  const byKey = Object.fromEntries(checks.map((check) => [check.key, check.state]));
  assert.equal(byKey.reachable, "fail");
  assert.equal(byKey.context, "fail");
});

test("localHealthChecks: unknown scan state reads as not assessed", () => {
  const checks = localHealthChecks(null, 0);
  assert.equal(checks.find((check) => check.key === "reachable").state, "not_assessed");
});

// ---------- family matching sanity ----------

test("familyForProviderName maps the Z3 families and defaults to other", () => {
  assert.equal(familyForProviderName("OpenAI"), "openai");
  assert.equal(familyForProviderName("Anthropic"), "anthropic");
  assert.equal(familyForProviderName("Moonshot / Kimi"), "moonshot");
  assert.equal(familyForProviderName("DeepSeek"), "deepseek");
  assert.equal(familyForProviderName("Alibaba Qwen"), "qwen");
  assert.equal(familyForProviderName("Zhipu GLM"), "glm");
  assert.equal(familyForProviderName("SomeObscureCloud"), "other");
});

test("PROVIDER_FAMILIES cover the eight commercial groups from the order", () => {
  const ids = PROVIDER_FAMILIES.map((family) => family.id);
  assert.deepEqual(ids, ["openai", "anthropic", "google", "moonshot", "deepseek", "qwen", "glm", "other"]);
});
