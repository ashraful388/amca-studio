import { useEffect, useMemo, useState } from "react";
import { amca } from "./api";
import {
  CAPABILITY_OPTIONS,
  SESSION_SCOPE_NOTE,
  capabilityRows,
  confirmationText,
  controlState,
  decisionLabel,
  humanCapabilityLabel,
  readinessLabel,
  readinessReason,
  stateLabel,
  validationRows,
} from "./intelligencePanelModel";

type LoadPhase = "loading" | "ready" | "error";

// M5.2 Studio Intelligence UI + M5.7 secure controlled enablement: a
// capability overview, a non-executing validation form, and session-scoped
// experimental enablement controls. The panel lives in the dedicated
// Settings surface, never beside normal chat controls. Mutations go only
// through the hardened backend route (POST /api/intelligence/configure),
// which refuses anything the security boundary or readiness rules reject;
// the backend stays the sole authority over gate state.
export function IntelligencePanel() {
  const [phase, setPhase] = useState<LoadPhase>("loading");
  const [loadError, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<Awaited<
    ReturnType<typeof amca.intelligenceStatus>
  > | null>(null);
  const [requested, setRequested] = useState<string[]>([]);
  const [validationBusy, setValidationBusy] = useState(false);
  const [validation, setValidation] = useState<Awaited<
    ReturnType<typeof amca.intelligenceValidate>
  > | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [mutationBusy, setMutationBusy] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);

  const refresh = () => {
    setPhase("loading");
    setError(null);
    amca
      .intelligenceStatus()
      .then((value) => {
        if (!value || value.contract_version !== 2 || !value.capabilities) {
          throw new Error("unsupported intelligence status response");
        }
        setStatus(value);
        setPhase("ready");
      })
      .catch((error: unknown) => {
        setError(
          error instanceof Error ? error.message : "Intelligence status unavailable",
        );
        setPhase("error");
      });
  };

  useEffect(() => {
    refresh();
  }, []);

  const rows = useMemo(() => (status ? capabilityRows(status) : []), [status]);

  const toggleRequested = (id: string) => {
    setRequested((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id].sort(),
    );
    setValidationError(null);
  };

  const applyChanges = (changes: Record<string, boolean>) => {
    setMutationBusy(true);
    setMutationError(null);
    setConfirming(null);
    amca
      .intelligenceConfigure(changes)
      .then(() => amca.intelligenceStatus())
      .then((value) => {
        if (!value || value.contract_version !== 2 || !value.capabilities) {
          throw new Error("unsupported intelligence status response");
        }
        setStatus(value);
        setPhase("ready");
      })
      .catch((error: unknown) => {
        setMutationError(
          error instanceof Error ? error.message : "Configuration failed",
        );
      })
      .finally(() => {
        setMutationBusy(false);
      });
  };

  const runValidation = () => {
    setValidationBusy(true);
    setValidationError(null);
    amca
      .intelligenceValidate(requested)
      .then((value) => {
        setValidation(value);
      })
      .catch((error: unknown) => {
        setValidationError(
          error instanceof Error ? error.message : "Validation failed",
        );
      })
      .finally(() => {
        setValidationBusy(false);
      });
  };

  if (phase === "loading") {
    return (
      <article className="panel settings-wide intelligence-panel">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">v0.5.0 · governed</span>
            <h2>Intelligence capabilities</h2>
          </div>
        </div>
        <p className="muted-copy">Loading intelligence status…</p>
      </article>
    );
  }

  if (phase === "error") {
    return (
      <article className="panel settings-wide intelligence-panel">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">v0.5.0 · governed</span>
            <h2>Intelligence capabilities</h2>
          </div>
        </div>
        <p className="settings-note intelligence-error" role="alert">
          Intelligence status unavailable: {loadError}
        </p>
        <div className="form-actions">
          <button className="secondary-button" onClick={refresh}>
            Retry
          </button>
        </div>
      </article>
    );
  }

  return (
    <article className="panel settings-wide intelligence-panel">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">v0.5.0 · governed</span>
          <h2>Intelligence capabilities</h2>
        </div>
        <div className="intelligence-heading-actions">
          <StatusChip label={status?.execution_authority ?? "existing_runtime"} />
          <button className="secondary-button" onClick={refresh}>
            Refresh
          </button>
        </div>
      </div>
      <p className="settings-note">
        Experimental capability controls are session-scoped and go through
        the hardened backend route, which enforces the security boundary,
        truthful readiness, and dependency rules. {SESSION_SCOPE_NOTE}
        Readiness is reported by the backend; capabilities whose readiness is
        unknown cannot be enabled.
      </p>
      <div className="intelligence-capability-list">
        {rows.map(({ id, state }) => (
          <article className="intelligence-capability" key={id}>
            <div className="intelligence-capability-heading">
              <strong>{stateLabel(state).split(" · ")[0] === "Not implemented" ? id : humanCapability(id)}</strong>
              <span className={`intelligence-badge ${state.enabled ? "on" : "off"}`}>
                {state.enabled ? "Enabled" : "Disabled"}
              </span>
            </div>
            <dl>
              <div><dt>Implemented</dt><dd>{state.implemented ? "Yes" : "No"}</dd></div>
              <div><dt>Readiness</dt><dd>{readinessLabel(state.runtime_ready)}</dd></div>
              <div><dt>Execution authority</dt><dd>{status?.execution_authority ?? "existing_runtime"}</dd></div>
            </dl>
            {readinessReason(state) && (
              <p className="settings-note intelligence-readiness-reason" role="status">
                {readinessReason(state)}
              </p>
            )}
            <ControlRow
              id={id}
              state={state}
              busy={mutationBusy}
              confirming={confirming === id}
              onBeginConfirm={() => {
                setConfirming(id);
                setMutationError(null);
              }}
              onCancelConfirm={() => setConfirming(null)}
              onConfirmEnable={() => applyChanges({ [id]: true })}
              onDisable={() => applyChanges({ [id]: false })}
            />
          </article>
        ))}
      </div>
      {mutationError && (
        <p className="settings-note intelligence-error" role="alert">
          {mutationError}
        </p>
      )}
      <div className="intelligence-validation">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">Non-executing</span>
            <h3>Validate a capability selection</h3>
          </div>
        </div>
        <p className="settings-note">
          Asks the backend whether a selection would be allowed. Nothing is
          executed and no setting is changed.
        </p>
        <div className="intelligence-request-chips">
          {CAPABILITY_OPTIONS.map((option) => (
            <label
              key={option.id}
              className={`intelligence-chip ${requested.includes(option.id) ? "active" : ""}`}
            >
              <input
                type="checkbox"
                checked={requested.includes(option.id)}
                onChange={() => toggleRequested(option.id)}
              />
              {option.label}
            </label>
          ))}
        </div>
        <div className="form-actions">
          <button
            className="primary-button"
            disabled={validationBusy || requested.length === 0}
            onClick={() => runValidation()}
          >
            {validationBusy ? "Validating…" : "Validate selection"}
          </button>
          {requested.length > 0 && (
            <button
              className="secondary-button"
              disabled={validationBusy}
              onClick={() => {
                setRequested([]);
                setValidation(null);
              }}
            >
              Clear
            </button>
          )}
        </div>
        {validationError && (
          <p className="settings-note intelligence-error" role="alert">
            {validationError}
          </p>
        )}
        {validation && (
          <div className="intelligence-validation-results">
            <div className="intelligence-validation-meta">
              <span>Contract v{validation.contract_version}</span>
              <span>Authority: {validation.execution_authority}</span>
            </div>
            <div className="intelligence-validation-list">
              {validationRows(validation).map(({ id, decision }) => (
                <article className="intelligence-validation-row" key={id}>
                  <strong>{humanCapability(id)}</strong>
                  <span className={`intelligence-badge ${decision.decision === "allowed" ? "on" : "off"}`}>
                    {decisionLabel(decision)}
                  </span>
                  {decision.reason && <small>{decision.reason}</small>}
                </article>
              ))}
            </div>
          </div>
        )}
      </div>
    </article>
  );
}

function ControlRow({
  id,
  state,
  busy,
  confirming,
  onBeginConfirm,
  onCancelConfirm,
  onConfirmEnable,
  onDisable,
}: {
  id: string;
  state: { implemented: boolean; enabled: boolean; runtime_ready: string };
  busy: boolean;
  confirming: boolean;
  onBeginConfirm: () => void;
  onCancelConfirm: () => void;
  onConfirmEnable: () => void;
  onDisable: () => void;
}) {
  const control = controlState(state);
  if (control.action === "disable") {
    return (
      <div className="intelligence-control-row">
        <button
          className="secondary-button"
          disabled={busy}
          onClick={onDisable}
        >
          Disable
        </button>
        <small className="muted-copy">{control.hint}</small>
      </div>
    );
  }
  if (control.action === "unavailable") {
    return (
      <div className="intelligence-control-row">
        <button className="secondary-button" disabled>
          Enable unavailable
        </button>
        <small className="muted-copy">{control.hint}</small>
      </div>
    );
  }
  if (confirming) {
    return (
      <div className="intelligence-control-row">
        <p className="settings-note">{confirmationText(id, humanCapabilityLabel(id))}</p>
        <div className="form-actions">
          <button
            className="primary-button"
            disabled={busy}
            onClick={onConfirmEnable}
          >
            {busy ? "Enabling…" : "Confirm enable"}
          </button>
          <button
            className="secondary-button"
            disabled={busy}
            onClick={onCancelConfirm}
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="intelligence-control-row">
      <button
        className="secondary-button"
        disabled={busy}
        onClick={onBeginConfirm}
      >
        Enable
      </button>
      <small className="muted-copy">{control.hint}</small>
    </div>
  );
}

function StatusChip({ label }: { label: string }) {
  return <span className="intelligence-badge off">{label}</span>;
}

function humanCapability(id: string): string {
  return humanCapabilityLabel(id);
}
