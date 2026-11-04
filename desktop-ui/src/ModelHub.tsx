import { useEffect, useMemo, useState } from "react";
import { amca } from "./api";
import type { ModelHubState, NewHubProvider } from "./types";
import {
  PROVIDER_PRESETS,
  AUTO_ROUTING_OBJECTIVE,
  buildRoutingCandidates,
  mapConnectionFailure,
} from "./modelHubModel";
import {
  buildProviderDirectory,
  estimateRunCost,
  findRateCard,
  formatUsdCost,
  localHealthChecks,
  selectionGroups,
} from "./modelHubCommercialModel";
import {
  MAX_TEAM_MEMBERS,
  classifyMemberRef,
  describeModelRef,
  participantHint,
  teamMembers,
  teamUpsertPayload,
  validateTeamDraft,
} from "./teamModel";
import {
  costBasisExplanation,
  modelComparisonRows,
  providerCardGuidance,
  unavailableModelSummary,
} from "./studioPolishModel";
import { settingsReadiness } from "./releasePolishModel";
import { evidenceBadge } from "./modelSettingsNavModel.ts";
import { plannedCardExplanation } from "./feUxBatchModel";
import {
  managementRows,
  providerConnectionStatus,
  localModelOperations,
  capabilityEvidence,
  MANAGEMENT_BOUNDARY,
  LOCAL_OPS_NOTE,
  CREDENTIAL_PRIVACY_NOTE,
} from "./modelHubManagementUxModel";
import { capabilityRows, type CapabilityEntry } from "./orchestrationUxModel";
import { ModelSettingsShell } from "./ModelSettingsShell";
import { ProviderDetail } from "./ProviderDetail";
import { AddModelModal, type AddModelPayload } from "./AddModelModal";
import { Icon } from "./icons";
import {
  type HubSection,
  type SettingsTarget,
} from "./modelSettingsNavModel";

type HubTab = HubSection;

const EMPTY_PROVIDER: NewHubProvider = {
  provider_kind: "openai",
  name: "OpenAI",
  endpoint: "https://api.openai.com/v1",
  pricing_mode: "auto",
  secret: "",
};

function money(value: number | null): string {
  return value === null ? "—" : new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(value);
}

function bytes(value: number | null): string {
  if (value === null) return "Unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toFixed(unit >= 3 ? 1 : 0)} ${units[unit]}`;
}

export function ModelHub({ onUseModels, onRefreshLocal }: {
  onUseModels: (modelRefs: string[]) => void;
  onRefreshLocal: () => Promise<void>;
}) {
  const [hub, setHub] = useState<ModelHubState | null>(null);
  const [defaultAuto, setDefaultAuto] = useState<boolean>(true);
  // Sections and the operator's own providers are peers in the left
  // navigation. Selecting a provider opens its detail page; the section it
  // came from is remembered so "Back to Model Hub" has a real destination.
  const [target, setTarget] = useState<SettingsTarget>({ kind: "section", section: "overview" });
  const setTab = (section: HubTab) => setTarget({ kind: "section", section });
  const tab: HubTab = target.kind === "section" ? target.section : "byok";
  // Round 27: the "+ Add provider" draft states are gone — providers are
  // created immediately and edited on their card. The add/edit-model dialog
  // state remains.
  const [modelModal, setModelModal] = useState<
    | { mode: "add"; providerId: string }
    | { mode: "edit"; providerId: string; modelId: string }
    | null
  >(null);
  const [teamName, setTeamName] = useState("");
  const [teamDescription, setTeamDescription] = useState("");
  const [teamRefs, setTeamRefs] = useState<string[]>([]);
  // v0.6.0 M4.1: cross-provider Team UX. Edit uses the EXISTING upsert
  // contract (saveModelTeam with id); stale members are offered Replace or
  // Remove and are never silently substituted.
  const [editingTeamId, setEditingTeamId] = useState<string | null>(null);
  const [teamFormError, setTeamFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // v0.6.0 M4: session-scoped bounded connection-test results per provider
  // id, produced only by the existing bounded catalog request. Never a
  // raw provider exception.
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; message: string }>>({});
  // Z3: read-only health checks per provider derived from the same
  // bounded discover outcome + client-measured latency; plus the cost
  // visibility calculator inputs (existing pricing sources only).
  const [costRef, setCostRef] = useState("");
  const [costInputTokens, setCostInputTokens] = useState("2000");
  const [costOutputTokens, setCostOutputTokens] = useState("800");

  const load = async () => {
    try {
      setHub(await amca.modelHub());
      // The Default Auto control reflects the persisted preference, not a
      // hardcoded claim about the default.
      setDefaultAuto((await amca.preferences()).preferences.default_auto);
      setError(null);
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
  };
  useEffect(() => { void load(); }, []);

  const localRefs = useMemo(() => hub?.local_models.models.map((item) => `ollama:${item.model}`) ?? [], [hub]);
  const selectableRefs = useMemo(() => [
    ...localRefs,
    ...(hub?.amca_api.models.map((item) => String((item as { logical_model_id?: string }).logical_model_id ?? "")).filter(Boolean) ?? []),
    ...(hub?.direct_models.filter((item) => item.configured).map((item) => item.credential_id) ?? []),
    ...(hub?.byok_providers.flatMap((item) => (item.enabled_model_ids ?? []).map((model) => `${item.id}:${model}`)) ?? []),
  ], [hub, localRefs]);

  // Z1: flat routing-candidate view over the same existing sources.
  const routingCandidates = useMemo(() => hub ? buildRoutingCandidates({
    localModels: hub.local_models.models,
    byokProviders: hub.byok_providers,
    directModels: hub.direct_models,
    amcaApiModels: hub.amca_api.models as Array<{ logical_model_id?: string }>,
  }) : [], [hub]);

  // Z3: commercial provider directory + grouped manual selection.
  const directory = useMemo(() => hub ? buildProviderDirectory(hub) : null, [hub]);
  const selection = useMemo(() => hub ? selectionGroups(hub) : [], [hub]);
  const costCard = useMemo(() => (hub && costRef ? findRateCard(costRef, hub) : null), [hub, costRef]);
  const costEstimate = useMemo(() => {
    if (!costCard) return null;
    return estimateRunCost(Number(costInputTokens) || 0, Number(costOutputTokens) || 0, costCard);
  }, [costCard, costInputTokens, costOutputTokens]);
  // Z4 UX refinement: comparison rows + unavailable summary, both derived
  // from the same existing hub document.
  const comparisonRows = useMemo(() => hub ? modelComparisonRows(selection) : [], [hub, selection]);
  const unavailable = useMemo(() => hub ? unavailableModelSummary({
    directModels: hub.direct_models,
    byokProviders: hub.byok_providers,
  }) : null, [hub]);
  // Z7 settings completeness: real document counts, no invented states.
  // Pricing records are registry-usable when their status is "current" or
  // "user_override" (the statuses the existing registry accepts); count
  // those only — "unverified" rows never count as ready.
  const readiness = useMemo(() => hub ? settingsReadiness({
    selectableModels: selectableRefs.length,
    connectedProviders: hub.byok_providers.filter((item) => item.configured).length + hub.direct_models.filter((item) => item.configured).length,
    localRuntimeReachable: hub.local_models.runtime_reachable === true,
    savedTeams: hub.model_teams.length,
    verifiedPrices: hub.pricing.filter((item) => {
      const status = String((item as Record<string, unknown>).status ?? "").toLowerCase();
      return status === "current" || status === "user_override";
    }).length,
  }) : [], [hub, selectableRefs]);
  // BETA-B4 capability matrix from recorded local measurements only —
  // context/tool/vision stay "unknown" because the hub document does not
  // record them.
  const capabilityMatrix = useMemo(() => hub ? capabilityRows(
    hub.local_models.models.map((item): CapabilityEntry => ({
      ref: `ollama:${item.model}`,
      provider: "ollama",
      parameterSize: item.parameter_size,
      quantization: item.quantization_level,
      tokensPerSecond: item.measurement?.tokens_per_second ?? null,
    })),
  ) : [], [hub]);

  // Adding and removing a model id reuses updateHubProvider's existing
  // model_ids contract. The credential is untouched: update only carries the
  // catalog, and the backend preserves the stored secret when no secret is
  // supplied, so this is metadata-only and starts no network call.
  const mutateProviderModels = async (
    item: ModelHubState["byok_providers"][number],
    modelIds: string[],
    ok: boolean,
    message: string,
  ): Promise<boolean> => {
    setBusy(true); setError(null);
    try {
      await amca.updateHubProvider(item.id, { model_ids: modelIds });
      setTestResults((current) => ({ ...current, [`models:${item.id}`]: { ok, message } }));
      await load();
      return true;
    } catch (caught) {
      setTestResults((current) => ({
        ...current,
        [`models:${item.id}`]: { ok: false, message: caught instanceof Error ? caught.message : String(caught) },
      }));
      return false;
    } finally { setBusy(false); }
  };

  const addModelToProvider = async (
    item: ModelHubState["byok_providers"][number],
    modelId: string,
  ): Promise<boolean> => {
    if (item.model_ids.includes(modelId)) {
      setTestResults((current) => ({
        ...current,
        [`models:${item.id}`]: { ok: false, message: `"${modelId}" is already in this provider's model list.` },
      }));
      return false;
    }
    return mutateProviderModels(item, [...item.model_ids, modelId], true, `Added "${modelId}".`);
  };

  const removeModelFromProvider = async (
    item: ModelHubState["byok_providers"][number],
    modelId: string,
  ) => {
    if (!window.confirm(`Remove "${modelId}" from "${item.name}"? The provider and its credential are kept.`)) return;
    await mutateProviderModels(
      item,
      item.model_ids.filter((entry) => entry !== modelId),
      true,
      `Removed "${modelId}".`,
    );
  };

  // Provider include/exclude and metadata patches reuse the existing
  // update contract. A metadata-only patch never carries a secret, so the
  // stored credential is untouched and no network call is started.
  const patchProvider = async (
    item: ModelHubState["byok_providers"][number],
    patch: Partial<NewHubProvider>,
  ): Promise<boolean> => {
    setBusy(true); setError(null);
    try {
      await amca.updateHubProvider(item.id, patch);
      // A changed endpoint or provider kind invalidates everything recorded
      // against the old configuration: the connection result, the read-only
      // health strip, and any per-model verification. Keeping them would show
      // a stale READY badge for a provider that now points somewhere else.
      // (Round 27: this moved here from the removed addProvider draft — the
      // card is now the only place a provider is reconfigured.)
      const before = item;
      if (
        (patch.endpoint !== undefined && patch.endpoint !== before.endpoint)
        || (patch.provider_kind !== undefined && patch.provider_kind !== before.provider_kind)
      ) {
        setTestResults((current) => {
          const next = { ...current };
          for (const key of Object.keys(next)) {
            if (key === item.id || key.startsWith(`${item.id}:`) || key.startsWith(`models:${item.id}`)) {
              delete next[key];
            }
          }
          return next;
        });
      }
      await load();
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      return false;
    } finally { setBusy(false); }
  };

  const toggleProviderEnabled = async (
    item: ModelHubState["byok_providers"][number],
    enabled: boolean,
  ) => {
    await patchProvider(item, { enabled });
  };

  const saveProviderSecret = async (
    item: ModelHubState["byok_providers"][number],
    secret: string,
  ): Promise<boolean> => {
    setBusy(true); setError(null);
    try {
      await amca.updateHubProvider(item.id, { secret });
      await load();
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      return false;
    } finally { setBusy(false); }
  };

  // The Add-model dialog save path. The model id itself goes through the
  // existing update contract; the recorded metadata and the initial
  // visibility go through their own dedicated surfaces. Every step is
  // sequential so a failure stops the chain with the dialog still open and
  // the draft intact.
  const saveModelModal = async (
    providerId: string,
    payload: AddModelPayload,
    mode: "add" | "edit",
  ): Promise<boolean> => {
    const item = hub?.byok_providers.find((entry) => entry.id === providerId);
    if (!item) return false;
    // A retry after a partial failure must complete instead of wedging on
    // the duplicate guard: if the id is already recorded, the add step is
    // simply done.
    if (mode === "add" && !item.model_ids.includes(payload.modelId)) {
      const added = await addModelToProvider(item, payload.modelId);
      if (!added) return false;
    }
    // The include decision is ALWAYS applied, never short-circuited on a
    // stale snapshot: the add step materializes a visibility row enabled=1,
    // so skipping the call when the pre-add state happened to match would
    // publish a model the operator asked to keep out of chat.
    try {
      await amca.setHubModelVisibility(providerId, payload.modelId, payload.include);
    } catch (caught) {
      setTestResults((current) => ({
        ...current,
        [`models:${providerId}`]: { ok: false, message: caught instanceof Error ? caught.message : String(caught) },
      }));
      return false;
    }
    // Edit mode always writes the recorded fields — an explicit blank clears
    // the stored number rather than silently keeping it.
    const needsMeta = mode === "edit" || payload.contextWindow !== null || payload.maxOutputTokens !== null;
    if (needsMeta) {
      try {
        await amca.setHubModelMeta(providerId, payload.modelId, {
          context_window: payload.contextWindow,
          max_output_tokens: payload.maxOutputTokens,
        });
      } catch (caught) {
        const prefix = mode === "edit" ? "The recorded details were not saved" : "Model added, but the recorded details were not saved";
        setTestResults((current) => ({
          ...current,
          [`models:${providerId}`]: { ok: false, message: `${prefix}: ${caught instanceof Error ? caught.message : String(caught)}` },
        }));
        return false;
      }
      await load();
    }
    if (payload.verify) {
      await verifyModelId(providerId, payload.modelId, item.endpoint);
    }
    return true;
  };

  const openAddModel = (providerId: string) => {
    setModelModal({ mode: "add", providerId });
  };

  const openEditModel = (providerId: string, modelId: string) => {
    setModelModal({ mode: "edit", providerId, modelId });
  };

  const discoverProvider = async (id: string) => {
    setBusy(true); setError(null);
    // M4 connection test: the existing bounded catalog request (free, no
    // inference) doubles as the configuration test. Success and failure both
    // land in a bounded, session-scoped result; the recorded health badges
    // come from the backend's health store, not from this message.
    try {
      const outcome = await amca.discoverHubProvider(id);
      setTestResults((current) => ({ ...current, [id]: { ok: true, message: `Connected — provider catalog retrieved (${outcome.catalog_count} model${outcome.catalog_count === 1 ? "" : "s"}).` } }));
      await load();
    } catch (caught) {
      const boundedFailure = mapConnectionFailure(caught);
      setTestResults((current) => ({ ...current, [id]: { ok: false, message: boundedFailure.message } }));
    } finally { setBusy(false); }
  };

  // H1-E: verify a single model id against endpoint+credential with a
  // bounded 1-token request; the result classifies the true health state.
  // Removal is USER-INITIATED and explicit.  The manager's decision was that
  // legacy profiles come out of the normal UI behind a migration path without
  // destructively deleting existing configuration, so the button sits inside
  // the migration section and removes only the profile the user points at.

  // v1.1.8 Phase 1: publishing a discovered model into the chat model
  // selectors is an explicit per-model decision.  Discovery on its own never
  // makes a provider's whole catalog selectable.
  const toggleDefaultAuto = async () => {
    const next = !defaultAuto;
    setBusy(true); setError(null);
    try {
      const result = await amca.setPreferences({ default_auto: next });
      setDefaultAuto(result.preferences.default_auto);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally { setBusy(false); }
  };

  const toggleModelVisibility = async (providerId: string, model: string, enabled: boolean) => {
    setBusy(true); setError(null);
    try {
      await amca.setHubModelVisibility(providerId, model, enabled);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally { setBusy(false); }
  };

  const setAllModelVisibility = async (providerId: string, models: string[], enabled: boolean) => {
    setBusy(true); setError(null);
    try {
      for (const model of models) {
        await amca.setHubModelVisibility(providerId, model, enabled);
      }
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally { setBusy(false); }
  };

  const verifyModelId = async (providerId: string, model: string, endpoint: string) => {
    setBusy(true); setError(null);
    try {
      const outcome = await amca.verifyModelConfiguration({ kind: "hub", ref: `${providerId}:${model}`, endpoint, model });
      setTestResults((current) => ({
        ...current,
        [`${providerId}:${model}`]: outcome.runnable
          ? { ok: true, message: `Runnable — provider accepted endpoint, credential, and model id (HTTP ${outcome.http_status ?? 200}).` }
          : { ok: false, message: `${evidenceBadge(outcome.state).label} — ${outcome.detail}` },
      }));
      await load();
    } catch (caught) {
      setTestResults((current) => ({ ...current, [`${providerId}:${model}`]: { ok: false, message: caught instanceof Error ? caught.message : String(caught) } }));
    } finally { setBusy(false); }
  };

  // NOTE: the Model Hub deliberately carries NO "verify all" action. The
  // owner removed the legacy verify-all section with the legacy direct-profile
  // surface (v1.2.1), and the composer menu is where a model that has never
  // been measured is surfaced and measured.

  const removeProvider = async (id: string) => {
    if (!window.confirm("Remove this provider connection and its protected credential?")) return;
    setBusy(true);
    try {
      await amca.deleteHubProvider(id);
      // Everything scoped to the deleted provider goes with it: the open edit
      // form, any secret draft still held in component state, and the
      // connection/health results. Leaving them would let a later visit to
      // BYOK reopen an editor for a provider that no longer exists.
      setTestResults((current) => {
        const next = { ...current };
        for (const key of Object.keys(next)) {
          if (key === id || key.startsWith(`${id}:`) || key.startsWith(`models:${id}`)) {
            delete next[key];
          }
        }
        return next;
      });
      setTarget({ kind: "section", section: "byok" });
      await load();
    }
    catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setBusy(false); }
  };

  const createTeam = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setTeamFormError(null);
    try {
      // Editing grandfathers the team's own original members: temporarily
      // unavailable members are preserved, never auto-deleted; only NEW
      // stale additions are rejected.
      const original = editingTeamId ? (hub?.model_teams.find((team) => team.id === editingTeamId)?.model_refs ?? []) : [];
      const validation = validateTeamDraft(
        { name: teamName, modelRefs: teamRefs },
        selectableRefs,
        original,
      );
      if (!validation.ok) { setTeamFormError(validation.errors.join(" ")); setBusy(false); return; }
      await amca.saveModelTeam(teamUpsertPayload({
        id: editingTeamId, name: teamName, description: teamDescription, modelRefs: teamRefs,
      }));
      setTeamName(""); setTeamDescription(""); setTeamRefs([]); setEditingTeamId(null); setTeamFormError(null); await load();
    } catch (caught) { setTeamFormError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setBusy(false); }
  };

  const editTeam = (team: ModelHubState["model_teams"][number]) => {
    setEditingTeamId(team.id);
    setTeamName(team.name);
    setTeamDescription(team.description);
    setTeamRefs([...team.model_refs]);
    setTeamFormError(null);
  };

  const resetTeamForm = () => {
    setEditingTeamId(null); setTeamName(""); setTeamDescription(""); setTeamRefs([]); setTeamFormError(null);
  };

  const removeTeamMember = async (team: ModelHubState["model_teams"][number], ref: string) => {
    setBusy(true); setTeamFormError(null);
    try {
      await amca.saveModelTeam(teamUpsertPayload({
        id: team.id, name: team.name, description: team.description,
        modelRefs: team.model_refs.filter((item) => item !== ref),
      }));
      await load();
    } catch (caught) { setTeamFormError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setBusy(false); }
  };

  const replaceTeamMember = (team: ModelHubState["model_teams"][number], ref: string) => {
    // Open the editor with the stale member removed; the user then picks a
    // replacement from the live member list. Nothing is auto-substituted.
    setEditingTeamId(team.id);
    setTeamName(team.name);
    setTeamDescription(team.description);
    setTeamRefs(team.model_refs.filter((item) => item !== ref));
    setTeamFormError(`Choose a replacement for ${ref}, then save.`);
  };

  // Round 27 (owner order): "+ Add provider" goes STRAIGHT to the new
  // provider card — the old fill-in-then-save draft is removed. The record
  // is created immediately and honestly "not configured" until the operator
  // saves a key on the card; an abandoned card is a visible provider with
  // no credential, never a half-filled form.
  const openProviderDraft = async (preset?: { kind: string; name: string; endpoint: string }) => {
    setBusy(true); setError(null);
    try {
      const outcome = await amca.createHubProvider({
        ...EMPTY_PROVIDER,
        provider_kind: preset?.kind ?? "custom",
        name: preset?.name || "New provider",
        endpoint: preset?.endpoint || "",
      });
      setTarget({ kind: "provider", providerId: outcome.provider.id });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const refreshLocal = async () => {
    setBusy(true);
    try { await onRefreshLocal(); await load(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setBusy(false); }
  };

  return (
    <ModelSettingsShell
      providers={hub?.byok_providers ?? []}
      health={hub?.model_health}
      target={target}
      onNavigate={(next) => {
        // Entering the provider list lands on a provider, matching the
        // reference design where a selection is always shown; an operator
        // with no providers yet sees the empty state below.
        if (next.kind === "section" && next.section === "byok") {
          const first = hub?.byok_providers[0];
          if (first) {
            setTarget({ kind: "provider", providerId: first.id });
            return;
          }
        }
        // A provider preset creates its card immediately, pre-filled with
        // the preset endpoint; the key is saved on the card (Round 27).
        if (next.kind === "preset") {
          const preset = PROVIDER_PRESETS[next.presetKind] ?? { name: "", endpoint: "" };
          void openProviderDraft({ kind: next.presetKind, name: preset.name, endpoint: preset.endpoint });
          return;
        }
        setTarget(next);
      }}
      onAddProvider={() => void openProviderDraft()}
      title="Model settings"
      description="Manage custom model providers and local models. Once configured, they can be selected during chat. Secrets stay in the operating-system vault."
      loading={!hub}
      error={error}
      action={
        <>
          <button
            type="button"
            className="msiconbtn"
            aria-label="Refresh model settings"
            title="Reload the recorded model state from the desktop database. No provider is contacted."
            disabled={busy}
            onClick={() => void load()}
          >
            <Icon name="refresh" />
          </button>
          <button
            type="button"
            className="msbtn primary"
            disabled={busy}
            onClick={() => void openProviderDraft()}
          >
            + Add provider
          </button>
        </>
      }
    >
      <>
      {hub && target.kind === "provider" ? (
        (() => {
          const selected = hub.byok_providers.find((item) => item.id === target.providerId);
          if (!selected) {
            return (
              <p className="msprovider-empty">
                That provider is no longer configured. Choose another from the navigation.
              </p>
            );
          }
          return (
            <ProviderDetail
              /* Re-keyed on the provider so an unfinished model id typed for
                 one provider cannot be submitted to the next one. */
              key={selected.id}
              provider={selected}
              health={hub.model_health}
              busy={busy}
              testResult={testResults[selected.id]}
              modelMessage={testResults[`models:${selected.id}`]}
              modelResults={testResults}
              onPatchProvider={(patch) => patchProvider(selected, patch)}
              onSaveSecret={(secret) => saveProviderSecret(selected, secret)}
              onToggleProvider={(enabled) => void toggleProviderEnabled(selected, enabled)}
              onRemove={() => void removeProvider(selected.id)}
              onTestConnection={() => void discoverProvider(selected.id)}
              onOpenAddModel={() => openAddModel(selected.id)}
              onEditModel={(modelId) => openEditModel(selected.id, modelId)}
              onRemoveModel={(modelId) => void removeModelFromProvider(selected, modelId)}
              onToggleModel={(modelId, enabled) => void toggleModelVisibility(selected.id, modelId, enabled)}
              onVerifyModel={(modelId) => void verifyModelId(selected.id, modelId, selected.endpoint)}
              onShowAll={() => void setAllModelVisibility(selected.id, selected.model_ids, true)}
              onHideAll={() => void setAllModelVisibility(selected.id, selected.model_ids, false)}
            />
          );
        })()
      ) : !hub ? (
        <p className="muted-copy">Loading unified model state…</p>
      ) : tab === "overview" ? (
            <>
              <div className="hub-source-grid">
                <button onClick={() => setTab("amca")}><span>Managed</span><strong>AMCA API</strong><small>{hub.amca_api.status === "not_active" ? "Not active" : "Active"} · AWS Bedrock backbone</small></button>
                <button onClick={() => setTab("byok")}><span>Your keys</span><strong>BYOK</strong><small>{hub.byok_providers.length} provider connection{hub.byok_providers.length === 1 ? "" : "s"}</small></button>
                <button onClick={() => setTab("local")}><span>On this PC</span><strong>Local Models</strong><small>{hub.local_models.models.length} installed · no external API charge</small></button>
              </div>

              {/* v1.0.0 Model Hub management UX: dashboard rows, local operations visibility, capability evidence — recorded fields only. */}
              <div className="hub-management-panel">
                <div className="panel-heading"><div><span className="section-kicker">Management</span><h2>Model Hub management dashboard</h2></div></div>
                <p className="muted-copy">{MANAGEMENT_BOUNDARY}</p>
                <div className="hub-list">
                  {managementRows([
                    ...hub.local_models.models.map((item) => ({
                      ref: `ollama:${item.model}`,
                      kind: "model" as const,
                      available: true,
                      capabilityTags: [item.parameter_size, item.quantization_level].filter((tag): tag is string => Boolean(tag)),
                    })),
                    ...hub.byok_providers.map((provider) => ({
                      ref: provider.id,
                      kind: "provider" as const,
                      available: provider.configured,
                      capabilityTags: provider.model_ids.slice(0, 4),
                      // A stored credential is not a connection. The connection
                      // line is driven by recorded health evidence, so a provider
                      // nobody has tested reads "not tested", never "connected".
                      connected: Object.values(hub.model_health ?? {}).some(
                        (record) => record.ref.startsWith(`${provider.id}:`)
                          && record.endpoint === provider.endpoint
                          && record.state === "READY",
                      ),
                    })),
                    ...hub.direct_models.map((credential) => ({
                      ref: credential.credential_id,
                      kind: "model" as const,
                      available: credential.configured,
                    })),
                  ]).map((row) => (
                    <article key={`${row.kind}-${row.ref}`}>
                      <div><strong><code>{row.ref.length > 28 ? `${row.ref.slice(0, 28)}…` : row.ref}</code></strong><span>{row.kind} · {row.locality} · {row.availability}</span></div>
                      {row.capabilityTags.length > 0 && <small>recorded catalog entries: {row.capabilityTags.join(", ")}</small>}
                      {row.connectionStatus !== "n/a" && <small>connection: {row.connectionStatus}</small>}
                    </article>
                  ))}
                </div>
                <p className="muted-copy">{CREDENTIAL_PRIVACY_NOTE}</p>
              </div>
              <div className="hub-management-panel">
                <div className="panel-heading"><div><span className="section-kicker">Local operations</span><h2>Local model operations</h2></div></div>
                <p className="muted-copy">{LOCAL_OPS_NOTE}</p>
                <div className="hub-list">
                  {hub.local_models.models.map((item) => {
                    const operations = localModelOperations({
                      runtimeRef: `ollama:${item.model}`,
                      location: "This PC — Ollama-managed storage",
                      available: true,
                      formats: [item.model_format].filter((format): format is string => Boolean(format)),
                    });
                    return (
                      <article key={item.model}>
                        <div><strong>{item.model}</strong><span>{operations.runtime} · {operations.runtimeAvailability}</span></div>
                        <small>location: {operations.location} · formats: {operations.supportedFormats.join(", ") || "Not recorded"}</small>
                      </article>
                    );
                  })}
                  {hub.byok_providers.filter((provider) => provider.configured).map((provider) => {
                    const status = providerConnectionStatus({
                      ref: provider.id,
                      providerType: provider.provider_kind,
                      configured: provider.configured,
                    });
                    // "connected" would be a claim about a request nobody made.
                    // Report the recorded health state, or say it is untested.
                    const record = Object.values(hub.model_health ?? {}).find(
                      (entry) => entry.ref.startsWith(`${provider.id}:`) && entry.endpoint === provider.endpoint,
                    );
                    const connection = !record
                      ? "not tested"
                      : record.state === "READY" ? "connected (verified)" : evidenceBadge(record.state).label;
                    return (
                      <article key={provider.id}>
                        <div><strong>{provider.name}</strong><span>{status.providerType} · {connection}</span></div>
                        <small>{CREDENTIAL_PRIVACY_NOTE}</small>
                      </article>
                    );
                  })}
                </div>
              </div>
              <div className="hub-management-panel">
                <div className="panel-heading"><div><span className="section-kicker">Capability evidence</span><h2>Capability evidence panel</h2></div></div>
                <div className="hub-list">
                  {capabilityEvidence([
                    { capability: "Local inference", evidenceSource: "Recorded local model catalog with compatibility evaluation" },
                    // A stored credential is not a connection test. This row is
                    // Verified only when a health record for the provider's
                    // current endpoint actually exists.
                    {
                      capability: "Provider connectivity",
                      evidenceSource: (hub.byok_providers ?? []).some((provider) =>
                        Object.values(hub.model_health ?? {}).some(
                          (entry) => entry.ref.startsWith(`${provider.id}:`) && entry.endpoint === provider.endpoint,
                        ),
                      )
                        ? "Recorded model health checks against the saved endpoint"
                        : null,
                    },
                    { capability: "Adaptive model selection", evidenceSource: null },
                  ]).map((row) => (
                    <article key={row.capability}>
                      <div><strong>{row.capability}</strong><span>{row.verdict}</span></div>
                      <small>{row.evidenceSource}</small>
                    </article>
                  ))}
                </div>
                <p className="muted-copy">Capability → Evidence Source → Verified/Unknown. No generated explanations.</p>
              </div>
              <div className="hub-available-summary">
                <span>Available today: <strong>{selectableRefs.length} model{selectableRefs.length === 1 ? "" : "s"}</strong></span>
                <span>Local · free: <strong>{hub.local_models.models.length}</strong></span>
                <span>Cloud connections: <strong>{hub.byok_providers.filter((item) => item.configured).length + hub.direct_models.filter((item) => item.configured).length}</strong></span>
              </div>
              {/* Z7 settings completeness strip: each line states its real count. */}
              <ul className="hub-readiness" aria-label="Setup completeness">
                {readiness.map((item) => (
                  <li key={item.key} className={item.done ? "done" : ""}>
                    <i aria-hidden="true">{item.done ? "✓" : ""}</i>
                    <span><strong>{item.label}</strong><small>{item.detail}</small></span>
                  </li>
                ))}
              </ul>
              {/* v1.1.8 Phase 2: this was a static card announcing a default
                  that had no effect.  It is now the Default Auto control: it
                  reads and writes the persisted preference a new conversation
                  starts from. */}
              <div className="hub-auto-card"><div><span>Default task selection</span><strong>{defaultAuto ? "Auto Intelligence" : "Manual model set"}</strong><small>{defaultAuto ? "Lowest cost, quality protected" : "Every run uses the models you pick"}</small></div><button type="button" disabled={busy} onClick={() => void toggleDefaultAuto()}>{defaultAuto ? "Turn Default Auto off" : "Turn Default Auto on"}</button><b>Quality and privacy are hard gates before price</b></div>
              <div className="hub-routing-candidates">
                <div className="hub-section-heading"><div><strong>Routing candidates</strong><span>What manual selection offers today and what automatic routing chooses from — the backend router stays authoritative.</span></div><small>{routingCandidates.length} available</small></div>
                <div className="hub-routing-summary">
                  <span><strong>{routingCandidates.filter((item) => item.source === "local").length}</strong> local · free</span>
                  <span><strong>{routingCandidates.filter((item) => item.source !== "local").length}</strong> API models</span>
                </div>
                {routingCandidates.length ? (
                  <div className="hub-routing-list">
                    {routingCandidates.slice(0, 12).map((candidate) => (
                      <div key={candidate.ref} className="hub-routing-row">
                        <code>{candidate.ref}</code>
                        <span>{candidate.providerLabel}</span>
                        <b className={candidate.source === "local" ? "ok" : ""}>{candidate.source === "local" ? "Local · free" : candidate.source === "amca_api" ? "Managed" : candidate.source === "byok" ? "BYOK" : "Direct profile"}</b>
                      </div>
                    ))}
                    {routingCandidates.length > 12 && <p className="settings-note">+{routingCandidates.length - 12} more available in selection.</p>}
                  </div>
                ) : (
                  <p className="hub-empty">No selectable models yet. Add a BYOK provider, install an Ollama model, or configure a direct profile.</p>
                )}
                <p className="settings-note">Automatic: {AUTO_ROUTING_OBJECTIVE}. Manual: pick an exact model or team in Chat.</p>
              </div>
              <div className="hub-stats"><div><span>Saved teams</span><strong>{hub.model_teams.length}</strong></div><div><span>Pricing records</span><strong>{hub.pricing.length}</strong></div><div><span>Metadata agent</span><strong>Event-driven</strong></div></div>
            </>
          ) : tab === "providers" ? (
            <>
              <div className="hub-section-heading"><div><strong>Commercial providers</strong><span>Every provider family the hub document knows about, with its real connection state. Connections are added in BYOK Providers.</span></div></div>
              <div className="hub-provider-directory">
                {directory?.providers.map((row) => {
                  const guidance = providerCardGuidance(row.label, row.connected, row.modelsAvailable);
                  return (
                    <article key={row.familyId} className={row.connected ? "connected" : ""}>
                      <div className={`compat-dot ${row.connected ? "green" : "red"}`} aria-label={row.connectionLabel} />
                      <div>
                        <strong>{row.label}</strong>
                        <span>{row.connectionLabel} · {row.modelsAvailable} model{row.modelsAvailable === 1 ? "" : "s"} available</span>
                        <small>{guidance.line}</small>
                        <small>{row.detail} · Source: existing hub document</small>
                      </div>
                      <button onClick={() => setTab("byok")}>{row.connected ? "Manage" : "Add key"}</button>
                    </article>
                  );
                })}
              </div>
              <div className="hub-section-heading"><div><strong>Local runtimes</strong><span>On this PC. Ollama status comes from the existing catalog scan; llama.cpp and vLLM/SGLang have no status document yet and say so.</span></div></div>
              <div className="hub-provider-directory">
                {directory?.runtimes.map((row) => (
                  <article key={row.familyId} className={row.connected ? "connected" : ""}>
                    <div className={`compat-dot ${row.connected ? "green" : "red"}`} aria-label={row.connectionLabel} />
                    <div>
                      <strong>{row.label}</strong>
                      <span>{row.connectionLabel}{row.familyId === "ollama" ? ` · ${row.modelsAvailable} model${row.modelsAvailable === 1 ? "" : "s"} installed` : ""}</span>
                      <small>{row.detail}</small>
                    </div>
                    {row.familyId === "ollama" && <button onClick={() => setTab("local")}>Open models</button>}
                  </article>
                ))}
              </div>
            </>
          ) : tab === "amca" ? (
            <>
              <div className="hub-status-card"><div><span>Status</span><strong>Not active</strong></div><div><span>Managed backbone</span><strong>{hub.amca_api.managed_backbone}</strong></div><div><span>Credit balance</span><strong>{money(hub.amca_api.credit_balance_usd)}</strong></div></div>
              <p className="settings-note">AMCA account infrastructure will provision one unique, revocable credential after an eligible subscription or credit purchase. AWS credentials are never exposed to the desktop app or agents.</p>
              <div className="hub-empty-action"><strong>Managed access is not connected yet</strong><span>{hub.amca_api.commercial_status}</span><button disabled>Buy credits</button></div>
            </>
          ) : tab === "byok" ? (
            <p className="msprovider-empty">
              No providers are configured yet. Use <strong>+ Add provider</strong> to create one —
              it appears in the list on the left.
            </p>
          ) : tab === "local" ? (
            <>
              <div className="hub-section-heading"><div><strong>Hardware-aware local models</strong><span>Green/red indicates feasibility on this PC, not model quality.</span></div><button className="secondary-button" onClick={() => void refreshLocal()} disabled={busy}>Scan Ollama</button></div>
              <div className="hub-section-heading"><div><strong>Recommended catalog</strong><span>Install explicitly through Ollama; AMCA never downloads model weights merely because this page opened.</span></div></div>
              {/* v1.1.8 Phase 1: one canonical card per model.  A recommended
                  model that is already installed is managed in the installed
                  list below, so it is not repeated here. */}
              {hub.local_models.recommended.some((item) => item.installed) && <p className="hub-empty">{hub.local_models.recommended.filter((item) => item.installed).length} recommended model(s) are already installed — manage them under “Installed on this PC” below.</p>}
              <div className="hub-local-grid">{hub.local_models.recommended.filter((item) => !item.installed).map((item) => <article key={item.model}><div className={`compat-dot ${item.compatibility.status}`} aria-label={`${item.compatibility.status} compatibility`} /><div><strong>{item.model}</strong><span>{item.parameter_size} · {bytes(item.size_bytes)}</span><small>{item.summary} {item.compatibility.explanation}</small></div><button onClick={() => void navigator.clipboard.writeText(item.install_command)} title={item.install_command}>Copy install command</button></article>)}{!hub.local_models.recommended.some((item) => !item.installed) && <p className="hub-empty">Every recommended model is already installed on this PC.</p>}</div>
              <div className="hub-section-heading"><div><strong>Installed on this PC</strong><span>Green check means discovered locally; compatibility is evaluated separately.</span></div></div>
              {(() => {
                const checks = localHealthChecks(hub.local_models.runtime_reachable, hub.local_models.models.length);
                return (
                  <div className="hub-health-strip" aria-label="Local runtime health">
                    {checks.map((check) => (
                      <span key={check.key} className={`hub-health-check hub-health-${check.state}`} title={check.detail}>
                        {check.state === "pass" ? "✓" : check.state === "fail" ? "✗" : "–"} {check.label}
                      </span>
                    ))}
                  </div>
                );
              })()}
              <div className="hub-local-grid">{hub.local_models.models.map((item) => <article key={item.model}><div className={`compat-dot ${item.compatibility.status}`} aria-label={`${item.compatibility.status} compatibility`} /><div><strong>{item.model}</strong><span>{item.parameter_size ?? "Unknown parameters"} · {item.quantization_level ?? "Unknown quantization"}</span><small>{item.compatibility.explanation}</small><details><summary>Compatibility details</summary><dl><div><dt>RAM needed</dt><dd>{bytes(item.compatibility.required_ram_bytes)}</dd></div><div><dt>VRAM needed</dt><dd>{bytes(item.compatibility.required_vram_bytes)}</dd></div><div><dt>Storage</dt><dd>{bytes(item.compatibility.storage_bytes)}</dd></div><div><dt>Offload</dt><dd>{item.compatibility.expected_offload.replaceAll("_", " ")}</dd></div></dl></details></div><button disabled={busy || item.fit === 2} onClick={async()=>{setBusy(true);try{await amca.verifyModelConfiguration({kind:"local",ref:`ollama:${item.model}`,endpoint:"local://ollama",model:item.model,local_runtime:true});await load();await onRefreshLocal();}catch(e){setError(String(e));}finally{setBusy(false);}}}>Verify local model</button><button onClick={() => onUseModels([`ollama:${item.model}`])} disabled={item.compatibility.status === "red"}>Use</button></article>)}{!hub.local_models.models.length && <p className="hub-empty">No installed Ollama models found. Search/install remains a runtime operation; AMCA will not download weights silently.</p>}</div>
            </>
          ) : tab === "selection" ? (
            <>
              <div className="hub-section-heading"><div><strong>Model selection</strong><span>The exact options Manual Strategy and Auto Intelligence choose from, grouped by provider. Selecting here teaches nothing new — the same governed payload is used at task time.</span></div></div>
              {selection.length ? (
                <div className="hub-selection-groups">
                  {selection.map((group) => (
                    <section key={group.familyId}>
                      <h3>{group.label} <small>{group.options.length}</small></h3>
                      <ul>
                        {group.options.map((option) => (
                          <li key={option.ref}>
                            <code>{option.ref}</code>
                            <span>{option.source === "local" ? "Local · free" : option.source === "byok" ? "BYOK" : option.source === "direct" ? "Direct profile" : "Managed"}</span>
                          </li>
                        ))}
                      </ul>
                    </section>
                  ))}
                </div>
              ) : (
                <p className="hub-empty">No selectable models yet. Add a BYOK key, configure a direct profile, or scan Ollama.</p>
              )}
              {unavailable && <p className="hub-unavailable-note">{unavailable.line}</p>}
              {comparisonRows.length > 0 && (
                <div className="hub-comparison">
                  <div className="hub-comparison-head" role="row">
                    <span>Provider family</span><span>Model</span><span>Source</span>
                  </div>
                  {comparisonRows.map((row) => (
                    <div key={`${row.familyId}:${row.ref}`} className="hub-comparison-row" role="row">
                      <span>{row.groupLabel}</span>
                      <code>{row.ref}</code>
                      <b>{row.sourceLabel}</b>
                    </div>
                  ))}
                </div>
              )}
              {/* BETA-B4: capability matrix over recorded measurements only. */}
              {capabilityMatrix.length > 0 && (
                <div className="hub-capabilities">
                  <div className="hub-comparison-head" role="row">
                    <span>Local model</span><span>Parameters</span><span>Speed (measured)</span><span>Status</span>
                  </div>
                  {capabilityMatrix.map((row) => (
                    <div key={row.ref} className="hub-comparison-row orch-capability-row" role="row">
                      <code>{row.ref}</code>
                      <span>{row.parameters}</span>
                      <span title="Context, tool, and vision support are not recorded by the local catalog — they stay unknown.">{row.speedTier}</span>
                      <b>{row.locality === "local" ? "Local · $0" : "unknown"}</b>
                    </div>
                  ))}
                  <p className="settings-note">Context window, tool support, and vision support are not recorded by the local catalog, so they display as unknown rather than guessed. Speed tiers come from Ollama's measured tokens-per-second only.</p>
                </div>
              )}
              <div className="hub-section-heading"><div><strong>Cost visibility</strong><span>Estimated cost from the existing Pricing Registry and direct-profile rates. Actual task costs stay in the existing Cost panel — this is a preview, not a billing engine.</span></div></div>
              <div className="hub-cost-calc">
                <label><span>Model</span>
                  <select value={costRef} onChange={(event) => setCostRef(event.target.value)}>
                    <option value="">Choose a model…</option>
                    {selection.flatMap((group) => group.options.map((option) => (
                      <option key={option.ref} value={option.ref}>{group.label} — {option.label}</option>
                    )))}
                  </select>
                </label>
                <label><span>Input tokens</span><input type="number" min="0" value={costInputTokens} onChange={(event) => setCostInputTokens(event.target.value)} /></label>
                <label><span>Output tokens</span><input type="number" min="0" value={costOutputTokens} onChange={(event) => setCostOutputTokens(event.target.value)} /></label>
                <div className="hub-cost-result" aria-live="polite">
                  {!costRef || !costCard ? (
                    <span>Choose a model to preview its estimated cost.</span>
                  ) : costCard.basis === "free_local" ? (
                    <span><strong>{costCard.ref}</strong> runs locally — no external API charge (estimated {formatUsdCost(0)}).</span>
                  ) : costEstimate ? (
                    <span>
                      <strong>{costCard.ref}</strong>{costCard.provider ? ` · ${costCard.provider}` : ""} — rate {costCard.inputPer1m === null ? "?" : formatUsdCost(costCard.inputPer1m)}/1M in · {costCard.outputPer1m === null ? "?" : formatUsdCost(costCard.outputPer1m)}/1M out → estimated total <b>{formatUsdCost(costEstimate.totalUsd)}</b> ({formatUsdCost(costEstimate.inputUsd)} in + {formatUsdCost(costEstimate.outputUsd)} out)
                    </span>
                  ) : (
                    <span><strong>{costCard.ref}</strong> has no verified price on record yet — no estimate is invented. BYOK pricing can be completed via the provider's pricing mode.</span>
                  )}
                </div>
                {costCard && <p className="hub-cost-explanation"><strong>{costBasisExplanation(costCard.basis).title}:</strong> {costBasisExplanation(costCard.basis).detail}</p>}
              </div>
            </>
          ) : tab === "teams" ? (
            <>
              <div className="hub-section-heading"><div><strong>Saved model combinations</strong><span>Cross-provider teams mix local and API models from different providers. A Model Team is a user preset, distinct from an Agent Squad.</span></div></div>
              <form className="hub-team-form" onSubmit={createTeam}>
                <input required value={teamName} onChange={(event) => setTeamName(event.target.value)} placeholder={editingTeamId ? "Team name" : "Team name"} />
                <input value={teamDescription} onChange={(event) => setTeamDescription(event.target.value)} placeholder="Description · optional" />
                <div>{selectableRefs.map((ref) => {
                  const identity = classifyMemberRef(ref);
                  const described = describeModelRef(ref);
                  // v1.1.8 Phase 1: readable name first, exact provider/model
                  // ids second, so a team can be built without decoding ids.
                  return <label key={ref}><input type="checkbox" checked={teamRefs.includes(ref)} disabled={busy || (!teamRefs.includes(ref) && teamRefs.length >= MAX_TEAM_MEMBERS)} onChange={() => setTeamRefs((current) => current.includes(ref) ? current.filter((item) => item !== ref) : [...current, ref])} /> <span title={ref}>{described.label} <small>{described.detail || identity.providerLabel}</small></span></label>;
                })}</div>
                <small className="hub-team-hint">{participantHint(teamRefs.length)}{teamRefs.length >= MAX_TEAM_MEMBERS ? ` · team limit reached (${MAX_TEAM_MEMBERS})` : ""}</small>
                {teamFormError && <small className="hub-test-fail" role="alert">{teamFormError}</small>}
                <div>{editingTeamId && <button type="button" className="chat-gate-action" onClick={resetTeamForm} disabled={busy}>Cancel edit</button>}<button className="primary-button" disabled={!teamRefs.length || busy}>{editingTeamId ? "Save team changes" : "Create team"}</button></div>
              </form>
              <div className="hub-list">{hub.model_teams.map((team) => {
                const members = teamMembers(team.model_refs, selectableRefs);
                const stale = members.filter((member) => !member.available).map((member) => member.ref);
                return <article key={team.id}>
                  <div>
                    <strong>{team.name}</strong><span>{team.description || "No description"}</span>
                    <small>{participantHint(team.model_refs.length)}</small>
                    <ul className="hub-team-members">
                      {members.map((member) => (
                        <li key={member.ref}>
                          <span>{member.providerLabel} · {member.ref}</span>
                          {member.available
                            ? <b className="ok">Available</b>
                            : <b>Unavailable</b>}
                          {!member.available && (
                            <span className="hub-team-member-actions">
                              <button type="button" onClick={() => replaceTeamMember(team, member.ref)} disabled={busy}>Replace</button>
                              <button type="button" onClick={() => void removeTeamMember(team, member.ref)} disabled={busy}>Remove</button>
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                    {stale.length > 0 && <small className="hub-test-fail">{stale.length} member{stale.length === 1 ? "" : "s"} unavailable — replace or remove them; execution-time validation stays authoritative.</small>}
                  </div>
                  <div>
                    <button onClick={() => onUseModels(team.model_refs)}>Use in Chat</button>
                    <button onClick={() => editTeam(team)} disabled={busy}>Edit</button>
                    <button onClick={() => void amca.deleteModelTeam(team.id).then(load)} disabled={busy}>Delete</button>
                  </div>
                </article>;
              })}{!hub.model_teams.length && <p className="hub-empty">No Model Teams saved.</p>}</div>
            </>
          ) : (
            <div className="hub-utility-grid">
              <article>
                <strong>Model Matchmaker</strong>
                <span>{plannedCardExplanation("model_matchmaker")?.purpose}</span>
                <small className="hub-card-unavailable">{plannedCardExplanation("model_matchmaker")?.whyUnavailable} {plannedCardExplanation("model_matchmaker")?.expectedDependency}</small>
                <button disabled>Planned</button>
              </article>
              <article>
                <strong>Cost Calculator</strong>
                <span>Uses the same versioned Pricing Registry as routing.</span>
                <small className="hub-card-unavailable">Waiting on verified prices — the card stays disabled until the Pricing Registry carries rates for the relevant providers.</small>
                <button disabled>Awaiting verified prices</button>
              </article>
              <article>
                <strong>VRAM Calculator</strong>
                <span>Backed by the Local Compute Profile and compatibility evaluator.</span>
                <button onClick={() => setTab("local")}>Open local models</button>
              </article>
              <article>
                <strong>Context Explorer</strong>
                <span>{plannedCardExplanation("context_explorer")?.purpose}</span>
                <small className="hub-card-unavailable">{plannedCardExplanation("context_explorer")?.whyUnavailable} {plannedCardExplanation("context_explorer")?.expectedDependency}</small>
                <button disabled>Planned</button>
              </article>
            </div>
          )}
      {modelModal && hub
        ? (() => {
            const modalProvider = hub.byok_providers.find((item) => item.id === modelModal.providerId);
            if (!modalProvider) return null;
            const meta =
              modelModal.mode === "edit"
                ? modalProvider.model_meta?.[modelModal.modelId]
                : undefined;
            const initialIncluded =
              modelModal.mode === "edit"
                ? modalProvider.enabled_model_ids.includes(modelModal.modelId)
                : true;
            return (
              <AddModelModal
                key={`${modelModal.providerId}:${modelModal.mode}:${modelModal.mode === "edit" ? modelModal.modelId : "new"}`}
                mode={modelModal.mode}
                providerName={modalProvider.name}
                modelId={modelModal.mode === "edit" ? modelModal.modelId : undefined}
                initialContextWindow={meta?.context_window ?? null}
                initialMaxOutputTokens={meta?.max_output_tokens ?? null}
                initialIncluded={initialIncluded}
                busy={busy}
                onClose={() => setModelModal(null)}
                onSave={(payload) => saveModelModal(modelModal.providerId, payload, modelModal.mode)}
              />
            );
          })()
        : null}
    </>
    </ModelSettingsShell>
  );
}
