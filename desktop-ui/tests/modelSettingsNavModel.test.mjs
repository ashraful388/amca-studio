// Model settings navigation model tests (pure presentation logic — no browser,
// no fetch, no backend). Run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  buildModelSettingsNav,
  buildProviderNav,
  showProviderNav,
  apiFormatLabel,
  API_FORMAT_OPTIONS,
  evidenceBadge,
  formatTokenCount,
  backToHubTarget,
  backToHubLabel,
  blurCommitsSecret,
  PROVIDER_NAV_GROUPS,
} from "../src/modelSettingsNavModel.ts";

function provider(overrides = {}) {
  return {
    id: "prov1",
    provider_kind: "openai",
    name: "OpenAI",
    endpoint: "https://api.openai.com/v1",
    status: "catalog_ready",
    pricing_mode: "auto",
    model_ids: ["gpt-4o", "gpt-4o-mini"],
    enabled_model_ids: ["gpt-4o"],
    configured: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function flatten(groups) {
  return groups.flatMap((group) => group.entries);
}

// -- hub mode: the section navigation --------------------------------------

test("every hub section appears in the navigation, in declared order", () => {
  const groups = buildModelSettingsNav({ kind: "section", section: "overview" });
  const sections = flatten(groups)
    .filter((entry) => entry.target.kind === "section")
    .map((entry) => entry.target.section);
  for (const section of [
    "overview",
    "providers",
    "byok",
    "local",
    "selection",
    "teams",
    "utilities",
    "amca",
  ]) {
    assert.ok(sections.includes(section), `${section} missing from navigation`);
  }
  assert.equal(sections[0], "overview");
});

test("hub navigation is grouped under visible headings", () => {
  const groups = buildModelSettingsNav({ kind: "section", section: "overview" });
  const names = groups.map((group) => group.group);
  assert.ok(names.includes("Model Hub"));
  assert.ok(names.includes("Account"));
  for (const group of groups) {
    for (const entry of group.entries) assert.equal(entry.group, group.group);
  }
});

test("hub-mode navigation lists sections only — providers live in provider mode", () => {
  // The operator's instruction: in provider mode the providers REPLACE the
  // options navigation. The mirror contract holds here: the options
  // navigation must not also list providers, or the two modes would double
  // the surface instead of replacing it.
  const groups = buildModelSettingsNav({ kind: "section", section: "overview" });
  const providerEntries = flatten(groups).filter(
    (entry) => entry.target.kind === "provider" || entry.target === "draft",
  );
  assert.equal(providerEntries.length, 0);
});

test("exactly one section is active when a section is selected", () => {
  const groups = buildModelSettingsNav({ kind: "section", section: "local" });
  const active = flatten(groups).filter((entry) => entry.active);
  assert.equal(active.length, 1);
  assert.equal(active[0].target.section, "local");
});

test("section labels are human labels, not raw ids", () => {
  const groups = buildModelSettingsNav({ kind: "section", section: "overview" });
  const labels = Object.fromEntries(
    flatten(groups)
      .filter((entry) => entry.target.kind === "section")
      .map((entry) => [entry.target.section, entry.label]),
  );
  assert.equal(labels.byok, "BYOK Providers");
  assert.equal(labels.local, "Local Models");
  assert.equal(labels.amca, "AMCA API");
  assert.equal(labels.overview, "Overview");
});

// -- provider mode replaces the options navigation --------------------------

test("provider mode is active for the BYOK section and provider pages only", () => {
  assert.equal(showProviderNav({ kind: "section", section: "byok" }), true);
  assert.equal(showProviderNav({ kind: "provider", providerId: "p" }), true);
  for (const section of ["overview", "providers", "local", "selection", "teams", "utilities", "amca"]) {
    assert.equal(showProviderNav({ kind: "section", section }), false, section);
  }
});

test("provider mode groups known API kinds apart from custom endpoints", () => {
  const providers = [
    provider({ id: "known", name: "OpenAI", provider_kind: "openai" }),
    provider({ id: "custom", name: "My Gateway", provider_kind: "custom" }),
    provider({ id: "anthropic", name: "Anthropic", provider_kind: "anthropic" }),
  ];
  const groups = buildProviderNav(providers, { kind: "section", section: "byok" });
  const known = groups.find((group) => group.group === PROVIDER_NAV_GROUPS.known);
  const custom = groups.find((group) => group.group === PROVIDER_NAV_GROUPS.custom);
  // Configured known-kind providers come first, in store order.
  assert.deepEqual(
    known.entries.filter((entry) => entry.target.kind === "provider").map((entry) => entry.label),
    ["OpenAI", "Anthropic"],
  );
  assert.deepEqual(custom.entries.map((entry) => entry.label), ["My Gateway"]);
});

test("default providers appear as unconfigured preset rows, never as connections", () => {
  const providers = [provider({ id: "known", name: "Work OpenAI", provider_kind: "openai" })];
  const groups = buildProviderNav(providers, { kind: "section", section: "byok" });
  const known = groups.find((group) => group.group === PROVIDER_NAV_GROUPS.known);
  const presets = known.entries.filter((entry) => entry.target.kind === "preset");
  // OpenAI is configured, so its preset row is not repeated; the other
  // defaults are offered as templates.
  const kinds = presets.map((entry) => entry.target.presetKind);
  assert.ok(!kinds.includes("openai"), "configured kind must not get a preset row");
  for (const expected of ["anthropic", "openrouter", "moonshot", "deepseek", "zai"]) {
    assert.ok(kinds.includes(expected), `${expected} preset missing`);
  }
  for (const preset of presets) {
    assert.equal(preset.tone, "none", "a preset is a template, so no status dot");
    assert.equal(preset.metaLabel, "Not configured");
    assert.match(preset.statusLabel, /not configured/i);
  }
});

test("selecting a preset is a navigation target the hub can open as a draft", () => {
  const groups = buildProviderNav([], { kind: "preset", presetKind: "zai" });
  const known = groups.find((group) => group.group === PROVIDER_NAV_GROUPS.known);
  const zai = known.entries.find((entry) => entry.target.kind === "preset" && entry.target.presetKind === "zai");
  assert.equal(zai.active, true);
  assert.equal(showProviderNav({ kind: "preset", presetKind: "zai" }), true);
  assert.deepEqual(backToHubTarget({ kind: "preset", presetKind: "zai" }), {
    kind: "section",
    section: "overview",
  });
});

test("an empty provider list still offers the default presets; the draft joins the custom group", () => {
  const plain = buildProviderNav([], { kind: "section", section: "byok" });
  const known = plain.find((group) => group.group === PROVIDER_NAV_GROUPS.known);
  assert.ok(known.entries.length >= 9, "default presets must be offered on an empty list");
  assert.equal(plain.find((group) => group.group === PROVIDER_NAV_GROUPS.custom), undefined);
  const drafting = buildProviderNav([], { kind: "section", section: "byok" }, undefined, true);
  const custom = drafting.find((group) => group.group === PROVIDER_NAV_GROUPS.custom);
  assert.equal(custom.entries.length, 1);
  assert.equal(custom.entries[0].label, "New provider");
});

test("provider rows keep the backend's own order, not an invented sort", () => {
  const providers = [
    provider({ id: "b", name: "Zeta", provider_kind: "custom" }),
    provider({ id: "a", name: "Alpha", provider_kind: "custom" }),
  ];
  const groups = buildProviderNav(providers, { kind: "section", section: "byok" });
  const custom = groups.find((group) => group.group === PROVIDER_NAV_GROUPS.custom);
  assert.deepEqual(custom.entries.map((entry) => entry.label), ["Zeta", "Alpha"]);
});

test("selecting a provider marks exactly that provider active", () => {
  const providers = [provider(), provider({ id: "prov2", name: "Zai", provider_kind: "custom" })];
  const groups = buildProviderNav(providers, { kind: "provider", providerId: "prov2" });
  const active = flatten(groups).filter((entry) => entry.active && entry.target.kind === "provider");
  assert.equal(active.length, 1);
  assert.equal(active[0].target.providerId, "prov2");
});

test("the new-provider draft appears as a selected row and is not a record", () => {
  const providers = [provider({ provider_kind: "custom" })];
  const groups = buildProviderNav(
    providers,
    { kind: "section", section: "byok" },
    undefined,
    true,
  );
  const custom = groups.find((group) => group.group === PROVIDER_NAV_GROUPS.custom);
  const draft = custom.entries[custom.entries.length - 1];
  assert.equal(draft.target, "draft");
  assert.equal(draft.active, true);
  assert.equal(draft.label, "New provider");
  assert.match(draft.statusLabel, /not saved/i);
  // Without the draft flag there is no synthetic row.
  const plain = buildProviderNav(providers, { kind: "section", section: "byok" });
  assert.equal(
    plain.find((group) => group.group === PROVIDER_NAV_GROUPS.custom).entries.length,
    1,
  );
});

// -- the honest status line -------------------------------------------------

test("a provider status dot is neutral until health is actually recorded", () => {
  // `status` is written by the backend from what the operator typed: a
  // provider whose model ids were entered by hand is catalog_ready without a
  // request ever being made, so metadata alone may not paint a green dot.
  const groups = buildProviderNav(
    [
      provider({ id: "typed", status: "catalog_ready", configured: true }),
      provider({ id: "missing", configured: false }),
    ],
    { kind: "section", section: "byok" },
  );
  const byId = Object.fromEntries(
    flatten(groups).map((entry) => [entry.target.providerId, entry]),
  );
  assert.equal(byId.typed.tone, "off");
  assert.match(byId.typed.statusLabel, /not verified/i);
  assert.equal(byId.missing.tone, "off");
  assert.equal(byId.missing.statusLabel, "No credential stored");
});

test("a recorded health state drives the dot, and only for the saved endpoint", () => {
  const health = {
    "provider.good:model-a": { ref: "good:model-a", endpoint: "https://a.example/v1", state: "READY" },
    "provider.stale:model-a": { ref: "stale:model-a", endpoint: "https://old.example/v1", state: "AUTH_REQUIRED" },
  };
  const groups = buildProviderNav(
    [
      provider({ id: "good", configured: true, endpoint: "https://a.example/v1" }),
      provider({ id: "stale", configured: true, endpoint: "https://new.example/v1" }),
    ],
    { kind: "section", section: "byok" },
    health,
  );
  const byId = Object.fromEntries(
    flatten(groups).map((entry) => [entry.target.providerId, entry]),
  );
  assert.equal(byId.good.tone, "ok");
  assert.equal(byId.good.statusLabel, "Runnable · 1 of 2 models shown in chat");
  // A record captured against a different endpoint is not evidence about this
  // configuration, so it must not paint the row.
  assert.equal(byId.stale.tone, "off");
  assert.match(byId.stale.statusLabel, /not verified/i);
});

test("an excluded provider says so, neutrally — exclusion is not a health state", () => {
  const groups = buildProviderNav(
    [provider({ enabled: false })],
    { kind: "section", section: "byok" },
  );
  const entry = flatten(groups)[0];
  assert.equal(entry.tone, "off");
  // Counts still tell the truth underneath the exclusion, in the tooltip.
  assert.equal(entry.statusLabel, "Excluded from chat · 1 of 2 models shown in chat");
});

test("provider rows are uniform: counts live in the tooltip, the line stays clean", () => {
  const groups = buildProviderNav([provider()], { kind: "section", section: "byok" });
  const entry = flatten(groups)[0];
  // The row is one line like the unconfigured presets; the recorded counts
  // and verification state travel together in the tooltip.
  assert.equal(entry.metaLabel, "");
  assert.equal(entry.statusLabel, "Not verified — no health check recorded · 1 of 2 models shown in chat");
});

test("a provider with no discovered models says discovery is pending", () => {
  const groups = buildProviderNav(
    [provider({ model_ids: [], enabled_model_ids: [] })],
    { kind: "section", section: "byok" },
  );
  const entry = flatten(groups)[0];
  assert.match(entry.statusLabel, /model discovery pending/i);
});

// -- back navigation --------------------------------------------------------

test("Back to Model Hub exists in provider mode and restores the hub navigation", () => {
  assert.deepEqual(backToHubTarget({ kind: "provider", providerId: "prov1" }), {
    kind: "section",
    section: "overview",
  });
  assert.deepEqual(backToHubTarget({ kind: "section", section: "byok" }), {
    kind: "section",
    section: "overview",
  });
  assert.equal(backToHubLabel(), "Back to Model Hub");
});

test("Back to Model Hub is absent in hub mode — no dead affordance", () => {
  for (const section of ["overview", "local", "teams", "utilities"]) {
    assert.equal(backToHubTarget({ kind: "section", section }), null);
  }
});

// -- API format derivation ---------------------------------------------------

test("API format is derived from the provider kind, never invented", () => {
  assert.equal(apiFormatLabel("anthropic"), "Anthropic messages (/v1/messages)");
  assert.equal(apiFormatLabel("openai"), "Chat completions (/chat/completions)");
  // An unknown kind must fall back to the documented compatible form rather
  // than claim a format nobody recorded.
  assert.equal(apiFormatLabel("something-new"), "Other / Custom (OpenAI-compatible)");
});

test("every selectable API format resolves to a label, including unknown kinds", () => {
  for (const option of API_FORMAT_OPTIONS) {
    assert.equal(apiFormatLabel(option.value), option.label);
  }
});

// -- recorded metadata formatting -------------------------------------------

test("operator-recorded token counts format compactly, never as capability claims", () => {
  assert.equal(formatTokenCount(128000), "128k");
  assert.equal(formatTokenCount(1000000), "1M");
  assert.equal(formatTokenCount(8192), "8192");
  assert.equal(formatTokenCount(15360), "15.4k");
  assert.equal(formatTokenCount(2500000), "2.5M");
  // Blank means no badge is drawn — never a guessed default.
  assert.equal(formatTokenCount(null), "");
  assert.equal(formatTokenCount(undefined), "");
  assert.equal(formatTokenCount(-5), "");
});

// -- capability vocabulary guard --------------------------------------------

test("the navigation model never asserts a capability", () => {
  // The reference design shows Vision / IM pills. AMCA must not: capability is
  // unknown until a probe records it, and the provider contract forbids
  // inferring support from a provider or model name. If a capability word ever
  // appears in this model, it has been smuggled in.
  const raw = readFileSync(
    new URL("../src/modelSettingsNavModel.ts", import.meta.url),
    "utf8",
  );
  // Comments legitimately discuss the reference design, so strip them and scan
  // the executable surface only — otherwise the guard tests its own prose.
  const code = raw.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  for (const forbidden of [/\bVision\b/i, /\btools?\b/i, /\breasoning\b/i]) {
    assert.ok(!forbidden.test(code), `capability vocabulary leaked: ${forbidden}`);
  }
});

test("badges report recorded health evidence only", () => {
  assert.deepEqual(evidenceBadge("READY"), { label: "Runnable", tone: "ok" });
  assert.equal(evidenceBadge("AUTH_REQUIRED").tone, "warn");
  assert.equal(evidenceBadge("MODEL_NOT_FOUND").tone, "warn");
  assert.equal(evidenceBadge("LOCAL_RUNTIME_OFFLINE").tone, "warn");
  assert.equal(evidenceBadge("UNVERIFIED").tone, "off");
  // No evidence is Unverified, never a capability.
  assert.deepEqual(evidenceBadge(undefined), { label: "Unverified", tone: "off" });
  assert.deepEqual(evidenceBadge(null), { label: "Unverified", tone: "off" });
  // An unrecognised state is also Unverified — it is not guessed at.
  assert.deepEqual(evidenceBadge("SOMETHING_ELSE"), { label: "Unverified", tone: "off" });
});


test("blurCommitsSecret: a pasted key commits on blur, but not on the eye toggle", () => {
  // The 2026-11-05 owner report: an API key pasted into the field only ever
  // committed on Enter — clicking anywhere else left it unstored while the
  // card read "Credential missing". Blur commits; focus moving inside the
  // key row (the reveal toggle) does not.
  const container = { contains: (t) => t === "eye-button" };
  // No related target (clicked a non-focusable area / window blurred): commit.
  assert.equal(blurCommitsSecret(null, container), true);
  assert.equal(blurCommitsSecret(undefined, container), true);
  // Focus moved to the eye inside the key row: never commit.
  assert.equal(blurCommitsSecret("eye-button", container), false);
  // Focus moved anywhere else: commit.
  assert.equal(blurCommitsSecret("save-button", container), true);
  // A missing container reference degrades to commit (the safe direction —
  // an unstored key is worse than a redundant save attempt).
  assert.equal(blurCommitsSecret("anything", null), true);
});
