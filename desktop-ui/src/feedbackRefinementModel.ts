// BETA-B19 Feedback Refinement UX — pure presentation model (no fetch,
// no DOM). Closes the loop between validation findings, known limitations,
// user feedback, and improvement tracking from EXISTING records only:
// the accepted limitation registry provides the recorded refinement chains,
// and feedback records are session-scoped local documents (this build has
// NO feedback persistence backend — disclosed, never hidden). No automatic
// classification, no sentiment analysis, no ranking, no telemetry, no
// cloud submission.

import type { LimitationRecord } from "./validationCenterModel";

// ---------- 1. Feedback categories (order scope 2) ----------
// Fixed list rendered verbatim; the user picks one — the app never
// classifies feedback automatically.

export const FEEDBACK_CATEGORIES = [
  { id: "ux", label: "UX" },
  { id: "performance", label: "Performance" },
  { id: "reliability", label: "Reliability" },
  { id: "documentation", label: "Documentation" },
  { id: "model_configuration", label: "Model Configuration" },
  { id: "workflow", label: "Workflow" },
  { id: "security", label: "Security" },
  { id: "feature_request", label: "Feature Request" },
] as const;

export type FeedbackCategoryId = (typeof FEEDBACK_CATEGORIES)[number]["id"];

export function categoryById(id: string): { id: string; label: string } | null {
  return FEEDBACK_CATEGORIES.find((category) => category.id === id) ?? null;
}

// Affected-module choices come from the recorded limitation modules so the
// refinement view can only reference modules that already exist in records,
// plus an explicit escape hatch. Selection is a user assertion, never an
// app inference.
export function feedbackModules(limitations: LimitationRecord[]): string[] {
  return [...new Set(limitations.map((record) => record.module)), "Other / unlisted"];
}

// ---------- 2. Feedback records (order scope 1) ----------
// Session-scoped local documents. The P1 panel composes copy-drafts and
// stores nothing; this center lets the user keep a working list in the
// open session, with the scope disclosed on every surface. There is no
// feedback persistence backend — records never claim to survive restarts.

export type FeedbackStatus = "open" | "resolved";

export interface FeedbackRecord {
  id: string;
  categoryId: FeedbackCategoryId | "unclassified";
  categoryLabel: string;
  module: string;
  text: string;
  status: FeedbackStatus;
  resolution: string;
  createdAt: string;
  storage: "session_local";
}

let recordCounter = 0;

export function addFeedbackRecord(input: {
  categoryId: string;
  module: string;
  text: string;
  createdAt: string;
}): FeedbackRecord | null {
  const text = input.text.trim();
  if (!text) return null;
  const category = categoryById(input.categoryId);
  const module = input.module.trim();
  if (!module) return null;
  recordCounter += 1;
  return {
    id: `feedback-${recordCounter}`,
    // No auto-classification: an unknown category id stays literally
    // "unclassified" rather than being guessed.
    categoryId: category ? (category.id as FeedbackCategoryId) : "unclassified",
    categoryLabel: category ? category.label : "Unclassified",
    module,
    text,
    status: "open",
    resolution: "not recorded",
    createdAt: input.createdAt,
    storage: "session_local",
  };
}

export function resolveFeedbackRecord(records: FeedbackRecord[], id: string): FeedbackRecord[] {
  return records.map((record) =>
    record.id === id
      ? { ...record, status: "resolved" as const, resolution: "resolved locally — no backend record exists" }
      : record,
  );
}

// ---------- 3. Refinement tracking (order scope 3) ----------
// Feedback → Affected Module → Known Limitation → Resolution Status →
// Target Release. Rendered ONLY where links are actually recorded: the
// limitation registry carries module/status/target for each record. User
// feedback records have no linkage backend, so their chain honestly shows
// "not recorded" — the app never pairs a complaint with a limitation by
// guessing.

export interface RefinementChain {
  key: string;
  feedback: string;
  affectedModule: string;
  knownLimitation: string;
  resolutionStatus: string;
  targetRelease: string;
  linksRecorded: boolean;
}

export function limitationRefinementChains(limitations: LimitationRecord[]): RefinementChain[] {
  return limitations.map((record, index) => ({
    key: `limitation-${index}`,
    feedback: "Recorded limitation record (not user feedback)",
    affectedModule: record.module,
    knownLimitation: record.title,
    resolutionStatus: record.workaround,
    targetRelease: record.targetRelease,
    linksRecorded: true,
  }));
}

export function feedbackRecordChain(record: FeedbackRecord): RefinementChain {
  return {
    key: record.id,
    feedback: record.text.length > 90 ? `${record.text.slice(0, 90)}…` : record.text,
    affectedModule: record.module,
    knownLimitation: "not recorded — no linkage exists between feedback and limitation records in this build",
    resolutionStatus: record.resolution,
    targetRelease: "not recorded",
    linksRecorded: false,
  };
}

// ---------- 4. Beta improvement summary (order scope 4) ----------

export interface ImprovementSummary {
  feedbackCount: number;
  openItems: number;
  resolvedItems: number;
  limitationCount: number;
  line: string;
}

export function improvementSummary(records: FeedbackRecord[], limitationCount: number): ImprovementSummary {
  const open = records.filter((record) => record.status === "open").length;
  const resolved = records.length - open;
  const summary: ImprovementSummary = {
    feedbackCount: records.length,
    openItems: open,
    resolvedItems: resolved,
    limitationCount,
    line: "",
  };
  summary.line =
    `${records.length} feedback record${records.length === 1 ? "" : "s"} this session · ${open} open · ${resolved} resolved · ${limitationCount} recorded limitation${limitationCount === 1 ? "" : "s"}`
    + (records.length === 0 ? " · no cross-session feedback store exists in this build" : "");
  return summary;
}

// No satisfaction score, no sentiment analysis, no AI ranking: the summary
// type above is the complete payload — counts and counts only.

// ---------- 5. Privacy UX (order scope 5) ----------

export const FEEDBACK_PRIVACY = {
  headline: "Stored locally · No telemetry · No automatic submission",
  detail:
    "Feedback records live in this open Studio session only — this build has no feedback persistence backend. Nothing is sent anywhere; copy a record's text if you want to keep or share it yourself. Records clear when Studio closes.",
  sessionScope: "Session records — cleared when Studio closes (no persistence backend in this build).",
} as const;
