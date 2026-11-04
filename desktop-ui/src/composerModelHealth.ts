import { evidenceBadge, isConfigurationDefect } from "./modelSettingsNavModel.ts";

/**
 * Composer model-menu health presentation.
 *
 * The model menu and the Model settings list must tell the operator the
 * same story about the same model: a recorded failure blocks the model and
 * says WHY in the same words, and an untested model says so instead of
 * pretending to be fine. The raw state constant ("AUTH_REQUIRED") is an
 * internal vocabulary and never reaches the operator.
 */

export interface ComposerHealthRecord {
  state?: string | null;
  runnable?: boolean;
}

export interface ComposerModelHealth {
  /** The model cannot be selected because a recorded verdict failed. */
  blocked: boolean;
  /** The label shown next to the model name ("Auth required", "Not found"). */
  label: string;
  /** The model has no recorded verdict: usable, but honestly untested. */
  unverified: boolean;
  /** The operator can measure this model right now (one bounded request). */
  verifiable: boolean;
}

export function composerModelHealth(
  record?: ComposerHealthRecord | null,
): ComposerModelHealth {
  if (!record) {
    return {
      blocked: false,
      label: evidenceBadge(null).label,
      unverified: true,
      verifiable: true,
    };
  }
  const badge = evidenceBadge(record.state ?? null);
  // Only a CONFIGURATION DEFECT excludes a model. A transient failure
  // (provider down, rate limited, no answer in time) says nothing about
  // whether the configuration is right, and the backend re-verifies a
  // blocked configuration before it gives up on a run — so the model stays
  // selectable and the label says what happened.
  const blocked = isConfigurationDefect(record.state);
  return {
    blocked,
    label: badge.label,
    unverified: false,
    verifiable: record.runnable === false || record.state !== "READY",
  };
}
