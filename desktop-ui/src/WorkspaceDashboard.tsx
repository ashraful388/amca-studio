
import {
  CONTINUATION_NOTE,
  CONTINUATION_EMPTY_STATES,
  continuationEntries,
} from "./workspaceProductivityAssistanceUxModel";
import { useCallback, useEffect, useState } from "react";
import { amca } from "./api";
import { Icon } from "./icons";
import {
  activeRuns,
  deriveAgentActivity,
  deriveCurrentTask,
  deriveDashboardMetrics,
  recentArtifacts,
} from "./workspaceDashboardModel";
import { buildNanoTree, type NanoTreeNode } from "./agentMonitorModel";
import {
  COMPOSER_MODES,
  TASK_TYPES,
  buildComposerPayload,
  executionTimeline,
  groupArtifacts,
  type ComposerDraft,
} from "./composerModel";
import { shouldShowManualCollaborationControls } from "./chatIntelligenceModel";
import {
  DEMO_CHECKLIST,
  GUIDED_JOURNEY,
  KNOWN_LIMITATIONS,
  activityBarSegments,
  emptyStateFor,
  runDurationLabel,
} from "./studioPolishModel";
import {
  firstRunExperience,
  onboardingSteps,
  type OnboardingStateInput,
} from "./releasePolishModel";
import { COMPOSER_TEMPLATES, templateToDraftPatch } from "./productivityModel";
import {
  buildOperationalReport,
  operationalCards,
  operationalTimeline,
  reportToText,
  resourceVisibility,
  type OperationalEvidence,
} from "./operationalIntelligenceModel";
import {
  completionEvidence,
  projectHealth,
  projectLifecycle,
} from "./projectLifecycleModel";
import type { AutomationDefinition, AutomationOccurrence } from "./types";
import {
  FEEDBACK_CHANNELS,
  buildFeedbackDraft,
  feedbackDraftToText,
  featureHints,
} from "./userExperienceModel";
import type {
  AgentFabricState,
  ArtifactMeta,
  ConversationSummary,
  NanoAgentsResponse,
  RunJob,
  StudioProjectSummary,
} from "./types";

// Z1 Part 1 — Workspace Dashboard. The primary AMCA Studio landing view.
// Exposes EXISTING backend documents only: /api/studio-projects (projects),
// /api/dashboard (governed runs), /api/conversations (sessions), /api/runs
// (current task + cancel), /api/agent-fabric + /api/nano-agents (active
// agents and execution tree), /api/projects/{id}/artifacts (artifacts).
// No new backend semantics; every action routes to an existing view.

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function relativeTime(value: string | null | undefined): string {
  if (!value) return "—";
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return value;
  const seconds = Math.round((timestamp - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (Math.abs(seconds) < 60) return formatter.format(seconds, "second");
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return formatter.format(hours, "hour");
  return formatter.format(Math.round(hours / 24), "day");
}

interface DashboardProps {
  studioProjects: StudioProjectSummary[];
  preferredProjectId: string | null;
  composeBusy: boolean;
  onComposeTask: (draft: ComposerDraft) => Promise<"submitted" | "approval">;
  onOpenProject: (projectId: string | null) => void;
  onOpenProjects: () => void;
  onOpenChat: (conversationId: string) => void;
  onOpenAgents: () => void;
  onOpenModels: () => void;
  onOpenArtifacts: () => void;
  /** W1 deep link: open the Artifact Center filtered by a recorded artifact type. */
  onOpenArtifactsFiltered: (projectId: string | null, artifactType: string) => void;
  /** Recorded runs for the Continue work surface (existing records only). */
  runs?: RunJob[];
}

// Z4 dashboard polish: a "now" clock for the live duration label, re-read
// once per refresh rather than on every frame.
const nowClock = () => Date.now();

function NanoTreeRow({ node, last }: { node: NanoTreeNode; last: boolean }) {
  return (
    <>
      <div className={`dash-tree-row state-${node.state}`}>
        <span className="dash-tree-branch">{last ? "└" : "├"}</span>
        <strong>{node.purpose || humanize(node.taskId)}</strong>
        <small>{humanize(node.statusLabel)}</small>
        <span className="dash-tree-id">{node.nanoId}</span>
      </div>
      {node.children.map((child, index) => (
        <NanoTreeRow key={child.nanoId} node={child} last={index === node.children.length - 1} />
      ))}
    </>
  );
}

const EMPTY_DRAFT: ComposerDraft = {
  goal: "",
  taskTypeId: "general_reasoning",
  mode: "solo", // H1.1-1: safe first-run default
  selectionPolicy: "auto_intelligence",
  modelRefs: [],
  workspaceProjectId: null,
};

export function WorkspaceDashboard({
  studioProjects,
  preferredProjectId,
  composeBusy,
  onComposeTask,
  onOpenProject,
  onOpenProjects,
  onOpenChat,
  onOpenAgents,
  onOpenModels,
  onOpenArtifacts,
  onOpenArtifactsFiltered,
  runs: dashboardRuns = [],
}: DashboardProps) {
  const [runs, setRuns] = useState<RunJob[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [fabric, setFabric] = useState<AgentFabricState | null>(null);
  const [nano, setNano] = useState<NanoAgentsResponse | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactMeta[]>([]);
  const [artifactProjectId, setArtifactProjectId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<ComposerDraft>({ ...EMPTY_DRAFT, workspaceProjectId: preferredProjectId });
  const [draftErrors, setDraftErrors] = useState<string[]>([]);
  const [modelRefs, setModelRefs] = useState<string[]>([]);
  const [composerResult, setComposerResult] = useState<string | null>(null);
  // P1: real hub/automation counts so discovery hints stay honest.
  const [hubTeams, setHubTeams] = useState<number | null>(null);
  const [hubProviders, setHubProviders] = useState<number | null>(null);
  const [automationCount, setAutomationCount] = useState<number | null>(null);
  // BETA-B14: operational evidence from existing registries only.
  const [opAutomations, setOpAutomations] = useState<AutomationDefinition[] | null>(null);
  const [opOccurrences, setOpOccurrences] = useState<AutomationOccurrence[] | null>(null);
  const [opSchedulerRunning, setOpSchedulerRunning] = useState<boolean | null>(null);
  const [opEvents, setOpEvents] = useState<import("./types").CognitiveEvent[] | null>(null);
  const [opCostState, setOpCostState] = useState<import("./api").CostStateWire | null>(null);
  const [reportCopied, setReportCopied] = useState(false);
  // Z4: user override for which project's artifacts the dashboard shows.
  const [artifactProjectChoice, setArtifactProjectChoice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [dashboardDoc, conversationDoc, fabricState, nanoState] = await Promise.all([
        amca.dashboard(),
        amca.conversations(),
        amca.agentFabric(),
        amca.nanoAgents(),
      ]);
      setRuns(dashboardDoc.runs);
      setConversations(conversationDoc.conversations);
      setFabric(fabricState);
      setNano(nanoState);
      const artifactProject =
        preferredProjectId ??
        studioProjects[0]?.project_id ??
        dashboardDoc.projects[0]?.project_id ??
        null;
      if (artifactProject) {
        const artifactDoc = await amca.artifactsList(artifactProject);
        setArtifacts(artifactDoc.artifacts);
        setArtifactProjectId(artifactProject);
      } else {
        setArtifacts([]);
        setArtifactProjectId(null);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, [studioProjects, preferredProjectId]);

  useEffect(() => { void refresh(); }, [refresh]);

  // Z4: project switcher for the artifact panel — loads the chosen
  // project's list through the existing artifact API. The auto-derived
  // project stays the default; the override never invents data.
  useEffect(() => {
    if (!artifactProjectChoice || artifactProjectChoice === artifactProjectId) return;
    let cancelled = false;
    void amca.artifactsList(artifactProjectChoice).then((doc) => {
      if (!cancelled) { setArtifacts(doc.artifacts); setArtifactProjectId(artifactProjectChoice); }
    }).catch(() => { /* keep the previously loaded list; refresh reports load errors */ });
    return () => { cancelled = true; };
  }, [artifactProjectChoice, artifactProjectId]);

  // Z2: load the selectable model refs for the manual strategy from the
  // existing Model Hub document (same sources the chat selector offers).
  useEffect(() => {
    let cancelled = false;
    void amca.modelHub().then((hub) => {
      if (cancelled) return;
      setModelRefs([
        ...hub.local_models.models.map((item) => `ollama:${item.model}`),
        ...hub.direct_models.filter((item) => item.configured).map((item) => item.credential_id),
        ...hub.byok_providers.flatMap((item) => item.model_ids.map((model) => `${item.id}:${model}`)),
      ]);
      setHubTeams(hub.model_teams.length);
      setHubProviders(hub.byok_providers.filter((item) => item.configured).length);
    }).catch(() => { /* selector stays empty; Auto Intelligence remains available */ });
    return () => { cancelled = true; };
  }, []);

  // P1: real automation count for the discovery hints (best-effort; the
  // hint degrades honestly when the list cannot be loaded).
  useEffect(() => {
    let cancelled = false;
    void amca.automations().then((doc) => {
      if (!cancelled) { setAutomationCount(doc.automations.length); setOpAutomations(doc.automations); }
    }).catch(() => { if (!cancelled) setAutomationCount(0); });
    return () => { cancelled = true; };
  }, []);

  // BETA-B14: operational evidence — occurrences, scheduler, events, cost.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [automationsDoc, scheduler] = await Promise.all([
          amca.automations(),
          amca.schedulerStatus(),
        ]);
        if (cancelled) return;
        setOpAutomations(automationsDoc.automations);
        setOpSchedulerRunning(scheduler.running && scheduler.worker_alive);
        const firstAutomation = automationsDoc.automations[0];
        if (firstAutomation) {
          const occurrenceDoc = await amca.automationOccurrences(firstAutomation.id, 20);
          if (!cancelled) setOpOccurrences(occurrenceDoc.occurrences);
        } else if (!cancelled) {
          setOpOccurrences([]);
        }
      } catch {
        if (!cancelled) { setOpOccurrences([]); setOpSchedulerRunning(null); }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const projectId = artifactProjectId ?? studioProjects[0]?.project_id ?? null;
    if (!projectId) return;
    void amca.events(projectId).then((doc) => {
      if (!cancelled) setOpEvents(doc.events);
    }).catch(() => { if (!cancelled) setOpEvents([]); });
    void amca.costState().then((doc) => {
      if (!cancelled) setOpCostState(doc);
    }).catch(() => { if (!cancelled) setOpCostState(null); });
    return () => { cancelled = true; };
  }, [artifactProjectId, studioProjects]);

  const submitComposer = async (event: React.FormEvent) => {
    event.preventDefault();
    setComposerResult(null);
    const decision = buildComposerPayload({ ...draft, modelRefs });
    if (!decision.ok) {
      setDraftErrors(decision.errors);
      return;
    }
    setDraftErrors([]);
    try {
      const outcome = await onComposeTask({ ...draft, modelRefs });
      setComposerResult(
        outcome === "approval"
          ? "Task created — approvals were requested before any network model call. Review them in Approvals."
          : "Task submitted to the governed run pipeline.",
      );
      setDraft({ ...EMPTY_DRAFT, workspaceProjectId: draft.workspaceProjectId });
    } catch (caught) {
      setDraftErrors([caught instanceof Error ? caught.message : String(caught)]);
    }
  };

  const currentTask = deriveCurrentTask(runs);
  const activity = deriveAgentActivity(nano ?? { assignments: [], statuses: {} }, fabric?.counts ?? {
    core: 0, built_in_specialists: 0, custom: 0, total_available: 0, active: 0,
  }, runs);
  const metrics = deriveDashboardMetrics({
    studioProjects: studioProjects.length,
    runs,
    activity,
    artifactCount: artifacts.length,
  });
  const liveRuns = activeRuns(runs);
  // Z4 polish: duration + activity-bar + artifact project switcher.
  const duration = currentTask.run ? runDurationLabel(currentTask.run, nowClock()) : null;
  const activitySegments = activityBarSegments(activity);
  const artifactSourceProjectId = artifactProjectChoice ?? artifactProjectId;
  const artifactChoices = studioProjects.length ? studioProjects : (artifactProjectId ? [{ project_id: artifactProjectId, name: `Project ${artifactProjectId.slice(0, 8)}` }] : []);
  const recent = recentArtifacts(artifacts);
  const artifactGroups = groupArtifacts(recent);
  const currentTimeline = currentTask.run ? executionTimeline(currentTask.run) : [];
  const nanoRoots = nano ? buildNanoTree(nano) : [];
  // Z7 onboarding: derive the five-step state from the documents already
  // loaded by this dashboard; the strip is hidden once setup is complete.
  const onboardingState: OnboardingStateInput = {
    hasProjects: studioProjects.length > 0,
    hasConversations: conversations.length > 0,
    hasSelectableModels: modelRefs.length > 0,
    hasRuns: runs.length > 0,
    hasArtifacts: artifacts.length > 0,
  };
  const onboarding = firstRunExperience(onboardingState);
  const onboardingList = onboardingSteps(onboardingState);
  // P1 feedback surfaces + feature discovery: local-only, document-based.
  const [feedbackChannel, setFeedbackChannel] = useState<string | null>(null);
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackCopied, setFeedbackCopied] = useState(false);
  const hintList = featureHints({
    hasSelectableModels: modelRefs.length > 0,
    hasProjects: studioProjects.length > 0,
    hasRuns: runs.length > 0,
    hasArtifacts: artifacts.length > 0,
    hasTeams: hubTeams !== null ? hubTeams > 0 : false,
    hasAutomations: automationCount !== null ? automationCount > 0 : false,
    hasProviders: hubProviders !== null ? hubProviders > 0 : false,
  });
  // BETA-B14: operational intelligence from existing registries only.
  const opEvidence: OperationalEvidence = {
    projects: studioProjects,
    runs,
    automations: opAutomations ?? [],
    occurrences: opOccurrences ?? [],
    schedulerRunning: opSchedulerRunning,
    artifacts,
  };
  const opCards = operationalCards(opEvidence);
  const opReport = buildOperationalReport(opEvidence, new Date().toISOString());
  const opVisibility = resourceVisibility(opCostState, runs);
  const opTimeline = operationalTimeline(runs, opOccurrences ?? [], opEvents ?? [], 12);

  return (
    <section className="workspace-view studio-dashboard-view">
      <div className="view-heading">
        <div>
          <span className="section-kicker">AMCA Studio</span>
          <h1>Workspace</h1>
          <p>Projects, current work, agents, and generated artifacts — everything local, nothing shared.</p>
        </div>
        <button className="secondary-button" onClick={() => void refresh()}><Icon name="refresh" /> Refresh</button>
      </div>

      {error && <div className="error-banner"><div><strong>Dashboard could not load live state</strong><span>{error}</span></div><button onClick={() => void refresh()}>Retry</button></div>}

      {/* Z7 onboarding strip: real document state, hidden when complete. */}
      {(() => {
        // v0.8.0 productivity assistance: "Continue work" over recorded
        // runs — recency order, no recommended next action, no urgency
        // ranking.
        const entries = continuationEntries(dashboardRuns);
        return (
          <article className="panel dash-continue-work" aria-label="Continue work">
            <div className="panel-heading">
              <div><span className="section-kicker">Continue work</span><h2>Unfinished workflows</h2></div>
              <small>{entries.length} recorded</small>
            </div>
            {entries.length ? (
              <div className="ops-timeline">
                {entries.map((entry) => (
                  <div key={entry.workflowId} className="ops-timeline-row">
                    <span className="status-pill status-muted"><i />{entry.status}</span>
                    <span>{entry.workflowId.slice(0, 18)}…</span>
                    <small>{entry.taskType ?? "task type not recorded"} · {entry.timestamp ?? "undated record"}</small>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted-copy">{CONTINUATION_EMPTY_STATES.no_unfinished}</p>
            )}
            <p className="muted-copy">{CONTINUATION_NOTE}</p>
            <p className="muted-copy">Recorded activity summary — see the Command Center for the full operational state.</p>
          </article>
        );
      })()}
      {onboarding.headline !== "Setup complete" && (
        <article className="panel dash-onboarding" aria-label="Getting started">
          <div className="panel-heading">
            <div><span className="section-kicker">Getting started</span><h2>{onboarding.headline}</h2></div>
            {onboarding.isFirstRun && <span className="status-pill status-ready"><i />Local-first</span>}
          </div>
          <p className="settings-note">{onboarding.detail}</p>
          <ol className="dash-onboarding-steps">
            {onboardingList.map((step) => {
              const onSelect =
                step.view === "overview" ? onOpenProjects
                : step.view === "settings" ? onOpenModels
                : step.view === "artifacts" ? onOpenArtifacts
                : step.view === "agents" ? onOpenAgents
                : undefined;
              return (
                <li key={step.key} className={step.done ? "done" : ""}>
                  <b>{step.done ? "✓" : ""}</b>
                  <div><strong>{step.label}</strong><span>{step.detail}</span></div>
                  {!step.done && onSelect && <button className="secondary-button" onClick={onSelect}>Open</button>}
                </li>
              );
            })}
          </ol>
        </article>
      )}

      <div className="metrics-grid dash-metrics">
        <article className="metric-card tone-green"><span>Projects</span><strong>{metrics.studioProjects}</strong><small>Local workspaces</small></article>
        <article className="metric-card"><span>Live runs</span><strong>{metrics.liveRuns}</strong><small>{metrics.totalRuns} governed runs total</small></article>
        <article className="metric-card"><span>Active agents</span><strong>{metrics.activeAgents}</strong><small>{activity.nanoLive} nano live · {activity.fabricActive} fabric</small></article>
        <article className="metric-card"><span>Artifacts</span><strong>{metrics.artifactCount}</strong><small>{artifactProjectId ? `project ${artifactProjectId.slice(0, 12)}` : "no project yet"}</small></article>
      </div>

      <form className="panel dash-composer" onSubmit={(event) => void submitComposer(event)}>
        <div className="panel-heading"><div><span className="section-kicker">New task</span><h2>Start governed work</h2></div><span className="status-pill status-ready"><i />Existing pipeline</span></div>
        <p className="settings-note">Creates Project → Conversation → Run through the existing AMCA lifecycle. No execution bypass: model strategy and approvals stay under the governor.</p>
        {/* P5: quick-start templates prefill the draft; execution stays governed. */}
        <div className="composer-templates" role="group" aria-label="Quick-start templates">
          <span>Quick start:</span>
          {COMPOSER_TEMPLATES.map((template) => (
            <button key={template.id} type="button" title={template.description} onClick={() => { setDraft((current) => ({ ...current, ...templateToDraftPatch(template), workspaceProjectId: current.workspaceProjectId })); setDraftErrors([]); }}>
              {template.label}
            </button>
          ))}
        </div>
        <div className="composer-grid">
          <label className="wide"><span>Goal</span><textarea rows={3} value={draft.goal} onChange={(event) => setDraft({ ...draft, goal: event.target.value })} placeholder="What should AMCA do?" /></label>
          <label><span>Project</span><select value={draft.workspaceProjectId ?? ""} onChange={(event) => setDraft({ ...draft, workspaceProjectId: event.target.value || null })}><option value="">No project (workspace-local)</option>{studioProjects.map((item) => <option key={item.project_id} value={item.project_id}>{item.name}</option>)}</select></label>
          <label><span>Task type</span><select value={draft.taskTypeId} onChange={(event) => setDraft({ ...draft, taskTypeId: event.target.value })}>{TASK_TYPES.map((type) => <option key={type.id} value={type.id}>{type.label}</option>)}</select><small>{TASK_TYPES.find((type) => type.id === draft.taskTypeId)?.hint}</small></label>
          {shouldShowManualCollaborationControls(draft.selectionPolicy) ? (
            <label><span>Collaboration mode</span><select value={draft.mode} onChange={(event) => setDraft({ ...draft, mode: event.target.value as ComposerDraft["mode"] })}>{COMPOSER_MODES.map((mode) => <option key={mode.id} value={mode.id}>{mode.label}</option>)}</select><small>{COMPOSER_MODES.find((mode) => mode.id === draft.mode)?.hint}</small></label>
          ) : (
            /* v1.1.8 Phase 3: under Auto Intelligence the backend owns the
               collaboration mode, so a mode select here was a control the run
               discarded.  It is shown as a fixed state instead. */
            <label><span>Collaboration mode</span><p className="settings-note">Auto · AMCA decides — Auto Intelligence picks the collaboration mode from the goal. Choose Manual selection to pick one yourself.</p></label>
          )}
          <fieldset className="composer-strategy">
            <legend>Model strategy</legend>
            <label><input type="radio" name="composer-strategy" checked={draft.selectionPolicy === "auto_intelligence"} onChange={() => setDraft({ ...draft, selectionPolicy: "auto_intelligence" })} /> Auto Intelligence <small>backend chooses lowest-cost qualified models</small></label>
            <label><input type="radio" name="composer-strategy" checked={draft.selectionPolicy === "manual_model_set"} onChange={() => setDraft({ ...draft, selectionPolicy: "manual_model_set" })} /> Manual selection <small>pick exact models below</small></label>
            {draft.selectionPolicy === "manual_model_set" && (
              <div className="composer-model-refs">
                {modelRefs.length ? modelRefs.map((ref) => (
                  <label key={ref}><input type="checkbox" checked={modelRefs.includes(ref)} disabled={composeBusy} onChange={() => setModelRefs((current) => current.includes(ref) ? current.filter((item) => item !== ref) : [...current, ref])} /> <code>{ref}</code></label>
                )) : (
                  <div className="empty-state composer-empty-models">
                    <h3>{emptyStateFor("models").title}</h3>
                    <p>{emptyStateFor("models").detail}</p>
                    <button className="empty-next" onClick={onOpenModels}>{emptyStateFor("models").action?.label}</button>
                  </div>
                )}
              </div>
            )}
          </fieldset>
        </div>
        {draftErrors.length > 0 && <div className="composer-errors" role="alert">{draftErrors.map((message) => <p key={message}>{message}</p>)}</div>}
        {composerResult && <p className="composer-result">{composerResult}</p>}
        <div className="form-actions"><button className="primary-button" type="submit" disabled={composeBusy || !draft.goal.trim()}>{composeBusy ? "Starting…" : "Execute task"}</button></div>
      </form>

      <div className="dash-grid">
        <article className="panel dash-current-task">
          <div className="panel-heading"><div><span className="section-kicker">Current task</span><h2>{currentTask.run ? "In the room" : "Idle"}</h2></div>{currentTask.run && <span className={`status-pill status-${currentTask.run.status === "running" || currentTask.run.status === "cancelling" ? "running" : currentTask.run.status === "queued" ? "queued" : "pending"}`}><i />{humanize(currentTask.run.status)}</span>}</div>
          {currentTask.run ? (
            <>
              <dl className="detail-list">
                <div><dt>Goal</dt><dd>{currentTask.run.goal}</dd></div>
                <div><dt>Mode</dt><dd>{humanize(currentTask.run.mode)}</dd></div>
                <div><dt>Models</dt><dd>{[currentTask.run.provider, currentTask.run.model].filter(Boolean).join(" / ") || "router-selected"}</dd></div>
                <div><dt>Started</dt><dd>{relativeTime(currentTask.run.started_at)}</dd></div>
                {duration && <div><dt>{duration.live ? "Elapsed" : "Duration"}</dt><dd>{duration.label}{duration.live ? " · running" : ""}</dd></div>}
              </dl>
              <div className="dash-timeline" aria-label="Execution timeline">
                {currentTimeline.map((step, index) => (
                  <div key={step.key} className={`dash-timeline-step phase-${step.phase}`}>
                    <i />
                    <span>{step.label}</span>
                    {index < currentTimeline.length - 1 && <b>→</b>}
                  </div>
                ))}
              </div>
              {liveRuns.length > 1 && <p className="settings-note">{liveRuns.length - 1} more run{liveRuns.length - 1 === 1 ? "" : "s"} active.</p>}
              {currentTask.run.status === "queued" || currentTask.run.status === "running" ? null : (
                <p className="settings-note">Showing the most recent run (created {relativeTime(currentTask.run.created_at)}); no run is active right now.</p>
              )}
            </>
          ) : (
            <p className="muted-copy">No run is active. Start one from the composer above, or open a Project to begin.</p>
          )}
        </article>

        <article className="panel dash-agents">
          <div className="panel-heading"><div><span className="section-kicker">Agents</span><h2>Active agents</h2></div><button className="secondary-button" onClick={onOpenAgents}>Agent Control Panel</button></div>
          {nano && nano.assignments.length > 0 ? (
            <>
              <div className="dash-agent-counts">
                <span><strong>{activity.nanoLive}</strong> live nano</span>
                <span><strong>{activity.nanoCompleted}</strong> completed</span>
                <span><strong>{activity.fabricActive}</strong> fabric active</span>
              </div>
              {activitySegments.length > 0 && (
                <div className="dash-activity-bar" role="img" aria-label={activitySegments.map((segment) => `${segment.count} ${segment.label.toLowerCase()}`).join(", ")}>
                  {activitySegments.map((segment) => (
                    <span key={segment.key} className={`dash-activity-segment tone-${segment.key}`} style={{ flexGrow: segment.fraction }} title={`${segment.label}: ${segment.count}`} />
                  ))}
                </div>
              )}
              <div className="dash-tree">
                {nanoRoots.map((root, index) => (
                  <NanoTreeRow key={root.nanoId} node={root} last={index === nanoRoots.length - 1} />
                ))}
              </div>
            </>
          ) : (
            <p className="muted-copy">No nano agents are assigned. Fabric roles activate automatically when a task needs them{fabric ? ` (${fabric.counts.total_available} roles available)` : ""}.</p>
          )}
        </article>

        <article className="panel dash-projects">
          <div className="panel-heading"><div><span className="section-kicker">Projects</span><h2>Workspaces</h2></div><small>{studioProjects.length}</small></div>
          {studioProjects.length ? (
            <div className="project-list">
              {studioProjects.slice(0, 6).map((item) => {
                // BETA-B13: empirical lifecycle per project — the run
                // registry has project_id; artifact evidence only when the
                // artifact panel has that project's list loaded.
                const lifecycle = projectLifecycle({
                  project: item,
                  runs,
                  artifacts: artifactSourceProjectId === item.project_id ? artifacts : [],
                  artifactsComplete: artifactSourceProjectId === item.project_id,
                });
                const health = projectHealth({ ...lifecycle }, runs.filter((run) => run.project_id === item.project_id));
                const evidence = completionEvidence({
                  project: item,
                  runs,
                  artifacts: artifactSourceProjectId === item.project_id ? artifacts : [],
                  artifactsComplete: artifactSourceProjectId === item.project_id,
                });
                return (
                  <details key={item.project_id} className="project-lifecycle">
                    <summary>
                      <div className="project-monogram">{item.name.charAt(0).toUpperCase()}</div>
                      <div className="project-row-copy"><strong>{item.name}</strong><span>{item.chat_count} chats · {humanize(item.template)}</span></div>
                      <span className={`status-pill health-${health.state}`}><i />{humanize(health.state)}</span>
                    </summary>
                    <div className="project-lifecycle-body">
                      <p className="settings-note">{lifecycle.disclosure} Health: {health.reason}</p>
                      <div className="lifecycle-progress">
                        <div className="lifecycle-progress-bar"><span style={{ width: `${lifecycle.percent}%` }} /></div>
                        <small>{lifecycle.completed}/{lifecycle.total} milestones · {lifecycle.percent}% (evidence-backed)</small>
                      </div>
                      <ol className="lifecycle-stages">
                        {lifecycle.stages.map((stage) => (
                          <li key={stage.key} className={stage.status}>
                            <b>{stage.status === "done" ? "✓" : ""}</b>
                            <div>
                              <strong>{stage.label}</strong>
                              {stage.evidence && <span>{stage.evidence.source}: {stage.evidence.detail}{stage.evidence.at ? ` · ${new Date(stage.evidence.at).toLocaleString()}` : ""}</span>}
                            </div>
                          </li>
                        ))}
                      </ol>
                      <details className="lifecycle-evidence">
                        <summary>Completion evidence</summary>
                        <ul>
                          {evidence.map((line) => (
                            <li key={line.label}>
                              <strong>{line.label}</strong>
                              <span>{line.recorded ? `✓ ${line.detail ?? "recorded"}` : "not recorded"}</span>
                            </li>
                          ))}
                        </ul>
                      </details>
                      <button className="secondary-button" onClick={() => onOpenProject(item.project_id)}>Open project</button>
                    </div>
                  </details>
                );
              })}
            </div>
          ) : <p className="muted-copy">No Projects yet. Create one from the Projects view.</p>}
          {conversations.length > 0 && (
            <details className="dash-recent-chats">
              <summary>Recent chats</summary>
              {conversations.slice(0, 5).map((item) => (
                <button key={item.conversation_id} onClick={() => onOpenChat(item.conversation_id)}>
                  <strong>{item.title}</strong>
                  <small>{item.last_message_preview || "Empty chat"}</small>
                </button>
              ))}
            </details>
          )}
        </article>

        <article className="panel dash-artifacts">
          <div className="panel-heading"><div><span className="section-kicker">Artifacts</span><h2>Recent outputs</h2></div><button className="secondary-button" onClick={onOpenArtifacts}>Artifact Center</button></div>
          {artifactChoices.length > 1 && (
            <label className="dash-artifact-switcher"><span>Project</span>
              <select value={artifactSourceProjectId ?? ""} onChange={(event) => setArtifactProjectChoice(event.target.value || null)}>
                {artifactChoices.map((item) => <option key={item.project_id} value={item.project_id}>{item.name}</option>)}
              </select>
            </label>
          )}
          {artifactGroups.length ? (
            <div className="dash-artifact-groups">
              {artifactGroups.map((group) => (
                <div key={group.type} className="dash-artifact-group">
                  <h3>{group.label}</h3>
                  <div className="dash-artifact-list">
                    {group.items.map((item) => (
                      <button
                        key={item.artifact_id}
                        type="button"
                        className="dash-artifact-open"
                        onClick={() => onOpenArtifactsFiltered(artifactSourceProjectId, item.artifact_type)}
                        title={`Open in Artifact Center · ${humanize(item.artifact_type)}`}
                      >
                        <span className="file-icon">{item.artifact_type.slice(0, 3).toUpperCase()}</span>
                        <div><strong>{item.title}</strong><small>{humanize(item.artifact_type)} · v{item.current_version}</small></div>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted-copy">No artifacts for {artifactSourceProjectId ? "this project" : "any project"} yet. Model-authored outputs appear here.</p>
          )}
          <p className="muted-copy dash-artifact-note">Full export and version history live in the Artifact Center.</p>
          <div className="dash-model-hub-link">
            <button className="secondary-button" onClick={onOpenModels}>Model Hub — configure providers & selection</button>
          </div>
        </article>
      </div>

      <div className="dash-grid dash-readiness-grid">
        <article className="panel dash-journey">
          <div className="panel-heading"><div><span className="section-kicker">Demo readiness</span><h2>End-to-end journey</h2></div><small>{GUIDED_JOURNEY.length} steps</small></div>
          <ol className="dash-journey-list">
            {GUIDED_JOURNEY.map((step, index) => (
              <li key={step.id}>
                <b>{index + 1}</b>
                <div><strong>{step.label}</strong><span>{step.detail}</span></div>
              </li>
            ))}
          </ol>
          <details className="dash-demo-checklist">
            <summary>Demonstration checklist</summary>
            <ul>
              {DEMO_CHECKLIST.map((item) => (
                <li key={item.id}><strong>{item.title}</strong><span>{item.detail}</span></li>
              ))}
            </ul>
          </details>
        </article>

        <article className="panel dash-limitations">
          <div className="panel-heading"><div><span className="section-kicker">Honest scope</span><h2>Known limitations</h2></div><small>{KNOWN_LIMITATIONS.length}</small></div>
          <ul className="dash-limitation-list">
            {KNOWN_LIMITATIONS.map((limitation) => (
              <li key={limitation.title}><strong>{limitation.title}</strong><span>{limitation.detail}</span></li>
            ))}
          </ul>
        </article>
      </div>

      {/* P1: feature discovery hints + local feedback surfaces. */}
      <div className="dash-grid dash-readiness-grid">
        <article className="panel dash-hints">
          <div className="panel-heading"><div><span className="section-kicker">Discover more</span><h2>Suggested for your current setup</h2></div><small>{hintList.length}</small></div>
          <ul className="dash-hint-list">
            {hintList.map((hint) => (
              <li key={hint.id}>
                <strong>{hint.label}</strong>
                <span>{hint.detail}</span>
              </li>
            ))}
          </ul>
        </article>

        <article className="panel dash-feedback">
          <div className="panel-heading"><div><span className="section-kicker">Beta feedback</span><h2>Tell us how it went</h2></div><span className="status-pill status-ready"><i />Local only</span></div>
          <p className="settings-note">Pick a channel, write your note, and copy the result. AMCA stores nothing and sends nothing anywhere — the draft is a local document you keep.</p>
          <div className="dash-feedback-channels" role="group" aria-label="Feedback channels">
            {FEEDBACK_CHANNELS.map((channel) => (
              <button
                key={channel.id}
                className={feedbackChannel === channel.id ? "active" : ""}
                aria-pressed={feedbackChannel === channel.id}
                onClick={() => { setFeedbackChannel(channel.id); setFeedbackCopied(false); }}
              >
                {channel.label}
              </button>
            ))}
          </div>
          {(() => {
            const channel = FEEDBACK_CHANNELS.find((item) => item.id === feedbackChannel);
            if (!channel) return null;
            const draft = buildFeedbackDraft({
              channelId: channel.id,
              text: feedbackText,
              screen: "dashboard",
              createdAtIso: new Date().toISOString(),
              appVersion: "0.6.2",
            });
            return (
              <form className="dash-feedback-form" onSubmit={(event) => event.preventDefault()}>
                <label><span>{channel.prompt}</span>
                  <textarea rows={4} value={feedbackText} placeholder={channel.placeholder} onChange={(event) => { setFeedbackText(event.target.value); setFeedbackCopied(false); }} />
                </label>
                <div className="form-actions">
                  <button
                    className="secondary-button"
                    disabled={!draft}
                    onClick={() => {
                      if (!draft) return;
                      void navigator.clipboard.writeText(feedbackDraftToText(draft)).then(() => setFeedbackCopied(true));
                    }}
                  >
                    {feedbackCopied ? "Copied ✓" : "Copy draft"}
                  </button>
                  <button type="button" className="secondary-button" disabled={!feedbackText} onClick={() => { setFeedbackText(""); setFeedbackCopied(false); }}>Clear</button>
                </div>
                {draft && <pre className="dash-feedback-preview">{feedbackDraftToText(draft)}</pre>}
              </form>
            );
          })()}
        </article>
      </div>

      {/* BETA-B14: operational intelligence — evidence-based cards, timeline,
          resource visibility, and a copyable report. Privacy: local only. */}
      <article className="panel dash-operational">
        <div className="panel-heading"><div><span className="section-kicker">Operational intelligence</span><h2>Studio operations</h2></div><span className="status-pill status-ready"><i />Evidence only</span></div>
        <div className="op-cards">
          {opCards.map((card) => (
            <div key={card.key} className={`op-card ${card.value === null ? "unrecorded" : ""}`} title={`Source: ${card.source}`}>
              <span>{card.label}</span>
              <strong>{card.value === null ? "—" : card.value}</strong>
              <small>{card.detail}</small>
            </div>
          ))}
        </div>
        <div className="op-grid">
          <div className="op-block">
            <h4>Resource visibility</h4>
            <p className="op-line">{opVisibility.line}</p>
            {opVisibility.perModel.length > 0 && (
              <ul className="op-per-model">
                {opVisibility.perModel.map((entry) => (
                  <li key={entry.model}><code>{entry.model}</code><span>${entry.costUsd.toFixed(4)}</span></li>
                ))}
              </ul>
            )}
            <h4>Operational timeline</h4>
            {opTimeline.length ? (
              <ul className="op-timeline">
                {opTimeline.map((entry) => (
                  <li key={entry.id} className={`kind-${entry.kind}`}>
                    <b>{entry.kind}</b>
                    <span>{entry.label}</span>
                    <small>{entry.state}{entry.at ? ` · ${new Date(entry.at).toLocaleTimeString()}` : ""}</small>
                  </li>
                ))}
              </ul>
            ) : <p className="muted-copy">No operational events recorded yet.</p>}
          </div>
          <div className="op-block">
            <h4>Operational report</h4>
            <p className="settings-note">{opReport.privacyNote}</p>
            <dl className="op-metrics">
              {opReport.metrics.map((metric) => (
                <div key={metric.label} className={metric.recorded ? "" : "unrecorded"}>
                  <dt>{metric.label}</dt>
                  <dd>{metric.value}{metric.recorded ? "" : " — no estimates"} <small>({metric.source})</small></dd>
                </div>
              ))}
            </dl>
            <div className="form-actions">
              <button
                className="secondary-button"
                onClick={() => {
                  void navigator.clipboard.writeText(reportToText(opReport)).then(() => setReportCopied(true));
                }}
              >
                {reportCopied ? "Copied ✓" : "Copy report JSON"}
              </button>
              <button type="button" className="secondary-button" onClick={() => setReportCopied(false)} disabled={!reportCopied}>Reset</button>
            </div>
          </div>
        </div>
      </article>
    </section>
  );
}
