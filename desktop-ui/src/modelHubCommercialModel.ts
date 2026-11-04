// Z3 Commercial Model Hub — pure presentation model over the EXISTING
// /api/model-hub document and the EXISTING bounded provider-discovery
// endpoint. Nothing here creates a router, stores a secret, or invents a
// backend fact: connection status comes from the hub document only,
// pricing comes from the existing Pricing Registry / direct profiles only,
// and local runtimes without a status document are labelled as such.

export interface ByokProviderLike {
  id: string;
  provider_kind: string;
  name: string;
  model_ids: string[];
  /**
   * The subset the user published into the selectors.  Absent means nothing is
   * published: an unknown visibility state must never widen the selection.
   */
  enabled_model_ids?: string[];
  configured: boolean;
  enabled?: boolean;
}

export interface DirectModelLike {
  credential_id: string;
  provider: string;
  model: string;
  label: string;
  configured: boolean;
  input_cost_per_million: number;
  output_cost_per_million: number;
}

export interface LocalModelLike {
  model: string;
  size_bytes: number;
  compatibility: { status: string };
}

export interface HubDocumentLike {
  byok_providers: ByokProviderLike[];
  direct_models: DirectModelLike[];
  local_models: {
    provider: string;
    runtime_reachable?: boolean | null;
    models: LocalModelLike[];
  };
  amca_api: { models: unknown[] };
  pricing: PricingRecordLike[];
}

export interface PricingRecordLike {
  access_source?: string;
  provider?: string;
  logical_model_id?: string;
  input_per_1m?: number | null;
  output_per_1m?: number | null;
  status?: string;
  verified_at?: string;
}

// ---------- provider directory ----------

export type ProviderSourceType = "byok" | "managed" | "local";

export interface ProviderFamily {
  id: string;
  label: string;
  kind: string | null;
  matchers: RegExp[];
}

// The commercial families from the Z3 order, matched against the hub
// document's EXISTING provider_kind/name/provider fields. Qwen and GLM
// have no dedicated backend kind — they arrive through OpenAI-compatible
// BYOK connections or local Ollama models, so they match by name.
export const PROVIDER_FAMILIES: ProviderFamily[] = [
  { id: "openai", label: "GPT (OpenAI)", kind: "openai", matchers: [/openai/i, /\bgpt\b/i] },
  { id: "anthropic", label: "Claude (Anthropic)", kind: "anthropic", matchers: [/anthropic/i, /claude/i] },
  { id: "google", label: "Gemini (Google)", kind: "google", matchers: [/google/i, /gemini/i] },
  { id: "moonshot", label: "Kimi (Moonshot)", kind: "moonshot", matchers: [/moonshot/i, /kimi/i] },
  { id: "deepseek", label: "DeepSeek", kind: "deepseek", matchers: [/deepseek/i] },
  { id: "qwen", label: "Qwen", kind: null, matchers: [/qwen/i, /dashscope/i, /alibaba/i] },
  { id: "glm", label: "GLM (Zhipu)", kind: null, matchers: [/glm/i, /zhipu/i] },
  { id: "other", label: "Other providers", kind: null, matchers: [] },
];

export function familyForProviderName(name: string): string {
  for (const family of PROVIDER_FAMILIES) {
    if (family.matchers.some((matcher) => matcher.test(name))) return family.id;
  }
  return "other";
}

export interface ProviderTreeRow {
  familyId: string;
  label: string;
  sourceType: ProviderSourceType;
  connected: boolean;
  connectionLabel: string;
  modelsAvailable: number;
  detail: string;
}

export interface ProviderDirectory {
  providers: ProviderTreeRow[];
  runtimes: ProviderTreeRow[];
}

/**
 * Provider directory over the EXISTING hub document: one row per
 * commercial family (aggregating BYOK connections and configured direct
 * profiles) plus one row per local runtime. llama.cpp / vLLM / SGLang are
 * reachable by the execution engine, but Model Hub has no status document
 * for them — the rows say exactly that instead of inventing a status.
 */
export function buildProviderDirectory(hub: HubDocumentLike): ProviderDirectory {
  const providers: ProviderTreeRow[] = PROVIDER_FAMILIES.map((family) => {
    const byok = hub.byok_providers.filter((provider) =>
      family.kind
        ? provider.provider_kind === family.kind
        : family.id !== "other" && familyForProviderName(provider.name) === family.id,
    );
    const direct = family.kind
      ? hub.direct_models.filter((model) => model.configured && familyForProviderName(model.provider) === family.id)
      : hub.direct_models.filter(
          (model) => model.configured && family.id !== "other" && familyForProviderName(model.provider) === family.id,
        );
    const modelsAvailable = byok.reduce((sum, provider) => sum + provider.model_ids.length, 0) + direct.length;
    const connected = byok.some((provider) => provider.configured) || direct.length > 0;
    return {
      familyId: family.id,
      label: family.label,
      sourceType: "byok" as const,
      connected,
      connectionLabel: connected ? "Connected" : "No connection yet",
      modelsAvailable,
      detail: `${byok.length} BYOK connection${byok.length === 1 ? "" : "s"} · ${direct.length} direct profile${direct.length === 1 ? "" : "s"}`,
    };
  });

  const ollamaReachable = hub.local_models.runtime_reachable;
  const runtimes: ProviderTreeRow[] = [
    {
      familyId: "ollama",
      label: "Ollama",
      sourceType: "local",
      connected: ollamaReachable === true,
      connectionLabel:
        ollamaReachable === true ? "Reachable" : ollamaReachable === false ? "Unreachable" : "Status unknown",
      modelsAvailable: hub.local_models.models.length,
      detail: "Catalog from the existing Ollama integration",
    },
    {
      familyId: "llama_cpp",
      label: "llama.cpp",
      sourceType: "local",
      connected: false,
      connectionLabel: "Not reported",
      modelsAvailable: 0,
      detail: "The engine can target llama.cpp endpoints, but Model Hub has no status document for it yet.",
    },
    {
      familyId: "vllm_sglang",
      label: "vLLM / SGLang",
      sourceType: "local",
      connected: false,
      connectionLabel: "Not reported",
      modelsAvailable: 0,
      detail: "The engine can target vLLM/SGLang endpoints, but Model Hub has no status document for them yet.",
    },
  ];

  return { providers, runtimes };
}

// ---------- secret masking ----------

const MASK = "••••••";

/**
 * Display-safe secret preview: a fixed mask, plus at most the final four
 * characters so the user can tell which key is entered. The full secret
 * is never part of the output, and short secrets reveal nothing.
 */
export function maskSecret(secret: string): string {
  if (!secret) return "";
  if (secret.length < 8) return MASK;
  return MASK + secret.slice(-4);
}

// ---------- selection groups ----------

export interface SelectionOption {
  ref: string;
  label: string;
  source: "local" | "byok" | "direct" | "amca_api";
}

export interface SelectionGroup {
  familyId: string;
  label: string;
  options: SelectionOption[];
}

const GROUP_ORDER = ["openai", "anthropic", "google", "moonshot", "deepseek", "qwen", "glm", "other", "amca_api", "ollama"];

/**
 * Manual-strategy options grouped by provider family, built from the same
 * EXISTING selectable sources the flat list used (local Ollama models,
 * configured BYOK model_ids, configured direct profiles, AMCA API
 * logical models). Refs are identical to the previous flat list, so the
 * governed run payload does not change.
 */
export function selectionGroups(hub: HubDocumentLike): SelectionGroup[] {
  const groups = new Map<string, SelectionGroup>();
  const push = (familyId: string, option: SelectionOption) => {
    const existing = groups.get(familyId);
    if (existing) {
      if (!existing.options.some((item) => item.ref === option.ref)) existing.options.push(option);
    } else {
      const family = PROVIDER_FAMILIES.find((item) => item.id === familyId);
      groups.set(familyId, { familyId, label: family ? family.label : "AMCA API (managed)", options: [option] });
    }
  };

  for (const provider of hub.byok_providers) {
    if (!provider.configured || provider.enabled === false) continue;
    const familyId = familyForProviderName(provider.provider_kind === "openai_compatible" ? provider.name : provider.provider_kind);
    // v1.1.8 Phase 1: only models the user published are offered for selection.
    for (const modelId of provider.enabled_model_ids ?? []) {
      push(familyId, { ref: `${provider.id}:${modelId}`, label: modelId, source: "byok" });
    }
  }
  for (const model of hub.direct_models) {
    if (!model.configured) continue;
    push(familyForProviderName(model.provider), { ref: model.credential_id, label: model.label, source: "direct" });
  }
  for (const entry of hub.amca_api.models) {
    const id = typeof (entry as { logical_model_id?: string }).logical_model_id === "string"
      ? (entry as { logical_model_id: string }).logical_model_id
      : "";
    if (id) push("amca_api", { ref: id, label: id, source: "amca_api" });
  }
  for (const model of hub.local_models.models) {
    push("ollama", { ref: `ollama:${model.model}`, label: model.model, source: "local" });
  }

  return [...groups.values()]
    .sort((a, b) => GROUP_ORDER.indexOf(a.familyId) - GROUP_ORDER.indexOf(b.familyId))
    .map((group) => ({ ...group, options: [...group.options].sort((a, b) => a.ref.localeCompare(b.ref)) }));
}

// ---------- cost visibility ----------

export type RateBasis = "free_local" | "direct_profile" | "pricing_registry" | "unknown";

export interface RateCard {
  ref: string;
  basis: RateBasis;
  provider: string | null;
  inputPer1m: number | null;
  outputPer1m: number | null;
}

/**
 * Resolve a per-million rate card for a selectable model ref using only
 * EXISTING price sources: local models are free by definition; direct
 * profiles carry their own rates; everything else consults the existing
 * Pricing Registry (latest verified record for the logical model).
 * No rates are invented — unknown stays unknown.
 */
export function findRateCard(ref: string, hub: HubDocumentLike): RateCard {
  if (ref.startsWith("ollama:")) {
    return { ref, basis: "free_local", provider: "Ollama · local", inputPer1m: 0, outputPer1m: 0 };
  }
  const direct = hub.direct_models.find((model) => model.credential_id === ref && model.configured);
  if (direct) {
    return {
      ref,
      basis: "direct_profile",
      provider: direct.provider,
      inputPer1m: direct.input_cost_per_million,
      outputPer1m: direct.output_cost_per_million,
    };
  }
  const modelId = ref.includes(":") ? ref.slice(ref.indexOf(":") + 1) : ref;
  const records = hub.pricing
    .filter((record) => record.logical_model_id === modelId && typeof record.input_per_1m === "number")
    .sort((a, b) => String(b.verified_at ?? "").localeCompare(String(a.verified_at ?? "")));
  const record = records[0];
  if (record) {
    return {
      ref,
      basis: "pricing_registry",
      provider: typeof record.provider === "string" ? record.provider : null,
      inputPer1m: record.input_per_1m ?? null,
      outputPer1m: typeof record.output_per_1m === "number" ? record.output_per_1m : null,
    };
  }
  return { ref, basis: "unknown", provider: null, inputPer1m: null, outputPer1m: null };
}

export interface RunCostEstimate {
  inputUsd: number;
  outputUsd: number;
  totalUsd: number;
}

/**
 * Deterministic token-cost math over a rate card: (tokens / 1e6) * rate.
 * Negative token counts are clamped to zero; nothing else is assumed.
 */
export function estimateRunCost(
  inputTokens: number,
  outputTokens: number,
  card: Pick<RateCard, "inputPer1m" | "outputPer1m">,
): RunCostEstimate | null {
  if (card.inputPer1m === null || card.outputPer1m === null) return null;
  const inputs = Math.max(0, inputTokens) / 1_000_000;
  const outputs = Math.max(0, outputTokens) / 1_000_000;
  const inputUsd = inputs * card.inputPer1m;
  const outputUsd = outputs * card.outputPer1m;
  return { inputUsd, outputUsd, totalUsd: inputUsd + outputUsd };
}

/** Bounded money format: tiny estimates keep 6 decimals, large ones 2. */
export function formatUsdCost(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (value === 0) return "$0.00";
  if (value < 0.01) return `$${value.toFixed(6)}`;
  if (value < 1) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

// ---------- health testing ----------

export type HealthState = "pass" | "fail" | "not_assessed";

export interface HealthCheck {
  key: string;
  label: string;
  state: HealthState;
  detail: string;
}

export interface DiscoverOutcome {
  ok: boolean;
  catalogCount: number;
  elapsedMs: number;
  failureKind: string | null;
}

/**
 * The four read-only health checks from the Z3 order, derived ONLY from
 * the existing bounded catalog-discovery outcome plus the client-measured
 * elapsed time. A failed connection leaves the remaining checks
 * "not assessed" rather than guessed.
 */
export function healthChecksFromDiscover(outcome: DiscoverOutcome): HealthCheck[] {
  return [
    {
      key: "reachable",
      label: "Reachable",
      state: outcome.ok ? "pass" : "fail",
      detail: outcome.ok ? "Catalog request succeeded" : "Catalog request failed",
    },
    {
      key: "authentication",
      label: "Authentication",
      state: outcome.ok ? "pass" : outcome.failureKind === "auth_failed" ? "fail" : "not_assessed",
      detail: outcome.ok
        ? "Credential accepted"
        : outcome.failureKind === "auth_failed"
          ? "Credential rejected"
          : "Not assessed — connection failed first",
    },
    {
      key: "latency",
      label: "Response latency",
      state: outcome.ok ? "pass" : "not_assessed",
      detail: outcome.ok ? `${Math.max(0, Math.round(outcome.elapsedMs))} ms` : "No response measured",
    },
    {
      key: "context",
      label: "Context available",
      state: outcome.ok ? (outcome.catalogCount > 0 ? "pass" : "fail") : "not_assessed",
      detail: outcome.ok
        ? `${outcome.catalogCount} model${outcome.catalogCount === 1 ? "" : "s"} listed in catalog`
        : "Catalog not retrieved",
    },
  ];
}

/**
 * Read-only health view for a LOCAL runtime: reachability and installed
 * model count come from the existing Ollama catalog document. Local
 * runtimes need no credential, and no request is made, so those checks
 * are explicitly "not assessed" instead of fabricated.
 */
export function localHealthChecks(reachable: boolean | null | undefined, modelCount: number): HealthCheck[] {
  return [
    {
      key: "reachable",
      label: "Reachable",
      state: reachable === true ? "pass" : reachable === false ? "fail" : "not_assessed",
      detail: reachable === true ? "Runtime responded to the last scan" : reachable === false ? "Runtime unreachable at last scan" : "No scan recorded",
    },
    {
      key: "authentication",
      label: "Authentication",
      state: "not_assessed",
      detail: "Local runtimes need no credential",
    },
    {
      key: "latency",
      label: "Response latency",
      state: "not_assessed",
      detail: "No request measured",
    },
    {
      key: "context",
      label: "Context available",
      state: modelCount > 0 ? "pass" : "fail",
      detail: modelCount > 0 ? `${modelCount} model${modelCount === 1 ? "" : "s"} installed` : "No models installed",
    },
  ];
}
