// P5 Alpha User Productivity Optimization — pure presentation model
// (no fetch, no DOM). Bounded daily-workflow friction reduction:
// (1) composer quick-start templates (prefill, never auto-run),
// (2) keyboard shortcut reference (documented accelerators),
// (3) workflow accelerator / friction findings with a Beta UX
// recommendation list. Frontend only; no backend changes; no v0.7.

import type { ComposerDraft } from "./composerModel";

// ---------- 1. Composer quick-start templates (order: workflow friction) ----------
// Templates only prefill the EXISTING composer draft; pressing Execute
// still goes through the same governed payload. Nothing auto-runs.

export interface ComposerTemplate {
  id: string;
  label: string;
  description: string;
  taskTypeId: string;
  mode: ComposerDraft["mode"];
  goalSeed: string;
}

export const COMPOSER_TEMPLATES: ComposerTemplate[] = [
  {
    id: "research",
    label: "Research a topic",
    description: "Evidence-first research with verification.",
    taskTypeId: "research",
    mode: "adaptive_hybrid",
    goalSeed: "Research [topic]: gather sources, verify claims, and synthesize findings.",
  },
  {
    id: "coding",
    label: "Build or fix code",
    description: "Coding task with explicit verification.",
    taskTypeId: "coding",
    mode: "solo",
    goalSeed: "Implement [change] in [module]; verify with tests and boundary cases.",
  },
  {
    id: "analysis",
    label: "Analyze data or documents",
    description: "Structured analysis over provided material.",
    taskTypeId: "analysis",
    mode: "joint",
    goalSeed: "Analyze [dataset or documents] and report key findings with evidence.",
  },
  {
    id: "document",
    label: "Create a document",
    description: "Report-style artifact output.",
    taskTypeId: "document_creation",
    mode: "solo",
    goalSeed: "Produce [document type] covering [scope]; include sections and references.",
  },
  {
    id: "multi_agent",
    label: "Multi-agent collaboration",
    description: "Several agents on one shared goal.",
    taskTypeId: "agent_collaboration",
    // H1.1-1: safe first-run default. Parallel requires >=2 READY models and
    // manufactured a failed run for fresh users; Solo with Auto routing lets
    // AMCA pick a safe architecture. Explicit Parallel remains available in
    // the architecture selector once enough healthy models exist.
    mode: "solo",
    goalSeed: "Collaboratively deliver [goal]; decompose work units and integrate results.",
  },
];

export function templateById(id: string): ComposerTemplate | null {
  return COMPOSER_TEMPLATES.find((template) => template.id === id) ?? null;
}

export function templateToDraftPatch(template: ComposerTemplate): Partial<ComposerDraft> {
  return {
    taskTypeId: template.taskTypeId,
    mode: template.mode,
    goal: template.goalSeed,
  };
}

// ---------- 2. Keyboard shortcut reference (order: interaction friction) ----------
// Documents the accelerators wired in the shell. `combo` strings are
// display text; the implementations live in the App shell key handler.

export interface KeyboardShortcut {
  combo: string;
  action: string;
  scope: string;
}

export const KEYBOARD_SHORTCUTS: KeyboardShortcut[] = [
  { combo: "Ctrl+K", action: "Jump to view — type a name and press Enter", scope: "Global" },
  { combo: "Ctrl+1 … Ctrl+6", action: "Open primary views (Dashboard, Chat, Projects, Research, Agents, Artifacts)", scope: "Global" },
  { combo: "Ctrl+,", action: "Open Model Hub (settings)", scope: "Global" },
  { combo: "Esc", action: "Cancel an open dialog or close an expanded detail", scope: "Context" },
];

// View jump data: the Ctrl+K palette lists exactly these existing views in
// stable order. ids are real View union members in App.tsx.
export interface ViewJumpEntry {
  id: string;
  label: string;
}

export const NAVIGABLE_VIEWS: ViewJumpEntry[] = [
  ...NAV_LIKE_ENTRIES(),
];

function NAV_LIKE_ENTRIES(): ViewJumpEntry[] {
  // Duplicated data would drift from the sidebar, so the shell passes its
  // own nav data in; this helper exists for tests of the jump flow itself.
  return [
    { id: "dashboard", label: "Dashboard" },
    { id: "workspace", label: "Command Center" },
    { id: "chat", label: "Chat" },
    { id: "overview", label: "Projects" },
    { id: "research", label: "Research" },
    { id: "agents", label: "Agents" },
    { id: "artifacts", label: "Artifacts" },
    { id: "workbench", label: "Files" },
    { id: "tools", label: "Tools" },
    { id: "settings", label: "Models" },
    { id: "review", label: "Review Center" },
    { id: "validation", label: "Validation" },
    { id: "feedback", label: "Feedback" },
    { id: "help", label: "Help" },
    { id: "automations", label: "Automations" },
    { id: "permissions", label: "Computer access" },
    { id: "plugins", label: "Plugins" },
  ];
}

export function filterViewJumps(entries: ViewJumpEntry[], query: string): ViewJumpEntry[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return entries;
  return entries.filter((entry) => entry.label.toLowerCase().includes(needle) || entry.id.includes(needle));
}

// ---------- 3. Productivity audit → Beta UX recommendations (order: patterns) ----------

export interface ProductivityFinding {
  id: string;
  area: string;
  friction: string;
  improvement: string;
  status: "shipped" | "recommended";
}

export const PRODUCTIVITY_FINDINGS: ProductivityFinding[] = [
  { id: "compose_from_scratch", area: "Task start", friction: "Every task starts from an empty composer; users retype common goals.", improvement: "Five one-click quick-start templates prefill task type, mode, and goal seed.", status: "shipped" },
  { id: "nav_mouse_only", area: "Navigation", friction: "View switching requires mouse clicks; keyboard users lose time.", improvement: "Ctrl+K view jump and Ctrl+1…6 direct access wired in the shell.", status: "shipped" },
  { id: "manual_switch_after_run", area: "Run follow-up", friction: "After a run completes, finding its artifacts takes two hops.", improvement: "Shipped (W1): Recent outputs entries deep-link into the Artifact Center's filtered type view.", status: "shipped" },
  { id: "team_rebuild", area: "Model selection", friction: "Users rebuild the same model set per chat.", improvement: "Shipped (W2): 'Save selection as team' in the chat picker writes through the existing Model Teams contract.", status: "shipped" },
  { id: "approval_context", area: "Approvals", friction: "Approval cards show scope but not the run context that requested them.", improvement: "Recommended: surface the requesting run's goal on each approval card (backend contract dependent).", status: "recommended" },
  { id: "repeat_export", area: "Export", friction: "Exporting several artifacts repeats the same format choice.", improvement: "Shipped (W4): the last export format is remembered per project locally and marked 'Last used'.", status: "shipped" },
];

export function betaUxRecommendations(): ProductivityFinding[] {
  return PRODUCTIVITY_FINDINGS.filter((finding) => finding.status === "recommended");
}

export function shippedProductivityWins(): ProductivityFinding[] {
  return PRODUCTIVITY_FINDINGS.filter((finding) => finding.status === "shipped");
}
