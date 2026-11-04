// BETA-B18 Validation Center UX — pure presentation model (no fetch, no
// DOM). Exposes the Beta validation picture from EXISTING records only:
// the three manager-named validation scenarios carry honest statuses
// (this build has no validation-record endpoint, so none claim
// "validated" without a record), known limitations come from the
// accepted KNOWN_LIMITATIONS record set, and feedback paths point at the
// local P1 feedback surfaces. No telemetry, no scoring, no inference.

export type ValidationStatus = "validated" | "pending" | "failed" | "not_recorded";

export interface ValidationScenario {
  id: string;
  name: string;
  purpose: string;
  modules: string[];
  status: ValidationStatus;
  statusReason: string;
  evidenceReferences: string[];
  knownLimitations: string[];
}

// Statuses: no validation-record endpoint exists in this build, so every
// scenario reports "not_recorded" rather than an assumed state.
export const VALIDATION_SCENARIOS: ValidationScenario[] = [
  {
    id: "research",
    name: "Research workflow",
    purpose: "Gather, verify, and synthesize findings into a report artifact.",
    modules: ["Research Center", "Task composer", "Agent Fabric", "Artifact registry"],
    status: "not_recorded",
    statusReason: "No validation-record endpoint exists in this build — status not recorded.",
    evidenceReferences: ["Run registry", "Artifact registry"],
    knownLimitations: ["Provider health is discover-derived (no standing probe)"],
  },
  {
    id: "software",
    name: "Software workflow",
    purpose: "Build or repair code with explicit verification.",
    modules: ["Task composer", "Engineering workbench", "Agent Fabric", "Artifact registry"],
    status: "not_recorded",
    statusReason: "No validation-record endpoint exists in this build — status not recorded.",
    evidenceReferences: ["Run registry", "Workbench verification runs"],
    knownLimitations: [],
  },
  {
    id: "productivity",
    name: "Productivity workflow",
    purpose: "Daily-driver tasks: documents, analysis, automation assistance.",
    modules: ["Dashboard composer", "Automations", "Model Hub", "Artifact registry"],
    status: "not_recorded",
    statusReason: "No validation-record endpoint exists in this build — status not recorded.",
    evidenceReferences: ["Run registry", "Automation occurrences"],
    knownLimitations: ["Cost is a published-rate preview (no usage metering)"],
  },
];

export function scenarioById(id: string): ValidationScenario | null {
  return VALIDATION_SCENARIOS.find((scenario) => scenario.id === id) ?? null;
}

// ---------- Release label (recorded only) ----------

// H1.1-2: derived from package.json — no hard-coded version.
import { APP_VERSION, RELEASE_CHANNEL } from "./versionLabel.ts";

export const RELEASE_RECORD = {
  track: RELEASE_CHANNEL,
  appVersion: APP_VERSION,
  note: "Showing the application version from package.json.",
};

// ---------- Known limitations (existing records only) ----------
// The accepted limitation records from studioPolishModel, extended with
// the module each one touches and the honest workaround/status.

export interface LimitationRecord {
  title: string;
  detail: string;
  module: string;
  workaround: string;
  targetRelease: string;
}

export const LIMITATION_RECORDS: LimitationRecord[] = [
  { title: "Provider health is discover-derived", detail: "Reachability/authentication checks reflect the bounded catalog test at connect time; there is no standing background probe.", module: "Model Hub", workaround: "Re-run Test connection for current status.", targetRelease: "Beta pass" },
  { title: "llama.cpp / vLLM / SGLang status not reported", detail: "The execution engine accepts their URLs, but the hub document carries no status for them yet, so the UI says \u201CNot reported\u201D instead of guessing.", module: "Model Hub — runtimes", workaround: "Verify the runtime manually outside Studio.", targetRelease: "Beta pass" },
  { title: "Cost is a published-rate preview", detail: "Estimates come from the verified Pricing Registry and saved profile rates; there is no usage metering or billing engine.", module: "Cost & compute", workaround: "Check per-task recorded costs in the Cost panel.", targetRelease: "Future runtime work" },
  { title: "Credential rotation is delete + re-add", detail: "Keys live in the operating-system vault; rotation replaces the credential rather than editing it.", module: "Model Hub — credentials", workaround: "Remove and re-add the credential.", targetRelease: "Beta pass" },
  { title: "Task type travels as a goal tag", detail: "The composer prefix records task type in the goal text; durable task-type persistence is post-alpha.", module: "Task composer", workaround: "None — documented behavior.", targetRelease: "Post-alpha backlog" },
  { title: "Artifact export lives only in Artifact Center", detail: "The dashboard groups outputs for review; format export stays in one canonical place.", module: "Artifact Center", workaround: "Export from the Artifact Center Export tab.", targetRelease: "Beta UX recommendations" },
  { title: "Experimental intelligence stays OFF", detail: "Adaptive Hybrid, Model Router, and Nano Agents follow v0.6.2 gating: session-scoped, user-enabled, default off.", module: "Intelligence panel", workaround: "Enable per session when wanted.", targetRelease: "Recorded design" },
  { title: "Alpha scope exclusions hold", detail: "Marketplace, enterprise billing, million-agent scaling, internet agents, mobile, and SaaS are out of scope for the alpha.", module: "Product scope", workaround: "None — scope boundary.", targetRelease: "v0.7 planning" },
];

// ---------- Beta health summary (order scope 4) ----------

export interface BetaHealthSummary {
  scenariosValidated: number;
  scenariosPending: number;
  scenariosFailed: number;
  openLimitations: number;
  feedbackRecords: number | null;
  line: string;
}

export function betaHealthSummary(input: { feedbackRecords: number | null }): BetaHealthSummary {
  const summary: BetaHealthSummary = {
    scenariosValidated: VALIDATION_SCENARIOS.filter((scenario) => scenario.status === "validated").length,
    scenariosPending: VALIDATION_SCENARIOS.filter((scenario) => scenario.status !== "validated" && scenario.status !== "failed").length,
    scenariosFailed: VALIDATION_SCENARIOS.filter((scenario) => scenario.status === "failed").length,
    openLimitations: LIMITATION_RECORDS.length,
    feedbackRecords: input.feedbackRecords,
    line: "",
  };
  summary.line = `${summary.scenariosValidated} validated · ${summary.scenariosPending} pending · ${summary.scenariosFailed} failed · ${summary.openLimitations} recorded limitations`
    + (input.feedbackRecords === null ? " · feedback records: not recorded (local feedback only)" : ` · ${input.feedbackRecords} local feedback draft(s) this session`);
  return summary;
}

// ---------- Feedback navigation (order scope 5) ----------

export interface FeedbackPath {
  id: string;
  label: string;
  detail: string;
  view: string;
}

export const FEEDBACK_PATHS: FeedbackPath[] = [
  { id: "report", label: "Report issue", detail: "Compose a local draft on the Dashboard's Beta feedback panel, then copy it wherever you choose.", view: "dashboard" },
  { id: "suggest", label: "Suggest improvement", detail: "Same local panel — drafts never leave the device unless you manually share them.", view: "dashboard" },
  { id: "diagnostics", label: "Export diagnostics", detail: "Copy the Operational report JSON (dashboard) for support context — still a local document.", view: "dashboard" },
];
