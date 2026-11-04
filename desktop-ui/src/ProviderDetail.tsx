// Per-provider detail page.
//
// Layout follows the operator's reference design: a header with the provider
// name, an include/exclude toggle and a kebab menu of provider actions; then
// live rows — Base URL, API format, API key, Model list — and one row per
// model with recorded badges, icon actions and an include/exclude toggle.
//
// Row edits commit on blur through the existing update contract, so nothing
// is written while the operator is still typing. The API key stays write-only:
// the field accepts a NEW key only, the stored key is never received, shown
// or reconstructed, and the eye control toggles the visibility of what is
// being typed right now — nothing else.

import { FocusEvent, useEffect, useRef, useState } from "react";
import { Icon } from "./icons";
import { apiFormatLabel, API_FORMAT_OPTIONS, blurCommitsSecret, evidenceBadge, formatTokenCount } from "./modelSettingsNavModel";
import type { ModelHealthRecord, ModelHubProvider } from "./types";

function healthFor(
  health: Record<string, ModelHealthRecord> | undefined,
  provider: ModelHubProvider,
  model: string,
): ModelHealthRecord | undefined {
  if (!health) return undefined;
  const ref = `${provider.id}:${model}`;
  // Match on the endpoint as well as the ref. The persisted record carries the
  // endpoint it was verified against, and the ref alone is not unique across a
  // reconfiguration — matching on ref alone would surface a stale READY badge
  // for an endpoint the provider no longer points at.
  return Object.values(health).find(
    (record) => record.ref === ref && record.endpoint === provider.endpoint,
  );
}

export function ProviderDetail({
  provider,
  health,
  busy,
  testResult,
  onPatchProvider,
  onSaveSecret,
  onToggleProvider,
  onRemove,
  onTestConnection,
  onOpenAddModel,
  onEditModel,
  onToggleModel,
  onVerifyModel,
  onRemoveModel,
  onShowAll,
  onHideAll,
  modelMessage,
  modelResults,
}: {
  provider: ModelHubProvider;
  health?: Record<string, ModelHealthRecord>;
  busy: boolean;
  testResult?: { ok: boolean; message: string };
  onPatchProvider: (patch: { endpoint?: string; provider_kind?: string; name?: string; pricing_mode?: "auto" | "manual" }) => Promise<boolean> | boolean;
  onSaveSecret: (secret: string) => Promise<boolean> | boolean;
  onToggleProvider: (enabled: boolean) => void;
  onRemove: () => void;
  onTestConnection: () => void;
  onOpenAddModel: () => void;
  onEditModel: (modelId: string) => void;
  onToggleModel: (modelId: string, enabled: boolean) => void;
  onVerifyModel: (modelId: string) => void;
  onRemoveModel: (modelId: string) => void;
  onShowAll: () => void;
  onHideAll: () => void;
  modelMessage?: { ok: boolean; message: string };
  modelResults?: Record<string, { ok: boolean; message: string }>;
}) {
  const [endpointDraft, setEndpointDraft] = useState(provider.endpoint);
  // The backend normalizes what it stores (a trailing slash, for one), so the
  // draft is re-synced whenever the saved prop changes — otherwise a
  // successful save leaves the draft permanently "different" and every later
  // blur re-commits the same patch.
  useEffect(() => {
    setEndpointDraft(provider.endpoint);
  }, [provider.endpoint]);
  const [secretDraft, setSecretDraft] = useState("");
  const [secretRevealed, setSecretRevealed] = useState(false);
  const keyFieldRef = useRef<HTMLDivElement | null>(null);
  const [patchState, setPatchState] = useState<{ ok: boolean; message: string } | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState(provider.name);
  const [kebabOpen, setKebabOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const models = provider.model_ids;
  const shown = provider.enabled_model_ids.length;
  const configured = provider.configured;
  const excluded = provider.enabled === false;
  // The subtitle states what is recorded, nothing more. `status` is written
  // from what the operator typed, so a catalog of hand-entered model ids is
  // "recorded", not "connected" — a connection is only ever claimed from a
  // persisted health record.
  const recorded = models.some((model) => healthFor(health, provider, model));
  const subtitle = !configured
    ? "Credential missing"
    : excluded
      ? "Excluded from chat — its models stay recorded but are not selectable"
      : recorded
        ? "Health check recorded · see the badge on each model below"
        : models.length > 0
          ? `Credential stored · ${models.length} model${models.length === 1 ? "" : "s"} recorded · not verified`
          : "Credential stored · no models recorded · not verified";

  const commitEndpoint = () => {
    const value = endpointDraft.trim();
    if (value === provider.endpoint) return;
    // An empty Base URL is accepted by the store (a custom provider may be
    // endpoint-less), but wiping it from the live row by accident must not be
    // reported as a routine save.
    if (!value) {
      setEndpointDraft(provider.endpoint);
      setPatchState({ ok: false, message: "Base URL was left empty — the saved endpoint is kept." });
      return;
    }
    commitPatch({ endpoint: value }, "Base URL");
  };

  const commitPatch = (patch: { endpoint?: string; provider_kind?: string; name?: string; pricing_mode?: "auto" | "manual" }, what: string) => {
    void Promise.resolve(onPatchProvider(patch)).then((ok) => {
      setPatchState(
        ok
          ? { ok: true, message: `${what} saved.` }
          : { ok: false, message: `${what} was not saved — see the message above the form.` },
      );
    });
  };

  const commitSecret = () => {
    const value = secretDraft;
    if (!value.trim()) return;
    void Promise.resolve(onSaveSecret(value)).then((ok) => {
      if (ok) {
        setSecretDraft("");
        setSecretRevealed(false);
        setPatchState({ ok: true, message: "API key stored in the operating-system vault." });
      } else {
        setPatchState({ ok: false, message: "The API key was not stored." });
      }
    });
  };

  // An API key commits on Enter AND on blur — clicking anywhere outside the
  // key row stores what was typed, so a pasted key can no longer sit unstored
  // while the card reads "Credential missing" (owner report 2026-11-05; the
  // same trap as the Meta round). Focus moving to a control inside the key
  // row (the reveal toggle) never commits: double-checking what was typed
  // must not store it first.
  const commitSecretOnBlur = (event: FocusEvent<HTMLInputElement>) => {
    if (blurCommitsSecret(event.relatedTarget as Element | null, keyFieldRef.current)) {
      commitSecret();
    }
  };

  const commitRename = () => {
    setRenaming(false);
    const value = nameDraft.trim();
    if (!value || value === provider.name) return;
    commitPatch({ name: value }, "Provider name");
  };

  return (
    <div className="msprovider">
      <div className="msprovider-head">
        <div className="msprovider-title">
          <span className="msprovider-glyph" aria-hidden="true"><Icon name="box" /></span>
          <div>
            {renaming ? (
              <input
                className="msrename"
                value={nameDraft}
                autoFocus
                disabled={busy}
                onChange={(event) => setNameDraft(event.target.value)}
                onBlur={commitRename}
                onKeyDown={(event) => { if (event.key === "Enter") commitRename(); if (event.key === "Escape") { setNameDraft(provider.name); setRenaming(false); } }}
                aria-label="Provider name"
              />
            ) : (
              <h2>{provider.name}</h2>
            )}
            <p className="msprovider-sub">{subtitle}</p>
          </div>
        </div>
        <div className="msprovider-head-actions">
          {/* Include/exclude the whole provider. Exclusion is the operator's
              stored decision surfaced honestly: no health meaning is attached. */}
          <button
            type="button"
            className={`mstoggle${!excluded ? " on" : ""}`}
            role="switch"
            aria-checked={!excluded}
            aria-label={excluded ? "Include this provider in chat" : "Exclude this provider from chat"}
            title={excluded ? "Excluded from chat — toggle to include" : "Included in chat — toggle to exclude"}
            disabled={busy}
            onClick={() => onToggleProvider(excluded)}
          >
            <span className="mstoggle-knob" />
          </button>
          <button
            type="button"
            className="msiconbtn"
            aria-label="Provider actions"
            aria-expanded={kebabOpen}
            title="Provider actions"
            disabled={busy}
            onClick={() => setKebabOpen((value) => !value)}
          >
            <Icon name="kebab" />
          </button>
          {kebabOpen ? (
            <>
              <div className="msoverlay-catch" role="presentation" onClick={() => setKebabOpen(false)} />
              <div className="msmenu" role="menu">
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => { setKebabOpen(false); onTestConnection(); }}
                  disabled={busy || !configured || !provider.endpoint}
                  title="One bounded catalog request. No chat content is sent and no inference runs."
                >
                  <Icon name="plug" /> Test connection
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => { setKebabOpen(false); setNameDraft(provider.name); setRenaming(true); }}
                  disabled={busy}
                >
                  <Icon name="pencil" /> Rename provider
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="danger"
                  onClick={() => { setKebabOpen(false); onRemove(); }}
                  disabled={busy}
                >
                  <Icon name="trash" /> Remove provider
                </button>
              </div>
            </>
          ) : null}
        </div>
      </div>

      {testResult ? (
        <p className={testResult.ok ? "msprovider-ok" : "msprovider-fail"} role="status">
          {testResult.message}
        </p>
      ) : null}

      <div className="msrows">
        <div className="msrow msrow-live">
          <label className="msrow-label" htmlFor={`endpoint-${provider.id}`}>Base URL</label>
          <input
            id={`endpoint-${provider.id}`}
            className="msrow-input"
            value={endpointDraft}
            onChange={(event) => setEndpointDraft(event.target.value)}
            onBlur={commitEndpoint}
            onKeyDown={(event) => { if (event.key === "Enter") commitEndpoint(); }}
            placeholder="https://api.example.com/v1"
            disabled={busy}
            spellCheck={false}
            autoComplete="off"
          />
        </div>

        <div className="msrow msrow-live">
          {/* Derived from provider_kind — the schema stores no format field. */}
          <label className="msrow-label" htmlFor={`kind-${provider.id}`}>API format</label>
          <select
            id={`kind-${provider.id}`}
            className="msrow-input"
            value={provider.provider_kind}
            disabled={busy}
            onChange={(event) => commitPatch({ provider_kind: event.target.value }, "API format")}
          >
            {API_FORMAT_OPTIONS.some((option) => option.value === provider.provider_kind)
              ? null
              : <option value={provider.provider_kind}>{apiFormatLabel(provider.provider_kind)}</option>}
            {API_FORMAT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>

        <div className="msrow msrow-live">
          <label className="msrow-label" htmlFor={`key-${provider.id}`}>API key</label>
          <div className="mskey-field" ref={keyFieldRef}>
            <input
              id={`key-${provider.id}`}
              className="msrow-input"
              type={secretRevealed ? "text" : "password"}
              value={secretDraft}
              onChange={(event) => setSecretDraft(event.target.value)}
              /* Enter commits immediately; blur commits too (see
                 commitSecretOnBlur) unless focus moved inside this row. */
              onKeyDown={(event) => { if (event.key === "Enter") commitSecret(); }}
              onBlur={commitSecretOnBlur}
              placeholder={configured ? "Enter a new key to replace the stored one" : "Enter API key"}
              disabled={busy}
              autoComplete="new-password"
              spellCheck={false}
            />
            {/* The eye toggles the NEW key being typed. A stored key is never
                returned by the API, so there is nothing to reveal about it —
                only the write-only draft can be shown or hidden. */}
            <button
              type="button"
              className="msiconbtn"
              aria-label={secretRevealed ? "Hide the key you are typing" : "Show the key you are typing"}
              title={secretRevealed ? "Hide the key you are typing" : "Show the key you are typing"}
              onClick={() => setSecretRevealed((value) => !value)}
              disabled={!secretDraft}
            >
              <Icon name={secretRevealed ? "eyeOff" : "eye"} />
            </button>
            <button
              type="button"
              className="secondary-button mssave-key"
              onClick={commitSecret}
              disabled={!secretDraft || busy}
              title="Store this API key (Enter also saves)"
            >
              Save key
            </button>
            {configured ? <span className="msbadge tone-ok">Stored</span> : null}
          </div>
          {/* A typed key lives only in this field until it is committed —
              the card and every model row keep reading "Credential missing"
              until then, so the state must be said out loud. */}
          {!configured && secretDraft ? (
            <p className="mshint" role="note">
              The key is typed but not stored yet — press Enter or use “Save key”.
              It stays only in this field until then.
            </p>
          ) : null}
        </div>

        {patchState ? (
          <p className={patchState.ok ? "msprovider-ok" : "msprovider-fail"} role="status">
            {patchState.message}
          </p>
        ) : null}

        <div className="msrow msrow-stack">
          <div className="msrow-label-row">
            <div className="msrow-label">Model list</div>
            <div className="msrow-actions">
              <button
                type="button"
                className="mslink"
                onClick={onShowAll}
                disabled={busy || models.length === 0 || shown === models.length}
              >
                Show all
              </button>
              <button
                type="button"
                className="mslink"
                onClick={onHideAll}
                disabled={busy || shown === 0}
              >
                Hide all
              </button>
              <button type="button" className="msbtn" onClick={onOpenAddModel} disabled={busy}>
                <span aria-hidden="true">+</span> Add model
              </button>
            </div>
          </div>

          <div className="msrow-value">
            {models.length === 0 ? (
              <p className="msprovider-empty">
                No models are configured. Add a model to use it in chat, or run Test connection
                to read this provider's catalog.
              </p>
            ) : (
              <ul className="msmodels">
                {models.map((model) => {
                  const record = healthFor(health, provider, model);
                  const badge = evidenceBadge(record?.state);
                  const visible = provider.enabled_model_ids.includes(model);
                  const meta = provider.model_meta?.[model];
                  const contextBadge = formatTokenCount(meta?.context_window);
                  const outputBadge = formatTokenCount(meta?.max_output_tokens);
                  const results = modelResults?.[`${provider.id}:${model}`];
                  return (
                    <li key={model} className="msmodel">
                      <code className="msmodel-name">{model}</code>
                      {contextBadge ? (
                        <span
                          className="msbadge meta"
                          title={`Context window, as you recorded it: ${meta?.context_window} tokens. AMCA has not measured this.`}
                        >
                          {contextBadge}
                        </span>
                      ) : null}
                      {outputBadge ? (
                        <span
                          className="msbadge meta"
                          title={`Max output tokens, as you recorded them: ${meta?.max_output_tokens}. AMCA has not measured this.`}
                        >
                          out {outputBadge}
                        </span>
                      ) : null}
                      <span className={`msbadge tone-${badge.tone}`}>{badge.label}</span>
                      {results ? (
                        <small className={results.ok ? "msprovider-ok" : "msprovider-fail"}>
                          {results.message}
                        </small>
                      ) : null}
                      <span className="msmodel-actions">
                        <button
                          type="button"
                          className="msiconbtn"
                          onClick={() => onVerifyModel(model)}
                          disabled={busy}
                          aria-label={`Verify ${model}`}
                          title="Verify: one bounded 1-token request records the true health state."
                        >
                          <Icon name="plug" />
                        </button>
                        <button
                          type="button"
                          className="msiconbtn"
                          onClick={() => onEditModel(model)}
                          disabled={busy}
                          aria-label={`Edit recorded details of ${model}`}
                          title="Edit the recorded details of this model."
                        >
                          <Icon name="pencil" />
                        </button>
                        <button
                          type="button"
                          className="msiconbtn danger"
                          onClick={() => onRemoveModel(model)}
                          disabled={busy}
                          aria-label={`Remove ${model}`}
                          title="Remove this model from the provider. The provider and its credential are kept."
                        >
                          <Icon name="trash" />
                        </button>
                        <button
                          type="button"
                          className={`mstoggle${visible ? " on" : ""}`}
                          role="switch"
                          aria-checked={visible}
                          aria-label={`${visible ? "Exclude" : "Include"} ${model} ${visible ? "from" : "in"} chat`}
                          title={visible ? "Shown in chat — toggle to exclude" : "Hidden — toggle to include in chat"}
                          disabled={busy}
                          onClick={() => onToggleModel(model, !visible)}
                        >
                          <span className="mstoggle-knob" />
                        </button>
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}

            {modelMessage ? (
              <p className={modelMessage.ok ? "msprovider-ok" : "msprovider-fail"} role="status">
                {modelMessage.message}
              </p>
            ) : null}
          </div>
        </div>
      </div>

      <button
        type="button"
        className="msdisclosure"
        aria-expanded={advancedOpen}
        onClick={() => setAdvancedOpen((value) => !value)}
      >
        <span aria-hidden="true">{advancedOpen ? "▾" : "›"}</span> Advanced settings
      </button>
      {advancedOpen ? (
        <div className="msprovider-advanced">
          <div className="msrow msrow-live">
            <label className="msrow-label" htmlFor={`pricing-${provider.id}`}>Pricing mode</label>
            <select
              id={`pricing-${provider.id}`}
              className="msrow-input"
              value={provider.pricing_mode}
              disabled={busy}
              onChange={(event) => commitPatch({ pricing_mode: event.target.value as "auto" | "manual" }, "Pricing mode")}
            >
              <option value="auto">Auto-detect later</option>
              <option value="manual">Enter manually later</option>
            </select>
          </div>
          <p className="msrow-note">
            Recorded badges on model rows show what you typed about this provider. AMCA has not
            measured those numbers, and routing never reads them.
          </p>
        </div>
      ) : null}

      <p className="msrow-note">
        Saving performs no inference call. Test connection makes one bounded catalog request.
        Verify sends one bounded 1-token request per model and records the true health state.
      </p>
    </div>
  );
}
