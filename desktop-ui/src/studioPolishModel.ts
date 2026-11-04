// Z4 Commercial Polish — pure presentation model (no fetch, no DOM).
// Centralizes the UI patterns the manager's Z4 order requires: the guided
// end-to-end journey, the unified empty-state copy catalog, sidebar
// navigation data, honest provider-card guidance, model comparison rows,
// cost explanations, unavailable-model summaries, and the demo-readiness
// package contents. Everything derives from EXISTING documents and
// components; no backend semantics are introduced.

// ---------- 1. Guided end-to-end journey (order work package 2 + 6) ----------
// The manager-defined alpha journey: each step names the Studio view where
// it happens so the demo walkthrough and any navigation fix target a real
// screen. Views must exist in App.tsx's View union; the data is the
// single source for the in-app checklist and the readiness document.

export interface JourneyStep {
  id: string;
  label: string;
  detail: string;
  view: string;
}

export const GUIDED_JOURNEY: JourneyStep[] = [
  { id: "new_user", label: "Launch AMCA Studio", detail: "Desktop opens on the Workspace Dashboard with local-only messaging visible.", view: "dashboard" },
  { id: "create_project", label: "Create Project", detail: "Projects view → + New Project → name, template, optional instructions.", view: "overview" },
  { id: "open_workspace", label: "Open Workspace", detail: "Pick the Project in the sidebar to scope chats, files, and history.", view: "overview" },
  { id: "compose_task", label: "Compose Task", detail: "Dashboard composer: goal, task type, collaboration mode, strategy.", view: "dashboard" },
  { id: "select_models", label: "Select Models", detail: "Auto Intelligence, or Manual with exact models from the Model Hub.", view: "settings" },
  { id: "run_collaboration", label: "Run Collaboration", detail: "Execute task creates Project → Conversation → governed Run.", view: "dashboard" },
  { id: "monitor_agents", label: "Monitor Agents", detail: "Agent Control Panel shows run rows, states, and the nano tree.", view: "agents" },
  { id: "review_artifact", label: "Review Artifact", detail: "Dashboard Recent outputs groups markdown / pdf / code artifacts.", view: "dashboard" },
  { id: "export_result", label: "Export Result", detail: "Artifact Center → Export tab with capability-checked formats.", view: "artifacts" },
];

export function journeyStepById(id: string): JourneyStep | null {
  return GUIDED_JOURNEY.find((step) => step.id === id) ?? null;
}

// ---------- 2. Unified empty-state copy (order work package 5) ----------
// One catalog, one voice. The two examples mandated by the order use these
// exact strings. Actions name an existing view; no invented destinations.

export interface EmptyStateCopy {
  title: string;
  detail: string;
  action: { label: string; view: string } | null;
}

export const EMPTY_STATE_COPY: Record<string, EmptyStateCopy> = {
  models: {
    title: "No models configured",
    detail: "Connect a provider or enable local models.",
    action: { label: "Open Model Hub", view: "settings" },
  },
  active_tasks: {
    title: "No active tasks",
    detail: "Start a new AMCA workflow.",
    action: { label: "Open composer", view: "dashboard" },
  },
  projects: {
    title: "No Projects yet",
    detail: "Create a Project to group related chats, files, and history.",
    action: { label: "Go to Projects", view: "overview" },
  },
  conversations: {
    title: "No chats yet",
    detail: "Start a chat from the sidebar — it stays on this device.",
    action: null,
  },
  artifacts: {
    title: "No artifacts yet",
    detail: "Model-authored outputs appear here after a run completes.",
    action: null,
  },
  teams: {
    title: "No Model Teams saved",
    detail: "Combine local and API models into a reusable preset.",
    action: null,
  },
  runs: {
    title: "No governed runs yet",
    detail: "Tasks started from the composer or Chat appear here.",
    action: null,
  },
};

const GENERIC_EMPTY_STATE: EmptyStateCopy = {
  title: "Nothing here yet",
  detail: "This area fills in as you work.",
  action: null,
};

export function emptyStateFor(contextId: string): EmptyStateCopy {
  return EMPTY_STATE_COPY[contextId] ?? GENERIC_EMPTY_STATE;
}

// ---------- 3. Sidebar navigation data (order work package 1) ----------
// Sidebar order and grouping as data so active-state rendering and the
// labels stay consistent. Every id is an existing view; Chat and Artifacts
// were previously unreachable from the primary nav.

export interface NavItem {
  id: string;
  label: string;
  icon: string;
}

export const NAV_WORK: NavItem[] = [
  { id: "dashboard", label: "Dashboard", icon: "home" },
  { id: "workspace", label: "Command Center", icon: "whiteboard" },
  { id: "chat", label: "Chat", icon: "brain" },
  { id: "overview", label: "Projects", icon: "files" },
  { id: "research", label: "Research", icon: "room" },
  { id: "agents", label: "Agents", icon: "agents" },
];

export const NAV_RESOURCES: NavItem[] = [
  { id: "workbench", label: "Files", icon: "files" },
  { id: "artifacts", label: "Artifacts", icon: "files" },
  { id: "tools", label: "Tools", icon: "shield" },
  { id: "settings", label: "Models", icon: "models" },
  { id: "validation", label: "Validation", icon: "shield" },
  { id: "feedback", label: "Feedback", icon: "spark" },
  { id: "help", label: "Help", icon: "logs" },
];

export const NAV_ADVANCED: NavItem[] = [
  { id: "automations", label: "Automations", icon: "automation" },
  { id: "review", label: "Review Center", icon: "logs" },
  { id: "permissions", label: "Computer access", icon: "shield" },
  { id: "plugins", label: "Plugins", icon: "plugins" },
];

export const NAV_KNOWN_IDS: string[] = [
  ...NAV_WORK,
  ...NAV_RESOURCES,
  ...NAV_ADVANCED,
].map((item) => item.id);

export function navLabel(view: string): string | null {
  for (const item of [...NAV_WORK, ...NAV_RESOURCES, ...NAV_ADVANCED]) {
    if (item.id === view) return item.label;
  }
  return null;
}

// ---------- 4. Provider card guidance (order work package 4) ----------
// Honest, non-invented status lines for the commercial provider directory.

export interface ProviderCardGuidance {
  tone: "ok" | "empty";
  line: string;
}

export function providerCardGuidance(
  familyLabel: string,
  connected: boolean,
  modelsAvailable: number,
): ProviderCardGuidance {
  if (connected) {
    return modelsAvailable > 0
      ? { tone: "ok", line: `Connected — ${modelsAvailable} model${modelsAvailable === 1 ? "" : "s"} selectable.` }
      : { tone: "ok", line: "Connected — catalog discovery has not reported models yet." };
  }
  return { tone: "empty", line: `Not connected — add a ${familyLabel} key to enable its models for selection.` };
}

// ---------- 5. Cost explanation (order work package 4) ----------
// One human sentence per rate-card basis, matching the Z3 cost model.

export function costBasisExplanation(basis: string): { title: string; detail: string } {
  switch (basis) {
    case "free_local":
      return { title: "Local and free", detail: "Runs on this PC's Ollama runtime; no external API charge is possible." };
    case "direct_profile":
      return { title: "Saved profile rate", detail: "Priced from the direct profile's stored per-request rates." };
    case "pricing_registry":
      return { title: "Verified registry rate", detail: "Priced from AMCA's verified Pricing Registry; the newest verified record wins." };
    default:
      return { title: "Unknown pricing", detail: "No verified price on record — AMCA does not invent an estimate." };
  }
}

// ---------- 6. Model comparison rows (order work package 4) ----------
// Flatten grouped selection options into ordered rows so the Selection &
// Cost tab can present a scannable comparison table.

export type SelectionSource = "local" | "byok" | "direct" | "amca_api";

export interface SelectionOptionLike {
  ref: string;
  label: string;
  source: SelectionSource;
}

export interface SelectionGroupLike {
  familyId: string;
  label: string;
  options: SelectionOptionLike[];
}

export interface ComparisonRow {
  familyId: string;
  groupLabel: string;
  ref: string;
  label: string;
  sourceLabel: string;
}

export const SOURCE_LABELS: Record<SelectionSource, string> = {
  local: "Local · free",
  byok: "BYOK",
  direct: "Direct profile",
  amca_api: "Managed",
};

export function modelComparisonRows(groups: SelectionGroupLike[]): ComparisonRow[] {
  const rows: ComparisonRow[] = [];
  for (const group of groups) {
    for (const option of group.options) {
      rows.push({
        familyId: group.familyId,
        groupLabel: group.label,
        ref: option.ref,
        label: option.label,
        sourceLabel: SOURCE_LABELS[option.source] ?? "Available",
      });
    }
  }
  return rows;
}

// ---------- 7. Unavailable-model summary (order work package 4) ----------
// Selection only lists configured sources; this explains what is NOT
// selectable and why, from the same hub document. No invented states.

export interface UnavailableModelSummary {
  missingCredentials: number;
  pendingDiscovery: number;
  line: string;
}

export function unavailableModelSummary(input: {
  directModels: Array<{ configured: boolean }>;
  byokProviders: Array<{ configured: boolean; model_ids: string[] }>;
}): UnavailableModelSummary | null {
  const missingCredentials = input.directModels.filter((item) => !item.configured).length;
  const pendingDiscovery = input.byokProviders.filter(
    (item) => item.configured && item.model_ids.length === 0,
  ).length;
  const unconfiguredProviders = input.byokProviders.filter((item) => !item.configured).length;
  const totalUnavailable = missingCredentials + pendingDiscovery + unconfiguredProviders;
  if (totalUnavailable === 0) return null;
  const parts: string[] = [];
  if (missingCredentials) parts.push(`${missingCredentials} profile${missingCredentials === 1 ? "" : "s"} missing a credential`);
  if (unconfiguredProviders) parts.push(`${unconfiguredProviders} provider${unconfiguredProviders === 1 ? "" : "s"} not connected`);
  if (pendingDiscovery) parts.push(`${pendingDiscovery} provider${pendingDiscovery === 1 ? "" : "s"} pending discovery`);
  return {
    missingCredentials,
    pendingDiscovery,
    line: `${totalUnavailable} source${totalUnavailable === 1 ? "" : "s"} not selectable yet — ${parts.join("; ")}.`,
  };
}

// ---------- 8. Run duration label (order work package 3) ----------
// Elapsed time for the dashboard's current-task card. `nowMs` is injected
// so the result is deterministic and testable; live runs show a growing
// duration, finished runs pin to finished_at.

export interface DurationView {
  label: string;
  live: boolean;
}

export function runDurationLabel(
  run: { started_at: string | null; finished_at: string | null },
  nowMs: number,
): DurationView | null {
  if (!run.started_at) return null;
  const started = Date.parse(run.started_at);
  if (Number.isNaN(started)) return null;
  const rawEnd = run.finished_at ? Date.parse(run.finished_at) : nowMs;
  const end = Number.isNaN(rawEnd) ? nowMs : rawEnd;
  const seconds = Math.max(0, Math.round((end - started) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  const label =
    hours > 0
      ? `${hours}h ${String(minutes).padStart(2, "0")}m`
      : minutes > 0
        ? `${minutes}m ${String(rest).padStart(2, "0")}s`
        : `${rest}s`;
  return { label, live: !run.finished_at };
}

// ---------- 9. Agent activity bar (order work package 3) ----------
// One-bar visualization of the existing nano activity counts: live,
// completed, and the remainder (retired/other). Fractions sum to ≤ 1.

export interface ActivitySegment {
  key: "live" | "completed" | "other";
  label: string;
  count: number;
  fraction: number;
}

export function activityBarSegments(activity: {
  nanoTotal: number;
  nanoLive: number;
  nanoCompleted: number;
}): ActivitySegment[] {
  const total = Math.max(0, activity.nanoTotal);
  if (total === 0) return [];
  const live = Math.min(Math.max(0, activity.nanoLive), total);
  const completed = Math.min(Math.max(0, activity.nanoCompleted), total - live);
  const other = total - live - completed;
  const segments: ActivitySegment[] = [];
  if (live) segments.push({ key: "live", label: "Live nano", count: live, fraction: live / total });
  if (completed) segments.push({ key: "completed", label: "Completed", count: completed, fraction: completed / total });
  if (other) segments.push({ key: "other", label: "Idle or retired", count: other, fraction: other / total });
  return segments;
}

// ---------- 10. Demo readiness package (order work package 6) ----------
// Static, honest content for the alpha walkthrough document. Screens list
// what a demonstrator should have on screen; limitations repeat only
// gaps already accepted by the manager (Z2/Z3 verdicts).

export interface DemoChecklistItem {
  id: string;
  title: string;
  detail: string;
  view: string;
}

export const DEMO_CHECKLIST: DemoChecklistItem[] = [
  { id: "core_running", title: "Core service running", detail: "Start the local Python service; the sidebar footer shows “Core connected”.", view: "dashboard" },
  { id: "project_ready", title: "Demo Project exists", detail: "Create or open a Project so chats, files, and history are scoped.", view: "overview" },
  { id: "models_ready", title: "Model Hub has a selectable model", detail: "One Ollama model installed or one BYOK provider connected — never a simulated entry.", view: "settings" },
  { id: "composer_ready", title: "Composer goal prepared", detail: "A concrete task goal typed into the dashboard composer before the audience arrives.", view: "dashboard" },
  { id: "monitor_ready", title: "Agent Control Panel open plan", detail: "Show run states and the nano tree during execution.", view: "agents" },
  { id: "artifact_ready", title: "Artifact outcome expected", detail: "Choose a task type that produces a markdown or PDF artifact so Review → Export completes the journey.", view: "artifacts" },
  { id: "privacy_ready", title: "Privacy story on screen", detail: "“Local-first” badge and Core-connected footer are visible during the whole demo.", view: "dashboard" },
];

export interface KnownLimitation {
  title: string;
  detail: string;
}

export const KNOWN_LIMITATIONS: KnownLimitation[] = [
  { title: "Provider health is discover-derived", detail: "Reachability/authentication checks reflect the bounded catalog test at connect time; there is no standing background probe." },
  { title: "llama.cpp / vLLM / SGLang status not reported", detail: "The execution engine accepts their URLs, but the hub document carries no status for them yet, so the UI says “Not reported” instead of guessing." },
  { title: "Cost is a published-rate preview", detail: "Estimates come from the verified Pricing Registry and saved profile rates; there is no usage metering or billing engine." },
  { title: "Credential rotation is delete + re-add", detail: "Keys live in the operating-system vault; rotation replaces the credential rather than editing it." },
  { title: "Task type travels as a goal tag", detail: "The composer prefix records task type in the goal text; durable task-type persistence is post-alpha." },
  { title: "Artifact export lives only in Artifact Center", detail: "The dashboard groups outputs for review; format export stays in one canonical place." },
  { title: "Experimental intelligence stays OFF", detail: "Adaptive Hybrid, Model Router, and Nano Agents follow v0.6.2 gating: session-scoped, user-enabled, default off." },
  { title: "Alpha scope exclusions hold", detail: "Marketplace, enterprise billing, million-agent scaling, internet agents, mobile, and SaaS are out of scope for the alpha." },
];
