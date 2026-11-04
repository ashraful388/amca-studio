import React, { useState } from "react";
import {
  amca,
} from "./api";
import {
  DEFAULT_KNOWLEDGE_RESULTS,
  RETRIEVAL_MODES,
  classifyEndpointFailure,
  presentResult,
  retrievalModeInfo,
  retrievalTransparencyLine,
  toWireRequest,
  validateKnowledgeRequest,
  KNOWLEDGE_SEARCH_EMPTY,
  type KnowledgeRequestDraft,
  type KnowledgeSearchResponse,
  type RetrievalMode,
} from "./knowledgeIntelligenceModel";

/**
 * v0.8.0 Knowledge Intelligence UX foundation — explicit request flow.
 *
 * Search runs ONLY when the user asks (the backend contract is explicit
 * search with audit logging; nothing here retrieves automatically). The
 * panel is transparent about which retrieval mode ran, shows both ranks and
 * the fusion score per result, renders provenance references verbatim, and
 * treats an absent endpoint / failed request / empty index as recorded
 * honest states rather than hiding them.
 */
export const KnowledgeSearchPanel: React.FC<{ workspaceId: string }> = ({ workspaceId }) => {
  const [draft, setDraft] = useState<KnowledgeRequestDraft>({ query: "", mode: "hybrid_rrf", maxItems: DEFAULT_KNOWLEDGE_RESULTS });
  const [busy, setBusy] = useState(false);
  const [validation, setValidation] = useState<string[]>([]);
  const [failure, setFailure] = useState<{ state: string; title: string; detail: string } | null>(null);
  const [response, setResponse] = useState<KnowledgeSearchResponse | null>(null);
  const [usedMode, setUsedMode] = useState<RetrievalMode | null>(null);

  const runSearch = async () => {
    const check = validateKnowledgeRequest(draft);
    setValidation(check.errors);
    if (!check.ok) return;
    setBusy(true);
    setFailure(null);
    setResponse(null);
    try {
      const result = await amca.knowledgeSearch(toWireRequest(draft, workspaceId));
      setResponse(result);
      setUsedMode(draft.mode);
    } catch (caught) {
      setFailure(classifyEndpointFailure(caught));
    } finally {
      setBusy(false);
    }
  };

  const transparency = response ? retrievalTransparencyLine(response.diagnostics) : null;
  const permissionDenied = response?.permission_decision ? /denied/i.test(response.permission_decision) : false;

  return (
    <div className="ki-search-panel">
      <h4 style={{ margin: "0 0 4px 0", color: "#fff" }}>Explicit knowledge search (foundation)</h4>
      <p className="ki-note">
        User-initiated only — nothing is retrieved in the background. Each search is audited by the service with your
        recorded role, and every result shows the retrieval mode, ranks, and provenance that produced it.
      </p>
      <div className="ki-request-row">
        <input
          type="text"
          value={draft.query}
          placeholder="What should the recorded index be searched for?"
          aria-label="Knowledge search query"
          onChange={(event) => setDraft((current) => ({ ...current, query: event.target.value }))}
          onKeyDown={(event) => { if (event.key === "Enter") void runSearch(); }}
          disabled={busy}
        />
        <select
          value={draft.mode}
          aria-label="Retrieval mode"
          onChange={(event) => setDraft((current) => ({ ...current, mode: event.target.value as RetrievalMode }))}
          disabled={busy}
        >
          {RETRIEVAL_MODES.map((entry) => (
            <option key={entry.mode} value={entry.mode}>{entry.label}</option>
          ))}
        </select>
        <button type="button" onClick={() => void runSearch()} disabled={busy}>
          {busy ? "Searching…" : "Search"}
        </button>
      </div>
      <small className="ki-note">{retrievalModeInfo(draft.mode).transparency}</small>
      {validation.length > 0 && (
        <p className="ki-state" role="alert">{validation.join(" ")}</p>
      )}

      {failure && (
        <div className="ki-state-box" role="note">
          <strong>{failure.title}</strong>
          <p>{failure.detail}</p>
        </div>
      )}

      {response && !failure && (
        <div className="ki-results">
          <small className="ki-note">
            {response.items.length} result(s) · package {response.package_id.slice(0, 12)}… · audited as {response.audit_event_id.slice(0, 12)}…
            {response.permission_decision ? ` · permission: ${response.permission_decision}` : ""}
          </small>
          {transparency && <small className="ki-note">{transparency}</small>}
          {permissionDenied && (
            <p className="ki-state" role="alert">
              The service denied this search for the recorded role — no results are shown.
            </p>
          )}
          {!permissionDenied && response.items.length === 0 && (
            <div className="ki-state-box">
              <strong>{KNOWLEDGE_SEARCH_EMPTY.title}</strong>
              <p>{KNOWLEDGE_SEARCH_EMPTY.detail}</p>
            </div>
          )}
          {!permissionDenied && usedMode && response.items.map((item) => {
            const view = presentResult(item, usedMode);
            return (
              <div key={item.artifact_id} className="ki-result">
                <div className="ki-result-head">
                  <strong>{item.title}</strong>
                  <span className="ki-chip">{view.modeLabel}</span>
                </div>
                <p className="ki-excerpt">{item.excerpt}</p>
                <small className="ki-meta">{view.rankLine} · {view.scoreLabel} · {view.permissionLine}</small>
                <small className="ki-meta ki-provenance">Provenance: {view.provenanceLine}</small>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
