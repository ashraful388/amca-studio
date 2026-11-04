// AMCA v0.6.0 M2.2 unit tests for the Cost & Budget panel model.
//
// Pure functions only - no browser, no fetch, no backend. Run with:
//   node --test tests/costBudgetPanelModel.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import {
  SESSION_ONLY_BUDGET_NOTICE,
  UNKNOWN_COST_TELEMETRY_MESSAGE,
  budgetHardness,
  budgetHardnessDescription,
  budgetHardnessLabel,
  classifyModelRow,
  classifyRunCost,
  deriveBudgetStatus,
  formatMoneyUsd,
  formatPerModelRow,
  formatTokens,
  hasAnyRecords,
  totalTokens,
  validateBudgetDraft,
} from "../src/costBudgetPanelModel.ts";

import { readFileSync } from "node:fs";

const PANEL_SOURCE_PATH = new URL(
  "../src/costBudgetPanel.tsx",
  import.meta.url,
).pathname.replace(/^\/([A-Za-z]:)/, "$1");

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function emptyState(overrides = {}) {
  return {
    run_id: null,
    project_id: null,
    currency: "USD",
    total_known_cost_usd: 0,
    total_unknown_calls: 0,
    total_calls: 0,
    total_local_calls: 0,
    total_input_tokens: 0,
    total_output_tokens: 0,
    per_model_cost_usd: {},
    project_aggregate_known_cost_usd: null,
    project_aggregate_unknown_calls: null,
    run_budget_usd: null,
    project_budget_usd: null,
    remaining_run_budget_usd: null,
    remaining_project_budget_usd: null,
    warning_threshold_usd: null,
    strict_monetary: false,
    known: true,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

test("formatMoneyUsd handles null/NaN", () => {
  assert.equal(formatMoneyUsd(null), "—");
  assert.equal(formatMoneyUsd(undefined), "—");
  assert.equal(formatMoneyUsd(NaN), "—");
  assert.equal(formatMoneyUsd(0), "$0.00");
  assert.equal(formatMoneyUsd(0.0001), "$0.0001");
  assert.equal(formatMoneyUsd(1.234), "$1.23");
});

test("formatTokens handles null/NaN", () => {
  assert.equal(formatTokens(null), "—");
  assert.equal(formatTokens(undefined), "—");
  assert.equal(formatTokens(NaN), "—");
  assert.equal(formatTokens(1000), "1,000");
  assert.equal(formatTokens(1234567), "1,234,567");
});

// ---------------------------------------------------------------------------
// Cost classification: known / local / unknown
// ---------------------------------------------------------------------------

test("classifyRunCost: empty state has no records", () => {
  const c = classifyRunCost(emptyState());
  assert.equal(c.category, "none");
  assert.match(c.headline, /No cost records yet/);
});

test("classifyRunCost: all-local run reports $0 API cost", () => {
  const c = classifyRunCost(emptyState({
    total_calls: 3,
    total_local_calls: 3,
    total_known_cost_usd: 0,
    per_model_cost_usd: { "ollama:qwen3:1.7b": 0 },
  }));
  assert.equal(c.category, "local");
  assert.match(c.headline, /Local-only/);
  assert.match(c.detail, /\$0\.00/);
  assert.doesNotMatch(c.detail, /Unknown/);
});

test("classifyRunCost: unknown-only run labels honestly", () => {
  const c = classifyRunCost(emptyState({
    total_calls: 1,
    total_local_calls: 0,
    total_unknown_calls: 1,
    total_known_cost_usd: 0,
  }));
  assert.equal(c.category, "unknown");
  assert.match(c.headline, /Unknown/);
  assert.doesNotMatch(c.headline, /\$0\.00/);
});

test("classifyRunCost: known-only run shows money", () => {
  const c = classifyRunCost(emptyState({
    total_calls: 2,
    total_known_cost_usd: 0.012,
    per_model_cost_usd: { "gpt-4o": 0.012 },
  }));
  assert.equal(c.category, "known");
  assert.match(c.headline, /Known API cost/);
  assert.match(c.detail, /2 calls/);
});

test("classifyRunCost: mixed known+unknown is explicit", () => {
  const c = classifyRunCost(emptyState({
    total_calls: 4,
    total_known_cost_usd: 0.05,
    total_unknown_calls: 1,
    per_model_cost_usd: { "gpt-4o": 0.05 },
  }));
  assert.equal(c.category, "known");
  assert.match(c.headline, /Mixed/);
  assert.match(c.detail, /1 call still has an unknown price/);
});

// ---------------------------------------------------------------------------
// Budget hardness (off / soft / hard) for the toggle UI
// ---------------------------------------------------------------------------

test("budgetHardness: off when run_budget_usd is null/0", () => {
  assert.equal(budgetHardness({ run_budget_usd: null, project_budget_usd: null, warning_threshold_usd: null, strict_monetary: false }), "off");
  assert.equal(budgetHardness({ run_budget_usd: 0, project_budget_usd: null, warning_threshold_usd: null, strict_monetary: true }), "off");
});

test("budgetHardness: soft when budget set without strict", () => {
  assert.equal(budgetHardness({ run_budget_usd: 1.0, project_budget_usd: null, warning_threshold_usd: 0.5, strict_monetary: false }), "soft");
});

test("budgetHardness: hard when budget set with strict", () => {
  assert.equal(budgetHardness({ run_budget_usd: 1.0, project_budget_usd: null, warning_threshold_usd: null, strict_monetary: true }), "hard");
});

test("budgetHardnessLabel and Description cover all branches", () => {
  for (const h of ["off", "soft", "hard"]) {
    const label = budgetHardnessLabel(h);
    const desc = budgetHardnessDescription(h);
    assert.ok(label.length > 0);
    assert.ok(desc.length > 0);
  }
});

// ---------------------------------------------------------------------------
// Budget status derivation
// ---------------------------------------------------------------------------

test("deriveBudgetStatus: no policy", () => {
  const s = deriveBudgetStatus(emptyState());
  assert.equal(s.status, "no-policy");
  assert.equal(s.has_policy, false);
  assert.equal(s.remaining_run_label, "—");
  assert.match(s.detail, /No session cost cap is set/);
});

test("deriveBudgetStatus: ok when within cap", () => {
  const s = deriveBudgetStatus(emptyState({
    run_budget_usd: 1.0,
    remaining_run_budget_usd: 0.5,
    total_known_cost_usd: 0.5,
  }));
  assert.equal(s.status, "ok");
  assert.equal(s.remaining_run_label, "$0.50");
  assert.equal(s.warning, false);
});

test("deriveBudgetStatus: warning at threshold", () => {
  const s = deriveBudgetStatus(emptyState({
    run_budget_usd: 2.0,
    warning_threshold_usd: 0.5,
    remaining_run_budget_usd: 0.0,
    total_known_cost_usd: 0.5,
  }));
  assert.equal(s.status, "warning");
  assert.equal(s.warning, true);
});

test("deriveBudgetStatus: blocked under strict + unknown", () => {
  const s = deriveBudgetStatus(emptyState({
    run_budget_usd: 1.0,
    strict_monetary: true,
    total_calls: 1,
    total_unknown_calls: 1,
  }));
  assert.equal(s.status, "blocked");
  assert.equal(s.has_policy, true);
  assert.match(s.detail, /unknown price/);
});

test("deriveBudgetStatus: unknown with policy but no spend", () => {
  const s = deriveBudgetStatus(emptyState({
    run_budget_usd: 1.0,
    remaining_run_budget_usd: 1.0,
    total_known_cost_usd: 0,
  }));
  assert.equal(s.status, "unknown");
  assert.equal(s.has_policy, true);
});

// ---------------------------------------------------------------------------
// Draft validation (mirrors backend _parse_budget_payload)
// ---------------------------------------------------------------------------

test("validateBudgetDraft: empty is valid", () => {
  assert.deepEqual(
    validateBudgetDraft({ run_budget_usd: null, project_budget_usd: null, warning_threshold_usd: null, strict_monetary: false }),
    [],
  );
});

test("validateBudgetDraft: rejects negative", () => {
  const errs = validateBudgetDraft({ run_budget_usd: -1, project_budget_usd: null, warning_threshold_usd: null, strict_monetary: false });
  assert.equal(errs.length, 1);
  assert.equal(errs[0].field, "run_budget_usd");
});

test("validateBudgetDraft: rejects warning > budget", () => {
  const errs = validateBudgetDraft({ run_budget_usd: 1, project_budget_usd: null, warning_threshold_usd: 5, strict_monetary: false });
  assert.equal(errs.length, 1);
  assert.equal(errs[0].field, "warning_threshold_usd");
});

test("validateBudgetDraft: rejects absurdly large value", () => {
  const errs = validateBudgetDraft({ run_budget_usd: 2_000_000, project_budget_usd: null, warning_threshold_usd: null, strict_monetary: false });
  assert.equal(errs.length, 1);
  assert.equal(errs[0].field, "run_budget_usd");
});

test("validateBudgetDraft: accepts a healthy draft", () => {
  const errs = validateBudgetDraft({ run_budget_usd: 5, project_budget_usd: 20, warning_threshold_usd: 3, strict_monetary: true });
  assert.deepEqual(errs, []);
});

// ---------------------------------------------------------------------------
// Model row classifier
// ---------------------------------------------------------------------------

test("classifyModelRow: ollama models are local", () => {
  const r = classifyModelRow("ollama:qwen3:1.7b", 0);
  assert.equal(r.category, "local");
  assert.match(r.label, /local/);
});

test("classifyModelRow: API model with cost is known", () => {
  const r = classifyModelRow("gpt-4o", 0.012);
  assert.equal(r.category, "known");
  assert.match(r.label, /API/);
});

test("classifyModelRow: API model with zero cost is unknown", () => {
  const r = classifyModelRow("mystery-model", 0);
  assert.equal(r.category, "unknown");
  assert.match(r.label, /not yet known/);
});

test("formatPerModelRow composes the row label", () => {
  const s = formatPerModelRow("gpt-4o", 0.012);
  assert.match(s, /gpt-4o/);
  assert.match(s, /\$/);
});

// ---------------------------------------------------------------------------
// Empty-state / aggregate helpers
// ---------------------------------------------------------------------------

test("hasAnyRecords: false on null, false on empty state, true on recorded", () => {
  assert.equal(hasAnyRecords(null), false);
  assert.equal(hasAnyRecords(undefined), false);
  assert.equal(hasAnyRecords(emptyState()), false);
  assert.equal(hasAnyRecords(emptyState({ total_calls: 1 })), true);
});

test("totalTokens sums input + output", () => {
  assert.equal(totalTokens(emptyState({ total_input_tokens: 100, total_output_tokens: 50 })), 150);
});

// ---------------------------------------------------------------------------
// Truthful UI wording (session-only caps, unknown-cost telemetry)
// ---------------------------------------------------------------------------

test("session-only wording matches the 28B telemetry contract", () => {
  assert.equal(
    SESSION_ONLY_BUDGET_NOTICE,
    "Recorded cost caps apply to the current AMCA session and reset after restart.",
  );
});

test("unknown-cost wording states telemetry, never a budget block", () => {
  assert.equal(
    UNKNOWN_COST_TELEMETRY_MESSAGE,
    "This model's API price is unknown, so its cost is recorded as unknown telemetry; budget never blocks the call.",
  );
});

// ---------------------------------------------------------------------------
// User-requested removal of budget setup/approval UI (regression guard).
// The panel must stay read-only telemetry; no POST /api/cost/budget calls
// from the UI and no hard-block copy anywhere in the panel.
// ---------------------------------------------------------------------------

test("panel no longer renders a budget setup form or posts budgets", () => {
  const source = readFileSync(PANEL_SOURCE_PATH, "utf8");
  assert.doesNotMatch(source, /BudgetSetupForm/);
  assert.doesNotMatch(source, /costSetBudget/);
  assert.doesNotMatch(source, /Apply budget/);
  assert.doesNotMatch(source, /Session budget setup/);
  assert.doesNotMatch(source, /validateBudgetDraft|BudgetDraft\b|budgetHardness/);
  // Read-only telemetry and privacy/provider framing stay intact.
  assert.match(source, /GET \/api\/cost\/state/);
  assert.match(source, /privacy approvals unchanged/);
});

test("panel copy describes unknown prices as telemetry, not hard block", () => {
  const source = readFileSync(PANEL_SOURCE_PATH, "utf8");
  assert.doesNotMatch(source, /hard block/i);
  assert.match(source, /unknown API prices are recorded as unknown telemetry/);
});
