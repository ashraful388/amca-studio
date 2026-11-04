// ZCODE-AMCA-STUDIO-V1.0.0-MODEL-HUB-MANAGEMENT-UX-AND-LOCAL-MODEL-OPERATIONS-01 tests.
// Minimum: registry rendering, credential privacy, local model visibility,
// capability honesty, no routing modification.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  SUPPORTED_LOCAL_RUNTIMES,
  MANAGEMENT_BOUNDARY,
  LOCAL_OPS_NOTE,
  CREDENTIAL_PRIVACY_NOTE,
  providerConnectionStatus,
  capabilityEvidence,
  localModelOperations,
  managementRows,
} from "../src/modelHubManagementUxModel.ts";

// ---------- registry rendering ----------

test("management rows restate recorded fields without ranking", () => {
  const rows = managementRows([
    { ref: "ollama:qwen", kind: "model", available: true, capabilityTags: ["vision"] },
    { ref: "cred-7", kind: "provider", connected: false },
    { ref: "agent-1", kind: "agent" },
  ]);
  assert.equal(rows[0].locality, "Local");
  assert.equal(rows[0].availability, "available");
  assert.deepEqual(rows[0].capabilityTags, ["vision"]);
  assert.equal(rows[1].connectionStatus, "disconnected");
  assert.equal(rows[2].locality, "Recorded agent");
  // unrecorded availability stays Not recorded, never a false zero
  assert.equal(rows[2].availability, "Not recorded");
  const rendered = JSON.stringify(rows).toLowerCase();
  assert.ok(!/best model|ranking|recommend/.test(rendered));
  assert.ok(MANAGEMENT_BOUNDARY.includes("no ranking"));
  assert.ok(MANAGEMENT_BOUNDARY.includes("no routing modification"));
});

// ---------- credential privacy ----------

test("provider connection status never carries credential material", () => {
  const status = providerConnectionStatus({
    ref: "cred-7",
    providerType: "openai-compatible",
    configured: true,
    credentialFingerprintPresent: true,
    lastManualVerification: "2026-09-03T09:00:00Z",
  });
  assert.equal(status.connected, true);
  assert.equal(status.credentialFingerprintPresent, true);
  assert.equal(status.lastManualVerification, "2026-09-03T09:00:00Z");
  // no secret material anywhere in the row
  const rendered = JSON.stringify(status).toLowerCase();
  assert.ok(!/sk-|api_key|apikey|secret_value|bearer /.test(rendered));
  assert.equal(status.credentialFingerprintPresent === null, false);

  const unknown = providerConnectionStatus({ ref: "cred-8", providerType: "openai-compatible", configured: true });
  assert.equal(unknown.credentialFingerprintPresent, null);
  assert.equal(unknown.lastManualVerification, null);
  assert.ok(CREDENTIAL_PRIVACY_NOTE.includes("never displays API keys, secrets, or tokens"));
});

// ---------- local model operations visibility ----------

test("local operations detect recorded runtimes and preserve honest gaps", () => {
  const ollama = localModelOperations({ runtimeRef: "ollama:qwen", location: "C:/models", available: true, formats: ["gguf"] });
  assert.equal(ollama.runtime, "ollama");
  assert.equal(ollama.location, "C:/models");
  assert.equal(ollama.runtimeAvailability, "available");
  assert.deepEqual(ollama.supportedFormats, ["gguf"]);

  const llama = localModelOperations({ runtimeRef: "llama.cpp:70b", available: false });
  assert.equal(llama.runtime, "llama.cpp");
  assert.equal(llama.runtimeAvailability, "unavailable");

  const vllm = localModelOperations({ runtimeRef: "vllm:endpoint", location: "  " });
  assert.equal(vllm.runtime, "vllm");
  assert.equal(vllm.location, "Not recorded");

  const unknown = localModelOperations({ runtimeRef: "mystery", available: null });
  assert.equal(unknown.runtime, "unknown");
  assert.equal(unknown.runtimeAvailability, "Not recorded");
  assert.deepEqual(unknown.supportedFormats, []);
  assert.deepEqual(SUPPORTED_LOCAL_RUNTIMES, ["ollama", "llama.cpp", "vllm"]);
  assert.ok(LOCAL_OPS_NOTE.includes("no automatic download"));
  assert.ok(LOCAL_OPS_NOTE.includes("no automatic activation"));
  assert.ok(LOCAL_OPS_NOTE.includes("no automatic routing"));
});

// ---------- capability honesty ----------

test("capability evidence is Verified only with a recorded source, else Unknown", () => {
  const rows = capabilityEvidence([
    { capability: "vision", evidenceSource: "recorded provider capability note" },
    { capability: "tools", evidenceSource: null },
    { capability: "long-context", evidenceSource: "  " },
  ]);
  assert.equal(rows[0].verdict, "Verified");
  assert.equal(rows[0].evidenceSource, "recorded provider capability note");
  assert.equal(rows[1].verdict, "Unknown");
  assert.equal(rows[1].evidenceSource, "No recorded evidence source");
  assert.equal(rows[2].verdict, "Unknown");
});
