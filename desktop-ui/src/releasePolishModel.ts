// Z7 Final UX Release Polish — pure presentation model (no fetch, no DOM).
// Covers the manager's release-polish scope: onboarding (first-run) state,
// settings completeness, user-facing consistency helpers, and the
// screenshot/demo manifest. Everything derives from EXISTING documents;
// no new features, no backend changes.

// ---------- 1. Shared label humanizer (UI consistency) ----------

const SMALL_WORDS = new Set(["and", "or", "the", "to", "of", "in", "on", "for"]);

export function humanizeLabel(value: string): string {
  if (!value.trim()) return value;
  const words = value.replaceAll("_", " ").split(/\s+/).filter(Boolean);
  return words
    .map((word, index) => {
      const lower = word.toLowerCase();
      if (index > 0 && SMALL_WORDS.has(lower)) return lower;
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(" ");
}

// ---------- 2. Onboarding (first-run) experience ----------

export interface OnboardingStateInput {
  hasProjects: boolean;
  hasConversations: boolean;
  hasSelectableModels: boolean;
  hasRuns: boolean;
  hasArtifacts: boolean;
}

export interface OnboardingStep {
  key: string;
  label: string;
  detail: string;
  view: string;
  done: boolean;
}

export function onboardingSteps(state: OnboardingStateInput): OnboardingStep[] {
  return [
    {
      key: "project",
      label: "Create a Project",
      detail: "Projects group related chats, files, and history on this device.",
      view: "overview",
      done: state.hasProjects,
    },
    {
      key: "models",
      label: "Enable a model",
      detail: "Install an Ollama model or connect a BYOK provider — one selectable model is enough to start.",
      view: "settings",
      done: state.hasSelectableModels,
    },
    {
      key: "chat",
      label: "Start a chat",
      detail: "Chats stay local; cloud models ask for exact approval before any network call.",
      view: "chat",
      done: state.hasConversations,
    },
    {
      key: "task",
      label: "Run your first task",
      detail: "The dashboard composer creates a governed run through the existing pipeline.",
      view: "dashboard",
      done: state.hasRuns,
    },
    {
      key: "artifact",
      label: "Review the output",
      detail: "Model-authored artifacts appear grouped by type, with export in the Artifact Center.",
      view: "artifacts",
      done: state.hasArtifacts,
    },
  ];
}

export interface FirstRunExperience {
  isFirstRun: boolean;
  headline: string;
  detail: string;
}

export function firstRunExperience(state: OnboardingStateInput): FirstRunExperience {
  const isFirstRun =
    !state.hasProjects && !state.hasConversations && !state.hasRuns && !state.hasArtifacts;
  if (isFirstRun) {
    return {
      isFirstRun,
      headline: "Welcome to AMCA Studio",
      detail: "Everything runs and stays on this device. Work through the five steps below to reach your first governed result.",
    };
  }
  const steps = onboardingSteps(state);
  const remaining = steps.filter((step) => !step.done);
  if (remaining.length === 0) {
    return {
      isFirstRun,
      headline: "Setup complete",
      detail: "Project, models, chats, runs, and artifacts are all in use — you are using the full studio loop.",
    };
  }
  return {
    isFirstRun,
    headline: `Getting started — ${steps.length - remaining.length} of ${steps.length} done`,
    detail: `Next: ${remaining[0].label}.`,
  };
}

// ---------- 3. Settings completeness (order: settings UX) ----------

export interface SettingsReadinessInput {
  selectableModels: number;
  connectedProviders: number;
  localRuntimeReachable: boolean;
  savedTeams: number;
  verifiedPrices: number;
}

export interface SettingsReadinessItem {
  key: string;
  label: string;
  done: boolean;
  detail: string;
}

export function settingsReadiness(input: SettingsReadinessInput): SettingsReadinessItem[] {
  return [
    {
      key: "models",
      label: "Selectable models",
      done: input.selectableModels > 0,
      detail: input.selectableModels > 0
        ? `${input.selectableModels} model${input.selectableModels === 1 ? "" : "s"} selectable today`
        : "Connect a provider or enable local models.",
    },
    {
      key: "providers",
      label: "Cloud connections",
      done: input.connectedProviders > 0,
      detail: input.connectedProviders > 0
        ? `${input.connectedProviders} provider connection${input.connectedProviders === 1 ? "" : "s"} configured`
        : "No BYOK or direct profile connected yet.",
    },
    {
      key: "local",
      label: "Local runtime",
      done: input.localRuntimeReachable,
      detail: input.localRuntimeReachable
        ? "Ollama reachable on this PC"
        : "Ollama not reachable — local models stay unavailable",
    },
    {
      key: "teams",
      label: "Model teams",
      done: input.savedTeams > 0,
      detail: input.savedTeams > 0
        ? `${input.savedTeams} saved combination${input.savedTeams === 1 ? "" : "s"}`
        : "Optional: combine models into a reusable team.",
    },
    {
      key: "pricing",
      label: "Verified pricing",
      done: input.verifiedPrices > 0,
      detail: input.verifiedPrices > 0
        ? `${input.verifiedPrices} verified record${input.verifiedPrices === 1 ? "" : "s"} in the registry`
        : "Cost previews show unknown until prices are verified.",
    },
  ];
}

// ---------- 4. Screenshot / demo manifest (release readiness) ----------
// Static capture plan for release docs and demos: every entry is a real
// view with real content; nothing staged beyond live data.

export interface ScreenshotTarget {
  id: string;
  view: string;
  title: string;
  focus: string;
}

export const SCREENSHOT_MANIFEST: ScreenshotTarget[] = [
  { id: "dashboard", view: "dashboard", title: "Workspace Dashboard", focus: "Composer, metrics, current task, demo-readiness panels; Local-first badge visible." },
  { id: "projects", view: "overview", title: "Projects", focus: "Project browser with chats, shared files, and instructions." },
  { id: "model_hub", view: "settings", title: "Model Hub — Provider Directory", focus: "Provider families with honest connection states; BYOK masked key form." },
  { id: "selection_cost", view: "settings", title: "Model Hub — Selection & Cost", focus: "Grouped selection, comparison table, cost explanation lines." },
  { id: "monitor", view: "agents", title: "Agent Control Panel", focus: "Run rows with states, UX banner, nano tree with lifecycle strips." },
  { id: "insights", view: "agents", title: "Collaboration Insights", focus: "Timeline, decision trace, cost insight on an inspected run." },
  { id: "artifacts", view: "artifacts", title: "Artifact Center — Export", focus: "Grouped artifacts plus capability-checked export tab." },
  { id: "review", view: "review", title: "Review Center", focus: "Governance review queue with unread indicators." },
];

export function screenshotTargetById(id: string): ScreenshotTarget | null {
  return SCREENSHOT_MANIFEST.find((target) => target.id === id) ?? null;
}
