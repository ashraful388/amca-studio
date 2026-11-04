// Model settings navigation model.
//
// The Model Hub and the BYOK provider surface are presented as one settings
// page in the style of the operator's reference design. The left navigation
// has TWO modes:
//
// * hub mode (every section except BYOK): the Model Hub's own grouped
//   section list;
// * provider mode (the BYOK Providers section, or a provider detail page):
//   the section list is REPLACED by the operator's provider list, grouped
//   like the reference into known API kinds and custom providers, with a
//   Back to Model Hub control that restores the section list.
//
// This module is deliberately pure — no React, no fetch, no browser — so the
// navigation, grouping, ordering and presentation rules can be unit tested.

import type { ModelHealthRecord, ModelHubProvider, ModelHealthState } from "./types";

export type HubSection =
  | "overview"
  | "providers"
  | "amca"
  | "byok"
  | "local"
  | "selection"
  | "teams"
  | "utilities";

export type SettingsTarget =
  | { kind: "section"; section: HubSection }
  | { kind: "provider"; providerId: string }
  /** A default provider preset that is not configured yet; selecting it
   * opens the new-provider draft pre-filled with that preset. */
  | { kind: "preset"; presetKind: string };

/** Default providers offered in the navigation even before they are
 * configured. The label is the preset name and nothing more: an unconfigured
 * row is a template, never a connection. */
export const DEFAULT_PROVIDER_PRESETS: Array<{ kind: string; label: string }> = [
  { kind: "openai", label: "OpenAI" },
  { kind: "anthropic", label: "Anthropic" },
  { kind: "openrouter", label: "OpenRouter" },
  { kind: "moonshot", label: "Kimi / Moonshot" },
  { kind: "deepseek", label: "DeepSeek" },
  { kind: "zai", label: "Z.ai" },
  { kind: "google", label: "Google / Gemini" },
  { kind: "mistral", label: "Mistral" },
  { kind: "xai", label: "xAI / Grok" },
];

export type BadgeTone = "ok" | "warn" | "off" | "none";

export interface NavEntry {
  key: string;
  target: SettingsTarget | "draft";
  group: string;
  label: string;
  icon: string;
  active: boolean;
  tone: BadgeTone;
  statusLabel: string;
  metaLabel: string;
}

export interface NavGroup {
  group: string;
  entries: NavEntry[];
}

/** Grouped section list. Order is the navigation order; nothing is sorted away. */
const SECTION_GROUPS: Array<{ group: string; sections: HubSection[] }> = [
  {
    group: "Model Hub",
    sections: ["overview", "providers", "byok", "local", "selection", "teams", "utilities"],
  },
  { group: "Account", sections: ["amca"] },
];

const SECTION_PRESENTATION: Record<HubSection, { label: string; icon: string }> = {
  overview: { label: "Overview", icon: "◎" },
  providers: { label: "Provider Directory", icon: "▤" },
  byok: { label: "BYOK Providers", icon: "⇄" },
  local: { label: "Local Models", icon: "▣" },
  selection: { label: "Selection & Cost", icon: "◈" },
  teams: { label: "Model Teams", icon: "⧉" },
  utilities: { label: "Utilities", icon: "⚙" },
  amca: { label: "AMCA API", icon: "◇" },
};

/**
 * Provider-mode navigation groups. The reference design shows first-party
 * providers apart from the operator's custom ones. AMCA has no first-party
 * remote provider; the honest equivalent split is the API kind the operator
 * picked: a KNOWN kind (OpenAI, Anthropic, a local runtime, …) versus a fully
 * custom OpenAI-compatible endpoint. Both groups are the operator's own
 * connections — nothing here is invented identity.
 */
export const PROVIDER_NAV_GROUPS = { known: "Providers", custom: "Custom providers" } as const;

/**
 * API format is DERIVED from `provider_kind`, never stored or guessed.
 *
 * `model_hub_providers` has no api_format column, and the backend contract
 * forbids inventing provider capability fields. The provider kind is the only
 * recorded fact about which wire format a connection uses, so the label is
 * computed from it and says so; an unknown kind falls back to the documented
 * OpenAI-compatible form rather than claiming a format nobody recorded.
 */
const API_FORMATS: Record<string, string> = {
  openai: "Chat completions (/chat/completions)",
  anthropic: "Anthropic messages (/v1/messages)",
  google: "Google generateContent",
  moonshot: "Moonshot / Kimi · chat/completions",
  deepseek: "DeepSeek · chat/completions",
  mistral: "Mistral · chat/completions",
  xai: "xAI / Grok · chat/completions",
  custom: "Other / Custom (OpenAI-compatible)",
};

/** Select options for the API format control, in display order. */
export const API_FORMAT_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "openai", label: API_FORMATS.openai },
  { value: "anthropic", label: API_FORMATS.anthropic },
  { value: "google", label: API_FORMATS.google },
  { value: "moonshot", label: API_FORMATS.moonshot },
  { value: "deepseek", label: API_FORMATS.deepseek },
  { value: "mistral", label: API_FORMATS.mistral },
  { value: "xai", label: API_FORMATS.xai },
  { value: "meta", label: API_FORMATS.openai },
  { value: "custom", label: API_FORMATS.custom },
];

export function apiFormatLabel(providerKind: string): string {
  // Meta speaks the plain OpenAI wire protocol; its label is the OpenAI one.
  if (providerKind === "meta") return API_FORMATS.openai;
  return API_FORMATS[providerKind] ?? API_FORMATS.custom;
}

/**
 * Badges describe RECORDED health evidence only.
 *
 * The reference design shows capability pills (Vision, IM). AMCA must not:
 * capability is unknown until a probe records it, and the provider contract
 * explicitly forbids inferring tool/vision support from a provider or model
 * name. A badge is therefore emitted only for a persisted health state, and an
 * absent state renders as "Unverified" rather than as a capability.
 */
const EVIDENCE: Record<string, { label: string; tone: BadgeTone }> = {
  READY: { label: "Runnable", tone: "ok" },
  AUTH_REQUIRED: { label: "Auth required", tone: "warn" },
  MODEL_NOT_FOUND: { label: "Not found", tone: "warn" },
  PROVIDER_UNAVAILABLE: { label: "Provider down", tone: "warn" },
  LOCAL_RUNTIME_OFFLINE: { label: "Runtime offline", tone: "warn" },
  QUOTA_EXHAUSTED: { label: "No credit", tone: "warn" },
  // A RECORDED UNVERIFIED verdict means the backend measured the
  // configuration and found it unusable (no endpoint configured). It is not
  // the same as a model that was never tested, which has no record at all
  // and renders as "Unverified" below.
  UNVERIFIED: { label: "Not configured", tone: "off" },
};

export function evidenceBadge(state?: ModelHealthState | string | null): {
  label: string;
  tone: BadgeTone;
} {
  if (!state) return { label: "Unverified", tone: "off" };
  return EVIDENCE[state] ?? { label: "Unverified", tone: "off" };
}

/**
 * States that describe a DEFECT in the configuration itself.
 *
 * The provider rejected the credential, the model id does not exist there,
 * or no endpoint was configured: the operator has to repair something.
 * These are the only states that exclude a model from selection.
 *
 * A transient failure (provider down, rate limited, no answer in time)
 * says nothing about whether the configuration is right, and the backend
 * re-verifies a blocked configuration before it gives up on a run — so a
 * provider hiccup must not lock a working model out of the composer until
 * someone re-measures it by hand. This list mirrors
 * model_health.CONFIGURATION_DEFECT_STATES in the backend.
 *
 * Note the difference from a model with NO record at all, which is simply
 * untested: that one is usable and says "Unverified".
 */
export const CONFIGURATION_DEFECT_STATES: readonly string[] = [
  "AUTH_REQUIRED",
  "MODEL_NOT_FOUND",
  "QUOTA_EXHAUSTED",
  "UNVERIFIED",
];

export function isConfigurationDefect(state?: string | null): boolean {
  return CONFIGURATION_DEFECT_STATES.includes(String(state ?? ""));
}

/**
 * Operator-recorded numbers are shown compactly (128000 → "128k",
 * 1000000 → "1M") and never presented as a measurement. A null/absent value
 * formats to "" so no badge is drawn for a field the operator left blank.
 */
export function formatTokenCount(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "";
  if (value < 0) return "";
  if (value >= 1_000_000) {
    const millions = value / 1_000_000;
    return `${Number.isInteger(millions) ? millions : millions.toFixed(1)}M`;
  }
  // Below ten thousand the compact form would lie by rounding (8192 is not
  // "8.2k"), so the exact number is shown instead.
  if (value >= 10_000) {
    const thousands = value / 1_000;
    return `${Number.isInteger(thousands) ? thousands : thousands.toFixed(1)}k`;
  }
  return String(value);
}

export function sectionEntry(section: HubSection, active: boolean): NavEntry {
  const presentation = SECTION_PRESENTATION[section];
  return {
    key: `section:${section}`,
    target: { kind: "section", section },
    group: "",
    label: presentation.label,
    icon: presentation.icon,
    active,
    tone: "none",
    statusLabel: "",
    metaLabel: "",
  };
}

/**
 * Provider mode replaces the section navigation. It is on for the BYOK
 * section itself and for any provider detail page; every other section keeps
 * the hub's own navigation.
 */
export function showProviderNav(target: SettingsTarget): boolean {
  if (target.kind === "provider") return true;
  if (target.kind === "preset") return true;
  return target.kind === "section" && target.section === "byok";
}

/**
 * The navigation dot reports RECORDED health, never stored metadata.
 *
 * `status` is written by the backend from what the operator typed: a provider
 * with model ids in it is `catalog_ready` without a single request having been
 * made, so a green "Connected" dot driven by that field would claim a
 * connection nobody performed. A dot is therefore drawn from a persisted health
 * record for this provider and endpoint, and stays neutral until one exists.
 * Exclusion is shown as neutral too: it is the operator's own stored decision,
 * not a health fact.
 */
function recordedEvidence(
  health: Record<string, ModelHealthRecord> | undefined,
  provider: ModelHubProvider,
): ModelHealthRecord | undefined {
  if (!health) return undefined;
  const records = Object.values(health);
  if (records.length === 0) return undefined;
  // The persisted fingerprint covers the endpoint, so a record captured against
  // a previous endpoint is not evidence about this one.
  return records.find(
    (record) => record.ref.startsWith(`${provider.id}:`) && record.endpoint === provider.endpoint,
  );
}

function providerTone(
  health: Record<string, ModelHealthRecord> | undefined,
  provider: ModelHubProvider,
): BadgeTone {
  if (!provider.configured) return "off";
  if (provider.enabled === false) return "off";
  const record = recordedEvidence(health, provider);
  if (!record) return "off";
  return evidenceBadge(record.state).tone;
}

function providerStatus(
  health: Record<string, ModelHealthRecord> | undefined,
  provider: ModelHubProvider,
): string {
  if (!provider.configured) return "No credential stored";
  if (provider.enabled === false) return "Excluded from chat";
  const record = recordedEvidence(health, provider);
  if (!record) return "Not verified — no health check recorded";
  const badge = evidenceBadge(record.state);
  return badge.label === "Unverified" ? "Not verified" : badge.label;
}

function providerMeta(provider: ModelHubProvider): string {
  if (!provider.configured) return "Credential missing";
  if (provider.model_ids.length === 0) return "Model discovery pending";
  const shown = provider.enabled_model_ids.length;
  const total = provider.model_ids.length;
  return `${shown} of ${total} model${total === 1 ? "" : "s"} shown in chat`;
}

/**
 * Every provider row renders identically: one line, name and status dot.
 * The real counts and the credential/health state travel in the row's
 * tooltip instead of a second line, so a configured provider and an
 * unconfigured preset look like the same list item.
 */
function providerRowLabel(
  provider: ModelHubProvider,
  status: string,
): string {
  const meta = providerMeta(provider);
  // 'Credential missing' restates the status for an unconfigured provider,
  // so only real counts or discovery state are appended.
  const extra = meta !== 'Credential missing' ? meta : '';
  return extra ? `${status} · ${extra}` : status;
}

function providerEntry(
  provider: ModelHubProvider,
  target: SettingsTarget,
  health?: Record<string, ModelHealthRecord>,
): NavEntry {
  return {
    key: `provider:${provider.id}`,
    target: { kind: "provider", providerId: provider.id },
    group: "",
    label: provider.name,
    icon: "box",
    active: target.kind === "provider" && target.providerId === provider.id,
    tone: providerTone(health, provider),
    statusLabel: providerRowLabel(provider, providerStatus(health, provider)),
    metaLabel: "",
  };
}

/**
 * Builds the hub-mode grouped navigation: the Model Hub's own sections. The
 * operator's providers do NOT appear here — in provider mode they take the
 * whole navigation (see buildProviderNav), which is the reference design the
 * operator asked for.
 */
export function buildModelSettingsNav(
  target: SettingsTarget,
): NavGroup[] {
  const isSection = (section: HubSection) =>
    target.kind === "section" && target.section === section;

  return SECTION_GROUPS.map(({ group, sections }) => ({
    group,
    entries: sections.map((section) => {
      const entry = sectionEntry(section, isSection(section));
      return { ...entry, group };
    }),
  }));
}

/**
 * Builds the provider-mode navigation: the operator's providers, grouped into
 * known API kinds and custom endpoints, in the store's own order. When the
 * new-provider draft is open it appears as a selected "New provider" row at
 * the end of the custom group — it is not yet a record, so it carries no dot.
 */
export function buildProviderNav(
  providers: ModelHubProvider[],
  target: SettingsTarget,
  health?: Record<string, ModelHealthRecord>,
  draftActive?: boolean,
): NavGroup[] {
  const known: NavEntry[] = [];
  const custom: NavEntry[] = [];
  for (const provider of providers) {
    const entry = { ...providerEntry(provider, target, health), group: "" };
    const group = provider.provider_kind === "custom" ? PROVIDER_NAV_GROUPS.custom : PROVIDER_NAV_GROUPS.known;
    (group === PROVIDER_NAV_GROUPS.custom ? custom : known).push({
      ...entry,
      group,
    });
  }
  // Default providers that are not configured yet sit under the configured
  // ones in the Providers group: visible templates, never connections.
  const configuredKinds = new Set(providers.map((provider) => provider.provider_kind));
  for (const preset of DEFAULT_PROVIDER_PRESETS) {
    if (configuredKinds.has(preset.kind)) continue;
    known.push({
      key: `preset:${preset.kind}`,
      target: { kind: "preset", presetKind: preset.kind },
      group: PROVIDER_NAV_GROUPS.known,
      label: preset.label,
      icon: "box",
      active: target.kind === "preset" && target.presetKind === preset.kind,
      tone: "none",
      statusLabel: "Not configured — select to add this provider",
      metaLabel: "Not configured",
    });
  }
  const groups: NavGroup[] = [];
  if (known.length > 0) {
    groups.push({ group: PROVIDER_NAV_GROUPS.known, entries: known });
  }
  const customGroup: NavGroup = {
    group: PROVIDER_NAV_GROUPS.custom,
    entries: custom,
  };
  if (draftActive) {
    customGroup.entries.push({
      key: "provider:draft",
      target: "draft",
      group: PROVIDER_NAV_GROUPS.custom,
      label: "New provider",
      icon: "box",
      active: true,
      tone: "off",
      statusLabel: "Not saved yet — fill in the form to create it",
      metaLabel: "",
    });
  }
  if (customGroup.entries.length > 0) {
    groups.push(customGroup);
  }
  return groups;
}

/**
 * The back control restores the hub navigation. It is offered exactly when
 * provider mode is active — there is nothing to go back to from the hub's own
 * sections, so the control is absent there rather than a dead affordance.
 */
export function backToHubTarget(target: SettingsTarget): SettingsTarget | null {
  if (showProviderNav(target)) {
    return { kind: "section", section: "overview" };
  }
  return null;
}

export function backToHubLabel(): string {
  return "Back to Model Hub";
}

// Response-reliability fix (owner report, 2026-11-05): an API key pasted into
// the provider form used to commit ONLY on Enter — clicking anywhere else left
// it sitting unstored in the field while the card kept reading "Credential
// missing" (the same trap as the Meta round). The key row now commits on blur
// too, except when focus moves to a control that is part of the key row itself
// (the reveal toggle): double-checking what was typed must not store it first.
export function blurCommitsSecret(
  relatedTarget: Element | null | undefined,
  fieldContainer: Element | null | undefined,
): boolean {
  if (!fieldContainer) return true;
  if (!relatedTarget) return true;
  return !fieldContainer.contains(relatedTarget);
}
