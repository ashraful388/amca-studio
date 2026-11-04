// ZCODE-AMCA-STUDIO-V1.0.0-MODEL-HUB-MANAGEMENT-UX-AND-LOCAL-MODEL-OPERATIONS-01 — pure model.
//
// Professional Model Hub management dashboard over recorded fields only:
// - registry rows (models, providers, locality, availability, capability
//   tags, connection status) restate recorded fields verbatim;
// - local model operations visibility: discovered local models, location,
//   runtime availability, supported formats (Ollama, llama.cpp, vLLM);
//   support means VISIBILITY — no automatic download, activation, or
//   routing;
// - provider connection status: connected/disconnected, provider type,
//   credential fingerprint presence, last manual verification state.
//   NEVER display API keys, secrets, or tokens;
// - capability evidence panel: Capability → Evidence Source →
//   Verified/Unknown. No generated explanations.
// - No ranking, no "best model" suggestions, no routing modification.

export type RuntimeKind = "ollama" | "llama.cpp" | "vllm" | "unknown";

export const SUPPORTED_LOCAL_RUNTIMES: ReadonlyArray<RuntimeKind> = ["ollama", "llama.cpp", "vllm"];

export const MANAGEMENT_BOUNDARY =
  "Recorded information only — no ranking, no 'best model' suggestions, no routing modification.";

export const LOCAL_OPS_NOTE =
  "Local model operations are visibility-only — no automatic download, no automatic activation, no automatic routing.";

export const CREDENTIAL_PRIVACY_NOTE =
  "Credentials never leave the operating-system vault. This surface never displays API keys, secrets, or tokens — only a fingerprint presence indicator.";

// ---------- provider connection status (recorded fields) ----------

export interface ProviderConnectionStatus {
  ref: string;
  providerType: string;
  connected: boolean;
  /** True when a credential fingerprint exists — never the credential. */
  credentialFingerprintPresent: boolean | null;
  lastManualVerification: string | null;
}

export function providerConnectionStatus(input: {
  ref: string;
  providerType: string;
  configured: boolean;
  credentialFingerprintPresent?: boolean | null;
  lastManualVerification?: string | null;
}): ProviderConnectionStatus {
  return {
    ref: input.ref,
    providerType: input.providerType,
    connected: Boolean(input.configured),
    credentialFingerprintPresent:
      input.credentialFingerprintPresent === undefined ? null : input.credentialFingerprintPresent,
    lastManualVerification: input.lastManualVerification ?? null,
  };
}

// ---------- capability evidence panel (recorded fields) ----------

export interface CapabilityEvidenceRow {
  capability: string;
  evidenceSource: string;
  verdict: "Verified" | "Unknown";
}

/**
 * Capability evidence: "Verified" only when a recorded evidence source
 * exists; otherwise "Unknown". No generated explanations.
 */
export function capabilityEvidence(
  capabilities: Array<{ capability: string; evidenceSource?: string | null }>,
): CapabilityEvidenceRow[] {
  return capabilities.map((entry) => ({
    capability: entry.capability,
    evidenceSource: entry.evidenceSource && entry.evidenceSource.trim() ? entry.evidenceSource : "No recorded evidence source",
    verdict: entry.evidenceSource && entry.evidenceSource.trim() ? "Verified" : "Unknown",
  }));
}

// ---------- local model operations (recorded fields) ----------

export interface LocalModelOperations {
  runtime: RuntimeKind;
  location: string;
  runtimeAvailability: "available" | "unavailable" | "Not recorded";
  supportedFormats: string[];
}

/**
 * Local model operations visibility. Formats and availability come from
 * recorded fields; nothing downloads or activates anything.
 */
export function localModelOperations(input: {
  runtimeRef: string;
  location?: string | null;
  available?: boolean | null;
  formats?: string[];
}): LocalModelOperations {
  const lowered = input.runtimeRef.toLowerCase();
  let runtime: RuntimeKind = "unknown";
  for (const candidate of SUPPORTED_LOCAL_RUNTIMES) {
    if (lowered.includes(candidate)) {
      runtime = candidate;
      break;
    }
  }
  return {
    runtime,
    location: input.location && input.location.trim() ? input.location : "Not recorded",
    runtimeAvailability:
      input.available === undefined || input.available === null
        ? "Not recorded"
        : input.available
          ? "available"
          : "unavailable",
    supportedFormats: input.formats ?? [],
  };
}

// ---------- management dashboard rows (recorded fields) ----------

export interface ManagementRow {
  ref: string;
  kind: "model" | "provider" | "agent";
  locality: string;
  availability: string;
  capabilityTags: string[];
  connectionStatus: string;
}

/** One dashboard row per recorded resource; tags are recorded tags only. */
export function managementRows(resources: Array<{
  ref: string;
  kind: "model" | "provider" | "agent";
  available?: boolean | null;
  capabilityTags?: string[];
  connected?: boolean | null;
}>): ManagementRow[] {
  return resources.map((resource) => ({
    ref: resource.ref,
    kind: resource.kind,
    locality: resource.ref.startsWith("ollama:")
      ? "Local"
      : resource.kind === "agent"
        ? "Recorded agent"
        : "Provider",
    availability:
      resource.available === undefined || resource.available === null
        ? "Not recorded"
        : resource.available
          ? "available"
          : "unavailable",
    capabilityTags: resource.capabilityTags ?? [],
    connectionStatus:
      resource.kind === "provider"
        ? resource.connected === undefined || resource.connected === null
          ? "Not recorded"
          : resource.connected
            ? "connected"
            : "disconnected"
        : "n/a",
  }));
}
