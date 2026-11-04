// ZCODE-AMCA-STUDIO-V0.8.0-KNOWLEDGE-INTELLIGENCE-UX-FOUNDATION-01 — pure model.
//
// Foundation UX for the recorded v0.8.0 Knowledge Intelligence backend
// contract (AG knowledge modules on the architecture track):
// - retrieval modes are the backend's recorded three; nothing invented;
// - requests are EXPLICIT ONLY (user-initiated) — the backend's contract is
//   explicit search with role validation and audit logging, so the UI never
//   retrieves in the background;
// - retrieval-mode transparency: every result renders the mode that produced
//   it, both lexical/semantic ranks when present, and the diagnostics line;
// - provenance references are shown verbatim; nothing is inferred;
// - absence/failure are first-class honest states (endpoint absent, request
//   failed, empty index, redacted query) with recorded reasons — the UI
//   never fabricates results or claims a connection that is not there.

export type RetrievalMode = "lexical_only" | "semantic_only" | "hybrid_rrf";

export const RETRIEVAL_MODES: ReadonlyArray<{
  mode: RetrievalMode;
  label: string;
  transparency: string;
}> = [
  {
    mode: "lexical_only",
    label: "Lexical",
    transparency: "Full-text match over the recorded artifact index — exact terms only.",
  },
  {
    mode: "semantic_only",
    label: "Semantic",
    transparency: "Local embedding similarity over the recorded index — meaning-based, still offline.",
  },
  {
    mode: "hybrid_rrf",
    label: "Hybrid (RRF)",
    transparency: "Reciprocal-rank fusion of the lexical and semantic lists — both ranks shown per result.",
  },
];

export function retrievalModeInfo(mode: string): { mode: RetrievalMode; label: string; transparency: string } {
  return RETRIEVAL_MODES.find((entry) => entry.mode === mode) ?? RETRIEVAL_MODES[2];
}

export function isRecordedMode(mode: string): mode is RetrievalMode {
  return RETRIEVAL_MODES.some((entry) => entry.mode === mode);
}

/** Recorded human-operator role for the governed search contract. */
export const REQUESTER_ROLE = "admin";
export const KNOWLEDGE_SEARCH_INTENT = "research_context";
export const MAX_KNOWLEDGE_RESULTS = 10;
export const DEFAULT_KNOWLEDGE_RESULTS = 5;

// ---------- Explicit request draft ----------

export interface KnowledgeRequestDraft {
  query: string;
  mode: RetrievalMode;
  maxItems: number;
}

export interface KnowledgeRequestValidation {
  ok: boolean;
  errors: string[];
}

export function validateKnowledgeRequest(draft: KnowledgeRequestDraft): KnowledgeRequestValidation {
  const errors: string[] = [];
  if (!draft.query.trim()) errors.push("Enter what to look for in the recorded knowledge index.");
  if (!isRecordedMode(draft.mode)) errors.push(`Retrieval mode must be one of the recorded modes: ${RETRIEVAL_MODES.map((m) => m.mode).join(", ")}.`);
  if (!Number.isInteger(draft.maxItems) || draft.maxItems < 1 || draft.maxItems > MAX_KNOWLEDGE_RESULTS) {
    errors.push(`Result count must be between 1 and ${MAX_KNOWLEDGE_RESULTS}.`);
  }
  return { ok: errors.length === 0, errors };
}

// ---------- Wire shapes (recorded backend contract) ----------

export interface RetrievalDiagnostics {
  query_latency_ms: number;
  candidate_count: number;
  retrieval_mode_used: string;
  fallback_triggered: boolean;
}

export interface RankFields {
  lexical_rank?: number | null;
  semantic_rank?: number | null;
}

export interface KnowledgeResultItem extends RankFields {
  artifact_id: string;
  workspace_id: string;
  title: string;
  excerpt: string;
  relevance_score: number;
  provenance_reference: string;
  permission_verified: boolean;
}

export interface KnowledgeSearchRequest {
  workspace_id: string;
  requester_role: string;
  query: string;
  mode: RetrievalMode;
  max_items: number;
  intent: string;
}

export interface KnowledgeSearchResponse {
  package_id: string;
  workspace_id: string;
  requester_role: string;
  items: KnowledgeResultItem[];
  audit_event_id: string;
  created_at: string;
  permission_decision?: string;
  diagnostics?: RetrievalDiagnostics;
}

/** Build the wire request from a validated draft — recorded fields only. */
export function toWireRequest(draft: KnowledgeRequestDraft, workspaceId: string): KnowledgeSearchRequest {
  return {
    workspace_id: workspaceId,
    requester_role: REQUESTER_ROLE,
    query: draft.query.trim(),
    mode: draft.mode,
    max_items: draft.maxItems,
    intent: KNOWLEDGE_SEARCH_INTENT,
  };
}

// ---------- Honest endpoint/panel states ----------

export type KnowledgeEndpointState = "ready" | "endpoint_absent" | "request_failed";

/**
 * Classify a search failure. An absent endpoint (the running service does
 * not bind /api/knowledge/search yet) is a recorded foundation state, not a
 * generic error — the panel says exactly that instead of a vague failure.
 */
export function classifyEndpointFailure(error: unknown): { state: KnowledgeEndpointState; title: string; detail: string } {
  const message = error instanceof Error ? error.message : String(error);
  if (/404|not found|unknown route|cannot (?:be )?found/i.test(message)) {
    return {
      state: "endpoint_absent",
      title: "Knowledge search endpoint not connected",
      detail:
        "The running service does not expose the knowledge search endpoint yet — the recorded backend contract exists on the architecture track, but no HTTP route is bound in this build. Nothing was retrieved and nothing is inferred.",
    };
  }
  return {
    state: "request_failed",
    title: "Knowledge search failed",
    detail: `The service returned an error and no results are shown: ${message}`,
  };
}

export const KNOWLEDGE_SEARCH_EMPTY = {
  title: "No matches in the recorded index",
  detail: "The search completed and found nothing for this query — that means no recorded artifact matches, not that knowledge does not exist. Try another mode or wording.",
} as const;

// ---------- Transparency presentation ----------

export interface ResultTransparency {
  modeLabel: string;
  rankLine: string;
  scoreLabel: string;
  provenanceLine: string;
  permissionLine: string;
}

/**
 * Present one result with full retrieval transparency: the mode that found
 * it, both recorded ranks when present ("—" when a rank was not produced),
 * the fusion score, and the verbatim provenance reference.
 */
export function presentResult(item: KnowledgeResultItem, mode: RetrievalMode): ResultTransparency {
  const info = retrievalModeInfo(mode);
  const rankLine =
    mode === "hybrid_rrf"
      ? `lexical rank ${item.lexical_rank ?? "—"} · semantic rank ${item.semantic_rank ?? "—"}`
      : mode === "lexical_only"
        ? `lexical rank ${item.lexical_rank ?? "—"}`
        : `semantic rank ${item.semantic_rank ?? "—"}`;
  return {
    modeLabel: info.label,
    rankLine,
    scoreLabel: `fusion score ${item.relevance_score.toFixed(3)}`,
    provenanceLine: item.provenance_reference || "No provenance reference recorded",
    permissionLine: item.permission_verified ? "Permission verified" : "Permission not verified by the service",
  };
}

/** Diagnostics line rendered above results — only real recorded values. */
export function retrievalTransparencyLine(diagnostics: RetrievalDiagnostics | undefined): string | null {
  if (!diagnostics) return null;
  const parts = [
    `mode used: ${diagnostics.retrieval_mode_used}`,
    `${diagnostics.candidate_count} candidate(s)`,
    `${Math.round(diagnostics.query_latency_ms)} ms`,
  ];
  if (diagnostics.fallback_triggered) parts.push("fallback triggered");
  return parts.join(" · ");
}
