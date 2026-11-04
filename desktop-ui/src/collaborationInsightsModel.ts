// Z6 Collaboration Insights — pure presentation model (no fetch, no DOM).
// Derives the collaboration timeline, architecture visibility, decision
// trace, and cost/efficiency insights from EXISTING run documents only:
// RunJob (mode, provider/model, collaboration_decision, result.calls,
// result.task_cost, result.live_organization) and the nano registry.
// Honesty rule: a stage without recorded evidence renders as "not
// recorded"; nothing is fabricated and no usage metering is created.

import type { RunJob } from "./types";

// ---------- 1. Architecture visibility (order scope 2) ----------

export type ArchitectureMode =
  | "solo"
  | "joint"
  | "independent_discuss"
  | "parallel"
  | "adaptive_hybrid"
  | "unknown";

const ARCHITECTURE_LABELS: Record<ArchitectureMode, string> = {
  solo: "Solo",
  joint: "Joint",
  independent_discuss: "Independent → Discuss",
  parallel: "Parallel",
  adaptive_hybrid: "Adaptive Hybrid",
  unknown: "Unknown",
};

export function architectureMode(run: {
  mode: string;
  collaboration_decision?: { architecture?: { mode?: string } } | null;
}): ArchitectureMode {
  const decided = run.collaboration_decision?.architecture?.mode;
  const value = (decided || run.mode || "").toLowerCase();
  return (value in ARCHITECTURE_LABELS ? value : "unknown") as ArchitectureMode;
}

export function architectureLabel(mode: ArchitectureMode): string {
  return ARCHITECTURE_LABELS[mode];
}

// ---------- 2. Participants and model assignments (order scope 2) ----------
// The run document records the executed provider/model combination and
// per-call adapter identity. Nothing more is claimed.

export interface ParticipantRow {
  ref: string;
  provider: string;
  model: string;
  calls: number;
}

export interface CallRecordLike {
  adapter_id?: string;
  provider?: string;
  model?: string;
  status?: string;
  cost_usd?: number;
  summary?: string;
  error?: string;
}

export function participantsFromRun(run: RunJob): ParticipantRow[] {
  const calls = run.result?.calls;
  const rows = new Map<string, ParticipantRow>();
  if (Array.isArray(calls)) {
    for (const call of calls as CallRecordLike[]) {
      const provider = String(call.provider ?? "").trim();
      const model = String(call.model ?? "").trim();
      const ref = String(call.adapter_id ?? ([provider, model].filter(Boolean).join("/") || "unknown"));
      const key = `${ref}|${provider}|${model}`;
      const existing = rows.get(key);
      if (existing) existing.calls += 1;
      else rows.set(key, { ref, provider, model, calls: 1 });
    }
  }
  if (rows.size === 0) {
    const provider = run.provider || "unrecorded";
    const model = run.model || "unrecorded";
    rows.set(`${provider}|${model}`, { ref: "run", provider, model, calls: 0 });
  }
  return [...rows.values()];
}

// ---------- 3. Collaboration timeline (order scope 1) ----------
// The manager's stage chain. Each stage maps onto evidence the run
// document actually carries; missing evidence degrades that stage to
// "not recorded" instead of inventing content.

export interface TimelineStage {
  key: "request" | "planner" | "coordinator" | "workers" | "responses" | "synthesis";
  label: string;
  state: "recorded" | "not_recorded";
  detail: string;
}

export function collaborationTimeline(run: RunJob): TimelineStage[] {
  const result = run.result ?? {};
  const calls = Array.isArray(result.calls) ? (result.calls as CallRecordLike[]) : [];
  const liveOrg = result.live_organization as { tasks?: Record<string, unknown> } | null | undefined;
  const nanoTasks = liveOrg?.tasks ? Object.keys(liveOrg.tasks).length : 0;
  const decision = run.collaboration_decision;
  const summaries = calls.filter((call) => typeof call.summary === "string" && call.summary.trim());
  const taskCost = result.task_cost as Record<string, unknown> | undefined;

  return [
    {
      key: "request",
      label: "User request",
      state: "recorded",
      detail: run.goal || "Not recorded",
    },
    {
      key: "planner",
      label: "Planner",
      state: decision ? "recorded" : "not_recorded",
      detail: decision
        ? decision.model_set?.policy
          ? `Policy "${decision.model_set.policy}" · ${decision.eligible_model_count ?? "?"} eligible model${(decision.eligible_model_count ?? 0) === 1 ? "" : "s"}`
          : "Collaboration decision recorded"
        : "No collaboration decision document on this run",
    },
    {
      key: "coordinator",
      label: "Coordinator agent",
      state: decision?.decomposition ? "recorded" : "not_recorded",
      detail: decision?.decomposition
        ? `${decision.decomposition.work_unit_count ?? "?"} work unit${(decision.decomposition.work_unit_count ?? 0) === 1 ? "" : "s"} · ${decision.decomposition.basis ?? "basis not recorded"}`
        : "No decomposition record (single-unit execution or evidence not recorded)",
    },
    {
      key: "workers",
      label: "Nano workers",
      state: nanoTasks > 0 ? "recorded" : "not_recorded",
      detail: nanoTasks > 0
        ? `${nanoTasks} task${nanoTasks === 1 ? "" : "s"} in the measured live organization`
        : "No live organization tasks recorded",
    },
    {
      key: "responses",
      label: "Model responses",
      state: calls.length > 0 ? "recorded" : "not_recorded",
      detail: calls.length > 0
        ? `${calls.length} measured call${calls.length === 1 ? "" : "s"} (${summaries.length} with summaries)`
        : "No measured model calls recorded",
    },
    {
      key: "synthesis",
      label: "Final synthesis",
      state: summaries.length > 0 ? "recorded" : "not_recorded",
      detail: summaries.length > 0
        ? `${summaries.length} public summar${summaries.length === 1 ? "y" : "ies"} from measured calls${taskCost ? " · cost recorded" : ""}`
        : "No synthesis evidence recorded",
    },
  ];
}

// ---------- 4. Decision trace (order scope 3) ----------
// "Why AMCA chose this approach" — only fields that exist in the
// collaboration decision document; each trace line can be missing.

export interface DecisionTraceRow {
  label: string;
  value: string;
  recorded: boolean;
}

export function decisionTrace(run: RunJob): DecisionTraceRow[] {
  const decision = run.collaboration_decision;
  const arch = decision?.architecture;
  const modelSet = decision?.model_set;
  const shape = decision?.task_shape as Record<string, unknown> | undefined;
  const rows: DecisionTraceRow[] = [];

  const push = (label: string, value: string | number | undefined | null, format?: (input: string) => string) => {
    const raw = value === undefined || value === null ? "" : String(value).trim();
    rows.push({ label, value: raw ? (format ? format(raw) : raw) : "", recorded: Boolean(raw) });
  };

  push("Architecture", arch?.mode ?? run.mode);
  push("Architecture reason", arch?.policy_rule);
  push("Architecture triggers", Array.isArray(arch?.triggers) ? arch.triggers.join(" · ") : undefined);
  push("Requested by", arch?.requested_by);
  push("Model policy", modelSet?.policy);
  push("Model set", Array.isArray(modelSet?.model_refs) ? modelSet.model_refs.join(" · ") : undefined);
  push("Model choice rationale", modelSet?.rationale);
  push("Eligible models", decision?.eligible_model_count);
  push("Expected rounds", arch?.expected_rounds);
  push("Task complexity", typeof shape?.complexity === "string" ? shape.complexity : undefined);
  push("Coupled task", typeof shape?.coupled === "boolean" ? (shape.coupled ? "yes" : "no") : undefined);
  push("Decomposable", typeof shape?.decomposable === "boolean" ? (shape.decomposable ? "yes" : "no") : undefined);
  push("Verification heavy", typeof shape?.verification_heavy === "boolean" ? (shape.verification_heavy ? "yes" : "no") : undefined);
  push("Decomposition basis", decision?.decomposition?.basis);
  push("Work units", decision?.decomposition?.work_unit_count);
  push("Levels", decision?.decomposition?.levels);

  if (typeof arch?.degradation_reason === "string" && arch.degradation_reason.trim()) {
    rows.push({ label: "Degradation", value: arch.degradation_reason.trim(), recorded: true });
  }
  return rows;
}

export function recordedTraceCount(rows: DecisionTraceRow[]): number {
  return rows.filter((row) => row.recorded).length;
}

// ---------- 5. Cost / efficiency insight (order scope 4) ----------
// Existing cost fields only: result.task_cost (already computed by the
// runtime) plus the Z3-style free-local framing. No new metering.

export interface CostInsight {
  totalUsd: number | null;
  basis: "recorded" | "free_local" | "not_recorded";
  inputTokens: number | null;
  outputTokens: number | null;
  localCalls: number;
  cloudCalls: number;
  breakdown: Array<{ model: string; costUsd: number }>;
  line: string;
}

export function costInsight(run: RunJob): CostInsight {
  const result = run.result ?? {};
  const taskCost = result.task_cost as Record<string, unknown> | undefined;
  const calls = Array.isArray(result.calls) ? (result.calls as CallRecordLike[]) : [];

  const localCalls = calls.filter((call) => {
    const provider = String(call.provider ?? "").toLowerCase();
    return provider.includes("ollama") || provider.includes("local") || provider.includes("llama");
  }).length;
  const cloudCalls = calls.length - localCalls;

  if (taskCost && typeof taskCost === "object") {
    const total = Number(taskCost.total_external_api_cost);
    const breakdownRaw = Array.isArray(taskCost.breakdown) ? taskCost.breakdown : [];
    const breakdown = breakdownRaw
      .map((entry) => ({ model: String((entry as Record<string, unknown>).model ?? "unknown"), costUsd: Number((entry as Record<string, unknown>).cost_usd) }))
      .filter((entry) => Number.isFinite(entry.costUsd));
    const inputTokens = Number(taskCost.input_tokens);
    const outputTokens = Number(taskCost.output_tokens);
    return {
      totalUsd: Number.isFinite(total) ? total : null,
      basis: "recorded",
      inputTokens: Number.isFinite(inputTokens) ? inputTokens : null,
      outputTokens: Number.isFinite(outputTokens) ? outputTokens : null,
      localCalls,
      cloudCalls,
      breakdown,
      line: cloudCalls === 0 && total === 0
        ? "All measured calls ran locally — no external API charge recorded."
        : `Recorded task cost ${total.toFixed(4)} USD across ${cloudCalls} cloud call${cloudCalls === 1 ? "" : "s"}${localCalls ? ` and ${localCalls} local call${localCalls === 1 ? "" : "s"}` : ""}.`,
    };
  }

  const providerIsLocal = ["ollama", "local", "llama_cpp", "llama-cpp"].includes(String(run.provider).toLowerCase());
  return {
    totalUsd: null,
    basis: providerIsLocal ? "free_local" : "not_recorded",
    inputTokens: null,
    outputTokens: null,
    localCalls,
    cloudCalls,
    breakdown: [],
    line: providerIsLocal
      ? "Local run — no external API charge is possible."
      : "Cost evidence not recorded on this run.",
  };
}

// ---------- 6. Where the insight renders (order scope 1-4 placement) ----------
// Insights attach to the Agent Monitor's run-inspect pane — the same place
// run details already appear — so no duplicate run-detail surface is born.

export function insightsAvailable(run: RunJob): boolean {
  return Boolean(
    run.collaboration_decision ||
    (run.result && (
      Array.isArray(run.result.calls) ||
      run.result.task_cost ||
      run.result.live_organization
    )),
  );
}
