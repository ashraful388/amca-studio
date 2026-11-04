// ZCODE-AMCA-STUDIO-V0.8.0-FE-UX-IMPROVEMENT-BATCH-01 — pure presentation model.
//
// Implements the manager-approved frontend-only v0.8.0 UX batch on top of
// EXISTING records and mechanisms only:
// - W1 deep links carry the recorded artifact type from the Dashboard's
//   Recent outputs groups into the Artifact Center's existing type filter.
// - W2 "Save as Team" writes through the EXISTING amca.saveModelTeam
//   contract (model_refs + name + description) — no new storage, no
//   routing behavior change.
// - W3 export empty state routes to Projects via honest recorded wording.
// - W4 remembers the last export format per project in localStorage — a
//   local preference only, no backend persistence, no telemetry.
// - U3/D1/D2 replace unexplained labels with recorded, honest explanations.
// No backend, no Agent Fabric/Model Router changes, no new limits.

// ---------- W1: Recent outputs → Artifact Center deep link ----------

/** Artifact types the Artifact Center's existing filter tabs can select. */
export const ARTIFACT_CENTER_FILTERS = ["all", "document", "spreadsheet", "presentation", "text"] as const;
export type ArtifactCenterFilter = (typeof ARTIFACT_CENTER_FILTERS)[number];

/**
 * Map a recorded artifact type to the Artifact Center filter that shows it.
 * Returns null for types outside the existing five tabs — the deep link then
 * uses "all" rather than inventing a new filter category.
 */
export function artifactCenterFilterForType(artifactType: string): ArtifactCenterFilter {
  const normalized = artifactType.trim().toLowerCase();
  if (normalized === "docx") return "document";
  if (normalized === "xlsx") return "spreadsheet";
  if (normalized === "pptx") return "presentation";
  if (normalized === "text" || normalized === "txt") return "text";
  if ((ARTIFACT_CENTER_FILTERS as readonly string[]).includes(normalized)) {
    return normalized as ArtifactCenterFilter;
  }
  return "all";
}

/** Deep-link descriptor the Dashboard hands to the shell on one click. */
export interface ArtifactCenterFocus {
  projectId: string | null;
  filter: ArtifactCenterFilter;
  source: "dashboard_recent_outputs";
}

export function artifactCenterFocus(
  projectId: string | null,
  artifactType: string,
): ArtifactCenterFocus {
  return { projectId, filter: artifactCenterFilterForType(artifactType), source: "dashboard_recent_outputs" };
}

// ---------- W3: honest export empty state ----------

export const EXPORT_EMPTY_STATE = {
  title: "No exports yet.",
  detail: "Create a task to generate artifacts.",
  actionLabel: "Open Projects",
} as const;

// ---------- W4: remember last export format per project (local only) ----------

export const LAST_EXPORT_FORMAT_KEY = "amca.last-export-format.v1";

export interface LastFormatStore {
  read(projectId: string): string | null;
  write(projectId: string, format: string): void;
}

/** Real localStorage-backed store used by the Artifact Center. */
export function localStorageFormatStore(storage: Pick<Storage, "getItem" | "setItem"> = window.localStorage): LastFormatStore {
  const readAll = (): Record<string, string> => {
    try {
      const value = JSON.parse(storage.getItem(LAST_EXPORT_FORMAT_KEY) ?? "{}");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch {
      return {};
    }
  };
  return {
    read(projectId: string): string | null {
      const value = readAll()[projectId];
      return typeof value === "string" && value.trim() ? value : null;
    },
    write(projectId: string, format: string): void {
      if (!projectId.trim() || !format.trim()) return;
      const next = { ...readAll(), [projectId]: format };
      try {
        storage.setItem(LAST_EXPORT_FORMAT_KEY, JSON.stringify(next));
      } catch {
        // A full or unavailable localStorage is not an export failure — the
        // preference simply does not persist for this session.
      }
    },
  };
}

// ---------- W2: save the current chat selection as a Model Team ----------

export const MAX_CHAT_TEAM_MEMBERS = 8;

export interface SaveSelectionDraft {
  name: string;
  modelRefs: string[];
}

export interface SaveSelectionValidation {
  ok: boolean;
  errors: string[];
}

/**
 * Validate the chat picker's "Save as Team" draft against the EXISTING
 * model-team contract (name required; at least one member; the chat picker
 * itself caps selection at 8 via MAX_TEAM_MODELS). Deliberately narrower
 * than teamModel.validateTeamDraft: the picker only offers selectable
 * (non-stale) references, so no availability check is needed here.
 */
export function validateSaveSelection(draft: SaveSelectionDraft): SaveSelectionValidation {
  const errors: string[] = [];
  if (!draft.name.trim()) errors.push("A team name is required.");
  if (draft.modelRefs.length === 0) errors.push("Add at least one model member.");
  if (draft.modelRefs.length > MAX_CHAT_TEAM_MEMBERS) {
    errors.push(`A chat selection can hold at most ${MAX_CHAT_TEAM_MEMBERS} models.`);
  }
  return { ok: errors.length === 0, errors };
}

/** Default team name offered in the picker's save row (editable before save). */
export function defaultSavedTeamName(modelRefs: string[]): string {
  return modelRefs.length === 1 ? "Chat selection · single model" : `Chat selection · ${modelRefs.length} models`;
}

// ---------- U3: automation trigger honesty ----------

export const AUTOMATION_TRIGGER_EXPLANATION =
  "Runs by manual execution only — the scheduler does not support this trigger type yet.";

/** Short label for the Next check row; explanation is rendered alongside. */
export function unsupportedTriggerLabel(): string {
  return "Manual only · trigger unsupported";
}

// ---------- D1: planned-card discoverability (Model Hub) ----------

export interface PlannedCardExplanation {
  cardId: "model_matchmaker" | "context_explorer";
  purpose: string;
  whyUnavailable: string;
  expectedDependency: string;
}

/** Recorded purposes only — no dates, no promises. */
export const PLANNED_CARD_EXPLANATIONS: PlannedCardExplanation[] = [
  {
    cardId: "model_matchmaker",
    purpose: "Would suggest collaboration candidates from the shared Model Registry for a chosen goal.",
    whyUnavailable: "Not implemented yet — the card stays disabled rather than guessing.",
    expectedDependency: "Depends on routing-candidate scoring over the existing registry.",
  },
  {
    cardId: "context_explorer",
    purpose: "Would compare a task's context size with each registered model's context limit.",
    whyUnavailable: "Not implemented yet — the card stays disabled rather than guessing.",
    expectedDependency: "Depends on recorded model context limits and task context accounting.",
  },
];

export function plannedCardExplanation(cardId: PlannedCardExplanation["cardId"]): PlannedCardExplanation | null {
  return PLANNED_CARD_EXPLANATIONS.find((card) => card.cardId === cardId) ?? null;
}

// ---------- D2: Review Center first-visit explanation ----------

export const REVIEW_CENTER_EXPLANATION =
  "What this panel shows: items recorded by runs and automations that may need human attention — evidence comes from existing run and review records only. No hidden AI judgment; every action here is yours.";
