// 32F Fix 2 — Retry re-resolution unit tests for the pure recovery model
// (no browser, no fetch, no backend). Run with:
//   node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  availableModelIds,
  resolveRetryOutcome,
  NO_USABLE_MODEL_ERROR,
} from "../src/chatRecoveryModel.ts";

test("test_retry_model_reprobe", () => {
  // Live re-probe after the runtime came back: local models are now visible,
  // so the held request is allowed to execute and the stale selection is
  // replaced by a freshly available model.
  const resolution = resolveRetryOutcome({
    localModels: [
      { model: "qwen3:0.6b", fit: 0 },
      { model: "qwen3:1.7b", fit: 1 },
      { model: "gpt-oss:20b", fit: 2 },
    ],
    credentials: [{ credential_id: "tabitoken.claude-opus-4-8", configured: false }],
    hubProviders: [{ id: "prov1", configured: true, model_ids: ["deepseek-chat"], enabled_model_ids: ["deepseek-chat"] }],
    selectedModel: "ollama:qwen3:1.7b",
    canResume: true,
  });
  assert.deepEqual(resolution.availableModelIds, [
    "ollama:qwen3:0.6b",
    "ollama:qwen3:1.7b",
    "prov1:deepseek-chat",
  ]);
  assert.equal(resolution.outcome, "execute");
  assert.equal(resolution.error, null);
  assert.equal(resolution.nextSelectedModel, "ollama:qwen3:1.7b");

  // A stale selection (runtime was down when it was chosen) is re-resolved
  // against the fresh availability instead of staying empty.
  const stale = resolveRetryOutcome({
    localModels: [{ model: "qwen3:0.6b", fit: 0 }],
    credentials: [],
    hubProviders: [],
    selectedModel: "",
    canResume: false,
  });
  assert.equal(stale.nextSelectedModel, "ollama:qwen3:0.6b");
  assert.equal(stale.outcome, "resolved");
  assert.equal(stale.error, null);

  // Re-probe still finds nothing usable: the probe result is reported and the
  // caller must keep a truthful error (never a silent banner removal).
  const empty = resolveRetryOutcome({
    localModels: [{ model: "gpt-oss:20b", fit: 2 }],
    credentials: [{ credential_id: "cred", configured: false }],
    hubProviders: [{ id: "prov1", configured: false, model_ids: ["deepseek-chat"], enabled_model_ids: ["deepseek-chat"] }],
    selectedModel: "",
    canResume: true,
  });
  assert.deepEqual(empty.availableModelIds, []);
  assert.equal(empty.outcome, "blocked");
  assert.equal(empty.error, NO_USABLE_MODEL_ERROR);

  // Oversized local models (fit 2) never count as available.
  assert.deepEqual(
    availableModelIds({ localModels: [{ model: "gpt-oss:20b", fit: 2 }], credentials: [], hubProviders: [] }),
    [],
  );
});

test("retry excludes disabled and unpublished BYOK models but preserves published choices", () => {
  const ids = availableModelIds({
    localModels: [],
    credentials: [{ credential_id: "direct", configured: true }],
    hubProviders: [
      { id: "enabled", configured: true, model_ids: ["published", "hidden"], enabled_model_ids: ["published"] },
      { id: "disabled", configured: true, model_ids: ["published"], enabled_model_ids: ["published"], enabled: false },
      { id: "legacy", configured: true, model_ids: ["legacy-model"], enabled_model_ids: ["legacy-model"] },
    ],
  });
  assert.deepEqual(ids, ["direct", "enabled:published", "legacy:legacy-model"]);
});

test("test_no_model_truthful_failure", () => {
  const resolution = resolveRetryOutcome({
    localModels: [],
    credentials: [],
    hubProviders: [],
    selectedModel: "ollama:qwen3:1.7b",
    canResume: true,
  });
  assert.equal(resolution.outcome, "blocked");
  assert.equal(resolution.error, NO_USABLE_MODEL_ERROR);
  assert.notEqual(resolution.error, null);
  assert.equal(resolution.nextSelectedModel, "");

  // A provider that exists but is not configured does not fake availability.
  const unconfigured = resolveRetryOutcome({
    localModels: [],
    credentials: [{ credential_id: "tabitoken.claude-opus-4-8", configured: false }],
    hubProviders: [{ id: "prov1", configured: false, model_ids: ["deepseek-chat"], enabled_model_ids: ["deepseek-chat"] }],
    selectedModel: "",
    canResume: false,
  });
  assert.equal(unconfigured.outcome, "blocked");
  assert.equal(unconfigured.error, NO_USABLE_MODEL_ERROR);
});
