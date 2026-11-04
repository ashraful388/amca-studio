// BETA-B20 Final UX Consolidation — pure presentation model (no fetch, no
// DOM). One canonical registry for the whole shell: view chrome labels,
// the navigation audit (consistent naming, palette coverage, no dead
// ends), the global Beta status surface (recorded version + channel, no
// released-status claims), the shared wording standard ("Not recorded" /
// "Unknown" / "Unavailable" / "Local only" / "No models configured"), the
// empty-state coverage audit, and the Help & Resources documentation hub.
// Audit inputs are injected so the same function audits both the test
// fixtures and the live sidebar data. No telemetry, no backend changes.

import type { NavItem } from "./studioPolishModel";

// ---------- 1. View registry ----------
// Secondary views are reachable only through in-app links (project work
// surfaces, logs, cost) and never appear in the sidebar; their labels
// mirror the App's secondary nav exactly.

export const SECONDARY_VIEWS: Array<{ id: string; label: string }> = [
  { id: "engineering", label: "Engineering Workspace" },
  { id: "room", label: "Research room" },
  { id: "whiteboard", label: "Shared whiteboard" },
  { id: "files", label: "Files & workspace" },
  { id: "cost", label: "Cost & compute" },
  { id: "benchmarks", label: "Benchmarks" },
  { id: "approvals", label: "Approvals" },
  { id: "logs", label: "Decision log" },
];

export function viewChromeLabel(
  view: string,
  sidebarItems: Array<{ id: string; label: string }>,
  secondaryItems: Array<{ id: string; label: string }> = SECONDARY_VIEWS,
): string {
  return sidebarItems.find((item) => item.id === view)?.label
    ?? secondaryItems.find((item) => item.id === view)?.label
    ?? "AMCA";
}

// ---------- 2. Unified navigation audit (order scope 1) ----------

export interface NavAuditFinding {
  severity: "ok" | "issue";
  rule: string;
  detail: string;
}

// Declared exits mirror the cross-view affordances the UI actually offers
// (dashboard quick links, card Open buttons, palette jumps, in-view
// actions). Every view must have at least one — no dead ends.
export const NAV_EXIT_LINKS: Record<string, string[]> = {
  dashboard: ["overview", "agents", "settings", "artifacts"],
  workspace: ["overview", "dashboard", "agents", "settings", "artifacts", "automations", "approvals"],
  chat: ["dashboard", "settings"],
  overview: ["chat", "dashboard"],
  research: ["approvals", "chat"],
  agents: ["dashboard"],
  workbench: ["artifacts"],
  artifacts: ["workbench"],
  tools: ["overview"],
  settings: ["chat"],
  validation: ["dashboard"],
  feedback: ["dashboard", "validation"],
  help: ["validation", "feedback"],
  automations: ["dashboard"],
  review: ["overview", "chat"],
  permissions: ["tools"],
  plugins: ["tools"],
  engineering: ["artifacts"],
  room: ["chat"],
  whiteboard: ["room"],
  files: ["workbench"],
  cost: ["dashboard"],
  benchmarks: ["dashboard"],
  approvals: ["review"],
  logs: ["review"],
};

export interface NavigationAudit {
  sidebarCount: number;
  secondaryCount: number;
  deadEnds: string[];
  findings: NavAuditFinding[];
}

export function auditNavigation(input: {
  sidebarGroups: Array<{ group: string; items: NavItem[] }>;
  palette: Array<{ id: string; label: string }>;
}): NavigationAudit {
  const findings: NavAuditFinding[] = [];
  const sidebarViews: Array<{ id: string; label: string; group: string }> = [];
  const seen = new Set<string>();
  for (const group of input.sidebarGroups) {
    for (const item of group.items) {
      if (seen.has(item.id)) {
        findings.push({ severity: "issue", rule: "unique-ids", detail: `View id "${item.id}" appears in more than one sidebar group.` });
      }
      seen.add(item.id);
      sidebarViews.push({ ...item, group: group.group });
    }
  }
  const labels = new Map<string, number>();
  for (const view of sidebarViews) labels.set(view.label, (labels.get(view.label) ?? 0) + 1);
  for (const [label, count] of labels) {
    if (count > 1) findings.push({ severity: "issue", rule: "unique-labels", detail: `Sidebar label "${label}" is used by ${count} views.` });
  }
  // Palette coverage + label agreement: Ctrl+K must reach every sidebar
  // view under the same name the sidebar shows.
  const paletteById = new Map(input.palette.map((entry) => [entry.id, entry.label]));
  for (const view of sidebarViews) {
    if (!paletteById.has(view.id)) {
      findings.push({ severity: "issue", rule: "palette-coverage", detail: `Sidebar view "${view.id}" is missing from the Ctrl+K palette.` });
      continue;
    }
    if (paletteById.get(view.id) !== view.label) {
      findings.push({ severity: "issue", rule: "label-agreement", detail: `Palette calls "${view.id}" "${paletteById.get(view.id)}" but the sidebar says "${view.label}".` });
    }
  }
  // Dead ends: every sidebar + secondary view needs at least one exit.
  const deadEnds: string[] = [];
  for (const view of [...sidebarViews, ...SECONDARY_VIEWS]) {
    const exits = NAV_EXIT_LINKS[view.id] ?? [];
    if (!exits.length) deadEnds.push(view.id);
  }
  if (deadEnds.length) {
    findings.push({ severity: "issue", rule: "no-dead-ends", detail: `Views with no recorded exit: ${deadEnds.join(", ")}.` });
  }
  if (!findings.some((finding) => finding.severity === "issue")) {
    findings.unshift({ severity: "ok", rule: "summary", detail: `Navigation audit clean: ${sidebarViews.length} sidebar views across ${input.sidebarGroups.length} groups, ${SECONDARY_VIEWS.length} secondary surfaces, palette labels agree, no dead ends.` });
  }
  return {
    sidebarCount: sidebarViews.length,
    secondaryCount: SECONDARY_VIEWS.length,
    deadEnds,
    findings,
  };
}

// ---------- 3. Global Beta status surface (order scope 2) ----------
// Recorded facts only: the application version and the release track the
// validation records already state. No "released" claim anywhere.

// H1.1-2: version/channel now derive from package.json (see versionLabel.ts).
import { APP_VERSION, RELEASE_CHANNEL } from "./versionLabel.ts";

export const BETA_STATUS = {
  appVersion: APP_VERSION,
  channel: RELEASE_CHANNEL,
  statusLine: `${APP_VERSION} ${RELEASE_CHANNEL.toLowerCase()}.`,
  documentationNote: "Full documentation ships with the application repository under software/docs — this build has no in-app documentation reader.",
} as const;

// ---------- 4. Cross-module wording standard (order scope 3) ----------

export interface WordingTerm {
  term: string;
  canonical: string;
  usageRule: string;
}

export const CANONICAL_WORDING: WordingTerm[] = [
  { term: "Not recorded", canonical: "Not recorded", usageRule: "Use when the platform has no record or endpoint for the value — always with the reason where possible." },
  { term: "Unknown", canonical: "unknown", usageRule: "Use only for facts the backend reports as literally unknown (e.g. capability matrix); never for missing records." },
  { term: "Unavailable", canonical: "Unavailable", usageRule: "Use when the capability exists but the runtime or service is not reachable right now." },
  { term: "Local only", canonical: "Local only", usageRule: "Use on every surface where data never leaves the device (feedback drafts, reports, diagnostics)." },
  { term: "No models configured", canonical: "No models configured. Connect a provider or enable local models.", usageRule: "The mandated model-hub empty state — quoted verbatim wherever models can be absent." },
];

export function auditWording(usages: Array<{ term: string; text: string }>): NavAuditFinding[] {
  return usages.map((usage) => {
    const standard = CANONICAL_WORDING.find((term) => term.term === usage.term);
    if (!standard) {
      return { severity: "issue" as const, rule: "known-term", detail: `"${usage.term}" is not part of the shared wording standard.` };
    }
    const matches = usage.text.trim().toLowerCase().startsWith(standard.canonical.toLowerCase());
    return matches
      ? { severity: "ok" as const, rule: standard.term, detail: `"${usage.text.slice(0, 80)}" conforms to the "${standard.term}" standard.` }
      : { severity: "issue" as const, rule: standard.term, detail: `"${usage.text.slice(0, 80)}" does not start with the canonical "${standard.canonical}".` };
  });
}

// ---------- 5. Empty-state final audit (order scope 4) ----------

export interface EmptyStateCoverage {
  scenario: string;
  title: string;
  detail: string;
  actionLabel: string | null;
  actionView: string | null;
  honestReason: string | null;
  source: string;
}

export const EMPTY_STATE_COVERAGE: EmptyStateCoverage[] = [
  {
    scenario: "no projects",
    title: "No Projects yet",
    detail: "Create a Project to group related chats, files, and history.",
    actionLabel: "Go to Projects",
    actionView: "overview",
    honestReason: null,
    source: "studioPolishModel.EMPTY_STATE_COPY.projects",
  },
  {
    scenario: "no models",
    title: "No models configured",
    detail: "Connect a provider or enable local models.",
    actionLabel: "Open Model Hub",
    actionView: "settings",
    honestReason: null,
    source: "studioPolishModel.EMPTY_STATE_COPY.models (mandated Z4 wording)",
  },
  {
    scenario: "no artifacts",
    title: "No artifacts yet",
    detail: "Artifacts appear after completed tasks.",
    actionLabel: "Open composer",
    actionView: "dashboard",
    honestReason: null,
    source: "studioPolishModel.EMPTY_STATE_COPY.artifacts (detail unified with the B11 mandated wording)",
  },
  {
    scenario: "no agents",
    title: "No agents available",
    detail: "The Agent Fabric catalog is empty. Create a custom specialist or check the service.",
    actionLabel: "Create agent (in-view)",
    actionView: null,
    honestReason: null,
    source: "AgentLibrary catalog empty state",
  },
  {
    scenario: "no workflows",
    title: "No active tasks",
    detail: "Start a new AMCA workflow.",
    actionLabel: "Open composer",
    actionView: "dashboard",
    honestReason: null,
    source: "studioPolishModel.EMPTY_STATE_COPY.active_tasks (mandated Z4 wording)",
  },
  {
    scenario: "no validation records",
    title: "Not recorded",
    detail: "No validation-record endpoint exists in this build — status not recorded.",
    actionLabel: null,
    actionView: null,
    honestReason: "The Validation Center discloses the missing endpoint instead of an empty state; no action can create validation records from the UI.",
    source: "validationCenterModel.VALIDATION_SCENARIOS statusReason",
  },
  {
    scenario: "no feedback",
    title: "No feedback records this session",
    detail: "Use the compose form, or the Dashboard's Beta feedback panel to produce a copy-draft.",
    actionLabel: "Open Dashboard feedback panel",
    actionView: "dashboard",
    honestReason: "Session-scoped records — this build has no feedback persistence backend.",
    source: "feedbackRefinementModel session disclosure",
  },
];

export function auditEmptyStates(): NavAuditFinding[] {
  return EMPTY_STATE_COVERAGE.map((state) => {
    const hasCopy = state.title.length > 0 && state.detail.length > 0;
    const hasExit = state.actionLabel !== null || state.honestReason !== null;
    if (hasCopy && hasExit) {
      return { severity: "ok" as const, rule: state.scenario, detail: `"${state.title}" — ${state.actionLabel ? `actionable (${state.actionLabel})` : "honest disclosure"} · source: ${state.source}` };
    }
    return { severity: "issue" as const, rule: state.scenario, detail: `Empty state for "${state.scenario}" lacks copy or an exit (action or honest reason).` };
  });
}

// ---------- 6. Documentation hub (order scope 5) ----------

export interface HelpResource {
  title: string;
  detail: string;
  targetView: string | null;
  docsPath: string | null;
}

export const HELP_RESOURCES: HelpResource[] = [
  { title: "Getting Started", detail: "Product specification — what AMCA Studio is and how the pieces fit.", targetView: null, docsPath: "software/docs/specification.md" },
  { title: "User Guide", detail: "Model Hub and Agent Fabric operating guide.", targetView: null, docsPath: "software/docs/model-hub-agent-fabric.md" },
  { title: "Troubleshooting", detail: "Port-conflict diagnostic script shipped with the validation docs.", targetView: null, docsPath: "software/docs/validation/diagnose_port_conflict.ps1" },
  { title: "Known Limitations", detail: "The recorded limitation registry with modules, workarounds, and targets.", targetView: "validation", docsPath: null },
  { title: "Beta Feedback", detail: "Session-local feedback records and refinement tracking.", targetView: "feedback", docsPath: null },
  { title: "Validation Center", detail: "Beta validation scenarios and the health summary.", targetView: "validation", docsPath: null },
  // v0.8.0 Cognitive Workflow UX final consolidation: documentation hub entries.
  { title: "Cognitive Workflow — UX Foundation", detail: "Transparency layer over recorded desktop workflow records: the 9-stage loop, resource selection, knowledge usage, verification.", targetView: null, docsPath: "software/docs/validation/AMCA-Studio-v0.8.0-Cognitive-Workflow-UX-Foundation-Report.md" },
  { title: "Cognitive Workflow — Advanced Insights UX", detail: "Record-based summary card, stage expansion, decision transparency, project history.", targetView: null, docsPath: "software/docs/validation/AMCA-Studio-v0.8.0-Cognitive-Workflow-Advanced-Insights-UX-Report.md" },
  { title: "Cognitive Workflow — Interaction UX", detail: "Navigation-only action surface, history filtering, failure review — no execution controls.", targetView: null, docsPath: "software/docs/validation/AMCA-Studio-v0.8.0-Cognitive-Workflow-Interaction-UX-Report.md" },
];
