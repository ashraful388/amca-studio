// P1 Alpha User Experience Intelligence — pure presentation model
// (no fetch, no DOM). Four deliverables from the manager's order:
// (1) local feedback surfaces ("Report issue" / "Suggest improvement" /
//     "Need help") that compose document-based payloads with no external
//     service, (2) first-time UX audit findings per major screen,
// (3) demo-mode feature discovery hints derived from real runtime state,
// (4) improved empty/error wording. No backend semantics are introduced.

// ---------- 1. Feedback surfaces (order scope 1) ----------

export interface FeedbackChannel {
  id: "report_issue" | "suggest_improvement" | "need_help";
  label: string;
  prompt: string;
  placeholder: string;
}

export const FEEDBACK_CHANNELS: FeedbackChannel[] = [
  {
    id: "report_issue",
    label: "Report issue",
    prompt: "What happened, and what did you expect instead?",
    placeholder: "Steps to reproduce, expected vs actual behaviour…",
  },
  {
    id: "suggest_improvement",
    label: "Suggest improvement",
    prompt: "What would make this workflow better for you?",
    placeholder: "The change you would like and why it helps…",
  },
  {
    id: "need_help",
    label: "Need help",
    prompt: "What were you trying to do when you got stuck?",
    placeholder: "The task you attempted and where it stalled…",
  },
];

export function feedbackChannelById(id: string): FeedbackChannel | null {
  return FEEDBACK_CHANNELS.find((channel) => channel.id === id) ?? null;
}

export interface FeedbackDraftInput {
  channelId: string;
  text: string;
  screen: string;
  createdAtIso: string;
  appVersion: string;
}

export interface FeedbackDraft {
  channel: string;
  channelLabel: string;
  screen: string;
  text: string;
  createdAt: string;
  appVersion: string;
  storage: "local_only";
}

// The payload is a plain document the user keeps or pastes into their
// support channel — AMCA stores nothing and sends nothing anywhere.
export function buildFeedbackDraft(input: FeedbackDraftInput): FeedbackDraft | null {
  const channel = feedbackChannelById(input.channelId);
  const text = input.text.trim();
  if (!channel || !text) return null;
  return {
    channel: channel.id,
    channelLabel: channel.label,
    screen: input.screen,
    text,
    createdAt: input.createdAtIso,
    appVersion: input.appVersion,
    storage: "local_only",
  };
}

export function feedbackDraftToText(draft: FeedbackDraft): string {
  return JSON.stringify(draft, null, 2);
}

// ---------- 2. First-time UX audit findings (order scope 2) ----------
// Structured findings from walking each major screen as a first-time
// user. Every finding names its screen, kind, and the improvement that
// shipped (or is recommended) — this is the source of the audit document.

export type UxFindingKind = "label" | "explanation" | "dead_end" | "state";

export interface UxFinding {
  screen: string;
  kind: UxFindingKind;
  observation: string;
  improvement: string;
  status: "addressed" | "recommended";
}

export const UX_AUDIT_FINDINGS: UxFinding[] = [
  { screen: "Dashboard", kind: "explanation", observation: "Composer's 'Existing pipeline' pill is unexplained for newcomers.", improvement: "Note states the composer creates Project → Conversation → Run with no bypass.", status: "addressed" },
  { screen: "Dashboard", kind: "state", observation: "Fresh installs showed four zero metrics with no guidance.", improvement: "Getting-started strip with five real-state steps now appears above the composer.", status: "addressed" },
  { screen: "Chat", kind: "state", observation: "Manual model selection with an empty hub stopped users without direction.", improvement: "Unified 'No models configured' empty state with an Open Model Hub action.", status: "addressed" },
  { screen: "Chat", kind: "explanation", observation: "Adaptive Hybrid gating (experimental, session-scoped) was easy to miss.", improvement: "Intelligence panel labels each gate with its scope and effect.", status: "addressed" },
  { screen: "Model Hub", kind: "state", observation: "BYOK providers without discovered models looked identical to broken ones.", improvement: "Provider guidance distinguishes 'Connected — discovery pending' from failures.", status: "addressed" },
  { screen: "Model Hub", kind: "explanation", observation: "Cost preview did not say where its rates come from.", improvement: "Per-basis cost explanation line (registry / profile / local / unknown).", status: "addressed" },
  { screen: "Model Hub", kind: "dead_end", observation: "Unconfigurable utility cards (Matchmaker, Context Explorer) dead-ended at 'Planned'.", improvement: "VRAM Calculator routes to Local models; planned cards now carry purpose, why-unavailable, and the expected dependency — still honestly disabled.", status: "addressed" },
  { screen: "Agent Monitor", kind: "state", observation: "Failed and cancelled runs were only visible as counts in the state strip.", improvement: "UX-state banner surfaces failures/cancellations with 'nothing auto-retries' wording.", status: "addressed" },
  { screen: "Agent Monitor", kind: "explanation", observation: "Run rows said 'router-selected' without explaining model assignment.", improvement: "Insights pane states the router chooses at task time and never pins models.", status: "addressed" },
  { screen: "Artifact Center", kind: "dead_end", observation: "Export tab depends on project selection; no artifacts meant no next step shown.", improvement: "Empty export state now offers 'No exports yet. Create a task to generate artifacts.' with an Open Projects action (W3).", status: "addressed" },
  { screen: "Automation Center", kind: "explanation", observation: "Monitor/event-triggered automations show 'Unsupported automatic trigger'.", improvement: "Cards now say 'Manual only · trigger unsupported' and explain manual execution and the scheduler dependency (U3).", status: "addressed" },
  { screen: "Review Center", kind: "state", observation: "Unread indicators exist in Advanced nav but the panel itself lacks onboarding.", improvement: "One-line purpose/evidence explanation shows on first visit per session (D2).", status: "addressed" },
];

export function uxAuditSummary(): Record<"addressed" | "recommended", number> {
  const summary = { addressed: 0, recommended: 0 };
  for (const finding of UX_AUDIT_FINDINGS) summary[finding.status] += 1;
  return summary;
}

// ---------- 3. Demo mode / feature discovery (order scope 3) ----------

export interface DemoStateInput {
  hasSelectableModels: boolean;
  hasProjects: boolean;
  hasRuns: boolean;
  hasArtifacts: boolean;
  hasTeams: boolean;
  hasAutomations: boolean;
  hasProviders: boolean;
}

export interface FeatureHint {
  id: string;
  label: string;
  detail: string;
  view: string;
}

// Hints surface only capabilities the real state says exist or clearly
// could — never invented features or fake data.
export function featureHints(state: DemoStateInput): FeatureHint[] {
  const hints: FeatureHint[] = [];
  if (state.hasSelectableModels && !state.hasTeams) {
    hints.push({ id: "teams", label: "Try Model Teams", detail: "Combine local and API models into a reusable preset for chat and the composer.", view: "settings" });
  }
  if (state.hasRuns && state.hasArtifacts) {
    hints.push({ id: "insights", label: "Open Collaboration Insights", detail: "Inspect a run in the Agent Monitor to see its timeline, decision trace, and cost insight.", view: "agents" });
  }
  if (state.hasProjects && !state.hasAutomations) {
    hints.push({ id: "automations", label: "Explore Automations", detail: "Scheduled monitors and event-triggered checks live under Advanced → Automations.", view: "automations" });
  }
  if (!state.hasProviders && state.hasSelectableModels) {
    hints.push({ id: "byok", label: "Connect your first provider", detail: "Add a BYOK key in Model Hub to widen the model catalog beyond local runtimes.", view: "settings" });
  }
  hints.push({ id: "review", label: "Keep an eye on Review Center", detail: "Governance reviews and unread items gather under Advanced → Review Center.", view: "review" });
  return hints;
}

export function demoWalkthroughSteps(): Array<{ id: string; label: string; detail: string }> {
  return [
    { id: "intro", label: "Show the shell", detail: "Dashboard first: composer, metrics, and the Local-first badge on screen." },
    { id: "project", label: "Open or create a Project", detail: "Demonstrate scoping: chats, files, and history belong to the Project." },
    { id: "models", label: "Walk the Model Hub", detail: "Provider directory, BYOK masking, and the Selection & Cost comparison." },
    { id: "run", label: "Execute one governed task", detail: "Composer → approvals if a cloud model is chosen → live run states." },
    { id: "monitor", label: "Watch agents work", detail: "Agent Control Panel states, activity bar, nano lifecycle strips." },
    { id: "results", label: "Review and export", detail: "Recent outputs grouping, then Artifact Center export with capabilities." },
  ];
}

// ---------- 4. Empty/error wording improvements (order scope 4) ----------
// Improved, action-first copy for the states the manager named. Each
// entry is wording only; wiring stays in the existing components.

export interface StateWording {
  key: string;
  title: string;
  detail: string;
  actionLabel: string | null;
}

export const IMPROVED_STATE_WORDING: Record<string, StateWording> = {
  no_model: {
    key: "no_model",
    title: "No models configured",
    detail: "Connect a provider or enable local models to unlock task execution.",
    actionLabel: "Open Model Hub",
  },
  no_project: {
    key: "no_project",
    title: "No Projects yet",
    detail: "Create a Project first — chats, files, and history stay organized inside it.",
    actionLabel: "Go to Projects",
  },
  failed_run: {
    key: "failed_run",
    title: "This run failed",
    detail: "The error is preserved for inspection below; nothing retried automatically and nothing was lost.",
    actionLabel: null,
  },
  missing_provider: {
    key: "missing_provider",
    title: "Provider connection missing",
    detail: "The selected model's provider has no credential — add a key in Model Hub or pick another model.",
    actionLabel: "Open Model Hub",
  },
  permission_denied: {
    key: "permission_denied",
    title: "Access needs approval",
    detail: "This action is outside the current grants. Request it through the Approvals view and the Governor will gate it exactly.",
    actionLabel: "Open Approvals",
  },
  interrupted_execution: {
    key: "interrupted_execution",
    title: "Run interrupted",
    detail: "The run stopped before finishing (cancelled or service stopped). Its partial state is recorded — restart the task when ready.",
    actionLabel: null,
  },
};

export function stateWordingFor(key: string): StateWording | null {
  return IMPROVED_STATE_WORDING[key] ?? null;
}
