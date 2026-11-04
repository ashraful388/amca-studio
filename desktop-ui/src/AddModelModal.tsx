// Add/edit-model dialog for a BYOK provider.
//
// Mirrors the operator's reference dialog: title with a close control, a
// Smart configuration toggle, the model id, operator-recorded context window
// and max output tokens, an Advanced settings disclosure, and a footer with
// Reset form / Cancel / Save.
//
// Honesty rules this dialog must keep:
// * the recorded numbers are what the operator TYPED about their own
//   provider. AMCA has not measured them; they are stored verbatim, shown
//   back as recorded, and read by no routing or verification path;
// * "Smart configuration" is the only network action here, and it is the
//   existing bounded 1-token verification — one request, true state recorded.
//   It is off unless the operator turns it on;
// * a rejected save keeps the operator's draft (it is never silently wiped).

import { useEffect, useState } from "react";

export interface AddModelPayload {
  modelId: string;
  contextWindow: number | null;
  maxOutputTokens: number | null;
  include: boolean;
  verify: boolean;
}

function parseTokenField(value: string): number | null | "invalid" {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed < 0 || !Number.isInteger(parsed)) return "invalid";
  return parsed;
}

export function AddModelModal({
  mode,
  providerName,
  modelId,
  initialContextWindow,
  initialMaxOutputTokens,
  initialIncluded,
  busy,
  onClose,
  onSave,
}: {
  mode: "add" | "edit";
  providerName: string;
  modelId?: string;
  initialContextWindow?: number | null;
  initialMaxOutputTokens?: number | null;
  initialIncluded?: boolean;
  busy: boolean;
  onClose: () => void;
  onSave: (payload: AddModelPayload) => Promise<boolean> | boolean;
}) {
  const [idDraft, setIdDraft] = useState(modelId ?? "");
  const [contextDraft, setContextDraft] = useState(
    initialContextWindow === null || initialContextWindow === undefined ? "" : String(initialContextWindow),
  );
  const [outputDraft, setOutputDraft] = useState(
    initialMaxOutputTokens === null || initialMaxOutputTokens === undefined ? "" : String(initialMaxOutputTokens),
  );
  const [smart, setSmart] = useState(mode === "add");
  const [include, setInclude] = useState(initialIncluded ?? true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  // Draft retention: the dialog is re-keyed by the caller per opening, so a
  // rejected save keeps everything the operator typed exactly as typed.
  useEffect(() => {
    setLocalError(null);
  }, [busy]);

  // A rejected save must be visible IN the dialog: the page-level failure
  // messages render behind the overlay, so a silent false would look like a
  // dead Save button. Escape closes, matching the × control.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const reset = () => {
    if (mode === "add") setIdDraft("");
    setContextDraft("");
    setOutputDraft("");
    setSmart(mode === "add");
    setInclude(true);
    setLocalError(null);
  };

  const submit = () => {
    const id = (mode === "edit" ? modelId ?? "" : idDraft).trim();
    if (!id) {
      setLocalError("A model ID is required.");
      return;
    }
    const contextWindow = parseTokenField(contextDraft);
    if (contextWindow === "invalid") {
      setLocalError("Context window must be a whole number of tokens (or blank).");
      return;
    }
    const maxOutputTokens = parseTokenField(outputDraft);
    if (maxOutputTokens === "invalid") {
      setLocalError("Max output tokens must be a whole number (or blank).");
      return;
    }
    void Promise.resolve(
      onSave({
        modelId: id,
        contextWindow,
        maxOutputTokens,
        include,
        verify: smart,
      }),
    ).then((ok) => {
      if (ok) {
        onClose();
        return;
      }
      // The provider page behind this dialog carries the specific failure;
      // say so here rather than leaving Save looking dead. The draft stays.
      setLocalError("Save failed — the message on the provider page explains what happened. Your draft is kept.");
    });
  };

  return (
    <div className="msmodal-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="msmodal" role="dialog" aria-modal="true" aria-label={mode === "add" ? "Add model" : "Edit model"}>
        <div className="msmodal-head">
          <h2>{mode === "add" ? "Add model" : "Edit model"}</h2>
          <button type="button" className="msiconbtn" onClick={onClose} aria-label="Close dialog" title="Close">
            <span aria-hidden="true">×</span>
          </button>
        </div>

        <div className="msmodal-body">
          <div className="mstoggle-row">
            <label className="mstoggle-label" htmlFor="msmodal-smart">
              Smart configuration
              <span
                className="msinfo"
                title="On: after saving, AMCA sends one bounded verification request for this model and records its true state. Off: the model is recorded with no network call."
                aria-label="Smart configuration help"
              >
                ?
              </span>
            </label>
            <button
              type="button"
              id="msmodal-smart"
              className={`mstoggle${smart ? " on" : ""}`}
              role="switch"
              aria-checked={smart}
              aria-label="Smart configuration: verify this model after saving"
              disabled={busy}
              onClick={() => setSmart((value) => !value)}
            >
              <span className="mstoggle-knob" />
            </button>
          </div>

          <label className="msfield">
            <span>Model ID</span>
            <input
              value={mode === "edit" ? modelId : idDraft}
              onChange={(event) => setIdDraft(event.target.value)}
              placeholder="Model ID"
              disabled={mode === "edit" || busy}
              spellCheck={false}
              autoComplete="off"
            />
          </label>

          <label className="msfield">
            <span>
              Context window
              <span
                className="msinfo"
                title="Recorded exactly as you typed it, for your own reference. AMCA has not measured this value, and routing never reads it."
                aria-label="Context window help"
              >
                ?
              </span>
            </span>
            <input
              value={contextDraft}
              onChange={(event) => setContextDraft(event.target.value)}
              placeholder="e.g. 128000"
              inputMode="numeric"
              disabled={busy}
              spellCheck={false}
              autoComplete="off"
            />
          </label>

          <label className="msfield">
            <span>
              Max output tokens
              <span
                className="msinfo"
                title="Recorded exactly as you typed it, for your own reference. AMCA has not measured this value, and routing never reads it."
                aria-label="Max output tokens help"
              >
                ?
              </span>
            </span>
            <input
              value={outputDraft}
              onChange={(event) => setOutputDraft(event.target.value)}
              placeholder="e.g. 8192"
              inputMode="numeric"
              disabled={busy}
              spellCheck={false}
              autoComplete="off"
            />
          </label>

          <button
            type="button"
            className="msdisclosure"
            aria-expanded={advancedOpen}
            onClick={() => setAdvancedOpen((value) => !value)}
          >
            <span aria-hidden="true">{advancedOpen ? "▾" : "›"}</span> Advanced settings
          </button>
          {advancedOpen ? (
            <div className="msmodal-advanced">
              <div className="mstoggle-row">
                <label className="mstoggle-label" htmlFor="msmodal-include">
                  Include this model in chat immediately
                </label>
                <button
                  type="button"
                  id="msmodal-include"
                  className={`mstoggle${include ? " on" : ""}`}
                  role="switch"
                  aria-checked={include}
                  disabled={busy}
                  onClick={() => setInclude((value) => !value)}
                >
                  <span className="mstoggle-knob" />
                </button>
              </div>
              <p className="msrow-note">
                Off keeps the model recorded but hidden from the model selectors — the same
                include/exclude control as the toggle on each model row.
              </p>
            </div>
          ) : null}
        </div>

        {localError ? (
          <p className="msprovider-fail" role="alert">
            {localError}
          </p>
        ) : null}

        <div className="msmodal-foot">
          <button type="button" className="mslink" onClick={reset} disabled={busy}>
            Reset form
          </button>
          <div className="msmodal-foot-actions">
            <button type="button" className="msbtn" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="button" className="msbtn primary" onClick={submit} disabled={busy}>
              Save
            </button>
          </div>
        </div>

        <p className="msrow-note msmodal-note">
          Adding a model to <strong>{providerName}</strong> performs no inference call. Smart
          configuration sends one bounded 1-token verification request and records the true state.
        </p>
      </div>
    </div>
  );
}
