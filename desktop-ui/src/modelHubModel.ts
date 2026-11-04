// v0.6.0 M4 Model Hub/BYOK UX: pure presentation model.
//
// Everything here is productization of EXISTING backend capabilities:
// provider CRUD and bounded catalog discovery already exist; this module
// only maps their results to bounded, truthful UI state. No capability
// badges are invented for BYOK providers (the backend does not expose
// capability metadata for them), no identity semantics are invented for
// duplicates, and no secret is ever represented beyond "Configured".

export interface ProviderPreset {
  name: string;
  endpoint: string;
}

export const PROVIDER_PRESETS: Record<string, ProviderPreset> = {
  openai: { name: "OpenAI", endpoint: "https://api.openai.com/v1" },
  anthropic: { name: "Anthropic", endpoint: "https://api.anthropic.com" },
  google: { name: "Google / Gemini", endpoint: "https://generativelanguage.googleapis.com/v1beta" },
  moonshot: { name: "Moonshot / Kimi", endpoint: "https://api.moonshot.ai/v1" },
  deepseek: { name: "DeepSeek", endpoint: "https://api.deepseek.com" },
  mistral: { name: "Mistral", endpoint: "https://api.mistral.ai/v1" },
  xai: { name: "xAI / Grok", endpoint: "https://api.x.ai/v1" },
  meta: { name: "Meta", endpoint: "https://api.meta.ai/v1" },
  openrouter: { name: "OpenRouter", endpoint: "https://openrouter.ai/api/v1" },
  zai: { name: "Z.ai", endpoint: "https://api.z.ai/api/paas/v4" },
  custom: { name: "", endpoint: "" },
};

export interface ProviderLike {
  id: string;
  name: string;
  endpoint: string;
}

export type TestOutcomeKind =
  | "connected"
  | "auth_failed"
  | "unreachable"
  | "catalog_unavailable"
  | "refused";

export interface BoundedTestResult {
  kind: TestOutcomeKind;
  message: string;
}

/**
 * Map a raw backend failure string (the discover catalog request failed)
 * to a bounded, user-safe connection-test result. Raw provider exceptions,
 * tracebacks, and internal details are never echoed.
 */
export function mapConnectionFailure(raw: unknown): BoundedTestResult {
  const text = (raw instanceof Error ? raw.message : String(raw ?? ""))
    .replace(/\s+/g, " ")
    .trim();
  // A provider-side CLIENT block (bot protection) is checked FIRST: its
  // status also contains "403", but the API key was never judged, and
  // telling the operator to replace a working key would be wrong.
  if (/bot protection|client signature|browser_signature|error 1010/i.test(text)) {
    return {
      kind: "unreachable",
      message: "The provider's bot protection blocked AMCA's request — the API key was not the problem.",
    };
  }
  if (/401|403|unauthorized|invalid.{0,20}key|authentication/i.test(text)) {
    return {
      kind: "auth_failed",
      message: "Authentication failed — the provider rejected the credential.",
    };
  }
  if (
    /urlopen error|10061|timed? ?out|timeout|unreachable|getaddrinfo|name or service|connection refused|failed to establish|network/i.test(
      text,
    )
  ) {
    return {
      kind: "unreachable",
      message: "Provider unreachable — check the endpoint URL and your connection.",
    };
  }
  if (/404|not found/i.test(text)) {
    return {
      kind: "catalog_unavailable",
      message: "The endpoint responded, but no model catalog was found at this URL.",
    };
  }
  if (/2 MB|expecting value|json|parse|catalog/i.test(text)) {
    return {
      kind: "catalog_unavailable",
      message: "The provider responded, but its model catalog could not be read.",
    };
  }
  return {
    kind: "refused",
    message: "The provider did not return a usable model catalog.",
  };
}

/**
 * Truthful provider status label. The backend distinguishes a saved
 * credential (live probed `configured`) from the endpoint state machine
 * (`endpoint_unverified` -> `connected_metadata_only` -> `catalog_ready`);
 * these are different facts and must not collapse into one word.
 */
export function providerStatusLabel(
  configured: boolean,
  status: string | null | undefined,
): string {
  if (!configured) return "Credential missing";
  switch (status) {
    case "catalog_ready":
      return "Connected · catalog ready";
    case "connected_metadata_only":
      return "Connected · metadata only";
    case "endpoint_unverified":
      return "Credential saved · endpoint unverified";
    default:
      return "Configured";
  }
}

/**
 * Find an existing provider connection that already uses the same
 * endpoint (case-insensitive). Duplicates for the same provider are
 * legitimate (two accounts); the caller only warns and confirms, using
 * the existing provider IDs — no new identity semantics.
 */
export function findSameEndpointProvider(
  providers: ProviderLike[],
  draft: { endpoint: string },
  editingProviderId: string | null,
): ProviderLike | null {
  const endpoint = draft.endpoint.trim().toLowerCase();
  if (!endpoint) return null;
  return (
    providers.find(
      (item) =>
        item.id !== editingProviderId &&
        (item.endpoint ?? "").trim().toLowerCase() === endpoint,
    ) ?? null
  );
}

/**
 * Which of a saved team's model references are no longer offered by the
 * current runtime source (deleted provider/profile, offline Ollama model,
 * etc.). Stale teams are shown truthfully; execution authority stays with
 * the backend's bounded participant validation.
 */
export function staleTeamMembers(
  modelRefs: string[],
  availableRefs: string[],
): string[] {
  const available = new Set(availableRefs);
  return modelRefs.filter((ref) => !available.has(ref));
}

// Z1 Model Hub MVP — routing candidates view. Maps the hub's EXISTING
// selectable model sources onto a flat candidate list so the user can see
// what manual selection offers today and what automatic routing chooses
// from. No new routing logic: the backend router stays authoritative.

export type RoutingCandidateSource = "local" | "byok" | "direct" | "amca_api";

export interface RoutingCandidate {
  ref: string;
  label: string;
  source: RoutingCandidateSource;
  providerLabel: string;
}

export interface RoutingCandidatesInput {
  localModels: Array<{ model: string }>;
  byokProviders: Array<{ id: string; name: string; model_ids: string[]; enabled_model_ids?: string[]; configured: boolean; enabled?: boolean }>;
  directModels: Array<{ credential_id: string; label: string; provider: string; configured: boolean }>;
  amcaApiModels: Array<{ logical_model_id?: string }>;
}

export function buildRoutingCandidates(input: RoutingCandidatesInput): RoutingCandidate[] {
  const candidates: RoutingCandidate[] = [];
  for (const model of input.localModels) {
    const ref = `ollama:${model.model}`;
    candidates.push({ ref, label: model.model, source: "local", providerLabel: "Ollama · local" });
  }
  for (const provider of input.byokProviders) {
    if (!provider.configured || provider.enabled === false) continue;
    // v1.1.8 Phase 1: routing candidates are the published models only.
    for (const modelId of provider.enabled_model_ids ?? []) {
      const ref = `${provider.id}:${modelId}`;
      candidates.push({ ref, label: modelId, source: "byok", providerLabel: provider.name });
    }
  }
  for (const model of input.directModels) {
    if (!model.configured) continue;
    candidates.push({ ref: model.credential_id, label: model.label, source: "direct", providerLabel: model.provider });
  }
  for (const model of input.amcaApiModels) {
    const id = typeof model.logical_model_id === "string" ? model.logical_model_id : "";
    if (!id) continue;
    candidates.push({ ref: id, label: id, source: "amca_api", providerLabel: "AMCA API" });
  }
  const seen = new Set<string>();
  return candidates.filter((candidate) => {
    if (seen.has(candidate.ref)) return false;
    seen.add(candidate.ref);
    return true;
  });
}

export const AUTO_ROUTING_OBJECTIVE = "Lowest cost among candidates that pass the quality and privacy gates";

