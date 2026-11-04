// AMCA v0.6.0 M2.2 Studio Cost & Budget UI - pure presentation model.
//
// All state derivation, formatting, and validation for the Cost &
// Budget panel lives here as pure functions so the surface stays
// unit-testable without a browser. No probing, no fetching, no
// execution: this module only shapes data that the v0.6.0 M2
// cost/budget backend already provides (GET /api/cost/state,
// GET/POST /api/cost/budget).

// ---------------------------------------------------------------------------
// Wire-shape mirrors of the v0.6.0 M2 /api/cost/* responses.
// These are independent of the backend types so the panel can be
// unit-tested without importing the full AMCA backend.
// ---------------------------------------------------------------------------

export interface CostStateWire {
  run_id: string | null;
  project_id: string | null;
  currency: string;
  total_known_cost_usd: number;
  total_unknown_calls: number;
  total_calls: number;
  total_local_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  per_model_cost_usd: Record<string, number>;
  project_aggregate_known_cost_usd: number | null;
  project_aggregate_unknown_calls: number | null;
  run_budget_usd: number | null;
  project_budget_usd: number | null;
  remaining_run_budget_usd: number | null;
  remaining_project_budget_usd: number | null;
  warning_threshold_usd: number | null;
  strict_monetary: boolean;
  known: boolean;
}

export interface BudgetPolicyWire {
  run_budget_usd: number | null;
  project_budget_usd: number | null;
  warning_threshold_usd: number | null;
  strict_monetary: boolean;
}

export interface BudgetResponseWire {
  policy: BudgetPolicyWire | null;
}

export interface BudgetDraft {
  run_budget_usd: number | null;
  project_budget_usd: number | null;
  warning_threshold_usd: number | null;
  strict_monetary: boolean;
}

export type BudgetHardness = "off" | "soft" | "hard";

// ---------------------------------------------------------------------------
// Budget hardness derivation (drives ALLOW / WARN / BLOCK UX).
// The panel does NOT recompute monetary policy; it only renders the
// backend-authoritative state. It does, however, classify a draft
// into "off / soft / hard" for the toggle UI, so the user can
// preview the meaning of a toggle before submitting.
// ---------------------------------------------------------------------------

export function budgetHardness(draft: BudgetDraft): BudgetHardness {
  if (draft.run_budget_usd === null || draft.run_budget_usd === 0) {
    return "off";
  }
  return draft.strict_monetary ? "hard" : "soft";
}

export function budgetHardnessLabel(hardness: BudgetHardness): string {
  switch (hardness) {
    case "off":
      return "No cost cap recorded";
    case "soft":
      return "Cap recorded — flags threshold (never blocks)";
    case "hard":
      return "Strict cap recorded — flags overflow (never blocks)";
  }
}

export function budgetHardnessDescription(hardness: BudgetHardness): string {
  switch (hardness) {
    case "off":
      return "Cost is recorded as telemetry only; nothing is warned or flagged. Provider and privacy authorization still govern every call.";
    case "soft":
      return "AMCA flags cost at the configured threshold. Budget figures are telemetry: they never block model execution.";
    case "hard":
      return "AMCA flags any billable call whose known cost would exceed the recorded cap. Budget figures are telemetry: they never block model execution, and calls with unknown prices still run with cost recorded as unknown.";
  }
}

// ---------------------------------------------------------------------------
// Money / token formatters.
// ---------------------------------------------------------------------------

const USD_FORMATTER = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});

const USD_COMPACT_FORMATTER = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const TOKEN_FORMATTER = new Intl.NumberFormat("en-US");

export function formatMoneyUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  if (value === 0) {
    return "$0.00";
  }
  if (value < 0.01) {
    return USD_FORMATTER.format(value);
  }
  return USD_COMPACT_FORMATTER.format(value);
}

export function formatTokens(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return TOKEN_FORMATTER.format(value);
}

// ---------------------------------------------------------------------------
// Cost classification (Known / Local / Unknown).
// This is the heart of the "known vs unknown" distinction the work
// order calls "extremely clear".
// ---------------------------------------------------------------------------

export type CostCategory = "known" | "local" | "unknown" | "none";

export interface CostClassification {
  category: CostCategory;
  headline: string;
  detail: string;
}

export function classifyRunCost(state: CostStateWire): CostClassification {
  if (state.total_calls === 0) {
    return {
      category: "none",
      headline: "No cost records yet",
      detail: "Once AMCA records a model call, the per-run cost summary appears here.",
    };
  }
  if (state.total_local_calls === state.total_calls && state.total_known_cost_usd === 0) {
    return {
      category: "local",
      headline: "Local-only run",
      detail: "Every recorded call in this run was a local model (e.g. Ollama). API cost is $0.00. AMCA does not measure electricity or hardware cost.",
    };
  }
  if (state.total_unknown_calls > 0 && state.total_known_cost_usd === 0) {
    return {
      category: "unknown",
      headline: "Unknown API price",
      detail: "AMCA has not yet resolved a price for one or more API models. The recorded cost is shown as Unknown, not as $0.00. These calls still run; the unknown price is recorded as unknown telemetry.",
    };
  }
  if (state.total_unknown_calls > 0) {
    return {
      category: "known",
      headline: "Mixed: known + unknown",
      detail: `Known API cost is ${formatMoneyUsd(state.total_known_cost_usd)} across ${state.total_calls - state.total_unknown_calls} priced call${state.total_calls - state.total_unknown_calls === 1 ? "" : "s"}. ${state.total_unknown_calls} call${state.total_unknown_calls === 1 ? "" : "s"} still has an unknown price.`,
    };
  }
  return {
    category: "known",
    headline: "Known API cost",
    detail: `Recorded cost across ${state.total_calls} call${state.total_calls === 1 ? "" : "s"}.`,
  };
}

// ---------------------------------------------------------------------------
// Budget status derivation.
// Renders the backend-authoritative remaining / status values into a
// stable, human-readable shape.
// ---------------------------------------------------------------------------

export type BudgetStatus = "ok" | "warning" | "blocked" | "unknown" | "no-policy";

export interface BudgetStatusShape {
  status: BudgetStatus;
  remaining_run_label: string;
  remaining_project_label: string;
  warning: boolean;
  has_policy: boolean;
  detail: string;
}

const fmtUsdMaybe = (value: number | null): string => {
  if (value === null) {
    return "—";
  }
  return formatMoneyUsd(value);
};

export function deriveBudgetStatus(state: CostStateWire): BudgetStatusShape {
  const has_policy =
    state.run_budget_usd !== null ||
    state.project_budget_usd !== null ||
    state.warning_threshold_usd !== null ||
    state.strict_monetary;
  const remainingRun = state.remaining_run_budget_usd;
  const remainingProject = state.remaining_project_budget_usd;
  const warning = state.warning_threshold_usd !== null &&
    (state.total_known_cost_usd >= state.warning_threshold_usd);
  // Blocked state: the backend would surface a structured
  // `BudgetBlockReason` only when the runner actually attempts a
  // call. The read API alone does not carry a block reason, so we
  // use the post-call remaining to derive a *historical* "blocked"
  // signal only when remaining has gone negative — which the
  // backend never returns; remaining is non-negative by contract.
  // Therefore "blocked" is reserved for the unknown-cost case.
  let status: BudgetStatus;
  if (!has_policy) {
    status = "no-policy";
  } else if (state.strict_monetary && state.total_unknown_calls > 0) {
    status = "blocked";
  } else if (warning) {
    status = "warning";
  } else if (state.total_known_cost_usd > 0) {
    status = "ok";
  } else {
    status = "unknown";
  }
  let detail: string;
  switch (status) {
    case "no-policy":
      detail = "No session cost cap is set. Cost is still recorded as telemetry.";
      break;
    case "ok":
      detail = "Spent cost is within the configured run/project cap.";
      break;
    case "warning":
      detail = "Spent cost has reached the configured warning threshold.";
      break;
    case "blocked":
      detail = "One or more API models have an unknown price; the call runs and its cost is recorded as unknown telemetry.";
      break;
    case "unknown":
      detail = "A budget is configured but no cost has been recorded yet.";
      break;
  }
  return {
    status,
    remaining_run_label: fmtUsdMaybe(remainingRun),
    remaining_project_label: fmtUsdMaybe(remainingProject),
    warning,
    has_policy,
    detail,
  };
}

// ---------------------------------------------------------------------------
// Draft validation (client-side; backend is still authoritative).
// Mirrors amca/cost_routes._parse_budget_payload semantics so a user
// sees a clear inline error before the round-trip.
// ---------------------------------------------------------------------------

export interface DraftValidationError {
  field: "run_budget_usd" | "project_budget_usd" | "warning_threshold_usd" | "strict_monetary";
  message: string;
}

export function validateBudgetDraft(draft: BudgetDraft): DraftValidationError[] {
  const errors: DraftValidationError[] = [];
  for (const field of [
    "run_budget_usd",
    "project_budget_usd",
    "warning_threshold_usd",
  ] as const) {
    const value = draft[field];
    if (value === null) {
      continue;
    }
    if (!Number.isFinite(value) || value < 0) {
      errors.push({ field, message: "Must be a non-negative number." });
      continue;
    }
    if (value > 1_000_000) {
      errors.push({ field, message: "Must be ≤ 1,000,000 USD." });
    }
  }
  if (
    draft.warning_threshold_usd !== null &&
    draft.run_budget_usd !== null &&
    draft.warning_threshold_usd > draft.run_budget_usd
  ) {
    errors.push({
      field: "warning_threshold_usd",
      message: "Warning threshold cannot exceed the run budget.",
    });
  }
  if (typeof draft.strict_monetary !== "boolean") {
    errors.push({
      field: "strict_monetary",
      message: "Must be a boolean.",
    });
  }
  return errors;
}

// ---------------------------------------------------------------------------
// Empty-state classifier (no records yet).
// ---------------------------------------------------------------------------

export function hasAnyRecords(state: CostStateWire | null | undefined): boolean {
  if (!state) {
    return false;
  }
  return state.total_calls > 0 || state.total_known_cost_usd > 0;
}

// ---------------------------------------------------------------------------
// Render-time helpers (kept pure for unit tests).
// ---------------------------------------------------------------------------

export function classifyModelRow(
  modelId: string,
  cost: number,
): {
  label: string;
  category: CostCategory;
} {
  const lowered = modelId.toLowerCase();
  if (
    lowered.startsWith("ollama") ||
    lowered.startsWith("local") ||
    lowered.startsWith("deterministic") ||
    lowered.startsWith("fallback") ||
    lowered.startsWith("stub")
  ) {
    return {
      label: `${modelId} · local (no API charge)`,
      category: "local",
    };
  }
  if (cost === 0) {
    return {
      label: `${modelId} · price not yet known`,
      category: "unknown",
    };
  }
  return {
    label: `${modelId} · API call`,
    category: "known",
  };
}

export function totalTokens(state: CostStateWire): number {
  return state.total_input_tokens + state.total_output_tokens;
}

export function formatPerModelRow(
  modelId: string,
  cost: number,
): string {
  const cls = classifyModelRow(modelId, cost);
  return `${cls.label} · ${formatMoneyUsd(cost)}`;
}

// ---------------------------------------------------------------------------
// Session-only truthfulness wording.
// The M2.1 closeout explicitly does NOT persist project budget
// configuration. The UI must say so truthfully.
// ---------------------------------------------------------------------------

export const SESSION_ONLY_BUDGET_NOTICE =
  "Recorded cost caps apply to the current AMCA session and reset after restart.";

// ---------------------------------------------------------------------------
// 28B: unknown-cost message. Budget figures are telemetry only, so this
// states the recorded-cost behavior instead of implying a block.
// ---------------------------------------------------------------------------

export const UNKNOWN_COST_TELEMETRY_MESSAGE =
  "This model's API price is unknown, so its cost is recorded as unknown telemetry; budget never blocks the call.";
