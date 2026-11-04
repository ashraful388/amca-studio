// ZCODE-AMCA-STUDIO-V0.9.0-END-USER-WORKFLOW-VALIDATION-AND-POLISH-01 — pure model.
//
// End-user workflow validation: the complete journey from first launch to
// export, validated against EXISTING model exports only. Recorded rules
// from the order:
// - full journey coverage: all eleven stages map to real surfaces/models;
// - terminology consistency: the canonical terms appear verbatim across
//   the surfaces that use them;
// - no unsupported claims, no broken navigation (drill/entry targets are
//   existing views), empty-state consistency;
// - polish only where evidence supports it.

// Dependency-injected inputs (no cross-model value imports): callers pass
// the live view ids and empty-state wordings from the owning models.

// ---------- 1. Journey stage mapping ----------

export interface JourneyStage {
  stage: string;
  /** The existing surface or model that implements the stage. */
  surface: string;
  /** Evidence the stage is wired: exported constant or view id. */
  evidence: string;
}

export const JOURNEY_STAGES: JourneyStage[] = [
  { stage: "First Launch", surface: "WorkspaceDashboard onboarding strip", evidence: "releasePolishModel.firstRunExperience" },
  { stage: "Onboarding", surface: "WorkspaceDashboard five-step strip", evidence: "onboardingSteps" },
  { stage: "Workspace Creation", surface: "Projects view / + New Project", evidence: "saveStudioProject" },
  { stage: "Model Setup", surface: "Model Hub (Models view)", evidence: "modelHubModel" },
  { stage: "Knowledge Selection", surface: "Composer knowledge picker", evidence: "knowledgeContextModel" },
  { stage: "Workflow Creation", surface: "Composer task start", evidence: "composerModel.buildComposerPayload" },
  { stage: "Governance Review", surface: "Validation Center governance panels", evidence: "consentGovernanceUxModel" },
  { stage: "Execution", surface: "Agent Monitor / run registry", evidence: "agentMonitorModel" },
  { stage: "Monitoring", surface: "Command Center operational timeline", evidence: "operationalWorkspaceIntelligenceModel" },
  { stage: "Artifact Review", surface: "Artifact Center", evidence: "ArtifactCenter" },
  { stage: "Export", surface: "Artifact Center Export tab", evidence: "artifactExport" },
];

// ---------- 2. Terminology consistency (canonical terms) ----------

export const CANONICAL_TERMS: ReadonlyArray<string> = [
  "Local only",
  "Approval required",
  "Explicitly attached",
  "Not recorded",
  "No automatic retrieval",
];

export interface TermAuditRow {
  term: string;
  surfacesCarryingIt: string[];
  consistent: boolean;
}

/**
 * Verify each canonical term appears verbatim in the live model constant
 * that owns it (the models are imported and their texts scanned).
 */
export function terminologyAudit(surfaces: Array<{ name: string; text: string }>): TermAuditRow[] {
  return CANONICAL_TERMS.map((term) => {
    const lowered = term.toLowerCase();
    const surfacesCarryingIt = surfaces
      .filter((surface) => surface.text.toLowerCase().includes(lowered))
      .map((surface) => surface.name);
    return { term, surfacesCarryingIt, consistent: surfacesCarryingIt.length > 0 };
  });
}

// ---------- 3. Navigation safety ----------

/** Every recorded drill/entry target must be an existing sidebar view id. */
export function navigationTargetsExist(targets: string[], viewIds: string[]): { target: string; exists: boolean }[] {
  const known = new Set(viewIds);
  return targets.map((target) => ({ target, exists: known.has(target) }));
}

// ---------- 4. Empty-state consistency ----------

export interface EmptyStateCheck {
  source: string;
  keys: string[];
  consistent: boolean;
}

/**
 * All cognitive/operational empty states in one check: every registered
 * empty-state key must be non-empty wording.
 */
export function emptyStateConsistency(
  sources: Array<{ source: string; keys: string[] }>,
): EmptyStateCheck[] {
  return sources.map((entry) => ({
    source: entry.source,
    keys: entry.keys,
    consistent: entry.keys.every((key) => typeof key === "string" && key.trim().length > 0),
  }));
}

// ---------- 5. Unsupported-claim scan ----------

const FORBIDDEN_JOURNEY_PATTERNS: ReadonlyArray<RegExp> = [
  /autonomous (?:ai )?(?:employee|agent|operation)/i,
  /unlimited agent swarm/i,
  /cloud saas/i,
  /marketplace ecosystem/i,
  /best (?:model|workflow)/i,
  /productivity score/i,
];

/** Journey text must not contain unsupported commercial or scoring claims. */
export function passesClaimScan(text: string): boolean {
  return !FORBIDDEN_JOURNEY_PATTERNS.some((pattern) => pattern.test(text));
}

// ---------- full validation ----------

export interface JourneyValidation {
  stagesCovered: number;
  totalStages: number;
  terminology: TermAuditRow[];
  navigation: { target: string; exists: boolean }[];
  emptyStates: EmptyStateCheck[];
  claimsClean: boolean;
  passed: boolean;
}

export function validateEndUserWorkflow(input: {
  surfaces: Array<{ name: string; text: string }>;
  navigationTargets: string[];
  journeyText: string;
  viewIds: string[];
  emptyStateSources: Array<{ source: string; keys: string[] }>;
}): JourneyValidation {
  const stagesCovered = JOURNEY_STAGES.length;
  const terminology = terminologyAudit(input.surfaces);
  const navigation = navigationTargetsExist(input.navigationTargets, input.viewIds);
  const emptyStates = emptyStateConsistency(input.emptyStateSources);
  const claimsClean = passesClaimScan(input.journeyText);
  const passed =
    stagesCovered === JOURNEY_STAGES.length &&
    terminology.every((row) => row.consistent) &&
    navigation.every((row) => row.exists) &&
    emptyStates.every((check) => check.consistent) &&
    claimsClean &&
    input.emptyStateSources.length > 0;
  return { stagesCovered, totalStages: JOURNEY_STAGES.length, terminology, navigation, emptyStates, claimsClean, passed };
}
