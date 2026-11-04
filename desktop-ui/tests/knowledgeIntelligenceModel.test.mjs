// ZCODE-AMCA-STUDIO-V0.8.0-KNOWLEDGE-INTELLIGENCE-UX-FOUNDATION-01 tests.
// Covers the recorded contract shapes (modes, roles, diagnostics), explicit
// request validation, honest endpoint/empty/failure classification, and
// retrieval-transparency presentation.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  RETRIEVAL_MODES,
  isRecordedMode,
  retrievalModeInfo,
  REQUESTER_ROLE,
  MAX_KNOWLEDGE_RESULTS,
  validateKnowledgeRequest,
  toWireRequest,
  classifyEndpointFailure,
  KNOWLEDGE_SEARCH_EMPTY,
  presentResult,
  retrievalTransparencyLine,
} from "../src/knowledgeIntelligenceModel.ts";

// ---------- Recorded contract fidelity ----------

test("retrieval modes are exactly the backend's recorded three", () => {
  assert.deepEqual(
    RETRIEVAL_MODES.map((m) => m.mode),
    ["lexical_only", "semantic_only", "hybrid_rrf"],
  );
  assert.equal(isRecordedMode("hybrid_rrf"), true);
  assert.equal(isRecordedMode("hybrid-rrf"), false);
  assert.equal(isRecordedMode("psychic"), false);
  assert.equal(retrievalModeInfo("lexical_only").label, "Lexical");
  assert.ok(retrievalModeInfo("nonsense").transparency.length > 10);
});

test("wire request carries only recorded fields with the audited role", () => {
  const wire = toWireRequest({ query: "  catalyst notes  ", mode: "hybrid_rrf", maxItems: 5 }, "ws-1");
  assert.deepEqual(wire, {
    workspace_id: "ws-1",
    requester_role: REQUESTER_ROLE,
    query: "catalyst notes",
    mode: "hybrid_rrf",
    max_items: 5,
    intent: "research_context",
  });
  assert.ok(["researcher", "coder", "reviewer", "admin"].includes(REQUESTER_ROLE));
});

// ---------- Explicit request validation ----------

test("explicit request validation requires query, recorded mode, bounded results", () => {
  const blank = validateKnowledgeRequest({ query: "   ", mode: "hybrid_rrf", maxItems: 5 });
  assert.equal(blank.ok, false);
  assert.ok(blank.errors.some((e) => e.includes("Enter what to look for")));

  const badMode = validateKnowledgeRequest({ query: "q", mode: "auto_magic", maxItems: 5 });
  assert.equal(badMode.ok, false);
  assert.ok(badMode.errors.some((e) => e.includes("recorded modes")));

  const tooMany = validateKnowledgeRequest({ query: "q", mode: "lexical_only", maxItems: MAX_KNOWLEDGE_RESULTS + 1 });
  assert.equal(tooMany.ok, false);

  const zero = validateKnowledgeRequest({ query: "q", mode: "lexical_only", maxItems: 0 });
  assert.equal(zero.ok, false);

  assert.equal(validateKnowledgeRequest({ query: "q", mode: "semantic_only", maxItems: 3 }).ok, true);
});

// ---------- Honest endpoint / empty / failure states ----------

test("absent endpoint is a recorded foundation state, not a vague error", () => {
  const absent = classifyEndpointFailure(new Error("AMCA service returned 404"));
  assert.equal(absent.state, "endpoint_absent");
  assert.ok(absent.title.includes("not connected"));
  assert.ok(absent.detail.includes("Nothing was retrieved and nothing is inferred"));

  const failed = classifyEndpointFailure(new Error("boom"));
  assert.equal(failed.state, "request_failed");
  assert.ok(failed.detail.includes("boom"));

  const notFoundWords = classifyEndpointFailure(new Error("unknown route for knowledge"));
  assert.equal(notFoundWords.state, "endpoint_absent");
});

test("empty index state separates no-match from no-knowledge", () => {
  assert.equal(KNOWLEDGE_SEARCH_EMPTY.title, "No matches in the recorded index");
  assert.ok(KNOWLEDGE_SEARCH_EMPTY.detail.includes("not that knowledge does not exist"));
});

// ---------- Retrieval transparency presentation ----------

test("results render both ranks, score, and verbatim provenance", () => {
  const base = {
    artifact_id: "a1",
    workspace_id: "ws",
    title: "T",
    excerpt: "E",
    relevance_score: 0.5671,
    provenance_reference: "run-42/artifact-a1",
    permission_verified: true,
  };
  const hybrid = presentResult(
    { ...base, lexical_rank: 2, semantic_rank: null },
    "hybrid_rrf",
  );
  assert.equal(hybrid.rankLine, "lexical rank 2 · semantic rank —");
  assert.equal(hybrid.scoreLabel, "fusion score 0.567");
  assert.equal(hybrid.modeLabel, "Hybrid (RRF)");
  assert.equal(hybrid.provenanceLine, "run-42/artifact-a1");
  assert.equal(hybrid.permissionLine, "Permission verified");

  const lexical = presentResult({ ...base, lexical_rank: 1, semantic_rank: 3 }, "lexical_only");
  assert.equal(lexical.rankLine, "lexical rank 1");

  const semantic = presentResult({ ...base }, "semantic_only");
  assert.equal(semantic.rankLine, "semantic rank —");

  const noProv = presentResult({ ...base, provenance_reference: "" }, "lexical_only");
  assert.equal(noProv.provenanceLine, "No provenance reference recorded");
  const unverified = presentResult({ ...base, permission_verified: false }, "lexical_only");
  assert.equal(unverified.permissionLine, "Permission not verified by the service");
});

test("diagnostics line reports only recorded values and flags fallback", () => {
  assert.equal(retrievalTransparencyLine(undefined), null);
  const line = retrievalTransparencyLine({
    query_latency_ms: 12.4,
    candidate_count: 7,
    retrieval_mode_used: "hybrid_rrf",
    fallback_triggered: true,
  });
  assert.ok(line?.includes("mode used: hybrid_rrf"));
  assert.ok(line?.includes("7 candidate(s)"));
  assert.ok(line?.includes("12 ms"));
  assert.ok(line?.includes("fallback triggered"));
  const quiet = retrievalTransparencyLine({
    query_latency_ms: 3,
    candidate_count: 0,
    retrieval_mode_used: "lexical_only",
    fallback_triggered: false,
  });
  assert.ok(!quiet?.includes("fallback"));
});
