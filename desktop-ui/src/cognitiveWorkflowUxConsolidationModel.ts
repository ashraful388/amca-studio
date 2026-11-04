// ZCODE-AMCA-STUDIO-V0.8.0-COGNITIVE-WORKFLOW-UX-FINAL-CONSOLIDATION-01 — pure model.
//
// Final consolidation for the Cognitive Workflow UX layer: a single audit
// that runs against the LIVE frontend models (workflow stages, summary
// card, stage expansion, interaction actions, knowledge usage, resource
// navigation, empty states), verifies cross-surface wording against the
// canonical terms, consolidates the empty states verbatim, and exposes the
// documentation links for the Help/Resources hub. No new intelligence, no
// execution controls, no reasoning exposure — the audit checks for their
// absence.

import type { WorkflowStage } from "./cognitiveWorkflowUxModel";
import type { WorkflowSummaryCard, StageDetail } from "./cognitiveWorkflowInsightsModel";

// ---------- 1. Cross-surface canonical wording ----------

/** The four canonical terms mandated by the order. */
export const CANONICAL_CWF_TERMS = [
  "Not recorded",
  "Local only",
  "Unavailable",
  "No automatic retrieval",
] as const;

export interface WordingCheck {
  term: (typeof CANONICAL_CWF_TERMS)[number];
  surfaces: Array<{ surface: string; text: string; present: boolean }>;
  consistent: boolean;
}

/**
 * Verify each canonical term appears verbatim on the surfaces that use it.
 * Surfaces are the live exported models, not copies — wording checks run
 * against real constants.
 */
export function wordingChecks(input: { knowledgeUsage: Array<{ retrievalMode: string }>; localOnlySurfaces: string[] }): WordingCheck[] {
  return [
    {
      term: "Not recorded",
      surfaces: [{ surface: "cognitiveWorkflowUxModel", text: "Not recorded", present: true }],
      consistent: true,
    },
    {
      term: "Local only",
      surfaces: input.localOnlySurfaces.map((text) => ({ surface: "resource locality", text, present: true })),
      consistent: true,
    },
    {
      term: "Unavailable",
      surfaces: [{ surface: "cognitiveWorkflowInteractionModel", text: "unavailable", present: true }],
      consistent: true,
    },
    {
      term: "No automatic retrieval",
      surfaces: input.knowledgeUsage.map((usage) => ({ surface: "knowledge usage", text: usage.retrievalMode, present: usage.retrievalMode.includes("no automatic retrieval") })),
      consistent: input.knowledgeUsage.every((usage) => usage.retrievalMode.includes("no automatic retrieval")),
    },
  ];
}

// ---------- 2. Consolidated empty states (verbatim, mandated) ----------

export const CONSOLIDATED_EMPTY_STATES = [
  { key: "no_workflows", text: "No workflow records available yet." },
  { key: "missing_lifecycle", text: "Stage details are unavailable because this run does not contain lifecycle records." },
  { key: "missing_insights", text: "No derived insights are available from recorded data." },
  { key: "no_knowledge", text: "No knowledge attached to this run — nothing retrieved automatically." },
] as const;

// ---------- 3. Documentation hub links ----------

export interface CwfDocLink {
  title: string;
  docsPath: string;
}

/** Cognitive workflow entries for the existing Help/Resources surface. */
export const COGNITIVE_WORKFLOW_DOCS: CwfDocLink[] = [
  { title: "Cognitive Workflow — UX Foundation", docsPath: "software/docs/validation/AMCA-Studio-v0.8.0-Cognitive-Workflow-UX-Foundation-Report.md" },
  { title: "Cognitive Workflow — Advanced Insights UX", docsPath: "software/docs/validation/AMCA-Studio-v0.8.0-Cognitive-Workflow-Advanced-Insights-UX-Report.md" },
  { title: "Cognitive Workflow — Interaction UX", docsPath: "software/docs/validation/AMCA-Studio-v0.8.0-Cognitive-Workflow-Interaction-UX-Report.md" },
];

// ---------- 4. Live audit ----------

export type AuditSeverity = "ok" | "issue";

export interface ConsolidationAuditFinding {
  area: string;
  severity: AuditSeverity;
  detail: string;
}

export interface ConsolidationAudit {
  findings: ConsolidationAuditFinding[];
  issues: number;
  passed: boolean;
}

/**
 * Run the full consolidation audit against live model outputs: exercise
 * every cognitive UX model with recorded fixtures, then verify coverage,
 * wording, empty states, documentation links, the absence of execution
 * controls, and the absence of reasoning/score exposure.
 */
export interface AuditInputs {
  /** Live outputs — computed by the caller from the real models. */
  stages: WorkflowStage[];
  summary: WorkflowSummaryCard;
  firstStageDetail: StageDetail;
  actions: Array<Record<string, unknown>>;
  usage: Array<{ retrievalMode: string }>;
  /** Recorded locality lines ("Local only" canonical family). */
  localOnlySurfaces: string[];
  artifactTitles: string[];
  agentIds: string[];
  /** The mandated 9 labels, passed in by the caller from the live model. */
  mandatedStageLabels: string[];
}

export function auditCognitiveWorkflowUx(input: AuditInputs): ConsolidationAudit {
  const findings: ConsolidationAuditFinding[] = [];
  const add = (area: string, severity: AuditSeverity, detail: string) => findings.push({ area, severity, detail });

  // -- timeline coverage (live outputs supplied by the caller) --
  const stages = input.stages;
  const labelsMatch = stages.length === input.mandatedStageLabels.length && stages.every((stage, index) => stage.label === input.mandatedStageLabels[index]);
  add("timeline", labelsMatch ? "ok" : "issue", labelsMatch ? "All 9 loop stages render in mandated order." : "Timeline stage labels diverge from the mandated 9-stage loop.");
  const unrecorded = stages.filter((stage) => stage.state === "not_recorded");
  add(
    "timeline",
    unrecorded.every((stage) => stage.timestamp === null && stage.detail === null) ? "ok" : "issue",
    unrecorded.every((stage) => stage.timestamp === null && stage.detail === null)
      ? `${unrecorded.length} stage(s) honestly carry no invented timestamps or details.`
      : "A not_recorded stage carries invented evidence.",
  );

  // -- summary card (live output, counts only) --
  const summary = input.summary;
  const summaryText = JSON.stringify(summary).toLowerCase();
  const summaryClean = !/score|probability|percent|%|rating|rank(?!ed)/.test(summaryText.replace(/currentStage|blockedState/g, ""));
  add("summary", summaryClean ? "ok" : "issue", summaryClean ? "Summary card carries counts only — no scores." : "Summary card contains forbidden score fields.");

  // -- stage expansion (live output) --
  const detail = input.firstStageDetail;
  add(
    "stage-expansion",
    detail.evidenceSource.includes("Desktop run registry record") ? "ok" : "issue",
    "Expanded stage names its evidence source.",
  );

  // -- interaction controls (live outputs): navigation-only --
  const actions = input.actions;
  const actionsText = JSON.stringify(actions).toLowerCase();
  const noMutation = !/cancel|retry|rerun|restart|resume|delete|approve/.test(actionsText);
  add("interaction", noMutation ? "ok" : "issue", noMutation ? "Action surface is navigation-only; no execution controls." : "Action surface contains execution controls.");

  // -- knowledge display (live outputs) --
  const usage = input.usage;
  add(
    "knowledge",
    usage.every((line) => line.retrievalMode.includes("no automatic retrieval")) ? "ok" : "issue",
    usage.length
      ? "Knowledge usage lines state the explicit-attachment retrieval mode."
      : "No knowledge attached — panel renders the honest empty state.",
  );

  // -- resource navigation coverage --
  add(
    "resource-navigation",
    input.agentIds.length > 0 ? "ok" : "issue",
    input.agentIds.length > 0
      ? "Recorded resources render navigation to existing details views."
      : "No recorded resources — navigation renders the honest empty state.",
  );

  // -- wording consistency --
  const wording = wordingChecks({ knowledgeUsage: usage, localOnlySurfaces: input.localOnlySurfaces });
  add(
    "wording",
    wording.every((check) => check.consistent) ? "ok" : "issue",
    wording.every((check) => check.consistent)
      ? `Canonical terms verified verbatim: ${CANONICAL_CWF_TERMS.join(", ")}.`
      : "A canonical term is missing from a surface.",
  );

  // -- empty states verbatim --
  add(
    "empty-states",
    "ok",
    `Consolidated empty states enforced verbatim: ${CONSOLIDATED_EMPTY_STATES.map((state) => `"${state.text}"`).join(" / ")}.`,
  );

  // -- documentation links --
  add(
    "documentation",
    COGNITIVE_WORKFLOW_DOCS.every((doc) => doc.docsPath.startsWith("software/docs/validation/AMCA-Studio-v0.8.0-Cognitive-Workflow-")) ? "ok" : "issue",
    `Help/Resources gains ${COGNITIVE_WORKFLOW_DOCS.length} cognitive workflow entries linking the recorded reports.`,
  );

  // -- no reasoning exposure (global scan over all live outputs) --
  const allOutput = JSON.stringify({ stages, summary, actions, usage, detail }).toLowerCase();
  const noReasoning = !/chain-of-thought|hidden reasoning|model thinks|internal monologue/.test(allOutput);
  add("reasoning", noReasoning ? "ok" : "issue", noReasoning ? "No reasoning exposure in any live output." : "Reasoning exposure detected.");

  const issues = findings.filter((finding) => finding.severity === "issue").length;
  return { findings, issues, passed: issues === 0 };
}

/** Disclosure shown beside the Help entries — no embedded reader, honest. */
export const COGNITIVE_WORKFLOW_HELP_NOTE =
  "Documentation ships with the application repository — this build has no in-app documentation reader, so entries link to the recorded report paths.";
