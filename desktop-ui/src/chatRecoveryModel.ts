// 32F Fix 2 — Retry re-resolution model.
//
// The error-banner Retry action must re-probe providers and refresh model
// availability before it touches the error banner: it never hides a failure
// whose condition is still present. This module keeps that decision pure so
// it is directly testable (tests/chatRecoveryModel.test.mjs).

export interface RetryLocalModel {
  model: string;
  fit?: number;
}

export interface RetryCredential {
  credential_id: string;
  configured: boolean;
}

export interface RetryHubProvider {
  id: string;
  configured: boolean;
  model_ids: string[];
  enabled_model_ids?: string[];
  enabled?: boolean;
}

export interface RetryProbeInput {
  localModels: RetryLocalModel[];
  credentials: RetryCredential[];
  hubProviders: RetryHubProvider[];
  selectedModel: string;
  canResume: boolean;
}

export type RetryOutcome = "execute" | "resolved" | "blocked";

export interface RetryResolution {
  availableModelIds: string[];
  nextSelectedModel: string;
  outcome: RetryOutcome;
  error: string | null;
}

export const NO_USABLE_MODEL_ERROR =
  "No usable model is configured. Open Model Hub to add or repair a model connection.";

export function availableModelIds(input: Omit<RetryProbeInput, "selectedModel" | "canResume">): string[] {
  return [
    ...input.localModels.filter((item) => item.fit !== 2).map((item) => `ollama:${item.model}`),
    ...input.credentials.filter((item) => item.configured).map((item) => item.credential_id),
    ...input.hubProviders
      .filter((provider) => provider.configured && provider.enabled !== false)
      .flatMap((provider) => (provider.enabled_model_ids ?? []).map((model) => `${provider.id}:${model}`)),
  ];
}

export function resolveRetryOutcome(input: RetryProbeInput): RetryResolution {
  const ids = availableModelIds(input);
  const nextSelectedModel =
    input.selectedModel && ids.includes(input.selectedModel)
      ? input.selectedModel
      : ids[0] ?? "";
  if (!ids.length) {
    // Truthful failure: the no-usable-model condition is still present, so the
    // banner keeps a real error instead of being silently cleared.
    return { availableModelIds: ids, nextSelectedModel: "", outcome: "blocked", error: NO_USABLE_MODEL_ERROR };
  }
  if (input.canResume) {
    // A model is now available and the composer still holds the unexecuted
    // message: the original request proceeds.
    return { availableModelIds: ids, nextSelectedModel, outcome: "execute", error: null };
  }
  return { availableModelIds: ids, nextSelectedModel, outcome: "resolved", error: null };
}
