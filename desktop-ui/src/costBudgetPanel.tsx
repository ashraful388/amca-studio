// AMCA v0.6.0 M2.2 Studio Cost & Budget UI panel.
//
// Renders read-only cost/budget telemetry. The panel:
//   * consumes the existing GET /api/cost/state route only;
//   * never invents a second backend surface;
//   * treats budget figures as informational, never as execution gates;
//   * leaves provider authorization and privacy approvals unchanged;
//   * never renders credentials, session tokens, raw prompts, or raw
//     responses;
//   * truthfully labels session-only persistence (the v0.6.0 M2 budget
//     is in-memory; restart resets it);
//   * uses user-triggered refresh only (no aggressive polling);
//   * offers no budget setup UI: user request removed the budget
//     approval/setup options; budget state remains read-only telemetry.

import { useCallback, useEffect, useMemo, useState } from "react";

import { amca, type CostStateWire } from "./api";
import {
  UNKNOWN_COST_TELEMETRY_MESSAGE,
  classifyModelRow,
  classifyRunCost,
  deriveBudgetStatus,
  formatMoneyUsd,
  formatTokens,
  hasAnyRecords,
  totalTokens,
  type BudgetPolicyWire,
} from "./costBudgetPanelModel";

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; cost: CostStateWire | null; policy: BudgetPolicyWire | null }
  | { kind: "error"; message: string };

export function CostBudgetPanel() {
  const [load, setLoad] = useState<LoadState>({ kind: "idle" });
  const [refreshing, setRefreshing] = useState(false);

  // ---- fetch ----
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [cost, budgetResponse] = await Promise.all([
        amca.costState().catch(() => null),
        amca.costGetBudget().catch(() => null),
      ]);
      setLoad({
        kind: "ready",
        cost: cost ?? null,
        policy: budgetResponse?.policy ?? null,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown error";
      setLoad({ kind: "error", message });
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (load.kind === "idle") {
      void refresh();
    }
    // We intentionally do NOT poll. The topbar's existing bounded
    // refresh button covers re-fetch needs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- derivations ----
  const cost = load.kind === "ready" ? load.cost : null;
  const budgetStatus = useMemo(() => (cost ? deriveBudgetStatus(cost) : null), [cost]);
  const runClassification = useMemo(() => (cost ? classifyRunCost(cost) : null), [cost]);

  // ---- render branches ----
  if (load.kind === "error") {
    return (
      <article className="panel settings-wide">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">Cost & Budget</span>
            <h2>v0.6.0 cost & budget</h2>
          </div>
          <StatusPill status="error" />
        </div>
        <p className="settings-note">
          The AMCA backend could not return cost state.{" "}
          {load.message}
        </p>
        <div className="form-actions">
          <button type="button" className="primary-button" onClick={() => void refresh()}>
            Retry
          </button>
        </div>
      </article>
    );
  }

  if (load.kind === "loading" || load.kind === "idle") {
    return (
      <article className="panel settings-wide">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">Cost & Budget</span>
            <h2>v0.6.0 cost & budget</h2>
          </div>
        </div>
        <p className="muted-copy">Loading cost state from the AMCA backend…</p>
      </article>
    );
  }

  if (!cost || !hasAnyRecords(cost)) {
    return (
      <article className="panel settings-wide">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">Cost & Budget</span>
            <h2>v0.6.0 cost & budget</h2>
          </div>
          <button
            type="button"
            className="secondary-button"
            onClick={() => void refresh()}
            disabled={refreshing}
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
        <p className="settings-note">
          AMCA will record the per-run cost summary here as soon as the
          first model call completes. This panel reads the accepted
          <code> GET /api/cost/state</code> endpoint only.
        </p>
      </article>
    );
  }

  return (
    <article className="panel settings-wide">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Cost & Budget</span>
          <h2>v0.6.0 cost & budget</h2>
        </div>
        <div className="panel-heading-actions">
          <StatusPill status={budgetStatus?.status ?? "unknown"} />
          <button
            type="button"
            className="secondary-button"
            onClick={() => void refresh()}
            disabled={refreshing}
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {runClassification && (
        <p className="settings-note">
          <strong>{runClassification.headline}.</strong> {runClassification.detail}
        </p>
      )}

      {budgetStatus && budgetStatus.status === "blocked" && (
        <p className="inline-warning">{UNKNOWN_COST_TELEMETRY_MESSAGE}</p>
      )}

      <section className="cost-summary-grid">
        <CostMetric
          label="Total known API cost"
          value={formatMoneyUsd(cost.total_known_cost_usd)}
          detail={`${cost.total_calls} call${cost.total_calls === 1 ? "" : "s"} recorded`}
        />
        <CostMetric
          label="Unknown-cost calls"
          value={String(cost.total_unknown_calls)}
          detail={
            cost.total_unknown_calls > 0
              ? "These calls are not yet priced"
              : "All calls have a known price"
          }
          tone={cost.total_unknown_calls > 0 ? "amber" : "green"}
        />
        <CostMetric
          label="Local calls"
          value={String(cost.total_local_calls)}
          detail="Ollama and other local models"
        />
        <CostMetric
          label="Input tokens"
          value={formatTokens(cost.total_input_tokens)}
        />
        <CostMetric
          label="Output tokens"
          value={formatTokens(cost.total_output_tokens)}
        />
        <CostMetric
          label="Total tokens"
          value={formatTokens(totalTokens(cost))}
        />
      </section>

      {cost.per_model_cost_usd && Object.keys(cost.per_model_cost_usd).length > 0 && (
        <section>
          <h3 className="cost-section-title">Per-model breakdown</h3>
          <table className="cost-table">
            <thead>
              <tr>
                <th>Model</th>
                <th>API cost</th>
                <th>Category</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(cost.per_model_cost_usd).map(([model, amount]) => {
                const cls = classifyModelRow(model, amount);
                return (
                  <tr key={model}>
                    <td>{cls.label}</td>
                    <td>{formatMoneyUsd(amount)}</td>
                    <td>
                      <CategoryPill category={cls.category} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      {cost.project_aggregate_known_cost_usd !== null && (
        <p className="settings-note">
          <strong>Project aggregate (from the existing ledger):</strong>{" "}
          {formatMoneyUsd(cost.project_aggregate_known_cost_usd)} known across{" "}
          {cost.project_aggregate_unknown_calls ?? 0} unknown-cost call
          {cost.project_aggregate_unknown_calls === 1 ? "" : "s"}.
        </p>
      )}

      {budgetStatus && (
        <section>
          <h3 className="cost-section-title">Budget status (authoritative)</h3>
          <dl className="detail-list">
            <div>
              <dt>Run cap</dt>
              <dd>
                {cost.run_budget_usd === null ? "Not set" : formatMoneyUsd(cost.run_budget_usd)}
              </dd>
            </div>
            <div>
              <dt>Project cap</dt>
              <dd>
                {cost.project_budget_usd === null
                  ? "Not set"
                  : formatMoneyUsd(cost.project_budget_usd)}
              </dd>
            </div>
            <div>
              <dt>Remaining (run)</dt>
              <dd>{budgetStatus.remaining_run_label}</dd>
            </div>
            <div>
              <dt>Remaining (project)</dt>
              <dd>{budgetStatus.remaining_project_label}</dd>
            </div>
            <div>
              <dt>Strict monetary</dt>
              <dd>
                {cost.strict_monetary
                  ? "Yes (unknown API prices are recorded as unknown telemetry)"
                  : "No"}
              </dd>
            </div>
          </dl>
          <p className="settings-note">{budgetStatus.detail}</p>
        </section>
      )}
    </article>
  );
}

// ---------------------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------------------

function CostMetric({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail?: string;
  tone?: "green" | "amber";
}) {
  return (
    <article className={`cost-metric${tone ? ` tone-${tone}` : ""}`}>
      <span className="cost-metric-label">{label}</span>
      <strong className="cost-metric-value">{value}</strong>
      {detail && <small className="cost-metric-detail">{detail}</small>}
    </article>
  );
}

function StatusPill({ status }: { status: string }) {
  const label = humanizeStatus(status);
  return <span className={`status-pill status-${status}`}>{label}</span>;
}

function CategoryPill({
  category,
}: {
  category: "known" | "local" | "unknown" | "none";
}) {
  const labels: Record<typeof category, string> = {
    known: "Known API cost",
    local: "Local (no API charge)",
    unknown: "Price not yet known",
    none: "—",
  };
  return (
    <span className={`cost-category-pill cost-category-${category}`}>
      {labels[category]}
    </span>
  );
}

function humanizeStatus(status: string): string {
  switch (status) {
    case "ok":
      return "Within cap";
    case "warning":
      return "Warning";
    case "blocked":
      return "Unknown price";
    case "no-policy":
      return "No policy";
    case "unknown":
      return "Unknown";
    case "error":
      return "Error";
    default:
      return status;
  }
}
