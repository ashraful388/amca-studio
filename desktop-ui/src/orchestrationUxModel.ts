// BETA-B4 Model Orchestration UX — pure presentation model (no fetch, no
// DOM). Renders the EXISTING orchestration records: the collaboration
// decision document (model_set, task_shape, signals), the run's measured
// calls, and the local model catalog's recorded measurements. Honesty
// rules: capability dimensions with no recorded data render "unknown";
// explanations cite the record they came from; no hidden model reasoning
// (chain-of-thought) is ever displayed — only the structured decision
// document the runtime already persisted.

import type { RunJob } from "./types";

// ---------- 1. Decision transparency (order scope 1) ----------

export interface DecisionFactor {
  label: string;
  value: string;
}

export interface DecisionPanelView {
  selectedRefs: string[];
  policy: string;
  locality: "local" | "cloud" | "mixed" | "unknown";
  factors: DecisionFactor[];
  alternativesCount: number | null;
  expectedCostUsd: number | null;
  rationale: string | null;
  hasDecision: boolean;
}

export interface OrchestrationDecisionLike {
  model_set?: { policy?: string; model_refs?: string[]; rationale?: string; expected_cost_usd?: number | null };
  eligible_model_count?: number;
  task_shape?: Record<string, unknown>;
  signals?: Record<string, unknown>;
  architecture?: Record<string, unknown>;
}

const LOCAL_PROVIDER_HINTS = ["ollama", "local", "llama", "vllm", "sglang"];

export function refLocality(ref: string): "local" | "cloud" | "unknown" {
  const lowered = ref.toLowerCase();
  return LOCAL_PROVIDER_HINTS.some((hint) => lowered.includes(hint)) ? "local" : "unknown";
}

export function decisionLocality(refs: string[]): DecisionPanelView["locality"] {
  if (!refs.length) return "unknown";
  const localities = refs.map(refLocality);
  if (localities.every((item) => item === "local")) return "local";
  if (localities.every((item) => item === "unknown")) return "unknown";
  return "mixed";
}

function shapeLabel(shape: Record<string, unknown> | undefined, key: string, yes: string, no: string): string | null {
  const value = shape?.[key];
  if (typeof value !== "boolean") return null;
  return value ? yes : no;
}

export function decisionPanel(run: RunJob): DecisionPanelView {
  const decision = run.collaboration_decision as OrchestrationDecisionLike | null | undefined;
  const modelSet = decision?.model_set;
  const refs = Array.isArray(modelSet?.model_refs) ? modelSet.model_refs : [];
  const shape = decision?.task_shape;
  const signals = decision?.signals;
  const factors: DecisionFactor[] = [];
  const push = (label: string, value: string | null) => {
    if (value) factors.push({ label, value });
  };
  push("Task complexity", typeof shape?.["complexity"] === "string" ? String(shape["complexity"]) : null);
  push("Coupled task", shapeLabel(shape, "coupled", "yes", "no"));
  push("Decomposable", shapeLabel(shape, "decomposable", "yes", "no"));
  push("Verification heavy", shapeLabel(shape, "verification_heavy", "yes", "no"));
  push("Eligible models", decision?.eligible_model_count != null ? String(decision.eligible_model_count) : null);
  push("Critical objection flag", typeof signals?.["has_critical_objection"] === "boolean" ? (signals["has_critical_objection"] ? "yes" : "no") : null);
  push("Parallel conflict flag", typeof signals?.["parallel_conflict"] === "boolean" ? (signals["parallel_conflict"] ? "yes" : "no") : null);
  const expected = modelSet?.expected_cost_usd;
  return {
    selectedRefs: refs,
    policy: modelSet?.policy ?? run.mode,
    locality: decisionLocality(refs),
    factors,
    alternativesCount: decision?.eligible_model_count ?? null,
    expectedCostUsd: typeof expected === "number" && Number.isFinite(expected) ? expected : null,
    rationale: typeof modelSet?.rationale === "string" && modelSet.rationale.trim() ? modelSet.rationale.trim() : null,
    hasDecision: Boolean(decision),
  };
}

// ---------- 2. Routing explanations (order scope 3) ----------
// Every explanation is derived from one recorded field and names it.
// Privacy-safe: the structured decision document only — never generated
// model reasoning.

export interface RoutingExplanation {
  sentence: string;
  evidence: string;
}

export function routingExplanations(run: RunJob): RoutingExplanation[] {
  const decision = run.collaboration_decision as OrchestrationDecisionLike | null | undefined;
  const modelSet = decision?.model_set;
  const arch = decision?.architecture as Record<string, unknown> | undefined;
  const explanations: RoutingExplanation[] = [];
  const policy = modelSet?.policy ?? "";
  const refs = Array.isArray(modelSet?.model_refs) ? modelSet.model_refs : [];
  const allLocal = refs.length > 0 && refs.every((ref) => refLocality(ref) === "local");

  if (policy === "local_only") {
    explanations.push({ sentence: "Local models only — the run's policy excludes network models entirely.", evidence: "model_set.policy" });
  } else if (policy === "local_preferred" && allLocal) {
    explanations.push({ sentence: "Local model selected to reduce cost — the run's policy prefers local runtimes.", evidence: "model_set.policy" });
  } else if (policy === "auto_intelligence") {
    explanations.push({ sentence: "Selected because it matched the required capability at the lowest cost among eligible models.", evidence: "model_set.policy" });
  } else if (policy === "manual_only") {
    explanations.push({ sentence: "Models were chosen explicitly for this run.", evidence: "model_set.policy" });
  } else if (policy) {
    explanations.push({ sentence: `Selection followed the "${policy.replaceAll("_", " ")}" policy recorded for this run.`, evidence: "model_set.policy" });
  }

  if (typeof modelSet?.rationale === "string" && modelSet.rationale.trim()) {
    explanations.push({ sentence: modelSet.rationale.trim(), evidence: "model_set.rationale" });
  }

  const degradation = typeof arch?.["degradation_reason"] === "string" ? arch["degradation_reason"].trim() : "";
  if (degradation) {
    explanations.push({ sentence: `Fallback used because: ${degradation}`, evidence: "architecture.degradation_reason" });
  }

  if (!explanations.length) {
    explanations.push({ sentence: "Routing decision factors were not recorded for this run.", evidence: "none" });
  }
  return explanations;
}

// ---------- 3. Capability visualization (order scope 2) ----------
// Only recorded data; every dimension without a record renders "unknown".

export interface CapabilityEntry {
  ref: string;
  provider: string | null;
  parameterSize: string | null;
  quantization: string | null;
  tokensPerSecond: number | null;
}

export type CapabilityValue = string;

export interface CapabilityRow {
  ref: string;
  locality: "local" | "unknown" | "cloud";
  contextWindow: CapabilityValue;
  toolSupport: CapabilityValue;
  visionSupport: CapabilityValue;
  speedTier: CapabilityValue;
  parameters: CapabilityValue;
}

const UNKNOWN = "unknown";

function speedTier(tokensPerSecond: number | null): CapabilityValue {
  if (tokensPerSecond === null || !Number.isFinite(tokensPerSecond) || tokensPerSecond <= 0) return UNKNOWN;
  if (tokensPerSecond >= 40) return "fast (≥40 tok/s measured)";
  if (tokensPerSecond >= 10) return "moderate (10–40 tok/s measured)";
  return "slow (<10 tok/s measured)";
}

export function capabilityRows(entries: CapabilityEntry[]): CapabilityRow[] {
  return entries.map((entry) => ({
    ref: entry.ref,
    locality: refLocality(`${entry.provider ?? ""} ${entry.ref}`),
    contextWindow: UNKNOWN,
    toolSupport: UNKNOWN,
    visionSupport: UNKNOWN,
    speedTier: speedTier(entry.tokensPerSecond),
    parameters: entry.parameterSize ?? UNKNOWN,
  }));
}

// ---------- 4. Cost transparency lines (order scope 4) ----------

export interface CostTransparency {
  ref: string;
  line: string;
}

export function costTransparencyLines(refs: string[]): CostTransparency[] {
  return refs.map((ref) => ({
    ref,
    line: refLocality(ref) === "local"
      ? `${ref} runs locally via Ollama — $0 external API charge, no network call.`
      : `${ref} is a cloud/network model — cost depends on the provider's verified pricing; unknown pricing is never estimated.`,
  }));
}
