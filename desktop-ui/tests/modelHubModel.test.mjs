// v0.6.0 M4 Model Hub/BYOK UX unit tests for the pure presentation model
// (no browser, no fetch, no backend). Run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  PROVIDER_PRESETS,
  findSameEndpointProvider,
  mapConnectionFailure,
  providerStatusLabel,
  staleTeamMembers,
} from "../src/modelHubModel.ts";

test("presets expose only provider categories the backend already knows", () => {
  // KNOWN_ENDPOINTS in amca/model_hub.py: openai, anthropic, google,
  // moonshot, deepseek, mistral, xai — plus the frontend-only custom form.
  for (const kind of ["openai", "anthropic", "google", "moonshot", "deepseek", "mistral", "xai", "custom"]) {
    assert.ok(kind in PROVIDER_PRESETS, kind);
  }
  assert.equal(PROVIDER_PRESETS.xai.endpoint, "https://api.x.ai/v1");
});

test("connection failures map to bounded user-safe results", () => {
  const auth = mapConnectionFailure(new Error("HTTP Error 401: Unauthorized"));
  assert.equal(auth.kind, "auth_failed");
  assert.match(auth.message, /Authentication failed/);

  // A provider-side client block also carries HTTP 403, but the API key was
  // never judged: saying "Authentication failed" would send the operator to
  // replace a working key.
  const blocked = mapConnectionFailure(
    new Error(
      "the provider's bot protection blocked AMCA's request (HTTP 403); the API key was not judged",
    ),
  );
  assert.equal(blocked.kind, "unreachable");
  assert.match(blocked.message, /bot protection/);
  assert.doesNotMatch(blocked.message, /Authentication failed/);

  const unreachable = mapConnectionFailure(
    new Error("<urlopen error [WinError 10061] connection refused>"),
  );
  assert.equal(unreachable.kind, "unreachable");
  assert.match(unreachable.message, /Provider unreachable/);

  const missing = mapConnectionFailure(new Error("HTTP Error 404: Not Found"));
  assert.equal(missing.kind, "catalog_unavailable");

  const garbage = mapConnectionFailure(new Error("Expecting value: line 1 column 1"));
  assert.equal(garbage.kind, "catalog_unavailable");

  const unknown = mapConnectionFailure(new Error("something completely different"));
  assert.equal(unknown.kind, "refused");
  assert.ok(!unknown.message.includes("something completely different"));
});

test("no raw exception text or tracebacks ever reach the result message", () => {
  const noisy = mapConnectionFailure(
    new Error(
      "Traceback (most recent call last): File \"service.py\" secret_value_abc HTTP Error 500",
    ),
  );
  assert.ok(!noisy.message.includes("Traceback"));
  assert.ok(!noisy.message.includes("secret_value_abc"));
  assert.ok(noisy.message.length <= 200);
});

test("provider status labels distinguish configured from ready", () => {
  assert.equal(providerStatusLabel(false, "catalog_ready"), "Credential missing");
  assert.equal(
    providerStatusLabel(true, "endpoint_unverified"),
    "Credential saved · endpoint unverified",
  );
  assert.equal(
    providerStatusLabel(true, "connected_metadata_only"),
    "Connected · metadata only",
  );
  assert.equal(providerStatusLabel(true, "catalog_ready"), "Connected · catalog ready");
  assert.equal(providerStatusLabel(true, undefined), "Configured");
});

test("same-endpoint duplicate detection ignores case and self", () => {
  const providers = [
    { id: "provider.a", name: "OpenAI work", endpoint: "https://api.openai.com/v1" },
    { id: "provider.b", name: "DeepSeek", endpoint: "https://api.deepseek.com" },
  ];
  assert.equal(
    findSameEndpointProvider(providers, { endpoint: "HTTPS://API.OPENAI.COM/V1" }, null)?.id,
    "provider.a",
  );
  // Editing that same provider is not a duplicate of itself.
  assert.equal(
    findSameEndpointProvider(providers, { endpoint: "https://api.openai.com/v1" }, "provider.a"),
    null,
  );
  assert.equal(findSameEndpointProvider(providers, { endpoint: "" }, null), null);
  assert.equal(
    findSameEndpointProvider(providers, { endpoint: "https://other.example/v1" }, null),
    null,
  );
});

test("stale team members are computed against the live runtime source", () => {
  const available = ["ollama:qwen3:1.7b", "provider.a:gpt-4o-mini"];
  assert.deepEqual(
    staleTeamMembers(["ollama:qwen3:1.7b", "provider.a:gpt-4o-mini"], available),
    [],
  );
  assert.deepEqual(
    staleTeamMembers(["ollama:deleted-model", "provider.gone:mist"], available),
    ["ollama:deleted-model", "provider.gone:mist"],
  );
});
