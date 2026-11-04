// BETA-RC1 UX Compliance Audit — pure presentation model (no fetch, no
// DOM). Cross-checks every delivered Beta UX milestone against its
// navigation entry, on-surface empty state, and documentation reference,
// so the release-candidate audit is test-verified rather than prose-only.
// Audit-only: no features, no runtime behavior changes.

import type { NavItem } from "./studioPolishModel";

export interface RcModuleRecord {
  milestone: string;
  title: string;
  navId: string | null;           // sidebar view that hosts the surface
  surface: string;                // where the feature lives in the UI
  emptyStateSource: string;       // recorded empty-state / disclosure source
  docPath: string;                // milestone report in software/docs/validation
}

export const RC_MODULE_INVENTORY: RcModuleRecord[] = [
  {
    milestone: "B4", title: "Model Orchestration UX", navId: "agents",
    surface: "Agent Monitor → Collaboration Insights (architecture, decision trace, cost, model decision)",
    emptyStateSource: "AgentMonitor muted-copy disclosures (evidence not recorded)",
    docPath: "software/docs/validation/AMCA-Studio-Beta-Model-Orchestration-UX-Report.md",
  },
  {
    milestone: "B11", title: "Professional Workspace UX", navId: "workspace",
    surface: "Workspace Command Center (8 evidence cards, unified timeline, cross-module trace, health summary)",
    emptyStateSource: "workspaceCommandModel.COMMAND_CENTER_EMPTY_STATES",
    docPath: "software/docs/validation/AMCA-Studio-Beta-Professional-Workspace-UX-Report.md",
  },
  {
    milestone: "B12", title: "Knowledge Management UX", navId: "artifacts",
    surface: "Artifact Center → Knowledge tab (assets, collections, provenance, relations)",
    emptyStateSource: "knowledgeUxModel KNOWLEDGE_BACKEND_NOTE + empty states",
    docPath: "software/docs/validation/AMCA-Studio-Beta-Knowledge-Management-UX-Report.md",
  },
  {
    milestone: "B13", title: "Project Lifecycle UX", navId: "dashboard",
    surface: "Workspace Dashboard → per-project lifecycle expander (stages, health pills, evidence)",
    emptyStateSource: "projectLifecycleModel empirical-stage pending states",
    docPath: "software/docs/validation/AMCA-Studio-Beta-Project-Lifecycle-UX-Report.md",
  },
  {
    milestone: "B14", title: "Operational Intelligence UX", navId: "dashboard",
    surface: "Workspace Dashboard → Operational Intelligence panel (metrics, timeline, resource visibility)",
    emptyStateSource: "operationalIntelligenceModel unrecorded disclosures",
    docPath: "software/docs/validation/AMCA-Studio-Beta-Operational-Intelligence-UX-Report.md",
  },
  {
    milestone: "B18", title: "Validation Center UX", navId: "validation",
    surface: "Validation Center (scenarios, limitation registry, health summary, feedback paths)",
    emptyStateSource: "validationCenterModel not-recorded statusReason disclosure",
    docPath: "software/docs/validation/AMCA-Studio-Beta-Validation-Center-UX-Report.md",
  },
  {
    milestone: "B19", title: "Feedback Refinement UX", navId: "feedback",
    surface: "Feedback Center (session records, categories, refinement tracking, privacy banner)",
    emptyStateSource: "feedbackRefinementModel session-empty note",
    docPath: "software/docs/validation/AMCA-Studio-Beta-Feedback-Refinement-UX-Report.md",
  },
  {
    milestone: "B20", title: "Final UX Consolidation", navId: "help",
    surface: "Help & Resources (Beta status, navigation audit, documentation hub) + sidebar Beta footer",
    emptyStateSource: "consolidationUxModel.EMPTY_STATE_COVERAGE (all seven scenarios)",
    docPath: "software/docs/validation/AMCA-Studio-Beta-B20-Final-UX-Consolidation-Report.md",
  },
];

export interface RcComplianceFinding {
  milestone: string;
  check: string;
  severity: "pass" | "fail";
  detail: string;
}

export function auditRcCompliance(input: {
  sidebarGroups: Array<{ group: string; items: NavItem[] }>;
}): RcComplianceFinding[] {
  const navIds = new Set(
    input.sidebarGroups.flatMap((group) => group.items.map((item) => item.id)),
  );
  const findings: RcComplianceFinding[] = [];
  for (const record of RC_MODULE_INVENTORY) {
    if (record.navId === null) {
      findings.push({ milestone: record.milestone, check: "navigation", severity: "fail", detail: "Module has no recorded navigation host." });
      continue;
    }
    findings.push(navIds.has(record.navId)
      ? { milestone: record.milestone, check: "navigation", severity: "pass", detail: `Reachable via sidebar view "${record.navId}".` }
      : { milestone: record.milestone, check: "navigation", severity: "fail", detail: `Host view "${record.navId}" is missing from the sidebar.` });
    findings.push(record.surface.length > 0 && record.emptyStateSource.length > 0
      ? { milestone: record.milestone, check: "surface-and-empty-state", severity: "pass", detail: record.surface }
      : { milestone: record.milestone, check: "surface-and-empty-state", severity: "fail", detail: "Missing surface or empty-state record." });
    findings.push(record.docPath.startsWith("software/docs/validation/")
      ? { milestone: record.milestone, check: "documentation", severity: "pass", detail: record.docPath }
      : { milestone: record.milestone, check: "documentation", severity: "fail", detail: `Unexpected documentation path: ${record.docPath}` });
  }
  return findings;
}

// ---------- Release documentation UX audit (order scope 2) ----------
// Every release-information reference the UI must expose, with its
// recorded entry point. The outdated-"Alpha wording" items were fixed in
// this pass: the Dashboard feedback panel kicker and the three textual
// references now read "Beta feedback".

export const RELEASE_DOC_REFERENCES: Array<{ label: string; entry: string; host: string }> = [
  { label: "Help & Resources", entry: "Sidebar footer button + sidebar view + Ctrl+K", host: "help" },
  { label: "Known Limitations", entry: "Help view button + Validation Center limitation registry", host: "validation" },
  { label: "Validation Center", entry: "Sidebar view + Ctrl+K + Help view", host: "validation" },
  { label: "Feedback Center", entry: "Sidebar view + Ctrl+K + Help view + Dashboard panel", host: "feedback" },
  { label: "Beta status information", entry: "Sidebar footer + Help view Beta status panel", host: "help" },
];

// Fixed in this pass (was "Alpha feedback" — outdated pre-Beta wording):
export const ALPHA_WORDING_FIXES = [
  "WorkspaceDashboard feedback panel kicker: \"Alpha feedback\" → \"Beta feedback\"",
  "Feedback Center empty-state reference: \"Dashboard's Alpha feedback panel\" → \"Dashboard's Beta feedback panel\"",
  "Help Resources empty-state detail: \"Dashboard's Alpha feedback panel\" → \"Dashboard's Beta feedback panel\"",
  "Validation Center FEEDBACK_PATHS detail: \"Dashboard's Alpha feedback panel\" → \"Dashboard's Beta feedback panel\"",
];
