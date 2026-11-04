import { Children, isValidElement, useCallback, useEffect, useRef, useState } from "react";
import {
  ThreadDialog,
  ProjectDeleteDialog,
  ResearchWorkspace,
  RecordedDetails,
  ArtifactInspector,
  RichOutput,
  ResponseActions,
  SourcePreview,
  BrowserInspector,
  SkillsWorkspace,
} from "./StudioComponents";
import { artifactFileName, saveFile } from "./outputModel";
import { artifactTypeLabel, canonicalSourceExtension } from "./artifactPreviewModel";
import { CLOCK_TICK_MS, PDF_PREVIEW_UNAVAILABLE, COMPOSER_MENU_ACTIONS, effortLabel } from "./chatUxModel";
import {
  addPendingAttachments,
  CHAT_ATTACHMENT_ACCEPT_ATTRIBUTE,
  type ChromiumFileEntry,
  filesFromDataTransfer,
  filesFromEntry,
  MAX_ATTACHMENT_BYTES,
  MAX_CHAT_ATTACHMENTS,
  pasteCarriesFiles,
  pasteCarriesText,
  removePendingAttachment,
} from "./composerAttachmentsModel";

/** Artifact types whose deliverable is a derived rendition, not the source. */
const RENDITION_ARTIFACT_TYPES = new Set(["docx", "pdf", "pptx", "xlsx"]);
import "katex/dist/katex.min.css";
import { amca, API_LABEL } from "./api";
import amcaSymbol from "./assets/amca-symbol.png";
import { IntelligencePanel } from "./IntelligencePanel";
import { CostBudgetPanel } from "./costBudgetPanel";
import { AboutUpdatesPanel } from "./AboutUpdatesPanel";
import {
  COLLABORATION_ARCHITECTURES,
  collaborationArchitectureOptions,
  shouldShowManualCollaborationControls,
  type CollaborationArchitecture,
} from "./chatIntelligenceModel";
import { humanModelName } from "./teamModel";
import { composerModelHealth } from "./composerModelHealth";
import { buildComposerModelMenu } from "./composerModelMenu";
import { isConfigurationDefect } from "./modelSettingsNavModel.ts";
import { invoke } from "@tauri-apps/api/core";
import { Icon, type IconName } from "./icons";
import { NanoAgentsPanel } from "./NanoAgents";
import { AgentLibrary, type AgentLibraryTab } from "./AgentLibrary";
import { StudioPanel, type PanelTab } from "./StudioPanel";
import type { PanelFacilityId } from "./studioFacilitiesModel";
import { ArtifactCenter } from "./ArtifactCenter";
import { EngineeringWorkspaceView } from "./EngineeringWorkspace";
import { ModelHub } from "./ModelHub";
import { ReviewCenter } from "./ReviewCenter";
import { ToolFabric } from "./ToolFabric";
import { ResearchCenter } from "./ResearchCenter";
import { WorkspaceDashboard } from "./WorkspaceDashboard";
import { buildComposerPayload, toggleComposerSelection, type ComposerDraft } from "./composerModel";
import {
  NAV_ADVANCED,
  NAV_RESOURCES,
  NAV_WORK,
} from "./studioPolishModel";
import { NAVIGABLE_VIEWS, filterViewJumps } from "./productivityModel";
import {
  addToSelection,
  selectionMetadata,
  withKnowledgeContext,
  KNOWLEDGE_CONTEXT_EMPTY,
  KNOWLEDGE_CONTEXT_UNAVAILABLE,
  permissionLine,
  MAX_KNOWLEDGE_CONTEXT_ITEMS,
  type KnowledgeContextItem,
} from "./knowledgeContextModel";
import { knowledgeAssets, fromArtifactItem, type KnowledgeAssetView } from "./knowledgeUxModel";
import { projectCognitiveHistory } from "./crossModuleWorkflowContinuityModel";
import { SEARCH_ENTRY_POINTS } from "./workspaceProductivityAssistanceUxModel";
import { adaptiveReadinessView, ADAPTIVE_BOUNDARY } from "./adaptiveWorkflowReadinessUxModel";
import {
  workflowTemplates,
  templateDetail,
  templateComparison,
  reusePreparation,
  TEMPLATE_BOUNDARY,
  TEMPLATE_EMPTY_STATES,
  COMPARISON_NOTE,
} from "./workflowTemplateReuseUxModel";
import {
  COLLABORATION_BOUNDARIES,
  COLLABORATION_NO_WORKFLOWS,
  projectCollaborationOverview,
  projectActivityFeed,
  reusableWorkflowPatterns,
  projectReviewSummary,
} from "./projectCollaborationWorkspaceUxModel";
import {
  AUTOMATION_EMPTY_STATES,
  automationContinuity,
  automationGovernanceFields,
  occurrenceSummary,
  AUTOMATION_APPROVAL_REQUIREMENT,
  AUTOMATION_EXECUTION_BOUNDARY,
  AUTOMATION_READINESS_NOTE,
} from "./automationReadinessUxModel";
import {
  operationalMetrics,
  projectOperationalEvents,
  operationalHealthCards,
  drillLink,
  OPERATIONAL_STATE_NOTE,
  OPERATIONAL_EMPTY_STATES,
} from "./operationalWorkspaceIntelligenceModel";
import {
  governanceSummary,
  approvalContextFromRequest,
  USER_CONTROL_BOUNDARY,
  privacyIndicator,
  runGovernanceLine,
} from "./consentGovernanceUxModel";
import { resolveRetryOutcome } from "./chatRecoveryModel";
import { pickProjectFolder } from "./projectFolderModel";
import { fetchUpdateManifest } from "./updateNetworking";
import { isUpdateAvailable } from "./updateCheckModel";
import { APP_VERSION } from "./versionLabel";
import {
  artifactCenterFocus,
  AUTOMATION_TRIGGER_EXPLANATION,
  unsupportedTriggerLabel,
  type ArtifactCenterFocus,
} from "./feUxBatchModel";
import {
  FEEDBACK_PATHS,
  LIMITATION_RECORDS,
  RELEASE_RECORD,
  VALIDATION_SCENARIOS,
  betaHealthSummary,
} from "./validationCenterModel";
import {
  commandCenterCards,
  crossModuleTrace,
  unifiedTimeline,
  workspaceHealth,
  COMMAND_CENTER_EMPTY_STATES,
} from "./workspaceCommandModel";
import {
  FEEDBACK_CATEGORIES,
  feedbackModules,
  addFeedbackRecord,
  resolveFeedbackRecord,
  limitationRefinementChains,
  feedbackRecordChain,
  improvementSummary,
  FEEDBACK_PRIVACY,
  type FeedbackRecord,
} from "./feedbackRefinementModel";
import {
  viewChromeLabel,
  auditNavigation,
  BETA_STATUS,
  HELP_RESOURCES,
} from "./consolidationUxModel";
import { participantsFromRun } from "./collaborationInsightsModel";

import type {
  AssetRecord,
  ApprovalRequest,
  AutomationDefinition,
  AutomationOccurrence,
  AutomationSchedulerStatus,
  BenchmarkTask,
  ChatApprovalPlan,
  CognitiveEvent,
  ConversationDocument,
  ConversationSummary,
  CredentialStatus,
  Dashboard,
  HardwareProfile,
  LocalModelCatalog,
  LocalFileCapability,
  LocalPermissionProfile,
  LocalToolSnapshot,
  LocalPathGrant,
  MediaRuntimeStatus,
  WorkspaceChangeEntry,
  WorkspaceFileInfo,
  WorkspaceOpenFile,
  ArtifactMeta,
  ArtifactSourceDoc,
  CollaborationDecisionDocument,
  ModelHubProvider,
  NewModelProfile,
  ProjectState,
  RunJob,
  StudioProjectDocument,
  StudioProjectSummary,
  ModelHealthRecord,
} from "./types";

type View =
  | "dashboard"
  | "chat"
  | "project"
  | "overview"
  | "research"
  | "room"
  | "whiteboard"
  | "files"
  | "artifacts"
  | "engineering"
  | "cost"
  | "benchmarks"
  | "approvals"
  | "logs"
  | "settings"
  | "agents"
  | "automations"
  | "evidence"
  | "skills"
  | "plugins"
  | "review"
  | "workbench"
  | "history"
  | "tools"
  | "permissions"
  | "validation"
  | "workspace"
  | "feedback"
  | "help";

type ChatMode =
  | "solo"
  | "joint"
  | "independent_discuss"
  | "parallel"
  | "adaptive_hybrid";
const CHAT_MODES: readonly ChatMode[] = [
  "solo",
  "joint",
  "independent_discuss",
  "parallel",
  "adaptive_hybrid",
];
type ModelSelectionMode = "auto" | "manual";

const NAVIGATION: Array<{ id: View; label: string; icon: IconName }> = [
  { id: "dashboard", label: "Studio Dashboard", icon: "home" },
  { id: "chat", label: "Chat", icon: "brain" },
  { id: "overview", label: "Overview", icon: "home" },
  { id: "research", label: "Research Center", icon: "room" },
  { id: "artifacts", label: "Artifact Center", icon: "files" },
  { id: "engineering", label: "Engineering Workspace", icon: "files" },
  { id: "room", label: "Research room", icon: "room" },

  { id: "whiteboard", label: "Shared whiteboard", icon: "whiteboard" },
  { id: "files", label: "Files & workspace", icon: "files" },
  { id: "workbench", label: "Engineering workbench", icon: "files" },
  { id: "cost", label: "Cost & compute", icon: "cost" },
  { id: "benchmarks", label: "Benchmarks", icon: "benchmark" },
  { id: "tools", label: "Tool Fabric", icon: "shield" },
  { id: "approvals", label: "Approvals", icon: "shield" },
  { id: "permissions", label: "Computer access", icon: "shield" },
  { id: "review", label: "Review Center", icon: "logs" },
  { id: "logs", label: "Decision log", icon: "logs" },
  { id: "settings", label: "Settings", icon: "settings" },
  { id: "agents", label: "Agents", icon: "agents" },
  { id: "automations", label: "Automations", icon: "automation" },
  { id: "plugins", label: "Plugins", icon: "plugins" },
];


const DEFAULT_GOAL =
  "Derive, prove, and verify a robust solution, explicitly test boundary cases, and resolve every critical objection with evidence.";

const EMPTY_DASHBOARD: Dashboard = { projects: [], runs: [] };
const EMPTY_MODELS: LocalModelCatalog = { provider: "ollama", refreshed: false, models: [] };
const EMPTY_MEDIA_RUNTIMES: MediaRuntimeStatus = {
  ocr: { available: false, runtime: null, scope: "English printed text" },
  transcription: { available: false, runtime: null, model: null, scope: "FLAC, MP3, Ogg, or WAV" },
  network_used: false,
};
const TEAM_STORAGE_KEY = "amca.selected-model-team.v1";
const THEME_STORAGE_KEY = "amca.theme.v1";
type ThemePreference = "system" | "light" | "dark";
function resolveTheme(preference: ThemePreference): "light" | "dark" {
  if (preference !== "system") return preference;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}
const MAX_TEAM_MODELS = 8;
const EMPTY_NEW_MODEL: NewModelProfile = {
  provider_kind: "openai",
  label: "",
  model: "",
  endpoint: "https://api.openai.com/v1",
  pricing_basis: "token",
  input_cost_per_million: 0,
  output_cost_per_million: 0,
  price_per_request_usd: 0,
  secret: "",
};
const PROVIDER_PRESETS: Record<NewModelProfile["provider_kind"], { label: string; endpoint: string; pricing: NewModelProfile["pricing_basis"] }> = {
  openai: { label: "OpenAI", endpoint: "https://api.openai.com/v1", pricing: "token" },
  anthropic: { label: "Anthropic / Claude", endpoint: "https://api.anthropic.com", pricing: "token" },
  kimi: { label: "Kimi / Moonshot", endpoint: "https://api.moonshot.ai/v1", pricing: "token" },
  openai_compatible: { label: "OpenAI-compatible / Groq / other", endpoint: "", pricing: "token" },
  tabitoken: { label: "Tabitoken or fixed-price compatible", endpoint: "https://tabitoken.com/v1", pricing: "request" },
};
type ChatMessage = { id: string; role: "user" | "assistant" | "system"; text: string; meta?: string };

const WELCOME_MESSAGE: ChatMessage = {
  id: "welcome",
  role: "system",
  text: "What would you like to work on? Your conversations and Project sources are saved on this computer.",
};

async function filesFromDrop(dataTransfer: DataTransfer): Promise<File[]> {
  const entries = Array.from(dataTransfer.items)
    .map((item) => (item as unknown as { webkitGetAsEntry?: () => ChromiumFileEntry | null }).webkitGetAsEntry?.() ?? null)
    .filter((entry): entry is ChromiumFileEntry => entry !== null);
  return entries.length
    ? (await Promise.all(entries.map((entry) => filesFromEntry(entry)))).flat()
    : Array.from(dataTransfer.files);
}

// H1: mirrors the backend config fingerprint (kind/ref/endpoint/model) so
// UI badges match exactly what the run pipeline gates on.
function healthKeyBackendSync(kind: string, ref: string, endpoint: string, model: string): string {
  const payload = JSON.stringify({ endpoint, kind, model, ref });
  // FNV-1a is only used for in-memory map bucketing after keys were resolved
  // against the backend SHA-256 map; the async backend-compatible key is the
  // source of truth (see healthKeyBackend).
  let hash = 0x811c9dc5;
  for (let i = 0; i < payload.length; i += 1) {
    hash ^= payload.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16);
}

async function healthKeyBackend(kind: string, ref: string, endpoint: string, model: string): Promise<string> {
  // Must match the backend byte-for-byte: sorted keys, no spaces (Python
  // json.dumps(..., sort_keys=True, separators=(",", ":"))).
  const payload = JSON.stringify({ endpoint, kind, model, ref });
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(payload));
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

function storedTeam(): string[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(TEAM_STORAGE_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function humanize(value: string | undefined | null): string {
  if (!value) return "Not set";
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

function formatMoney(value: number | undefined): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: value && value < 0.01 ? 4 : 2,
    maximumFractionDigits: 4,
  }).format(value ?? 0);
}

function modelPriceLabel(model: CredentialStatus["credentials"][number]): string {
  if (model.pricing_basis === "free") return "Free · one-call limit";
  if (model.pricing_basis === "request") return `${formatMoney(model.price_per_request_usd)} per request`;
  return `${formatMoney(model.input_cost_per_million)} in / ${formatMoney(model.output_cost_per_million)} out per 1M tokens`;
}

function formatBytes(value: number | null | undefined): string {
  if (value == null) return "Unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount.toFixed(unit > 2 ? 1 : 0)} ${units[unit]}`;
}

function valueText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  return JSON.stringify(value);
}

function eventSummary(event: CognitiveEvent): string {
  const preferred = [
    event.payload.content,
    event.payload.summary,
    event.payload.description,
    event.payload.resolution,
    event.payload.accepted_fact,
  ].find((value) => typeof value === "string");
  return typeof preferred === "string"
    ? preferred
    : Object.entries(event.payload)
        .slice(0, 3)
        .map(([key, value]) => `${humanize(key)}: ${valueText(value)}`)
        .join(" · ");
}

function ProgressRing({ value }: { value: number }) {
  const percent = Math.max(0, Math.min(100, Math.round(value * 100)));
  return (
    <div className="progress-ring" style={{ "--progress": `${percent * 3.6}deg` } as React.CSSProperties}>
      <div><strong>{percent}%</strong><span>verified</span></div>
    </div>
  );
}

function EmptyState({ title, detail, action }: { title: string; detail: string; action?: { label: string; onSelect: () => void } }) {
  return (
    <div className="empty-state">
      <div className="empty-glyph"><Icon name="brain" /></div>
      <h3>{title}</h3>
      <p>{detail}</p>
      {action && <button className="empty-next" onClick={action.onSelect}>{action.label}</button>}
    </div>
  );
}

function nextCheckLabel(automation: AutomationDefinition): string {
  if (!automation.enabled) return "Disabled";
  if (automation.schedule.trim().toLowerCase() === "manual") return "Manual";
  if (automation.automation_type === "monitor" || automation.automation_type === "event_triggered") {
    return unsupportedTriggerLabel();
  }
  return automation.next_scheduled_check_at
    ? new Date(automation.next_scheduled_check_at).toLocaleString()
    : "No next check";
}

function occurrenceGlyph(occurrence: AutomationOccurrence): string {
  if (occurrence.status === "succeeded") return "✓";
  if (occurrence.status === "skipped") return "○";
  if (occurrence.status === "running" || occurrence.status === "claimed") return "●";
  return "×";
}

function conditionSummary(occurrence: AutomationOccurrence): string | null {
  const check = occurrence.check;
  if (!check) return null;
  if (check.kind === "file_exists") {
    const evidence = check.evidence as { path?: string; exists?: boolean } | undefined;
    if (evidence && typeof evidence.path === "string") {
      return `Checked: ${evidence.path} — Result: ${evidence.exists ? "Exists" : "Not found"}`;
    }
  }
  if (check.kind === "always") return "Condition: Always — Result: True";
  if (check.result === "error") return `Condition error: ${check.error ?? "unknown"}`;
  return `Condition: ${check.kind} — ${check.result}`;
}

function AutomationCard({
  automation,
  schedulerRunning,
  starting,
  onRunNow,
  onToggle,
  onDelete,
}: {
  automation: AutomationDefinition;
  schedulerRunning: boolean | null;
  starting: boolean;
  onRunNow: (automation: AutomationDefinition) => void;
  onToggle: (automation: AutomationDefinition) => void;
  onDelete: (automationId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [occurrences, setOccurrences] = useState<AutomationOccurrence[] | null>(null);
  const toggleHistory = () => {
    const next = !expanded;
    setExpanded(next);
    if (next && occurrences === null) {
      void amca
        .automationOccurrences(automation.id)
        .then((document) => setOccurrences(document.occurrences))
        .catch(() => setOccurrences([]));
    }
  };
  return (
    <article key={automation.id}>
      <div>
        <StatusPill status={automation.enabled ? "enabled" : "disabled"} />
        <span>{humanize(automation.automation_type)}</span>
      </div>
      <h3>{automation.name}</h3>
      <p>{automation.goal}</p>
      {(() => {
        const continuity = automationContinuity(automation);
        return continuity.hasWorkflowLink ? (
          <p className="settings-note">Workflow run: <code>{continuity.lastRunId}</code> — {continuity.artifactsNote}</p>
        ) : (
          <p className="settings-note">{continuity.unavailableReason}</p>
        );
      })()}
      {(automation.automation_type === "monitor" || automation.automation_type === "event_triggered") && (
        <p className="settings-note">{AUTOMATION_TRIGGER_EXPLANATION}</p>
      )}
      <dl>
        <div><dt>Project</dt><dd>{automation.project_id}</dd></div>
        <div><dt>Schedule</dt><dd>{automation.schedule}</dd></div>
        <div><dt>Trigger capability</dt><dd>{automationGovernanceFields({ enabled: automation.enabled, triggerType: automation.automation_type, schedulerRunning: schedulerRunning }).triggerCapability}</dd></div>
        <div><dt>Approval requirement</dt><dd>{AUTOMATION_APPROVAL_REQUIREMENT}</dd></div>
        <div><dt>Execution boundary</dt><dd>{AUTOMATION_EXECUTION_BOUNDARY}</dd></div>
        <div><dt>Next check</dt><dd>{nextCheckLabel(automation)}</dd></div>
        <div><dt>Last checked</dt><dd>{automation.last_check_at ? relativeTime(automation.last_check_at) : "Never"}</dd></div>
        <div><dt>Last action</dt><dd>{automation.last_run_at ? relativeTime(automation.last_run_at) : "Never"}</dd></div>
        <div><dt>Last result</dt><dd>{automation.latest_occurrence_status ? humanize(automation.latest_occurrence_status) : "No activity yet"}{automation.latest_error ? ` — ${automation.latest_error}` : ""}</dd></div>
        <div><dt>Updated</dt><dd>{relativeTime(automation.updated_at)}</dd></div>
      </dl>
      <div className="automation-actions">
        <button onClick={() => onRunNow(automation)} disabled={starting}>Run now</button>
        <button onClick={() => onToggle(automation)}>{automation.enabled ? "Disable" : "Enable"}</button>
        <button className="danger" onClick={() => onDelete(automation.id)}>Delete</button>
        <button onClick={toggleHistory}>{expanded ? "Hide activity" : "Recent activity"}</button>
      </div>
      {expanded ? (
        <div className="automation-history">
          {occurrences === null ? (
            <p className="settings-note">Loading recent activity…</p>
          ) : occurrences.length === 0 ? (
            <p className="settings-note">{AUTOMATION_EMPTY_STATES.no_history} No recorded occurrences yet.</p>
          ) : (
            <>
              <p className="settings-note">{occurrenceSummary(occurrences).total} recorded occurrence(s).</p>
              <ul>
                {occurrences.map((occurrence) => {
                  const summary = conditionSummary(occurrence);
                  return (
                    <li key={occurrence.id}>
                      <span aria-hidden>{occurrenceGlyph(occurrence)}</span>{" "}
                      <span>{new Date(occurrence.scheduled_for).toLocaleString()}</span>{" "}
                      <span>{humanize(occurrence.status)}</span>
                      {summary ? <span> — {summary}</span> : null}
                      {occurrence.run_id ? <span> — run {occurrence.run_id.slice(0, 8)}</span> : null}
                      {occurrence.error ? <span> — {occurrence.error.slice(0, 160)}</span> : null}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>
      ) : null}
    </article>
  );
}

function AutostartToggle() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void invoke<boolean>("autostart_status")
      .then((value) => setEnabled(value))
      .catch((issue) => setError(String(issue)));
  }, []);
  const update = async (next: boolean) => {
    try {
      const actual = await invoke<boolean>(next ? "autostart_enable" : "autostart_disable");
      setEnabled(actual); // truthful registered state, never the requested one
      setError(null);
    } catch (issue) {
      setError(String(issue)); // keep previous state visible on failure
    }
  };
  return (
    <label className="autostart-toggle" style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <input
        type="checkbox"
        checked={enabled === true}
        disabled={enabled === null}
        onChange={(event) => void update(event.target.checked)}
      />
      Start AMCA Studio when I sign in
      {error ? <span role="alert">— {error.slice(0, 140)}</span> : null}
    </label>
  );
}

function StatusPill({ status }: { status: string }) {
  const key = status.toLowerCase().replaceAll("_", "-");
  return <span className={`status-pill status-${key}`}><i />{humanize(status)}</span>;
}

function MetricCard({ label, value, detail, tone = "neutral" }: { label: string; value: string; detail: string; tone?: string }) {
  return (
    <article className={`metric-card tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

export default function App() {
  const [view, setView] = useState<View>("chat");
  const [previousView, setPreviousView] = useState<View>("chat");
  const [openThreadMenuId, setOpenThreadMenuId] = useState<string | null>(null);
  const [viewJumpOpen, setViewJumpOpen] = useState(false);
  const [viewJumpQuery, setViewJumpQuery] = useState("");
  const [researchInspection, setResearchInspection] = useState<{title:string;value:unknown}|null>(null);
  const [previewAsset, setPreviewAsset] = useState<AssetRecord | null>(null);
  const [browserUrl, setBrowserUrl] = useState("");
  const [leftOpen, setLeftOpen] = useState(() => window.innerWidth >= 1000 && localStorage.getItem("amca.left") !== "false");
  // 19B1 Amendment 4: sidebar navigation upgrade state (all view-local, no new backend subsystem)
  const [moreNavOpen, setMoreNavOpen] = useState(false);
  const [projectsSectionOpen, setProjectsSectionOpen] = useState(true);
  // 19B1 Am4 (8) revised: expansion is its own state, not a side effect of
  // the view. Opening a chat under a project used to collapse the project
  // because the expansion was derived from view === "project"; now only the
  // project row itself opens and closes it.
  const [expandedProjectId, setExpandedProjectId] = useState<string | null>(null);
  const [projectsShowAll, setProjectsShowAll] = useState(false);
  const [chatsSectionOpen, setChatsSectionOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(() => localStorage.getItem("amca.right") === "true");
  const [inspectorView, setInspectorView] = useState<View>("room");
  const [inspectorWidth, setInspectorWidth] = useState(() => Math.max(320, Math.min(680, Number(localStorage.getItem("amca.inspectorWidth")) || 400)));
  const [settingsCategory, setSettingsCategory] = useState("Models & Providers");
  const inspect = (target: View) => { setInspectorView(target); setInspectorOpen(true); };
  const useSkillInComposer = (name: string, text: string) => { setPrompt((current) => `${current}\n\n$${name}\n${text}`.trim()); setView("chat"); setInspectorOpen(false); };
  // Round 24 ZCode-style panel: open tabs + active tab (null = New tab
  // launcher grid). Tabs are instances, so two Terminal tabs are two
  // independent PowerShell sessions.
  const [panelTabs, setPanelTabs] = useState<PanelTab[]>([]);
  const [activePanelKey, setActivePanelKey] = useState<string | null>(null);
  const panelCounter = useRef(0);
  const openPanelFacility = (facility: PanelFacilityId) => {
    // Gate F2: terminals are per-tab sessions, so every Terminal open
    // gets a fresh tab; other facilities reuse their single tab.
    const existing =
      facility === "terminal"
        ? undefined
        : panelTabs.find((tab) => tab.facility === facility);
    if (existing) {
      setActivePanelKey(existing.key);
      return;
    }
    panelCounter.current += 1;
    const key = `panel-${panelCounter.current}`;
    setPanelTabs([...panelTabs, { key, facility }]);
    setActivePanelKey(key);
  };
  const closePanelTab = (key: string) => {
    const index = panelTabs.findIndex((tab) => tab.key === key);
    const next = panelTabs.filter((tab) => tab.key !== key);
    setPanelTabs(next);
    if (activePanelKey === key)
      setActivePanelKey(next[Math.max(0, index - 1)]?.key ?? null);
  };
  const openSettings = (category?: string) => {
    if (view !== "settings") {
      setPreviousView(view);
    }
    if (category) {
      // Callers pass a section name ("About & Updates"); navigation now shows
      // groups, so map the section to the group that contains it.
      setSettingsCategory(settingsGroupFor(category));
    }
    setView("settings");
  };
  useEffect(() => { localStorage.setItem("amca.left", String(leftOpen)); }, [leftOpen]);
  useEffect(() => { localStorage.setItem("amca.right", String(inspectorOpen)); }, [inspectorOpen]);
  useEffect(() => { localStorage.setItem("amca.inspectorWidth", String(inspectorWidth)); }, [inspectorWidth]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (openThreadMenuId) {
          setOpenThreadMenuId(null);
          return;
        }
        if (viewJumpOpen) {
          setViewJumpOpen(false);
          return;
        }
        if (inspectorOpen) {
          setInspectorOpen(false);
          return;
        }
        if (view === "settings") {
          setView(previousView || "chat");
          return;
        }
        if (window.innerWidth < 1100 && leftOpen) {
          setLeftOpen(false);
          return;
        }
      }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [openThreadMenuId, viewJumpOpen, inspectorOpen, view, previousView, leftOpen]);
  useEffect(() => {
    if (!openThreadMenuId) return;
    const handleOutsideClick = (event: MouseEvent | PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target?.closest(".thread-menu")) {
        setOpenThreadMenuId(null);
      }
    };
    window.addEventListener("pointerdown", handleOutsideClick);
    return () => window.removeEventListener("pointerdown", handleOutsideClick);
  }, [openThreadMenuId]);
  useEffect(() => { if(window.innerWidth<1000)setLeftOpen(false); },[view]);
  const [agentsTab] = useState<AgentLibraryTab>("monitor");
  // P5 productivity: Ctrl+K view-jump palette + accelerators.
  // W1: recorded focus carried from the Dashboard's Recent outputs deep link.
  const [artifactFocus, setArtifactFocus] = useState<ArtifactCenterFocus | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard>(EMPTY_DASHBOARD);
  const [project, setProject] = useState<ProjectState | null>(null);
  const [events, setEvents] = useState<CognitiveEvent[]>([]);
  const [assets, setAssets] = useState<AssetRecord[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [localTools, setLocalTools] = useState<LocalToolSnapshot | null>(null);
  const [localApprovals, setLocalApprovals] = useState<ApprovalRequest[]>([]);

  // Round 27 (owner order): three simple access modes, ZCode-style. They
  // map onto the existing backend profiles — nothing about enforcement,
  // approvals, or the agent's boundaries changes; only what the UI offers
  // and what it is called. Restricted is no longer offered (existing
  // projects keep working and can still be switched out of it).
  const ACCESS_MODES: Array<{ id: LocalPermissionProfile; title: string; detail: string }> = [
    { id: "standard", title: "Ask before changes", detail: "Works in the project folder; asks before file changes." },
    { id: "extended", title: "Autonomous mode", detail: "Intelligent mode — keeps working, asks when there is risk." },
    { id: "full_computer", title: "Full access", detail: "The whole PC: every fixed drive, any chat. Fewer confirmations." },
  ];
  const [permissionBusy, setPermissionBusy] = useState(false);
  const [permissionProfileDraft, setPermissionProfileDraft] = useState<LocalPermissionProfile>("standard");
  const [automationDefinitions, setAutomationDefinitions] = useState<AutomationDefinition[]>([]);
  const [automationSchedulerStatus, setAutomationSchedulerStatus] = useState<AutomationSchedulerStatus | null>(null);
  const [showAutomationForm, setShowAutomationForm] = useState(false);
  // 16A P3: manual model selection supports multiple usable models through a
  // compact checkbox dropdown; the checked set feeds the existing
  // selectedTeam/participant contract (no second collaboration engine).
  // 19B1 D1: the menu renders in a FIXED layer above every composer surface —
  // position is computed from the trigger button each open, so it can never be
  // clipped by composer/ancestor overflow, and Escape closes it.
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  // v1.1.8 Phase 9: the composer Plus menu, and the file input it triggers.
  const chatFileInputRef = useRef<HTMLInputElement | null>(null);
  const chatComposerRef = useRef<HTMLTextAreaElement | null>(null);

  /** R6: run one + menu entry. Every entry has a real handler. */
  const runComposerAction = (id: string) => {
    if (id === "attach") chatFileInputRef.current?.click();
    else if (id === "commands") { setViewJumpOpen(true); setViewJumpQuery(""); }
    else if (id === "context") void openKnowledgePicker();
    else if (id === "skills") inspect("skills");
  };

  // R5: the footer Check-for-Updates icon highlights when a newer release is
  // published. The check is the same read-only manifest fetch the About &
  // Updates panel performs — best-effort, no auto-download, no install; an
  // offline or failed check simply leaves the icon unhighlighted.
  const [plusMenuOpen, setPlusMenuOpen] = useState(false);
  // v1.1.15: the + menu is a real popup anchored to its button, and it
  // closes on Escape and on a window resize, exactly like the model menu.
  const plusButtonRef = useRef<HTMLButtonElement | null>(null);
  const [plusMenuPosition, setPlusMenuPosition] = useState<{ left: number; bottom: number }>({ left: 0, bottom: 0 });
  const updatePlusMenuPosition = useCallback(() => {
    const rect = plusButtonRef.current?.getBoundingClientRect();
    if (!rect) return;
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - 312));
    setPlusMenuPosition({ left, bottom: window.innerHeight - rect.top + 6 });
  }, []);
  useEffect(() => {
    if (!plusMenuOpen) return;
    updatePlusMenuPosition();
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.stopPropagation(); setPlusMenuOpen(false); }
    };
    window.addEventListener("keydown", onEscape, true);
    window.addEventListener("resize", updatePlusMenuPosition);
    return () => {
      window.removeEventListener("keydown", onEscape, true);
      window.removeEventListener("resize", updatePlusMenuPosition);
    };
  }, [plusMenuOpen, updatePlusMenuPosition]);
  const [updateAvailable, setUpdateAvailable] = useState(false);
  // R6: the Effort selector — scales this run's bounded frontier-call
  // ceiling. Persisted like the other composer choices.
  const [composerEffort, setComposerEffort] = useState<"low" | "high" | "max">(() => {
    const stored = window.localStorage.getItem("amca.effort");
    return stored === "high" || stored === "max" ? stored : "low";
  });
  useEffect(() => {
    window.localStorage.setItem("amca.effort", composerEffort);
  }, [composerEffort]);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await fetchUpdateManifest();
        if (!cancelled && result.success && isUpdateAvailable(APP_VERSION, result.manifest.version)) {
          setUpdateAvailable(true);
        }
      } catch {
        // Offline or unreachable: the icon stays unhighlighted.
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const modelMenuRef = useRef<HTMLDivElement | null>(null);
  const modelButtonRef = useRef<HTMLButtonElement | null>(null);
  const [modelMenuPosition, setModelMenuPosition] = useState<{ left: number; bottom: number }>({ left: 0, bottom: 0 });
  const updateModelMenuPosition = useCallback(() => {
    const rect = modelButtonRef.current?.getBoundingClientRect();
    if (!rect) return;
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - 332));
    setModelMenuPosition({ left, bottom: window.innerHeight - rect.top + 6 });
  }, []);
  useEffect(() => {
    if (!modelMenuOpen) return;
    updateModelMenuPosition();
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.stopPropagation(); setModelMenuOpen(false); }
    };
    window.addEventListener("keydown", onEscape, true);
    window.addEventListener("resize", updateModelMenuPosition);
    return () => {
      window.removeEventListener("keydown", onEscape, true);
      window.removeEventListener("resize", updateModelMenuPosition);
    };
  }, [modelMenuOpen, updateModelMenuPosition]);
  const [automationDraft, setAutomationDraft] = useState({ name: "", goal: "", automation_type: "once" as AutomationDefinition["automation_type"], schedule: "manual", enabled: false });
  const [profile, setProfile] = useState<HardwareProfile | null>(null);
  const [benchmarks, setBenchmarks] = useState<BenchmarkTask[]>([]);
  const [activeRun, setActiveRun] = useState<RunJob | null>(null);
  // Response-UX R5: live thinking feed for the in-flight run (AMCA's
  // voice lines + elapsed), polled alongside the run status and cleared
  // when the run settles — like ZCode's thinking block, it disappears on
  // the final answer.
  const [runActivity, setRunActivity] = useState<string[]>([]);
  const [runElapsed, setRunElapsed] = useState<string>("");
  // Gate-2 fix: approval/automation relative timestamps ("4 seconds ago")
  // must keep ticking instead of freezing until an unrelated re-render.
  const [, setClockTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setClockTick((value) => value + 1), CLOCK_TICK_MS);
    return () => window.clearInterval(timer);
  }, []);
  const [workspaceFileList, setWorkspaceFileList] = useState<WorkspaceFileInfo[]>([]);
  const [workspaceChangesList, setWorkspaceChangesList] = useState<WorkspaceChangeEntry[]>([]);
  const [workspaceEditor, setWorkspaceEditor] = useState<WorkspaceOpenFile | null>(null);
  const [workspaceDraft, setWorkspaceDraft] = useState("");
  const [workspaceNotice, setWorkspaceNotice] = useState<string | null>(null);
  const [artifactList, setArtifactList] = useState<ArtifactMeta[]>([]);
  const [sessionFeedback, setSessionFeedback] = useState<FeedbackRecord[]>([]);
  const [feedbackCategory, setFeedbackCategory] = useState("ux");
  const [feedbackModule, setFeedbackModule] = useState("");
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackNotice, setFeedbackNotice] = useState<string | null>(null);
  const [artifactView, setArtifactView] = useState<{ meta: ArtifactMeta; source: ArtifactSourceDoc; editing: boolean; draft: string; notice: string | null } | null>(null);
  const [argvDraft, setArgvDraft] = useState("git, --version");
  const [commandApprovalId, setCommandApprovalId] = useState("");
  const [commandRunLog, setCommandRunLog] = useState("");
  const [commandBusy, setCommandBusy] = useState(false);
  const [chatArtifactChips, setChatArtifactChips] = useState<ArtifactMeta[]>([]);
  const [artifactDownloadNotice, setArtifactDownloadNotice] = useState<{ artifact_id: string; text: string } | null>(null);
  const [focusedArtifactId, setFocusedArtifactId] = useState("");
  const [commandGrants, setCommandGrants] = useState<LocalPathGrant[]>([]);
  const [selectedCommandGrant, setSelectedCommandGrant] = useState<LocalPathGrant | null>(null);
  const [models, setModels] = useState<LocalModelCatalog>(EMPTY_MODELS);
  const [mediaRuntimes, setMediaRuntimes] = useState<MediaRuntimeStatus>(EMPTY_MEDIA_RUNTIMES);
  const [credentialStatus, setCredentialStatus] = useState<CredentialStatus | null>(null);
  const [credentialDrafts, setCredentialDrafts] = useState<Record<string, string>>({});
  const [credentialBusy, setCredentialBusy] = useState<string | null>(null);
  const [showAddModel, setShowAddModel] = useState(false);
  const [newModel, setNewModel] = useState<NewModelProfile>(EMPTY_NEW_MODEL);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [studioProjects, setStudioProjects] = useState<StudioProjectSummary[]>([]);
  const [activeStudioProject, setActiveStudioProject] = useState<StudioProjectDocument | null>(null);
  const [showProjectForm, setShowProjectForm] = useState(false);
  // v1.2.0: the Project folder is part of the draft — it defaults to
  // Documents\<name> and the operator may pick any location (or leave the
  // prefill; the backend creates the folder either way).
  const [projectDraft, setProjectDraft] = useState({ name: "", description: "", instructions: "", template: "general", root_path: "" });
  const [projectFolderEdited, setProjectFolderEdited] = useState(false);
  const [projectFolderBusy, setProjectFolderBusy] = useState(false);
  // Guards the name->folder prefill against out-of-order responses.
  const projectFolderRequest = useRef(0);
  const [projectPendingDelete, setProjectPendingDelete] = useState<StudioProjectSummary | null>(null);
  const [projectDeleteBusy, setProjectDeleteBusy] = useState(false);
  const [projectFileDragging, setProjectFileDragging] = useState(false);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [conversationSearch, setConversationSearch] = useState("");
  const [conversationBusy, setConversationBusy] = useState(false);
  const [threadDialog,setThreadDialog]=useState<{action:"rename"|"delete";conversation:ConversationSummary}|null>(null);
  const [selectedTeam, setSelectedTeam] = useState<string[]>(storedTeam);
  const [hubProviders, setHubProviders] = useState<ModelHubProvider[]>([]);
  const [chatMode, setChatMode] = useState<ChatMode>("solo");
  const [modelSelectionMode, setModelSelectionMode] = useState<ModelSelectionMode>("auto");
  // v1.1.8 Phase 2: the Default Auto preference a NEW conversation starts from.
  // A restored conversation still uses its own recorded policy.  A ref is used
  // because the value is read from an async boot closure, not rendered here.
  const defaultAutoRef = useRef(true);
  // v1.1.8 Phase 9: the interface text size, applied to the document root so
  // every layout scales from one place.
  const [uiFontScale, setUiFontScale] = useState<string>("standard");
  const [cloudApprovalAutomatic, setCloudApprovalAutomatic] = useState<boolean>(false);

  const chooseCloudApprovalAutomatic = async (enabled: boolean) => {
    const previous = cloudApprovalAutomatic;
    setCloudApprovalAutomatic(enabled);
    try {
      const result = await amca.setPreferences({ cloud_approval_automatic: enabled });
      setCloudApprovalAutomatic(Boolean(result.preferences.cloud_approval_automatic));
    } catch (caught) {
      setCloudApprovalAutomatic(previous);
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };
  useEffect(() => {
    document.documentElement.dataset.uiScale = uiFontScale;
  }, [uiFontScale]);

  const chooseUiFontScale = async (scale: string) => {
    const previous = uiFontScale;
    setUiFontScale(scale);
    try {
      const result = await amca.setPreferences({ ui_font_scale: scale });
      setUiFontScale(result.preferences.ui_font_scale);
    } catch (caught) {
      setUiFontScale(previous);
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };
  // H1: observed model health; zero runnable models means Chat guides the
  // user to Model Hub instead of manufacturing a failed run.
  const [modelHealth, setModelHealth] = useState<Record<string, ModelHealthRecord> | null>(null);
  const [verifyingModels, setVerifyingModels] = useState(false);

  // H1: per-config health lookup. Fingerprints are computed asynchronously
  // (SHA-256) once per credential/hub/local list change; a configuration
  // without a recorded verdict counts as UNVERIFIED, never silently usable.
  const [healthKeyMap, setHealthKeyMap] = useState<Map<string, ModelHealthRecord>>(new Map());
  useEffect(() => {
    let cancelled = false;
    const configs: Array<{ kind: string; ref: string; endpoint: string; model: string }> = [];
    for (const entry of credentialStatus?.credentials ?? []) {
      configs.push({ kind: "profile", ref: entry.credential_id, endpoint: entry.endpoint, model: entry.model });
    }
    for (const provider of hubProviders) {
      for (const model of provider.model_ids) {
        configs.push({ kind: "hub", ref: `${provider.id}:${model}`, endpoint: provider.endpoint, model });
      }
    }
    for (const item of models.models) {
      configs.push({ kind: "local", ref: `ollama:${item.model}`, endpoint: "local://ollama", model: item.model });
    }
    void Promise.all(
      configs.map((config) =>
        healthKeyBackend(config.kind, config.ref, config.endpoint, config.model).then(
          (backendKey) => [healthKeyBackendSync(config.kind, config.ref, config.endpoint, config.model), modelHealth?.[backendKey] ?? null] as [string, ModelHealthRecord | null],
        ),
      ),
    ).then((resolved) => {
      if (cancelled) return;
      const next = new Map<string, ModelHealthRecord>();
      for (const [syncKey, record] of resolved) {
        if (record) next.set(syncKey, record);
      }
      setHealthKeyMap(next);
    });
    return () => { cancelled = true; };
  }, [modelHealth, credentialStatus, hubProviders, models]);
  const healthFor = (kind: string, ref: string, endpoint: string, model: string): ModelHealthRecord | null =>
    healthKeyMap.get(healthKeyBackendSync(kind, ref, endpoint, model)) ?? null;
  const runnableModelCount = (() => {
    if (!modelHealth) return null; // unknown until loaded
    const direct = credentialStatus?.credentials ?? [];
    let count = 0;
    for (const entry of direct) {
      if (!entry.configured) continue;
      // 33A usability repair (mirrors the backend gate): a configuration with
      // no recorded verdict is usable — the backend re-verifies on demand at
      // run time — and only a CONFIGURATION DEFECT (rejected credential,
      // model id that does not exist) excludes a model. A transient failure
      // (provider down, no answer in time) must not empty the pool: the
      // backend re-verifies before it gives up on a run.
      const record = healthFor("profile", entry.credential_id, entry.endpoint, entry.model);
      if (record && isConfigurationDefect(record.state)) continue;
      count += 1;
    }
    for (const provider of hubProviders) {
      if (!provider.configured || provider.enabled === false) continue;
      // Only published models count as usable; a hidden model is not runnable.
      for (const model of provider.enabled_model_ids ?? []) {
        const record = healthFor("hub", `${provider.id}:${model}`, provider.endpoint, model);
        if (record && isConfigurationDefect(record.state)) continue;
        count += 1;
      }
    }
    for (const item of models.models) {
      if (models.runtime_reachable === false) continue;
      // 09C (DEF-09B-1): a reachable local runtime with a non-oversized fit
      // verdict IS the live health truth for local models — a persisted
      // verified-ready record also counts, and a fresh verified-not-runnable
      // record excludes the model. Inventory presence alone never suffices.
      if (item.fit === 2) continue;
      const record = healthFor("local", `ollama:${item.model}`, "local://ollama", item.model);
      if (record?.runnable) {
        count += 1;
        continue;
      }
      if (record && !record.runnable) continue;
      count += 1;
    }
    return count;
  })();
  // v0.6.0 M0: explicit collaboration architecture for manual runs. "auto"
  // preserves the previous behavior exactly (backend decides; default chat
  // stays easy). Any explicit value is an existing backend mode identifier.
  const [explicitChatMode, setExplicitChatMode] = useState<ChatMode | "auto">("auto");
  // The architecture follows the SELECTION: one model can only run Solo,
  // two or more can run every multi-model architecture — and Solo would
  // silently drop one of them, so it is not offered there.
  const availableArchitectures = collaborationArchitectureOptions(selectedTeam.length);
  useEffect(() => {
    if (availableArchitectures.includes(explicitChatMode as CollaborationArchitecture)) return;
    const next = availableArchitectures[0];
    setExplicitChatMode(next as ChatMode);
    if (next !== "auto") changeChatMode(next as ChatMode);
  }, [availableArchitectures.join(","), explicitChatMode]);
  // v0.6.0 M0.1: in-chat intelligence controls for Adaptive Hybrid. The
  const [pendingChat, setPendingChat] = useState<ChatApprovalPlan | null>(null);
  const [selectedModel, setSelectedModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState<File[]>([]);
  // Knowledge context (explicit, user-controlled): picked from the recorded
  // artifact registry; rides in the run goal as a transparent annotation.
  const [knowledgePickerOpen, setKnowledgePickerOpen] = useState(false);
  const [knowledgePickerItems, setKnowledgePickerItems] = useState<KnowledgeAssetView[] | null>(null);
  const [knowledgePickerState, setKnowledgePickerState] = useState<"idle" | "loading" | "empty" | "unavailable">("idle");
  const [knowledgeSelection, setKnowledgeSelection] = useState<KnowledgeContextItem[]>([]);
  const [draggingAttachments, setDraggingAttachments] = useState(false);
  const [workbenchTab, setWorkbenchTab] = useState<"files" | "changes" | "terminal" | "tests" | "git">("files");
  const [selectedWorkbenchAsset, setSelectedWorkbenchAsset] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([
    WELCOME_MESSAGE,
  ]);
  // Gate-2 fix: new messages (and replies) land below the fold without an
  // auto-scroll, which read as "the response is missing" until the user
  // scrolled or reloaded the conversation.
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = transcriptRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages]);
  const [goal, setGoal] = useState(DEFAULT_GOAL);
  const [online, setOnline] = useState(false);
  const [, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [composeBusy, setComposeBusy] = useState(false);
  const [themePreference, setThemePreference] = useState<ThemePreference>(() => {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
  });
  const [projectTab, setProjectTab] = useState<"overview" | "research" | "artifacts" | "history" | "settings">("overview");
  const [projectAssets, setProjectAssets] = useState<AssetRecord[] | null>(null);
  const [projectSessions, setProjectSessions] = useState<Array<Record<string, unknown>> | null>(null);
  const [projectSettingsDraft, setProjectSettingsDraft] = useState<{ name: string; description: string; instructions: string } | null>(null);
  useEffect(() => {
    const apply = () => {
      document.documentElement.dataset.theme = resolveTheme(themePreference);
    };
    apply();
    localStorage.setItem(THEME_STORAGE_KEY, themePreference);
    if (themePreference !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: light)");
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [themePreference]);

  // P5 productivity accelerators: Ctrl+K view jump, Ctrl+1…6 direct view
  // access, Ctrl+, settings. Only real view switches — no execution bypass.
  useEffect(() => {
    const JUMP_VIEWS: View[] = ["dashboard", "chat", "overview", "research", "agents", "artifacts"];
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.ctrlKey && !event.altKey && !event.metaKey && !event.shiftKey) {
        const key = event.key.toLowerCase();
        if (key === "k") {
          event.preventDefault();
          setViewJumpOpen((open) => !open);
          setViewJumpQuery("");
          return;
        }
        if (key === ",") {
          event.preventDefault();
          setView("settings");
          return;
        }
        if (/^[1-6]$/.test(event.key)) {
          event.preventDefault();
          setView(JUMP_VIEWS[Number(event.key) - 1]);
        }
        return;
      }
      if (event.key === "Escape") {
        setViewJumpOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (view !== "project" || !activeStudioProject) return;
    const projectId = activeStudioProject.project_id;
    if (projectTab === "artifacts" && projectAssets === null) {
      void amca.assets(projectId)
        .then((result) => setProjectAssets(result.assets))
        .catch(() => setProjectAssets([]));
    }
    if (projectTab === "research" && projectSessions === null) {
      void amca.listResearchSessions(projectId)
        .then((result) => setProjectSessions(result.sessions as unknown as Array<Record<string, unknown>>))
        .catch(() => setProjectSessions([]));
    }
  }, [view, projectTab, activeStudioProject]);

  const saveProjectSettings = async () => {
    if (!activeStudioProject || !projectSettingsDraft) return;
    setConversationBusy(true);
    setError(null);
    try {
      await amca.saveStudioProject({
        project_id: activeStudioProject.project_id,
        name: projectSettingsDraft.name,
        description: projectSettingsDraft.description,
        instructions: projectSettingsDraft.instructions,
      });
      setActiveStudioProject(await amca.studioProject(activeStudioProject.project_id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setConversationBusy(false);
    }
  };
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [projectNameError, setProjectNameError] = useState<string | null>(null);
  const [, setReviewUnreadCount] = useState<number>(0);
  const permissionProjectId = activeStudioProject?.project_id ?? project?.project_id ?? (activeConversationId ? (`conv-${activeConversationId}`) : null);

  useEffect(() => {
    window.localStorage.setItem(TEAM_STORAGE_KEY, JSON.stringify(selectedTeam));
  }, [selectedTeam]);

  // W1: when a new deep-link focus arrives, re-open the artifacts view is
  // handled by the click handler; this effect only clears the stale focus
  // marker once the user has navigated elsewhere manually.
  useEffect(() => {
    if (view !== "artifacts" && artifactFocus) setArtifactFocus(null);
  }, [view, artifactFocus]);

  useEffect(() => {
    if (models.runtime_reachable !== false) return;
    setSelectedTeam((current) => current.filter((item) => !item.startsWith("ollama:")));
    if (selectedModel.startsWith("ollama:")) setSelectedModel("");
  }, [models.runtime_reachable, selectedModel]);

  const toggleTeamModel = (participantId: string) => {
    const next = selectedTeam.includes(participantId)
      ? selectedTeam.filter((item) => item !== participantId)
      : selectedTeam.length >= MAX_TEAM_MODELS ? selectedTeam : [...selectedTeam, participantId];
    setSelectedTeam(next);
    if (activeConversationId && chatMode === "joint") {
      void amca.updateConversation(activeConversationId, { mode: "joint", participant_ids: next })
        .then(({ conversation }) => setConversations((current) => current.map((item) => item.conversation_id === conversation.conversation_id ? conversation : item)))
        .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught)));
    }
  };

  const changeChatMode = (mode: ChatMode) => {
    setChatMode(mode);
    if (!activeConversationId) return;
    const participantIds = mode === "solo" ? [selectedModel].filter(Boolean) : selectedTeam;
    void amca.updateConversation(activeConversationId, { mode, participant_ids: participantIds })
      .then(({ conversation }) => setConversations((current) => current.map((item) => item.conversation_id === conversation.conversation_id ? conversation : item)))
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught)));
  };

  const saveCredential = async (credentialId: string) => {
    const secret = credentialDrafts[credentialId]?.trim() ?? "";
    if (!secret) return;
    setCredentialBusy(credentialId);
    setError(null);
    try {
      setCredentialStatus(await amca.saveCredential(credentialId, secret));
      setCredentialDrafts((current) => ({ ...current, [credentialId]: "" }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setCredentialBusy(null);
    }
  };

  const removeCredential = async (credentialId: string) => {
    setCredentialBusy(credentialId);
    setError(null);
    try {
      setCredentialStatus(await amca.deleteCredential(credentialId));
      setSelectedTeam((current) => current.filter((item) => item !== credentialId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setCredentialBusy(null);
    }
  };

  const changeProviderPreset = (provider: NewModelProfile["provider_kind"]) => {
    const preset = PROVIDER_PRESETS[provider];
    setNewModel((current) => ({
      ...current,
      provider_kind: provider,
      endpoint: preset.endpoint,
      pricing_basis: preset.pricing,
      input_cost_per_million: 0,
      output_cost_per_million: 0,
      price_per_request_usd: 0,
    }));
  };

  const addModelProfile = async (event: React.FormEvent) => {
    event.preventDefault();
    setCredentialBusy("new-model");
    setError(null);
    try {
      const result = await amca.createModelProfile(newModel);
      setCredentialStatus(result.status);
      setSelectedModel(result.profile.credential_id);
      setChatMode("solo");
      setNewModel(EMPTY_NEW_MODEL);
      setShowAddModel(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setCredentialBusy(null);
    }
  };

  const deleteModelProfile = async (profileId: string, label: string) => {
    if (!window.confirm(`Remove “${label}” and its saved credential from AMCA?`)) return;
    setCredentialBusy(profileId);
    setError(null);
    try {
      const result = await amca.deleteModelProfile(profileId);
      setCredentialStatus(result.status);
      setSelectedTeam((current) => current.filter((item) => item !== profileId));
      if (selectedModel === profileId) setSelectedModel("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setCredentialBusy(null);
    }
  };

  const loadProject = useCallback(async (projectId: string) => {
    const [nextProject, eventDocument, assetDocument, approvalDocument] = await Promise.all([
      amca.project(projectId),
      amca.events(projectId),
      amca.assets(projectId),
      amca.approvals(projectId),
    ]);
    setProject(nextProject);
    setEvents(eventDocument.events);
    setAssets(assetDocument.assets);
    setApprovals(approvalDocument.approvals);
  }, []);

  const loadConversation = useCallback(async (conversationId: string) => {
    const document: ConversationDocument = await amca.conversation(conversationId);
    const summary = document.conversation;
    setActiveConversationId(summary.conversation_id);
    setChatMode(summary.mode);
    const restoredPolicyManual = localStorage.getItem(`amca.policy.${summary.conversation_id}`) === "manual";
    const restoredParticipants = summary.participant_ids.filter(Boolean);
    setSelectedTeam(restoredParticipants);
    // 10B (DEF-10A-1): restoring a manual-policy conversation whose recorded
    // participants are empty (or were dropped by history) previously left
    // manual mode with an empty team, so chatReady stayed false and Run was
    // permanently disabled ("Choose a model below"). Fall back to Auto
    // Intelligence whenever there is no concrete participant set to restore.
    if (restoredPolicyManual && restoredParticipants.length >= 1) {
      setModelSelectionMode("manual");
      // v1.1.8 Phase 3: a reloaded manual conversation shows the architecture
      // it actually recorded.  Leaving the selector on "Auto · AMCA decides"
      // displayed a mode the backend was not going to run.
      setExplicitChatMode(
        CHAT_MODES.includes(summary.mode as ChatMode) ? (summary.mode as ChatMode) : "auto",
      );
    } else {
      setModelSelectionMode("auto");
      setExplicitChatMode("auto");
    }
    if (summary.mode === "solo") {
      setSelectedModel(restoredParticipants[0] ?? "");
    } else {
      setSelectedTeam(restoredParticipants);
    }
    setMessages(document.messages.length ? document.messages.map((message) => ({
      id: message.message_id,
      role: message.role,
      text: message.content,
      meta: message.meta ?? undefined,
    })) : [WELCOME_MESSAGE]);

    if (summary.workspace_project_id) {
      setActiveStudioProject(await amca.studioProject(summary.workspace_project_id));
    } else {
      setActiveStudioProject(null);
    }

    if (summary.project_id) {
      await loadProject(summary.project_id);
    } else {
      setProject(null);
      setEvents([]);
      setAssets([]);
      setApprovals([]);
    }

    if (summary.active_run_id) {
      const run = await amca.run(summary.active_run_id);
      setActiveRun(["queued", "running", "cancelling"].includes(run.status) ? run : null);
    } else {
      setActiveRun(null);
    }

    if (summary.status === "waiting_approval" && summary.project_id) {
      const [approvalDocument, credentials] = await Promise.all([
        amca.approvals(summary.project_id),
        amca.credentialStatus(),
      ]);
      const pending = approvalDocument.approvals.filter((item) => ["pending", "approved"].includes(item.status));
      if (pending.length) {
        const maxCost = summary.participant_ids.reduce((total, participantId) => (
          total + (credentials.credentials.find((item) => item.credential_id === participantId)?.price_per_request_usd ?? 0)
        ), 0);
        setPendingChat({
          status: "waiting_for_approval",
          project_id: summary.project_id,
          conversation_id: summary.conversation_id,
          mode: summary.mode,
          participant_ids: summary.participant_ids,
          approval_ids: pending.map((item) => item.id),
          approvals: pending,
          max_api_cost_usd: maxCost,
          network_started: false,
          selection_policy: "manual_model_set",
        });
      } else {
        setPendingChat(null);
      }
    } else {
      setPendingChat(null);
    }
  }, [loadProject]);

  const refreshConversationList = useCallback(async (search = "") => {
    const document = await amca.conversations(search);
    setConversations(document.conversations);
    return document.conversations;
  }, []);

  const loadLocalPermissions = useCallback(async (projectId: string) => {
    const [snapshot, approvalDocument] = await Promise.all([
      amca.localTools(projectId),
      amca.approvals(projectId),
    ]);
    setLocalTools(snapshot);
    setLocalApprovals(approvalDocument.approvals);
    setPermissionProfileDraft(snapshot.profile.profile);
  }, []);

  const refresh = useCallback(async (): Promise<boolean> => {
    setError(null);
    try {
      setVerifyingModels(true);
      const [nextDashboard, nextProfile, benchmarkDocument, modelDocument, mediaRuntimeDocument, credentials, conversationDocument, hubDocument, automationDocument, studioProjectDocument, nextSchedulerStatus, reviewStatus] = await Promise.all([
        amca.dashboard(),
        amca.profile(),
        amca.benchmarks(),
        amca.localModels(),
        amca.mediaRuntimes(),
        amca.credentialStatus(),
        amca.conversations(conversationSearch),
        amca.modelHub(),
        amca.automations(),
        amca.studioProjects(),
        amca.schedulerStatus().catch(() => null),
        amca.reviewQueueStatus().catch(() => ({ unread: 0 })),
      ]);
      setDashboard(nextDashboard);
      setProfile(nextProfile);
      setBenchmarks(benchmarkDocument.tasks);
      setModels(modelDocument);
      setMediaRuntimes(mediaRuntimeDocument);
      setCredentialStatus(credentials);
      setHubProviders(hubDocument.byok_providers);
      setVerifyingModels(false);
      setModelHealth(hubDocument.model_health ?? null);
      setAutomationDefinitions(automationDocument.automations);
      setAutomationSchedulerStatus(nextSchedulerStatus);
      setReviewUnreadCount(reviewStatus?.unread ?? 0);
      setStudioProjects(studioProjectDocument.projects);
      try {
        const prefs = (await amca.preferences()).preferences;
        defaultAutoRef.current = prefs.default_auto;
        setUiFontScale(prefs.ui_font_scale || "standard");
        setCloudApprovalAutomatic(Boolean(prefs.cloud_approval_automatic));
      } catch { /* the shipped default stays in effect */ }
      const availableSoloIds = [
        ...modelDocument.models.filter((item) => item.fit !== 2).map((item) => `ollama:${item.model}`),
        ...credentials.credentials.filter((item) => item.configured).map((item) => item.credential_id),
        ...hubDocument.byok_providers.filter((item) => item.configured && item.enabled !== false).flatMap((item) => (item.enabled_model_ids ?? []).map((model) => `${item.id}:${model}`)),
      ];
      setSelectedTeam((current) => current.filter((participantId) => availableSoloIds.includes(participantId)));
      if (!selectedModel || !availableSoloIds.includes(selectedModel)) {
        setSelectedModel(availableSoloIds[0] ?? "");
      }
      let nextConversations = conversationDocument.conversations;
      if (!nextConversations.length && !conversationSearch.trim()) {
        const created = await amca.createConversation(
          "solo",
          availableSoloIds[0] ? [availableSoloIds[0]] : [],
        );
        nextConversations = [created.conversation];
        // A new chat honours the Default Auto control.
        setModelSelectionMode(defaultAutoRef.current ? "auto" : "manual");
      }
      setConversations(nextConversations);
      setOnline(true);
      const conversationId = (
        activeConversationId && nextConversations.some((item) => item.conversation_id === activeConversationId)
          ? activeConversationId
          : nextConversations[0]?.conversation_id
      );
      if (conversationId) await loadConversation(conversationId);
      return true;
    } catch (caught) {
      setOnline(false);
      setError(caught instanceof Error ? caught.message : String(caught));
      return false;
    } finally {
      setLoading(false);
    }
  }, [activeConversationId, conversationSearch, loadConversation, selectedModel]);

  useEffect(() => {
    // Startup race: the shell mounts before the backend has bound its port
    // (fresh starts lose this race by about a second), which left the model
    // lists empty until a manual reload. Retry the initial load a few times
    // before surfacing the offline error.
    let cancelled = false;
    const boot = async () => {
      for (let attempt = 0; attempt < 5; attempt += 1) {
        if (cancelled) return;
        const ok = await refresh();
        if (cancelled) return;
        if (ok) return;
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
      }
    };
    void boot();
    return () => { cancelled = true; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // 19B1 D2/D3: selecting an off-screen chat or project scrolls it into view
  // inside its own sidebar scroll region — rows never hide behind the footer
  // and the page itself never scrolls.
  useEffect(() => {
    if (!activeConversationId) return;
    const row = document.querySelector(`[data-conversation-id="${activeConversationId}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [activeConversationId, view]);
  useEffect(() => {
    if (!activeStudioProject || view !== "project") return;
    const row = document.querySelector(`[data-project-id="${activeStudioProject.project_id}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [activeStudioProject, view, studioProjects.length]);

  // 11A (owner defect: local models missing from the chat selector): if the
  // model/provider truth was fetched while the Ollama runtime was unreachable,
  // both the Local tier (empty inventory) and the Cloud/BYOK tier (stale
  // configured/hub state) go stale together. On chat-view entry with either
  // tier stale, run the canonical full refresh (the same bounded path Settings
  // uses) so both tiers update — not a new polling loop.
  // 11A1 (DEF-11A1-2): provider/model configuration changes made in Model Hub
  // (or outside the app) while the local runtime is healthy must also be
  // reflected when the user returns to Chat — Model Hub state lives in its own
  // component and never reaches the composer. The canonical full refresh now
  // runs once per chat-view entry (still bounded, single-flight, no polling
  // loop), covering local AND cloud/provider tiers.
  useEffect(() => {
    if (view === "chat" && !verifyingModels && !localBusy) {
      void refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  useEffect(() => {
    if (!permissionProjectId) {
      setLocalTools(null);
      setLocalApprovals([]);
      return;
    }
    void loadLocalPermissions(permissionProjectId).catch((caught: unknown) => {
      setError(caught instanceof Error ? caught.message : String(caught));
    });
  }, [loadLocalPermissions, permissionProjectId]);

  // Native Windows Notification Dispatcher (B1.4C)
  const dispatchPendingNotifications = useCallback(async () => {
    try {
      const pendingRes = await amca.listPendingNotifications("windows_native", 5);
      const candidates = pendingRes.pending_notifications ?? [];
      if (!candidates.length) return;

      for (const item of candidates) {
        // Atomic claim before dispatch
        const claimRes = await amca.claimNotification(item.review_item_id, "windows_native");
        if (!claimRes.claimed) continue;

        let issued = false;
        let issueError: string | undefined;
        try {
          if ("__TAURI_INTERNALS__" in window || "__TAURI__" in window) {
            const { invoke } = await import("@tauri-apps/api/core");
            await invoke("send_native_notification", {
              title: item.title,
              body: item.body,
            });
            issued = true;
          } else if ("Notification" in window && Notification.permission === "granted") {
            new Notification(item.title, { body: item.body });
            issued = true;
          } else {
            issued = false;
            issueError = "Notification permission not granted or desktop environment unavailable";
          }
        } catch (err) {
          issued = false;
          issueError = err instanceof Error ? err.message : String(err);
        }

        await amca.recordNotificationResult(
          item.review_item_id,
          issued,
          "windows_native",
          issueError
        );
      }
    } catch {
      // Quiet background polling
    }
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void dispatchPendingNotifications();
      void amca.reviewQueueStatus().then((status) => {
        setReviewUnreadCount(status.unread ?? 0);
      }).catch(() => {});
    }, 3000);
    return () => window.clearInterval(timer);
  }, [dispatchPendingNotifications]);

  useEffect(() => {
    if (!activeRun || !["queued", "running", "cancelling"].includes(activeRun.status)) return;
    const timer = window.setInterval(() => {
      void amca.run(activeRun.run_id).then(async (next) => {
        setActiveRun(next);
        // Response-UX R5: refresh the thinking feed on the same 700ms
        // tick.  A feed failure never fails the run poll — the static
        // "AMCA is thinking" line stays as the fallback.
        try {
          const activity = await amca.runActivity(activeRun.run_id);
          if (Array.isArray(activity.lines)) setRunActivity(activity.lines.slice(-8));
          setRunElapsed(typeof activity.elapsed === "string" ? activity.elapsed : "");
        } catch { /* keep the last feed lines */ }
        if (!["queued", "running", "cancelling"].includes(next.status)) {
          setRunActivity([]);
          setRunElapsed("");
          if (activeConversationId) await loadConversation(activeConversationId);
          await refreshConversationList(conversationSearch);
          setDashboard(await amca.dashboard());
        }
      }).catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : String(caught));
      });
    }, 700);
    return () => window.clearInterval(timer);
  }, [activeConversationId, activeRun, conversationSearch, loadConversation, refreshConversationList]);

  const refreshWorkspaceData = async (projectId: string) => {
    try {
      const [files, changes] = await Promise.all([
        amca.workspaceFiles(projectId),
        amca.workspaceChanges(projectId, 20),
      ]);
      setWorkspaceFileList(files.files);
      setWorkspaceChangesList(changes.changes);
      try {
        const arts = await amca.artifactsList(projectId);
        setArtifactList(arts.artifacts);
        const authoredCount = Number(
          (latestRunRef.current?.result as { artifacts_authored?: number } | null)
            ?.artifacts_authored ?? 0,
        );
        setChatArtifactChips(arts.artifacts.slice(0, Math.max(0, authoredCount)));
      } catch {
        setArtifactList([]);
        setChatArtifactChips([]);
      }
      try {
        const snapshot = await amca.localTools(projectId);
        const usable = snapshot.grants.filter(
          (grant) =>
            !grant.revoked_at &&
            grant.capabilities.includes("propose_command" as LocalFileCapability),
        );
        setCommandGrants(usable);
        setSelectedCommandGrant((current: LocalPathGrant | null) => {
          if (current && usable.some((grant) => grant.grant_id === current.grant_id)) return current;
          return usable[0] ?? null;
        });
      } catch {
        setCommandGrants([]);
      }
    } catch {
      setWorkspaceFileList([]);
      setWorkspaceChangesList([]);
    }
  };

  useEffect(() => {
    if (view !== "workbench" || !project?.project_id) return;
    void refreshWorkspaceData(project.project_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, project?.project_id]);

  const openWorkspaceFile = async (path: string) => {
    if (!project?.project_id) return;
    setError(null);
    try {
      const opened = await amca.workspaceOpen(project.project_id, path);
      setWorkspaceEditor(opened.file);
      setWorkspaceDraft(opened.file.content);
      setWorkspaceNotice(null);
    } catch (caught) {
      setWorkspaceNotice(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const saveWorkspaceFile = async () => {
    if (!project?.project_id || !workspaceEditor) return;
    setError(null);
    try {
      const saved = await amca.workspaceSave(
        project.project_id,
        workspaceEditor.path,
        workspaceDraft,
        workspaceEditor.version,
      );
      setWorkspaceNotice(
        `${saved.file.created ? "Created" : "Saved"} ${saved.file.path} as v${saved.file.version}.`,
      );
      setWorkspaceEditor({ path: saved.file.path, content: workspaceDraft, version: saved.file.version });
      await refreshWorkspaceData(project.project_id);
    } catch (caught) {
      setWorkspaceNotice(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const revertWorkspaceFile = async (path: string, version: number) => {
    if (!project?.project_id) return;
    setError(null);
    try {
      const reverted = await amca.workspaceRevert(project.project_id, path, version);
      setWorkspaceNotice(`Reverted ${path} to v${version} as v${reverted.file.version}.`);
      await refreshWorkspaceData(project.project_id);
    } catch (caught) {
      setWorkspaceNotice(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const openArtifact = async (meta: ArtifactMeta, editing = false) => {
    setError(null);
    try {
      const doc = await amca.artifactSource(project!.project_id, meta.artifact_id);
      setArtifactView({ meta: { ...meta, current_version: doc.source.version }, source: doc.source, editing, draft: editing ? doc.source.content : "", notice: null });
    } catch (caught) {
      setWorkspaceNotice(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const saveArtifactEdit = async () => {
    if (!project?.project_id || !artifactView) return;
    try {
      const saved = await amca.artifactEdit(
        project.project_id,
        artifactView.meta.artifact_id,
        artifactView.draft,
        artifactView.source.version,
      );
      setArtifactView((current) => current ? { ...current, meta: saved.artifact, source: { version: saved.artifact.current_version, content: artifactView.draft }, editing: false, notice: `Saved as v${saved.artifact.current_version}.` } : current);
      await refreshWorkspaceData(project.project_id);
    } catch (caught) {
      setArtifactView((current) => current ? { ...current, notice: caught instanceof Error ? caught.message : String(caught) } : current);
    }
  };

  const exportArtifactAs = async (format: string) => {
    if (!project?.project_id || !artifactView) return;
    try {
      const result = await amca.artifactExport(project.project_id, artifactView.meta.artifact_id, format);
      if (result.export.status === "exported") {
        setArtifactView((current) => current ? { ...current, notice: `Exported v${result.export.version} to ${format} (${result.export.bytes} bytes).` } : current);
      } else {
        setArtifactView((current) => current ? { ...current, notice: `Converter unavailable: ${result.export.reason ?? format}` } : current);
      }
    } catch (caught) {
      setArtifactView((current) => current ? { ...current, notice: caught instanceof Error ? caught.message : String(caught) } : current);
    }
  };

  /**
   * Save a generated artifact to disk from the chat transcript.
   *
   * Tries the real rendition first (docx/pdf/pptx/xlsx), and when no converter
   * is installed falls back to the canonical source under its true extension,
   * saying so.  The user always gets a file, and never a silent no-op.
   */
  const downloadArtifactFromChat = async (meta: ArtifactMeta) => {
    setArtifactDownloadNotice({ artifact_id: meta.artifact_id, text: "Preparing…" });
    const report = (text: string) => setArtifactDownloadNotice({ artifact_id: meta.artifact_id, text });
    try {
      const type = String(meta.artifact_type || "").toLowerCase();
      const rendition = RENDITION_ARTIFACT_TYPES.has(type) ? type : "";
      if (rendition) {
        const result = await amca.artifactExport(meta.project_id, meta.artifact_id, rendition);
        if (result.export.data_base64) {
          const bytes = Uint8Array.from(atob(result.export.data_base64), (char) => char.charCodeAt(0));
          const outcome = await saveFile(
            artifactFileName(meta.title, type, rendition),
            new Blob([bytes], { type: "application/octet-stream" }),
          );
          report(outcome.ok ? `Saved ${outcome.path || "file"}` : `Could not save: ${outcome.reason ?? "unknown error"}`);
          return;
        }
      }
      const doc = await amca.artifactSource(meta.project_id, meta.artifact_id);
      const extension = canonicalSourceExtension(type);
      const outcome = await saveFile(artifactFileName(meta.title, type, extension), doc.source.content);
      if (!outcome.ok) {
        report(`Could not save: ${outcome.reason ?? "unknown error"}`);
        return;
      }
      report(rendition
        ? `No ${rendition} converter available — saved the ${extension} source instead.`
        : `Saved ${outcome.path || `file.${extension}`}`);
    } catch (caught) {
      report(`Download failed: ${caught instanceof Error ? caught.message : String(caught)}`);
    }
  };

  const restoreArtifactVersion = async (version: number) => {    if (!project?.project_id || !artifactView) return;
    try {
      await amca.artifactRestore(project.project_id, artifactView.meta.artifact_id, version);
      const doc = await amca.artifactSource(project.project_id, artifactView.meta.artifact_id);
      setArtifactView((current) => current ? { ...current, meta: { ...current.meta, current_version: doc.source.version }, source: doc.source, notice: `Restored v${version} as v${doc.source.version}.` } : current);
      await refreshWorkspaceData(project.project_id);
    } catch (caught) {
      setArtifactView((current) => current ? { ...current, notice: caught instanceof Error ? caught.message : String(caught) } : current);
    }
  };

  const proposeArgvCommand = async () => {
    if (!project?.project_id) return;
    setError(null);
    const argv = argvDraft.split(",").map((item) => item.trim()).filter(Boolean);
    if (!argv.length || !selectedCommandGrant) return;
    setCommandBusy(true);
    try {
      const proposed = await amca.proposeArgvCommand({
        project_id: project.project_id,
        grant_id: selectedCommandGrant.grant_id,
        argv,
        reason: `Terminal run of ${argv[0]}`,
      });
      setCommandApprovalId(String(proposed.approval.id));
      setCommandRunLog(
        `Proposed ${argv.join(" ")}\nApproval ${proposed.approval.id} is waiting in the Review Queue. Approve it there, then press Run.`,
      );
    } catch (caught) {
      setCommandRunLog(`Refused: ${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setCommandBusy(false);
    }
  };

  const runApprovedArgvCommand = async () => {
    if (!project?.project_id || !selectedCommandGrant || !commandApprovalId.trim()) return;
    setCommandBusy(true);
    try {
      let snapshot = await amca.runArgvCommand({
        project_id: project.project_id,
        grant_id: selectedCommandGrant.grant_id,
        approval_id: commandApprovalId.trim(),
        timeout_seconds: 60,
      });
      setCommandRunLog(`$ ${snapshot.argv.join(" ")}\n${snapshot.output}`);
      const deadline = Date.now() + 65_000;
      while (snapshot.status === "running" && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 300));
        snapshot = (await amca.commandRunSnapshot(snapshot.run_id)).run;
        setCommandRunLog(`$ ${snapshot.argv.join(" ")}\n${snapshot.output}`);
      }
      setCommandRunLog(
        `$ ${snapshot.argv.join(" ")}\n${snapshot.output}\n[${snapshot.status}${snapshot.exit_code !== null ? ` · exit ${snapshot.exit_code}` : ""}${snapshot.truncated ? " · truncated" : ""}]`,
      );
    } catch (caught) {
      setCommandRunLog(`Refused: ${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setCommandBusy(false);
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshConversationList(conversationSearch).catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : String(caught));
      });
    }, 220);
    return () => window.clearTimeout(timer);
  }, [conversationSearch, refreshConversationList]);

  const refreshModels = async () => {
    setError(null);
    try {
      const document = await amca.refreshLocalModels();
      setModels(document);
      const hub=await amca.modelHub(); setModelHealth(hub.model_health ?? null);
      if (selectedModel.startsWith("ollama:") && !document.models.some((item) => `ollama:${item.model}` === selectedModel)) {
        setSelectedModel(document.models.find((item) => item.fit !== 2) ? `ollama:${document.models.find((item) => item.fit !== 2)!.model}` : "");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  // 32F Fix 2 — Retry re-resolution. The error banner's Retry re-probes the
  // providers and refreshes Model Hub availability before it touches the
  // banner: a still-broken condition keeps a truthful error, and when a model
  // is available again the held request proceeds instead of a no-op.
  const retryFailure = async () => {
    if (retrying) return;
    setRetrying(true);
    try {
      const [localDocument, hubDocument, credentials] = await Promise.all([
        amca.refreshLocalModels(),
        amca.modelHub(),
        amca.credentialStatus(),
      ]);
      setModels(localDocument);
      setModelHealth(hubDocument.model_health ?? null);
      setHubProviders(hubDocument.byok_providers);
      setCredentialStatus(credentials);
      const canResume = Boolean(
        (prompt.trim() || pendingAttachments.length) &&
        !pendingChat &&
        !(activeRun && ["queued", "running", "cancelling"].includes(activeRun.status)),
      );
      const resolution = resolveRetryOutcome({
        localModels: localDocument.models,
        credentials: credentials.credentials,
        hubProviders: hubDocument.byok_providers,
        selectedModel,
        canResume,
      });
      setSelectedModel(resolution.nextSelectedModel);
      if (resolution.outcome === "blocked") {
        setError(resolution.error);
        return;
      }
      await refresh();
      if (resolution.outcome === "execute") {
        await startChat({ availabilityVerified: true });
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRetrying(false);
    }
  };

  /**
   * Create a conversation.
   *
   * `target` is explicit because the destination was previously implicit: with a
   * project open, every new chat silently joined that project and there was no
   * way to start a standalone one.  "standalone" always creates outside any
   * project; "project" always creates inside the open one.
   */
  const createNewConversation = async (target: "standalone" | "project" = "project") => {
    setConversationBusy(true);
    setError(null);
    try {
      const workspaceProjectId = target === "standalone" ? undefined : activeStudioProject?.project_id;
      const participantIds = modelSelectionMode === "auto" ? [] : selectedTeam;
      const executionMode: ChatMode = participantIds.length > 1 ? "joint" : "solo";
      const created = await amca.createConversation(executionMode, participantIds, workspaceProjectId);
      setConversationSearch("");
      setConversations((current) => [created.conversation, ...current]);
      await loadConversation(created.conversation.conversation_id);
      if (workspaceProjectId) setStudioProjects((await amca.studioProjects()).projects);
      setPrompt("");
      setPendingAttachments([]);
      setView("chat");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setConversationBusy(false);
    }
  };

  /**
   * Delete a project after the confirmation dialog.
   *
   * The dialog states what happens to the project's chats before anything is
   * removed, and the UI is refreshed from the server afterwards so the sidebar
   * reflects what actually happened rather than what we assumed happened.
   */
  const confirmDeleteStudioProject = async (project: StudioProjectSummary) => {
    setProjectDeleteBusy(true);
    setError(null);
    try {
      await amca.deleteStudioProject(project.project_id);
      if (activeStudioProject?.project_id === project.project_id) {
        setActiveStudioProject(null);
        setView("chat");
      }
      if (expandedProjectId === project.project_id) setExpandedProjectId(null);
      setStudioProjects((current) => current.filter((entry) => entry.project_id !== project.project_id));
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setProjectDeleteBusy(false);
      setProjectPendingDelete(null);
    }
  };

  const openStudioProject = async (projectId: string) => {    setProjectsSectionOpen(true); // 19B1 Am4 (8): selecting a project reveals it even if Projects was collapsed
    setExpandedProjectId(projectId);
    setConversationBusy(true);
    setError(null);
    try {
      setActiveStudioProject(await amca.studioProject(projectId));
      setProjectTab("overview");
      setProjectAssets(null);
      setProjectSessions(null);
      setView("project");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setConversationBusy(false);
    }
  };

  /**
   * v1.2.0: keep the Project folder prefill in step with the name until the
   * user edits it. The default itself is computed by the backend, so the path
   * shown is exactly the one that will be created.
   */
  const refreshProjectFolderDefault = async (name: string) => {
    if (projectFolderEdited) return;
    const generation = ++projectFolderRequest.current;
    try {
      const { default_folder } = await amca.studioProjectDefaultFolder(name);
      // Only the newest request may write: a response for an earlier name
      // that arrives late must not overwrite the current one.
      if (generation !== projectFolderRequest.current) return;
      setProjectDraft((current) => (
        current.root_path === default_folder ? current : { ...current, root_path: default_folder }
      ));
    } catch {
      // A missing prefill is cosmetic: the backend still defaults the folder
      // when none is supplied, so creation is never blocked by this.
    }
  };

  /** Clear the New Project form so a cancelled draft cannot reappear later. */
  const resetProjectDraft = () => {
    projectFolderRequest.current += 1;
    setProjectDraft({ name: "", description: "", instructions: "", template: "general", root_path: "" });
    setProjectFolderEdited(false);
    setProjectNameError(null);
  };

  /** v1.2.0: open the native folder chooser for the Project being created. */
  const chooseProjectFolder = async () => {
    setProjectFolderBusy(true);
    setProjectNameError(null);
    try {
      const outcome = await pickProjectFolder(projectDraft.root_path);
      if (outcome.ok) {
        setProjectFolderEdited(true);
        setProjectDraft((current) => ({ ...current, root_path: outcome.path }));
      } else if (outcome.reason) {
        setProjectNameError(outcome.reason);
      }
    } finally {
      setProjectFolderBusy(false);
    }
  };

  const createStudioProject = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = projectDraft.name.trim();
    if (!trimmed) {
      setProjectNameError("Project name is required.");
      return;
    }
    if (studioProjects.some((p) => p.name.trim().toLowerCase() === trimmed.toLowerCase())) {
      setProjectNameError("A project with this name already exists.");
      return;
    }
    setProjectNameError(null);
    setConversationBusy(true);
    setError(null);
    try {
      const { project: created } = await amca.saveStudioProject({
        ...projectDraft,
        name: trimmed,
        root_path: projectDraft.root_path.trim(),
      });
      const projects = (await amca.studioProjects()).projects;
      setStudioProjects(projects);
      setActiveStudioProject(await amca.studioProject(created.project_id));
      setProjectDraft({ name: "", description: "", instructions: "", template: "general", root_path: "" });
      setProjectFolderEdited(false);
      setShowProjectForm(false);
      setView("project");
    } catch (caught) {
      const msg = caught instanceof Error ? caught.message : String(caught);
      setProjectNameError(msg);
      setError(msg);
    } finally {
      setConversationBusy(false);
    }
  };

  const saveStudioProjectInstructions = async () => {
    if (!activeStudioProject) return;
    setConversationBusy(true);
    try {
      await amca.saveStudioProject(activeStudioProject);
      setActiveStudioProject(await amca.studioProject(activeStudioProject.project_id));
      setStudioProjects((await amca.studioProjects()).projects);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setConversationBusy(false);
    }
  };

  const addStudioProjectFiles = async (files: File[]) => {
    if (!activeStudioProject || !files.length) return;
    setUploading(true);
    setError(null);
    try {
      for (const file of files.slice(0, MAX_CHAT_ATTACHMENTS)) {
        if (file.size > MAX_ATTACHMENT_BYTES) throw new Error(`${file.name} exceeds the 25 MB file limit.`);
        await amca.uploadStudioAsset(activeStudioProject.project_id, file);
      }
      setActiveStudioProject(await amca.studioProject(activeStudioProject.project_id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setUploading(false);
      setProjectFileDragging(false);
    }
  };

  const moveConversationToProject = async (conversation: ConversationSummary, projectId: string | null) => {
    try {
      const { conversation: updated } = await amca.updateConversation(conversation.conversation_id, { workspace_project_id: projectId });
      setConversations((current) => current.map((item) => item.conversation_id === updated.conversation_id ? updated : item));
      setStudioProjects((await amca.studioProjects()).projects);
      if (activeConversationId===conversation.conversation_id) { setActiveStudioProject(projectId ? await amca.studioProject(projectId) : null); setView("chat"); }
      else if(activeStudioProject)setActiveStudioProject(await amca.studioProject(activeStudioProject.project_id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const chooseConversation = async (conversationId: string) => {
    setChatsSectionOpen(true); // 19B1 Am4 (10): activating a chat reveals it even if Chats was collapsed
    setConversationBusy(true);
    setError(null);
    try {
      await loadConversation(conversationId);
      setPrompt("");
      setPendingAttachments([]);
      setView("chat");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setConversationBusy(false);
    }
  };

  const renameConversation = (conversation:ConversationSummary) => setThreadDialog({action:"rename",conversation});
  const saveConversationTitle = async (conversation: ConversationSummary,title:string) => {
    if (!title || title === conversation.title) return;
    try {
      const updated = await amca.updateConversation(conversation.conversation_id, { title });
      setConversations((current) => current.map((item) => item.conversation_id === updated.conversation.conversation_id ? updated.conversation : item));
      if(activeStudioProject)setActiveStudioProject(await amca.studioProject(activeStudioProject.project_id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const deleteConversation = (conversation:ConversationSummary) => setThreadDialog({action:"delete",conversation});
  const confirmDeleteConversation = async (conversation: ConversationSummary) => {
    setConversationBusy(true);
    try {
      await amca.deleteConversation(conversation.conversation_id);
      setConversationSearch("");
      const remaining = await refreshConversationList("");
      if (conversation.workspace_project_id) {
        setStudioProjects((await amca.studioProjects()).projects);
        if (activeStudioProject?.project_id === conversation.workspace_project_id) {
          setActiveStudioProject(await amca.studioProject(conversation.workspace_project_id));
        }
      }
      if (activeConversationId === conversation.conversation_id) {
        if (remaining.length) await loadConversation(remaining[0].conversation_id);
        else await createNewConversation();
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setConversationBusy(false);
    }
  };

  // Knowledge context: load the recorded artifact registry on demand (the
  // user opens the picker — no background retrieval), then attach explicitly.
  const openKnowledgePicker = async () => {
    setKnowledgePickerOpen(true);
    if (knowledgePickerItems !== null) return;
    setKnowledgePickerState("loading");
    try {
      const projectId = activeStudioProject?.project_id ?? project?.project_id;
      if (!projectId) {
        setKnowledgePickerState("empty");
        setKnowledgePickerItems([]);
        return;
      }
      const document = await amca.listArtifacts(projectId);
      const assets = knowledgeAssets((document.artifacts || []).map(fromArtifactItem));
      setKnowledgePickerItems(assets);
      setKnowledgePickerState(assets.length === 0 ? "empty" : "idle");
    } catch {
      setKnowledgePickerItems([]);
      setKnowledgePickerState("unavailable");
    }
  };

  const attachKnowledge = (asset: KnowledgeAssetView) => {
    const result = addToSelection(knowledgeSelection, asset);
    if (result.ok) setKnowledgeSelection(result.items);
  };

  const removeKnowledge = (artifactId: string) => {
    setKnowledgeSelection((current) => current.filter((item) => item.artifactId !== artifactId));
  };

  const clearKnowledgeSelection = () => {
    setKnowledgeSelection([]);
    setKnowledgePickerOpen(false);
  };

  const startChat = async (options: { availabilityVerified?: boolean } = {}) => {
    const message = prompt.trim() || (pendingAttachments.length ? "Analyze the attached files and answer with evidence." : "");
    const participants = modelSelectionMode === "auto" ? [] : selectedTeam;
    const executionMode: ChatMode =
      modelSelectionMode === "manual" && explicitChatMode !== "auto"
        ? explicitChatMode
        : participants.length > 1
          ? "joint"
          : "solo";
    if (starting || (activeRun && ["queued", "running", "cancelling"].includes(activeRun.status))) return;
    // v1.1.8: a fresh chat has no conversation yet, and the previous early
    // return made Run do nothing at all with a typed message. Create the
    // conversation here (keeping the typed message) and send into it.
    let conversationIdForSend = activeConversationId;
    if (!conversationIdForSend) {
      if (!message) return;
      try {
        const created = await amca.createConversation(
          executionMode, participants, activeStudioProject?.project_id,
        );
        conversationIdForSend = created.conversation.conversation_id;
        setConversations((current) => [created.conversation, ...current]);
        await loadConversation(conversationIdForSend);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
        return;
      }
    }
    if (!message || (modelSelectionMode === "manual" && (!participants.length || !participants.every(Boolean)))) return;
    // 32F Fix 2: the retry path has just re-probed live availability in this
    // same action, so it must not be re-judged by stale pre-probe state; the
    // backend health verification stays authoritative for the actual run.
    if (!options.availabilityVerified) {
      // H1: zero runnable models -> guide to Model Hub, never a failed run.
      if (runnableModelCount === 0) {
        setError("No usable model is configured. Open Model Hub to add or repair a model connection.");
        setView("settings");
        return;
      }
      // H1: never manufacture a failed run from an unusable configuration.
      if (modelSelectionMode === "manual" && modelHealth) {
        const configuredModels = credentialStatus?.credentials ?? [];
        const blocked = participants
          .map((ref) => {
            const entry = configuredModels.find((item) => item.profile_id === ref);
            if (!entry) return null;
            const key = healthKeyBackendSync("profile", ref, entry.endpoint, entry.model);
            const record = modelHealth[key];
            // The operator reads this message, so it speaks the same words
            // as the badge and the backend refusal: never the raw state
            // constant.
            return record && isConfigurationDefect(record.state)
              ? `${ref} (${composerModelHealth(record).label})`
              : null;
          })
          .filter(Boolean) as string[];
        const usable = participants.filter((ref) => !blocked.some((item) => item.startsWith(ref)));
        if (!usable.length) {
          setError(`No usable model is configured. The selected model(s) failed verification: ${blocked.join(", ")}. Open Model Hub to add or repair a model connection.`);
          return;
        }
      }
    }
    localStorage.setItem(`amca.policy.${conversationIdForSend}`,modelSelectionMode);
    setStarting(true);
    setError(null);
    try {
      const result = await amca.startChat(
        withKnowledgeContext(message, knowledgeSelection),
        participants,
        executionMode,
        modelSelectionMode === "auto" ? "auto_intelligence" : "manual_model_set",
        permissionProjectId ?? undefined,
        [],
        conversationIdForSend,
        pendingAttachments,
        activeStudioProject?.project_id,
        composerEffort,
      );
      setPrompt("");
      setPendingAttachments([]);
      setKnowledgeSelection([]);
      if (result.status === "waiting_for_approval") {
        setPendingChat(result);
        await loadConversation(result.conversation_id);
        await refreshConversationList(conversationSearch);
        inspect("approvals");
      } else {
        setActiveRun(result);
        await loadConversation(conversationIdForSend);
        await refreshConversationList(conversationSearch);
      }
    } catch (caught) {
      setMessages((current) => [...current, { id: `error-${Date.now()}`, role: "system", text: caught instanceof Error ? caught.message : String(caught) }]);
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setStarting(false);
    }
  };

  // Z2 — Studio Task Composer: reuses the exact governed chat-run pipeline
  // (createConversation → POST /api/chat-runs). Approval plans surface in
  // the existing Approvals view; runs land in the existing chat room.
  const composeTask = async (draft: ComposerDraft): Promise<"submitted" | "approval"> => {
    const decision = buildComposerPayload(draft);
    if (!decision.ok) throw new Error(decision.errors.join(" "));
    setComposeBusy(true);
    setError(null);
    try {
      const payload = decision.payload;
      const conversation = await amca.createConversation(payload.mode, payload.participant_ids, payload.workspace_project_id ?? undefined);
      const result = await amca.startChat(
        payload.goal,
        payload.participant_ids,
        payload.mode,
        payload.selection_policy,
        payload.workspace_project_id ?? (`conv-${conversation.conversation.conversation_id}`),
        [],
        conversation.conversation.conversation_id,
        [],
        payload.workspace_project_id ?? undefined,
      );
      if (result.status === "waiting_for_approval") {
        setPendingChat(result);
        await refreshConversationList(conversationSearch);
        setView("approvals");
        return "approval";
      }
      setActiveRun(result);
      await refreshConversationList(conversationSearch);
      setView("chat");
      return "submitted";
    } finally {
      setComposeBusy(false);
    }
  };

  const executePendingChat = async () => {
    if (!pendingChat) return;
    setStarting(true);
    setError(null);
    try {
      const result = await amca.startChat(
        project?.goal ?? "",
        pendingChat.participant_ids,
        pendingChat.mode,
        pendingChat.selection_policy,
        pendingChat.project_id,
        pendingChat.approval_ids,
        pendingChat.conversation_id,
      );
      if (result.status === "waiting_for_approval") throw new Error("Approved run unexpectedly requested new approvals");
      setPendingChat(null);
      setActiveRun(result);
      await loadConversation(pendingChat.conversation_id);
      await refreshConversationList(conversationSearch);
      setView("chat");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setStarting(false);
    }
  };

  const cancelLocal = async () => {
    if (!activeRun) return;
    try {
      setActiveRun(await amca.cancelRun(activeRun.run_id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const startDemo = async () => {
    setStarting(true);
    setError(null);
    try {
      const run = await amca.startDemo(goal);
      setActiveRun(run);
      setView("room");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setStarting(false);
    }
  };

  const chooseProject = async (projectId: string) => {
    setLoading(true);
    try {
      await loadProject(projectId);
      setView("room");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  };

  const uploadAsset = async (file: File | undefined) => {
    if (!file || !project) return;
    setUploading(true);
    setError(null);
    try {
      await amca.uploadAsset(project.project_id, file);
      await loadProject(project.project_id);
      setView("files");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setUploading(false);
    }
  };

  const addChatAttachments = (selected: FileList | File[] | null) => {
    if (!selected || !selected.length) return;
    // v1.1.15: the picker, a drop and a paste all go through ONE rule set,
    // so the same file behaves the same way however it arrived.
    const outcome = addPendingAttachments(pendingAttachments, Array.from(selected));
    setError(outcome.error);
    setPendingAttachments(outcome.files);
  };

  const dropChatAttachments = async (event: React.DragEvent<HTMLElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setDraggingAttachments(false);
    try {
      const files = await filesFromDrop(event.dataTransfer);
      // A drop that yields nothing means the entry could not be read.  The
      // operator must be told, not left wondering why the drop did
      // nothing at all.
      if (files.length === 0) {
        setError("AMCA could not read the dropped file or folder.");
        return;
      }
      addChatAttachments(files);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "AMCA could not read the dropped folder.");
    }
  };

  /**
   * v1.1.15: paste a file straight into the composer.
   *
   * A text paste must reach the textarea untouched, so only a paste that
   * actually carries FILES is intercepted.  A RICH paste can carry both
   * text and a file (copying a spreadsheet range also brings its logo
   * image): there the text is still inserted by the browser, because
   * swallowing it would lose the operator's content.
   */
  const pasteChatAttachments = async (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const transfer = event.clipboardData;
    if (!pasteCarriesFiles(transfer)) return;
    // Only a FILES-ONLY paste replaces the browser's own paste.  A mixed
    // paste keeps its text in the composer AND attaches the file.
    if (!pasteCarriesText(transfer)) event.preventDefault();
    try {
      // The reader walks a FOLDER when the clipboard exposes one as an
      // entry.  Chromium does not guarantee directory entries for clipboard
      // data, so a pasted folder may arrive as a single item instead; it is
      // then refused by the type allow-list rather than silently attached
      // as an empty file.
      const files = await filesFromDataTransfer(
        transfer,
        (entry) => filesFromEntry(entry as unknown as ChromiumFileEntry),
      );
      if (files.length === 0) return;
      const outcome = addPendingAttachments(pendingAttachments, files);
      setError(outcome.error);
      setPendingAttachments(outcome.files);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "AMCA could not read the pasted file.");
    }
  };

  /**
   * v1.2.0: set the global level and make sure the Project folder is actually
   * usable under it.
   *
   * Both entry points - the composer's Local PC Access selector and the
   * Computer Access panel - go through here.  They used to differ: the panel
   * created the Project-folder grant and the selector did not, so choosing
   * Restricted or Standard from the chat left the Project with a level and no
   * grant, and every tool call was refused with no obvious cause.
   */
  const applyPermissionProfile = async (level: LocalPermissionProfile) => {
    if (!permissionProjectId) return;
    // v1.2.0: the folder is the Project's own, chosen at Project creation.
    // A standalone chat has no folder, and under the folder-bound levels
    // that is exactly why its tools stay denied; Full Access does not need
    // one because it is provisioned per drive by the backend.
    //
    // Only the active Project's own folder is ever sent. Echoing a stored
    // workspace_root back would re-post whatever an earlier build happened
    // to record for this id, which is how a chat ended up bounded by a
    // folder it never legitimately had.
    const root = activeStudioProject?.root_path ?? "";
    await amca.setLocalPermissionProfile(permissionProjectId, level, root);
    // A folder-bound level still needs an explicit grant for the Project
    // folder, or every tool call in it would be refused. Full Access already
    // has its drive grants from the backend and must not be re-granted here.
    if (root && level !== "full_computer") {
      const snapshot = await amca.localTools(permissionProjectId);
      const profileCaps = snapshot.profile.capabilities as LocalFileCapability[];
      const existing = snapshot.grants.some(
        (grant) => grant.active && grant.canonical_root === root,
      );
      if (profileCaps.length && !existing) {
        await amca.createLocalGrant({
          project_id: permissionProjectId,
          root_path: root,
          capabilities: profileCaps,
          reason: "Project folder granted for local tools",
        });
      }
    }
    await loadLocalPermissions(permissionProjectId);
  };

  const savePermissionProfile = async () => {
    if (!permissionProjectId) return;
    if (permissionProfileDraft === "full_computer" && !window.confirm(
      "Enable Full Access for the whole PC? Local tools will be able to read and create files on every FIXED drive attached at the moment you switch this on, in any Project and any chat. Removable and network drives are never included. A drive attached later stays OUTSIDE access until you press Refresh drives. Overwriting, moving, deleting, or restoring an existing file still asks you first, and running commands or controlling apps is still separately approved.",
    )) return;
    setPermissionBusy(true);
    setError(null);
    try {
      await applyPermissionProfile(permissionProfileDraft);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPermissionBusy(false);
    }
  };

  /**
   * v1.2.0: re-enumerate drives for Full Access. A drive attached after the
   * level was switched on is deliberately not covered until this runs, so the
   * coverage the user sees is never a guess.
   */
  const refreshFullAccessScope = async () => {
    if (!permissionProjectId) return;
    setPermissionBusy(true);
    setError(null);
    try {
      await amca.refreshLocalFullAccess(permissionProjectId);
      await loadLocalPermissions(permissionProjectId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPermissionBusy(false);
    }
  };

  const revokePermissionGrant = async (grantId: string) => {
    if (!permissionProjectId || !window.confirm("Revoke this AMCA folder grant now?")) return;
    setPermissionBusy(true);
    try {
      await amca.revokeLocalGrant(grantId, "Revoked from Computer access settings.");
      await loadLocalPermissions(permissionProjectId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPermissionBusy(false);
    }
  };

  const decideApproval = async (requestId: string, approve: boolean) => {
    setError(null);
    try {
      await amca.decideApproval(requestId, approve);
      if (project) {
        const document = await amca.approvals(project.project_id);
        setApprovals(document.approvals);
      }
      if (permissionProjectId) {
        const document = await amca.approvals(permissionProjectId);
        setLocalApprovals(document.approvals);
      }
      // 32F Fix 1: reflect the decided state immediately. A denied desktop
      // Chat approval returns the conversation to its normal state, so the
      // pending-run card clears and the composer is usable without a reload.
      if (activeConversationId) {
        await loadConversation(activeConversationId);
        await refreshConversationList(conversationSearch);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const unresolved = project?.active_disagreements.filter((item) => !item.resolved) ?? [];
  const activeOrganization = project?.artifacts?.agent_organization as {
    selected_agents?: string[];
    lifecycle?: string;
    economy?: { organization_size?: string; logical_workers?: number; manager_depth?: number };
    governor?: { authorized?: boolean; reason?: string };
  } | undefined;
  const activeModelSelection = project?.artifacts?.model_selection as {
    policy?: string;
    participant_ids?: string[];
    rationale?: string;
    confidence?: string;
    expected_cost_usd?: number | null;
    task_complexity?: string;
    requested_participant_target?: number;
    collaboration_rounds?: number;
  } | undefined;
  const decision = (project?.collaboration_progress?.collaboration_decision || project?.artifacts?.collaboration_decision || activeRun?.collaboration_decision) as CollaborationDecisionDocument | undefined;
  const criteria = project?.goal_contract?.criteria ?? [];
  const progress = criteria.length
    ? criteria.filter((criterion) => criterion.satisfied).length / criteria.length
    : project?.progress ?? 0;
  const latestRun = activeRun ?? dashboard.runs[0] ?? null;
  const latestRunRef = useRef<RunJob | null>(latestRun);
  latestRunRef.current = latestRun;
  const pageTitle = viewChromeLabel(view, [...NAV_WORK, ...NAV_RESOURCES, ...NAV_ADVANCED], NAVIGATION);
  const localBusy = Boolean(activeRun && ["queued", "running", "cancelling"].includes(activeRun.status));
  const eligibleModels = models.models.filter((item) => item.fit !== 2);
  const configuredCloudModels = credentialStatus?.credentials.filter((item) => item.configured) ?? [];
  // v1.1.8 Phase 1: only models the user explicitly published from a provider
  // catalog are selectable.  Discovery alone no longer fills this menu.
  const configuredProviderModels = hubProviders.filter((item) => item.configured && item.enabled !== false).flatMap((item) => (item.enabled_model_ids ?? []).map((model) => ({ provider: item, model, ref: `${item.id}:${model}` })));
  // The composer model menu lists models THROUGH their provider, and only
  // the ones that have been measured: a model with no verdict, or one whose
  // configuration is wrong, is not a choice the operator can make
  // responsibly. The rule is the same one the selection uses — a model the
  // composer would refuse to let you pick is not offered.
  const composerModelMenu = buildComposerModelMenu([
    ...configuredProviderModels.map((item) => {
      const record = healthFor("hub", item.ref, item.provider.endpoint, item.model);
      const health = composerModelHealth(record);
      // The menu shows the same health story as Settings: a recorded
      // failure renders as a short note next to the model, and a transient
      // provider failure (down, rate-limited, timed out) sorts below
      // healthy models instead of hiding.
      const degraded = !health.blocked && !health.unverified && record !== null && record.state !== "READY";
      return {
        ref: item.ref,
        model: item.model,
        provider: item.provider.name,
        label: humanModelName(item.model),
        verified: record !== null && !health.blocked,
        healthNote: degraded || health.blocked ? health.label : "",
        degraded,
      };
    }),
    ...configuredCloudModels.map((item) => {
      const record = healthFor("profile", item.credential_id, item.endpoint, item.model);
      const health = composerModelHealth(record);
      const degraded = !health.blocked && !health.unverified && record !== null && record.state !== "READY";
      return {
        ref: item.credential_id,
        model: item.model,
        provider: item.provider,
        label: item.label,
        verified: record !== null && !health.blocked,
        healthNote: degraded || health.blocked ? health.label : "",
        degraded,
      };
    }),
  ]);
  // 16A P3: composer model-menu helpers. The checked set IS the existing
  // selectedTeam/participant contract; switching to Auto Intelligence keeps
  // the manual set suspended in state so returning to manual restores it.
  const toggleComposerModel = (ref: string) => {
    setSelectedTeam((current) => toggleComposerSelection(current, ref, modelSelectionMode));
    setModelSelectionMode("manual");
    setSelectedModel(ref);
    setExplicitChatMode("auto");
    if (activeConversationId) localStorage.setItem(`amca.policy.${activeConversationId}`, "manual");
  };
  const chooseComposerAuto = () => {
    setModelSelectionMode("auto");
    setModelMenuOpen(false);
    if (activeConversationId) localStorage.setItem(`amca.policy.${activeConversationId}`, "auto");
  };
  // Measure every configured model from inside the composer menu.
  //
  // The menu offers only verified models, so a model that was just added —
  // and has never been measured — would be invisible. The same bounded
  // 1-token probe the per-model Verify uses is run for all of them, and
  // the menu then lists the ones that work.
  const [verifyingAllModels, setVerifyingAllModels] = useState(false);
  const verifyAllComposerModels = async () => {
    setVerifyingAllModels(true);
    setError(null);
    try {
      await amca.verifyAllConfiguredModels();
      const hubDocument = await amca.modelHub();
      setModelHealth(hubDocument.model_health ?? null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setVerifyingAllModels(false);
    }
  };
  const composerMenuLabel = () => {
    if (modelSelectionMode === "auto") return "Auto Intelligence";
    if (selectedTeam.length > 1) return `Custom team · ${selectedTeam.length} models`;
    if (selectedTeam.length === 1) {
      const cloudHit = configuredProviderModels.find((item) => item.ref === selectedTeam[0]);
      if (cloudHit) return `${cloudHit.model} · ${cloudHit.provider.name}`;
      const profileHit = configuredCloudModels.find((item) => item.credential_id === selectedTeam[0]);
      if (profileHit) return profileHit.label;
      return selectedTeam[0].startsWith("ollama:") ? selectedTeam[0].slice(7) : selectedTeam[0];
    }
    return "Choose models";
  };
  const autoPoolAvailable = eligibleModels.length + configuredCloudModels.length + configuredProviderModels.length > 0;
  const chatReady = Boolean(prompt.trim() || pendingAttachments.length) && (
    modelSelectionMode === "auto" ? autoPoolAvailable : selectedTeam.length >= 1
  );

  const workflowDetails = <div className="workflow-details">        {(() => {
          const decision = latestRun?.collaboration_decision;
          if (!decision?.architecture) return null;
          const arch = decision.architecture;
          return (
            <details className="collab-card">
              <summary>
                <strong>Why AMCA chose this</strong>
                <small> {arch.mode ?? latestRun?.mode} · {arch.sync_mode} · {arch.expected_rounds ?? latestRun?.collaboration_rounds} round(s)</small>
              </summary>
              <div className="collab-card-body">
                <p><strong>Decision:</strong> {arch.requested_by === "user_lock" ? "your explicit architecture lock" : "Auto Intelligence"} via <code>{arch.policy_rule}</code></p>
                {(arch.reasons ?? []).slice(0, 2).map((reason, index) => <p key={index}>{reason}</p>)}
                {decision.model_set?.rationale && <p><strong>Models:</strong> {decision.model_set.rationale}</p>}
                {typeof decision.eligible_model_count === "number" && <p><small>{decision.eligible_model_count} model(s) passed the quality/capability gate.</small></p>}
                {arch.degradation_reason && <p className="inline-warning">{arch.degradation_reason}</p>}
                {decision.decomposition && (
                  <p><small>Parallel plan: {String(decision.decomposition.work_unit_count)} work unit(s) from {String(decision.decomposition.basis)} cues, {String(decision.decomposition.levels)} DAG level(s).</small></p>
                )}
                <p><small>Adaptive runs record every architecture switch as an audited artifact in Project events.</small></p>
              </div>
            </details>
          );
        })()}
</div>;
  /**
   * A conversation is "empty" until the user sends something.
   *
   * This used to be tested with `!messages.length`, which could never be true
   * because the transcript always holds the welcome message — so the centered
   * empty state never appeared and the composer was permanently bottom-anchored.
   */
  const hasUserTurn = messages.some((message) => message.role === "user");
  const chat = (
    <section
      className={`chat-shell ${hasUserTurn ? "" : "empty-conversation"} ${draggingAttachments ? "dragging-attachments" : ""}`}
      onDragEnter={(event) => { event.preventDefault(); setDraggingAttachments(true); }}
      onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; setDraggingAttachments(true); }}
      onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDraggingAttachments(false); }}
      onDrop={(event) => void dropChatAttachments(event)}
    >
      {draggingAttachments && <div className="chat-drop-overlay"><Icon name="files" /><strong>Drop files, folders, or a repository</strong><span>AMCA will keep them local until the routing and permission policy is resolved.</span></div>}
      <div className="chat-transcript" aria-live="polite" ref={transcriptRef}>
        {messages.map((message) => (
          <article className={`chat-message chat-${message.role}`} key={message.id}>
            <div className="chat-avatar">{message.role === "user" ? "AB" : message.role === "assistant" ? "A" : "i"}</div>
            <div>
              <strong>{message.role === "user" ? "You" : message.role === "assistant" ? "AMCA" : "Local system"}</strong>
              {message.role === "assistant" ? <><RichOutput text={message.text} onLink={(url) => { setBrowserUrl(url); inspect("tools"); }}/><ResponseActions id={`${activeConversationId}:${message.id}`} text={message.text} onRetry={() => { const index=messages.findIndex(item=>item.id===message.id); const original=messages.slice(0,index).reverse().find(item=>item.role==="user"); if(original)setPrompt(original.text); }} onDetails={() => inspect("room")} onSource={() => { if(!activeStudioProject) { setError("Open or move this chat to a Project before adding a source."); return; } void addStudioProjectFiles([new File([message.text],`response-${message.id.replace(/[^a-zA-Z0-9-]/g,"-")}.md`,{type:"text/markdown"})]); }} /></> : <p>{message.text}</p>}
              {/* v1.1.8: show the governed tools that actually ran, in the
                  transcript, instead of hiding them inside Run details. */}
              {(() => {
                const tools = (message.meta ?? "").split(" · ").find((part) => part.startsWith("Tools: "));
                if (!tools) return null;
                return (
                  <div className="message-tools" aria-label="Governed tools used">
                    {tools.replace(/^Tools: /, "").split(", ").filter(Boolean).map((tool) => <span key={tool}>{tool}</span>)}
                  </div>
                );
              })()}
              {(() => {
                // Response-UX R3: the "What was done" detail block renders
                // the backend meta segment (executed ops with sizes,
                // rounds, cost) as a collapsed list next to the Tools
                // chips.  Pure presentation of the existing meta string —
                // no new data source, no secrets (paths + sizes + cost).
                const detail = (message.meta ?? "").split(" · ").find((part) => part.startsWith("What was done: "));
                if (!detail) return null;
                const items = detail.replace(/^What was done: /, "").split("; ").filter(Boolean);
                if (!items.length) return null;
                return (
                  <details className="message-what-done">
                    <summary>What was done</summary>
                    <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
                  </details>
                );
              })()}
              {message.meta && <details className="message-provenance"><summary>Run details</summary><small>{message.meta}</small></details>}
            </div>
          </article>
        ))}
        {!localBusy && chatArtifactChips.length > 0 && (
          <div className="chip-row artifact-chip-row" aria-label="Files created in this conversation">
            {chatArtifactChips.map((meta) => (
              <article key={meta.artifact_id} className="chip-artifact-card">
                <div className="chip-artifact-heading">
                  <Icon name="files" />
                  <span>
                    <strong>{meta.title}</strong>
                    <small>{artifactTypeLabel(meta.artifact_type)} · v{meta.current_version}</small>
                  </span>
                </div>
                <div className="chip-artifact-actions">
                  <button className="primary-button" onClick={() => void downloadArtifactFromChat(meta)}>
                    Download
                  </button>
                  <button
                    className="secondary-button"
                    onClick={() => {
                      setFocusedArtifactId(meta.artifact_id);
                      inspect("artifacts");
                    }}
                  >
                    Open
                  </button>
                </div>
                {artifactDownloadNotice?.artifact_id === meta.artifact_id && (
                  <small role="status" className="chip-artifact-notice">{artifactDownloadNotice.text}</small>
                )}
              </article>
            ))}
          </div>
        )}
        {localBusy && (
          <article className="chat-message chat-assistant thinking-message">
            <div className="chat-avatar">A</div>
            <div>
              <strong>AMCA{runElapsed ? <span className="thinking-elapsed"> · {runElapsed}</span> : null}</strong>
              {/* ZCode-style thinking block: "AMCA is thinking…" header
                  plus the live feed; the whole bubble disappears when the
                  final answer lands (localBusy clears). */}
              <p className="thinking-header"><i /><i /><i /> {activeRun?.status === "cancelling" ? "Cancelling…" : "AMCA is thinking…"}</p>
              {runActivity.length > 0 && (
                <ul className="message-activity-feed" aria-label="Live run activity">
                  {runActivity.map((line, index) => <li key={`${index}-${line.slice(0, 24)}`}>{line}</li>)}
                </ul>
              )}
            </div>
          </article>
        )}
      </div>
      <div className="chat-composer">
        {activeStudioProject && <button className="project-context-bar" onClick={() => void openStudioProject(activeStudioProject.project_id)}>{activeStudioProject.name} · {activeStudioProject.files.length} sources</button>}
        {pendingAttachments.length > 0 && (
          <div className="chat-attachment-list" aria-label="Files attached to this message">
            {pendingAttachments.map((file) => (
              <div className="chat-attachment-chip" key={`${file.name}-${file.size}-${file.lastModified}`}>
                <span className="chat-attachment-icon"><Icon name="files" /></span>
                <span><strong>{file.name}</strong><small>{formatBytes(file.size)}</small></span>
                <button
                  type="button"
                  aria-label={`Remove ${file.name}`}
                  title={`Remove ${file.name}`}
                  onClick={() => setPendingAttachments((current) => removePendingAttachment(current, file))}
                >×</button>
              </div>
            ))}
          </div>
        )}
        {knowledgeSelection.length > 0 && (
          <div className="chat-attachment-list" aria-label="Knowledge context attached to this message">
            {knowledgeSelection.map((item) => (
              <div className="chat-attachment-chip ki-context-chip" key={item.artifactId}>
                <span className="chat-attachment-icon"><Icon name="logs" /></span>
                <span>
                  <strong>{item.title}</strong>
                  <small>{item.typeLabel} v{item.currentVersion} · {item.provenanceLine}</small>
                </span>
                <button
                  type="button"
                  aria-label={`Remove knowledge context ${item.title}`}
                  title={`Remove ${item.title}`}
                  onClick={() => removeKnowledge(item.artifactId)}
                >×</button>
              </div>
            ))}
            <small className="ki-context-meta">{selectionMetadata(knowledgeSelection)}</small>
          </div>
        )}
        {knowledgePickerOpen && (
          <div className="ki-picker" role="dialog" aria-label="Attach knowledge context">
            <div className="ki-picker-head">
              <strong>Attach knowledge context</strong>
              <span>explicit · user-selected · max {MAX_KNOWLEDGE_CONTEXT_ITEMS}</span>
              <button type="button" onClick={() => setKnowledgePickerOpen(false)} aria-label="Close knowledge picker">×</button>
            </div>
            {activeStudioProject?.files.map(asset => <div className="ki-picker-row" key={asset.record_id}><span>{asset.filename}</span><button onClick={() => {setPrompt(current=>`${current} @${asset.filename}`);setKnowledgePickerOpen(false);}}>Reference</button><button onClick={() => {setPreviewAsset(asset);inspect("files");}}>Preview</button></div>)}
            {conversations.filter(item => item.conversation_id !== activeConversationId).slice(0,10).map(item => <div className="ki-picker-row" key={item.conversation_id}><span>{item.title}</span><button onClick={async()=>{try{const doc=await amca.conversation(item.conversation_id);setPrompt(current=>`${current}\n\nContext from chat: ${item.title}\n${JSON.stringify(doc.messages)}`);setKnowledgePickerOpen(false);}catch(e){setError(String(e));}}}>Reference chat</button></div>)}
            {knowledgePickerState === "loading" && <p className="ki-picker-state">Reading the artifact registry…</p>}
            {knowledgePickerState === "unavailable" && (
              <p className="ki-picker-state"><strong>{KNOWLEDGE_CONTEXT_UNAVAILABLE.title}</strong> {KNOWLEDGE_CONTEXT_UNAVAILABLE.detail}</p>
            )}
            {knowledgePickerState === "empty" && (
              <p className="ki-picker-state"><strong>{KNOWLEDGE_CONTEXT_EMPTY.title}</strong> {KNOWLEDGE_CONTEXT_EMPTY.detail}</p>
            )}
            {(knowledgePickerItems ?? []).map((asset) => {
              const attached = knowledgeSelection.some((item) => item.artifactId === asset.artifactId);
              const blocked = asset.shareability !== "shareable";
              return (
                <div key={asset.artifactId} className="ki-picker-row">
                  <span className="ki-picker-info">
                    <strong>{asset.title}</strong>
                    <small>{asset.typeLabel} v{asset.currentVersion} · {permissionLine({
                      artifactId: asset.artifactId,
                      title: asset.title,
                      typeLabel: asset.typeLabel,
                      currentVersion: asset.currentVersion,
                      shareability: asset.shareability,
                      shareabilityReason: asset.shareabilityReason,
                      provenanceLine: "",
                    })}</small>
                  </span>
                  <button
                    type="button"
                    onClick={() => attachKnowledge(asset)}
                    disabled={attached || blocked}
                    title={blocked ? asset.shareabilityReason : attached ? "Already attached" : `Attach ${asset.title}`}
                  >
                    {attached ? "Attached" : blocked ? "Not attachable" : "Attach"}
                  </button>
                </div>
              );
            })}
            {knowledgeSelection.length > 0 && (
              <button type="button" className="ki-picker-clear" onClick={clearKnowledgeSelection}>Remove all</button>
            )}
          </div>
        )}
        <textarea
          ref={chatComposerRef}
          value={prompt}
          onChange={(event) => { setPrompt(event.target.value); event.target.style.height = "auto"; event.target.style.height = `${Math.min(180, event.target.scrollHeight)}px`; }}
          onPaste={(event) => { void pasteChatAttachments(event); }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void startChat();
            }
          }}
          placeholder={modelSelectionMode === "manual"
            ? selectedTeam.length >= 1 ? "Ask the selected model set… (or paste a file)" : "Choose a model below"
            : autoPoolAvailable ? "Describe the task — AMCA will choose the models and agents… (or paste a file)" : "Scan Ollama or save an API key in Model Hub"}
          aria-label="Message AMCA"
          rows={2}
          disabled={localBusy}
        />
        <div className="chat-composer-footer">
          <div className="chat-composer-tools">
            {/* R6: /, @, $ and Attachments live INSIDE the + menu — one entry
                point on the toolbar, every entry wired to its working
                handler. */}
            <div className="composer-plus-wrap">
              <button
                type="button"
                className="chat-attach-button"
                title="Add"
                aria-label="Add — attachments, commands, context, skills"
                aria-expanded={plusMenuOpen}
                disabled={localBusy || starting}
                ref={plusButtonRef}
                onClick={() => setPlusMenuOpen((value) => !value)}
              ><Icon name="plus" /></button>
              {plusMenuOpen && (
                <>
                  <div className="model-menu-overlay" onClick={() => setPlusMenuOpen(false)} />
                  <div
                    className="composer-plus-menu"
                    role="menu"
                    aria-label="Add"
                    style={plusMenuPosition}
                  >
                    {COMPOSER_MENU_ACTIONS.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        role="menuitem"
                        onClick={() => { setPlusMenuOpen(false); runComposerAction(item.id); }}
                      >
                        <Icon name={item.icon} />
                        <span>{item.label}</span>
                        <small>{item.description}</small>
                      </button>
                    ))}
                  </div>
                </>
              )}
              <input
                ref={chatFileInputRef}
                type="file"
                className="visually-hidden-input"
                aria-label="Attach files"
                multiple
                accept={CHAT_ATTACHMENT_ACCEPT_ATTRIBUTE}
                disabled={localBusy || starting}
                onChange={(event) => { addChatAttachments(event.target.files); event.currentTarget.value = ""; }}
              />
            </div>
            <select aria-label="Local PC Access" title="Ask before changes: works in the project folder and asks before file changes. Autonomous mode: keeps working, asks when there is risk. Full access: the whole PC, fewer confirmations. Security approvals still apply." value={localTools?.profile.profile ?? "standard"} disabled={permissionBusy || !permissionProjectId} onChange={async (event) => {
              const value = event.target.value as LocalPermissionProfile;
              // v1.2.0: the folder is a Project property, not something chosen
              // per chat, so the level is applied directly against the
              // Project's own folder. Full Access does not use a folder at all -
              // it is provisioned per drive by the backend.
              if (value === "full_computer" && !window.confirm(
                "Enable Full Access for the whole PC? Local tools will be able to read and create files on every FIXED drive attached at the moment you switch this on, in any Project and any chat. Removable and network drives are never included. A drive attached later stays OUTSIDE access until you press Refresh drives. Overwriting, moving, deleting, or restoring an existing file still asks you first, and running commands or controlling apps is still separately approved.",
              )) return;
              setPermissionBusy(true);
              setError(null);
              // The same path the Computer Access panel uses, so choosing a
              // level here cannot leave the Project without its folder grant.
              try { await applyPermissionProfile(value); }
              catch (caught) { setError(String(caught)); } finally { setPermissionBusy(false); }
          }}>{ACCESS_MODES.map((mode) => <option key={mode.id} value={mode.id}>{mode.title}</option>)}</select>
          </div>
          <div className="composer-execution">
            {/* 16A P3: multi-model manual selection. Auto Intelligence stays the
                first special option; checked models map into the existing
                selectedTeam/participant runtime contract (joint for >1). The
                menu is grouped by provider and lists only the models that
                have been VERIFIED: an unmeasured or broken model is not a
                choice the operator can make responsibly. */}
            <div className="model-select-wrap">
              <button type="button" className="model-select-button" aria-label="Model" aria-expanded={modelMenuOpen} title="Model selection" disabled={localBusy || starting} ref={modelButtonRef} onClick={() => setModelMenuOpen((value) => !value)}>
                {composerMenuLabel()} <span aria-hidden="true">▾</span>
              </button>
              {modelMenuOpen && (
                <>
                  <div className="model-menu-overlay" onClick={() => setModelMenuOpen(false)} />
                  <div className="model-select-menu" role="dialog" aria-label="Model selection" ref={modelMenuRef} style={modelMenuPosition}>
                    <button type="button" className={"model-menu-row model-menu-auto" + (modelSelectionMode === "auto" ? " active" : "")} onClick={() => chooseComposerAuto()}>
                      <span>Auto Intelligence</span>
                      <small>AMCA chooses the models and agents</small>
                    </button>
                    <div className="model-menu-group">Cloud</div>
                    {composerModelMenu.groups.map((group, groupIndex) => (
                      <div className="model-menu-provider" key={group.provider}>
                        <div className="model-menu-provider-name">{group.provider}</div>
                        {group.models.map((entry, entryIndex) => {
                          // The id must be unique per row: folding the ref to
                          // safe characters would make "a/b" and "a_b"
                          // collide, and the second row's label would then
                          // toggle the FIRST row's checkbox. The provider
                          // and model indexes are unique by construction.
                          const inputId = `composer-model-${groupIndex}-${entryIndex}`;
                          return (
                            <div className={"model-menu-row" + (entry.degraded ? " model-menu-degraded" : "")} key={entry.ref}>
                              <input id={inputId} type="checkbox" checked={modelSelectionMode === "manual" && selectedTeam.includes(entry.ref)} onChange={() => toggleComposerModel(entry.ref)} />
                              <label className="model-menu-text" htmlFor={inputId} title={entry.healthNote ? `${entry.model} — ${entry.healthNote}` : entry.model}><span>{entry.label}</span>{entry.healthNote ? <small className="model-menu-health"> — {entry.healthNote}</small> : null}</label>
                            </div>
                          );
                        })}
                      </div>
                    ))}
                    {composerModelMenu.unverifiedCount > 0 && (
                      <div className="model-menu-pending">
                        <small>{composerModelMenu.unverifiedCount} configured model(s) are not verified yet.</small>
                        <button type="button" className="model-menu-verify" disabled={verifyingAllModels} onClick={() => void verifyAllComposerModels()}>
                          {verifyingAllModels ? "Verifying…" : "Verify all models"}
                        </button>
                      </div>
                    )}
                    <div className="model-menu-group">Local</div>
                    {models.models.map((item) => {
                      const localBlocked = item.fit === 2 || models.runtime_reachable === false;
                      const suffix = item.fit === 2 ? " — hardware incompatible" : models.runtime_reachable === false ? " — offline" : "";
                      return (
                        <label className="model-menu-row" key={item.model}>
                          <input type="checkbox" checked={modelSelectionMode === "manual" && selectedTeam.includes(`ollama:${item.model}`)} disabled={localBlocked} onChange={() => toggleComposerModel(`ollama:${item.model}`)} />
                          <span>{item.model}{suffix}</span>
                        </label>
                      );
                    })}
                  </div>
                </>
              )}
            </div>
            {/* v1.1.8 Phase 3: manual architectures belong to manual model
                selection.  Under Auto Intelligence the router owns the
                architecture, so the selector is replaced by a fixed label
                rather than offering a choice the backend discards. */}
            {shouldShowManualCollaborationControls(modelSelectionMode) ? (
              <select
                aria-label="Collaboration architecture"
                title={`Collaboration architecture (${availableArchitectures.map((value) => COLLABORATION_ARCHITECTURES.find((entry) => entry.value === value)?.label ?? value).join(", ")})`}
                value={explicitChatMode}
                disabled={localBusy || starting}
                onChange={(event) => {
                  const next = event.target.value as ChatMode | "auto";
                  setExplicitChatMode(next);
                  if (next !== "auto") changeChatMode(next);
                }}
              >
                {COLLABORATION_ARCHITECTURES
                  .filter((entry) => availableArchitectures.includes(entry.value))
                  .map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
              </select>
            ) : (
              <span className="effort-fixed" aria-label="Collaboration architecture" title="Auto Intelligence chooses the collaboration architecture from the task. Select models manually to choose one yourself."><Icon name="spark" /> Auto architecture</span>
            )}
            {/* 19B1 D5: the runtime exposes exactly one effort level (Default) and no
                backend effort field — the control is a fixed, clearly-labeled state,
                not a disabled dropdown that looks broken. */}

            {/* R6: the Effort selector works — it scales this run's bounded
                frontier-call ceiling (low x1 baseline, high x2, max x3). */}
            <select
              aria-label="Effort"
              title="Effort — scales how long this run may keep working: Low x1 (baseline), High x2, Max x3. It never changes what the run may touch."
              value={composerEffort}
              disabled={localBusy || starting}
              onChange={(event) => setComposerEffort(event.target.value as "low" | "high" | "max")}
            >
              {(["low", "high", "max"] as const).map((level) => (
                <option key={level} value={level}>{effortLabel(level)}</option>
              ))}
            </select>
            {localBusy ? <button className="danger-button" onClick={() => void cancelLocal()}>Stop</button> : <button className="primary-button" onClick={() => void startChat()} disabled={starting || !chatReady}>{starting ? "Starting…" : "Run"}</button>}
          </div>
        </div>
      </div>
    </section>
  );

  const executionOverview = (
    <>
      <section className="hero-panel">
        <div className="hero-copy">
          <span className="eyebrow"><Icon name="spark" /> Local-first collaborative intelligence</span>
          <h1>Put a difficult goal<br /><em>in the room.</em></h1>
          <p>AMCA coordinates heterogeneous reasoning participants around one shared state, preserving objections, evidence, cost, and decisions as the work evolves.</p>
        </div>
        <div className="goal-composer">
          <div className="composer-heading">
            <span>New research goal</span>
            <span className="privacy-label"><Icon name="shield" /> Offline</span>
          </div>
          <textarea value={goal} onChange={(event) => setGoal(event.target.value)} rows={5} aria-label="Research goal" />
          <div className="composer-footer">
            <span>Scripted local team · no cloud calls</span>
            <button className="primary-button" onClick={() => void startDemo()} disabled={!online || starting || !goal.trim()}>
              <Icon name={starting ? "refresh" : "play"} className={starting ? "spinning" : ""} />
              {starting ? "Starting…" : "Start collaboration"}
            </button>
          </div>
        </div>
      </section>

      <section className="metrics-grid">
        <MetricCard label="Local projects" value={String(dashboard.projects.length)} detail="Durably stored on this device" tone="green" />
        <MetricCard label="Latest state" value={project ? `v${project.version}` : "—"} detail={project ? `${project.cognitive_revision} cognitive revisions` : "No project selected"} />
        <MetricCard label="Open objections" value={String(unresolved.length)} detail={unresolved.some((item) => item.severity === "critical") ? "Critical review required" : "No critical blocker"} tone={unresolved.length ? "amber" : "green"} />
        <MetricCard label="API spend" value={formatMoney(project?.cost_state.api_cost_usd)} detail={`${project?.cost_state.api_model_calls ?? 0} frontier calls`} />
      </section>

      <section className="split-grid overview-lower">
        <article className="panel project-panel">
          <div className="panel-heading"><div><span className="section-kicker">Workspace</span><h2>Recent projects</h2></div><span>{dashboard.projects.length} total</span></div>
          {dashboard.projects.length ? (
            <div className="project-list">
              {dashboard.projects.slice(0, 6).map((item) => (
                <button key={item.project_id} className={`project-row ${project?.project_id === item.project_id ? "selected" : ""}`} onClick={() => void chooseProject(item.project_id)}>
                  <div className="project-monogram">{item.goal.charAt(0).toUpperCase()}</div>
                  <div className="project-row-copy"><strong>{item.goal}</strong><span>{item.project_id} · {humanize(item.mode)}</span></div>
                  <div className="project-row-meta"><StatusPill status={item.status} /><small>{relativeTime(item.updated_at)}</small></div>
                </button>
              ))}
            </div>
          ) : <EmptyState title="No saved projects yet" detail="Pick a goal above and start your first run - it will be saved here automatically." />}
        </article>
        <article className="panel pulse-panel">
          <div className="panel-heading"><div><span className="section-kicker">Orchestration</span><h2>Latest run</h2></div>{latestRun && <StatusPill status={latestRun.status} />}</div>
          {latestRun ? (
            <div className="run-summary">
              <div className="run-orbit"><span /><span /><span /><div><Icon name="brain" /></div></div>
              <h3>{latestRun.goal}</h3>
              <dl><div><dt>Project</dt><dd>{latestRun.project_id}</dd></div><div><dt>Started</dt><dd>{relativeTime(latestRun.started_at ?? latestRun.created_at)}</dd></div><div><dt>Result</dt><dd>{humanize(latestRun.status)}</dd></div></dl>
              {(() => {
                const result = latestRun.result as { live_organization?: { completed_count?: number; active_count?: number; total_cost_usd?: number; tasks?: Array<{ status: string }> } } | null;
                const live = result?.live_organization;
                if (!live || !live.tasks?.length) return null;
                return (
                  <p>
                    <small>
                      Live organization from measured calls: {live.completed_count ?? 0} completed ·{" "}
                      {live.active_count ?? 0} active · ${Number(live.total_cost_usd ?? 0).toFixed(4)} actual
                    </small>
                  </p>
                );
              })()}
              {latestRun.error && <p className="inline-warning">{latestRun.error}</p>}
            </div>
          ) : <EmptyState title="Nothing is running right now" detail="Start a task from Chat and its live progress will appear here." />}
        </article>
      </section>
    </>
  );

  const overview = (
    <section className="workspace-view studio-projects-view">
      <div className="view-heading">
        <div><span className="section-kicker">Durable workspaces</span><h1>Projects</h1><p>A Project contains its own chats, shared files, instructions, and execution history. Chats remain independent conversations inside it.</p></div>
        <button className="primary-button" onClick={() => { setShowProjectForm((value) => { if (value) resetProjectDraft(); return !value; }); }}>+ New Project</button>
      </div>

      {showProjectForm && <form className="panel studio-project-form" onSubmit={createStudioProject}>
        <div className="panel-heading"><div><span className="section-kicker">Project workspace</span><h2>Create Project</h2></div><span>Local & durable</span></div>
        <div className="studio-project-fields">
          <label><span>Name</span><input value={projectDraft.name} onChange={(event) => { setProjectNameError(null); setProjectDraft((current) => ({ ...current, name: event.target.value })); void refreshProjectFolderDefault(event.target.value); }} placeholder="Research, product, client, or codebase" autoFocus /></label>
          <label><span>Template</span><select value={projectDraft.template} onChange={(event) => setProjectDraft((current) => ({ ...current, template: event.target.value }))}><option value="general">General</option><option value="research">Research</option><option value="software">Software</option><option value="analysis">Analysis</option><option value="writing">Writing</option></select></label>
          {/* v1.2.0: the folder belongs to the Project, not to a chat. It is
              mandatory - the backend creates it if it does not exist - and it
              only scopes the folder-bound access levels, never Full Access. */}
          <label className="wide project-folder-field"><span>Project folder</span><div className="project-folder-row">
            <input aria-label="Project folder" value={projectDraft.root_path} onChange={(event) => { setProjectFolderEdited(true); setProjectNameError(null); setProjectDraft((current) => ({ ...current, root_path: event.target.value })); }} placeholder="Documents\\My Project" spellCheck={false} />
            <button type="button" className="secondary-button" onClick={() => void chooseProjectFolder()} disabled={projectFolderBusy}>{projectFolderBusy ? "Choosing…" : "Choose folder"}</button>
          </div><small>AMCA creates this folder if it does not exist. Leave it empty to use Documents\\&lt;Project Name&gt;.</small></label>
          <label className="wide"><span>Description</span><input value={projectDraft.description} onChange={(event) => setProjectDraft((current) => ({ ...current, description: event.target.value }))} placeholder="What this Project is for" /></label>
          <label className="wide"><span>Project instructions</span><textarea value={projectDraft.instructions} onChange={(event) => setProjectDraft((current) => ({ ...current, instructions: event.target.value }))} placeholder="Shared context and rules applied to chats in this Project" /></label>
        </div>
        {projectNameError && <p className="inline-field-error" style={{ color: "var(--danger, #f87171)", fontSize: "0.85rem", padding: "0 4px", margin: "4px 0 0" }}>{projectNameError}</p>}
        <div className="form-actions"><button type="button" onClick={() => { resetProjectDraft(); setShowProjectForm(false); }}>Cancel</button><button className="primary-button" type="submit" disabled={conversationBusy || !projectDraft.name.trim()}>Create Project</button></div>
      </form>}

      <div className="studio-project-layout">
        <aside className="panel studio-project-browser">
          <div className="panel-heading"><div><span className="section-kicker">Your workspaces</span><h2>{studioProjects.length} Projects</h2></div></div>
          <button className={!activeStudioProject ? "active" : ""} onClick={() => setActiveStudioProject(null)}><div className="project-monogram">A</div><span><strong>All chats</strong><small>Chats outside and across Projects</small></span></button>
          {studioProjects.map((item) => <button key={item.project_id} className={activeStudioProject?.project_id === item.project_id ? "active" : ""} onClick={() => void openStudioProject(item.project_id)}><div className="project-monogram">{item.name.charAt(0).toUpperCase()}</div><span><strong>{item.name}</strong><small>{item.chat_count} chats · {humanize(item.template)}</small></span></button>)}
          {!studioProjects.length && <p className="settings-note">Create a Project to group related chats and files.</p>}
        </aside>

        <div className="studio-project-detail">
          {activeStudioProject ? <>
            <article className="panel project-workspace-header">
              <div><span className="section-kicker">{humanize(activeStudioProject.template)} Project</span><h2>{activeStudioProject.name}</h2><p>{activeStudioProject.description || "No description yet."}</p>{activeStudioProject.root_path
                ? <p className="project-folder-path" title={activeStudioProject.root_path}>📁 {activeStudioProject.root_path}</p>
                : <p className="project-folder-path">📁 No Project folder set</p>}</div>
              <button className="primary-button" onClick={() => void createNewConversation()} disabled={conversationBusy}>+ New chat in Project</button>
            </article>
            <div className="project-content-grid">
              <article className="panel">
                <div className="panel-heading"><div><span className="section-kicker">Conversations</span><h2>Chats</h2></div><span>{activeStudioProject.chats.length}</span></div>
                {activeStudioProject.chats.length ? <div className="project-chat-list">{activeStudioProject.chats.map((item) => <button key={item.conversation_id} onClick={() => void chooseConversation(item.conversation_id)}><span><strong>{item.title}</strong><small>{item.last_message_preview || "Empty chat"}</small></span><time>{relativeTime(item.updated_at)}</time></button>)}</div> : <EmptyState title="No chats in this Project" detail="Start a new chat here; the Project’s shared files and instructions remain available to every conversation." />}
              </article>
              <article className={`panel project-file-panel ${projectFileDragging ? "dragging" : ""}`} onDragEnter={(event) => { event.preventDefault(); setProjectFileDragging(true); }} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; setProjectFileDragging(true); }} onDragLeave={(event) => { event.preventDefault(); if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setProjectFileDragging(false); }} onDrop={(event) => { event.preventDefault(); void filesFromDrop(event.dataTransfer).then(addStudioProjectFiles).catch((caught) => { setError(caught instanceof Error ? caught.message : "AMCA could not read the dropped files."); }); }}>
                <div className="panel-heading"><div><span className="section-kicker">Shared context</span><h2>Files</h2></div><span>{activeStudioProject.files.length}</span></div>
                <label className="project-file-drop"><Icon name="files" /><strong>{uploading ? "Importing files…" : "Drop files or a folder here"}</strong><span>Shared by chats inside this Project · 25 MB per file</span><input type="file" multiple disabled={uploading} onChange={(event) => { void addStudioProjectFiles(Array.from(event.target.files ?? [])); event.currentTarget.value = ""; }} /></label>
                {activeStudioProject.files.length > 0 && <div className="project-file-list">{activeStudioProject.files.map((file) => <div key={file.record_id}><span className="file-icon">{file.filename.split(".").pop()?.slice(0, 3).toUpperCase() || "DOC"}</span><span><strong>{file.filename}</strong><small>{formatBytes(file.size_bytes)} · {humanize(file.modality)}</small></span></div>)}</div>}
              </article>
            </div>
            <article className="panel project-instructions-panel"><div className="panel-heading"><div><span className="section-kicker">Shared behavior</span><h2>Project instructions</h2></div><button onClick={() => void saveStudioProjectInstructions()} disabled={conversationBusy}>Save</button></div><textarea value={activeStudioProject.instructions} onChange={(event) => setActiveStudioProject((current) => current ? { ...current, instructions: event.target.value } : current)} placeholder="Add shared goals, terminology, constraints, output preferences, and rules for chats in this Project." /></article>
          </> : <>
            <article className="panel unassigned-chats-panel"><div className="panel-heading"><div><span className="section-kicker">Conversation library</span><h2>All chats</h2></div><span>{conversations.length}</span></div>{conversations.length ? <div className="project-chat-list">{conversations.map((item) => <div className="chat-project-row" key={item.conversation_id}><button onClick={() => void chooseConversation(item.conversation_id)}><span><strong>{item.title}</strong><small>{item.last_message_preview || "Empty chat"}</small></span><time>{relativeTime(item.updated_at)}</time></button><select value={item.workspace_project_id ?? ""} onChange={(event) => void moveConversationToProject(item, event.target.value || null)} aria-label={`Project for ${item.title}`}><option value="">No Project</option>{studioProjects.map((candidate) => <option key={candidate.project_id} value={candidate.project_id}>{candidate.name}</option>)}</select></div>)}</div> : <EmptyState title="No chats yet" detail="Create a chat from the sidebar." />}</article>
            <details className="execution-activity"><summary>Advanced: governed task/run state</summary>{executionOverview}</details>
          </>}
        </div>
      </div>
    </section>
  );

  // v1.1.8 Phase 8: the right panel's required tab set includes an Execution
  // History.  It lists every recorded run newest-first with its status, the
  // models that served it, its mode and any error - the same facts the run
  // registry holds, without exposing internal orchestration detail.
  const executionHistory = (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Recorded runs</span>
          <h2>Execution History</h2>
        </div>
        <span>{dashboard.runs.length}</span>
      </div>
      {dashboard.runs.length ? (
        <div className="grant-list">
          {[...dashboard.runs].reverse().map((run) => (
            <article key={run.run_id}>
              <div>
                <StatusPill status={run.status} />
                <time>{run.started_at || run.created_at}</time>
              </div>
              <strong>{run.goal || "(no goal recorded)"}</strong>
              <p>
                {run.provider || "unknown provider"}
                {run.model ? ` · ${run.model}` : ""}
                {run.mode ? ` · ${run.mode}` : ""}
                {run.collaboration_rounds ? ` · ${run.collaboration_rounds} round(s)` : ""}
              </p>
              {run.error ? <p role="alert">{run.error}</p> : null}
            </article>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No runs recorded"
          detail="Executed tasks appear here with their status, models and errors."
        />
      )}
    </section>
  );

  const researchRoom = !project && !activeRun ? <EmptyState title="No active research room" detail="Start a task from Chat or select its execution state." /> : (
    <>
      <section className="room-banner">
        <div><span className="section-kicker">Active focus</span><h1>{project?.current_focus || activeRun?.goal}</h1><p>{project?.project_id ?? activeRun?.project_id}</p></div>
        <div className="room-banner-side">{activeRun && ["queued", "running"].includes(activeRun.status) ? <div className="live-indicator"><i /> Collaboration in progress</div> : <StatusPill status={project?.status ?? activeRun?.status ?? "idle"} />}</div>
      </section>
      <section className="room-layout">
        <div className="room-main">
          <article className="panel focus-panel">
            <div className="focus-topline">
              <div><span>Architecture</span><strong>{humanize(project?.collaboration_progress?.top_level_architecture ?? project?.current_collaboration_mode ?? "initializing")}</strong></div>
              <div><span>Current Phase</span><strong>{humanize(project?.current_collaboration_mode ?? "initializing")}</strong></div>
              <div><span>Synchronization</span><strong>{humanize(project?.current_sync_mode ?? "initializing")}</strong></div>
              <div><span>Complexity</span><strong>{humanize(project?.complexity ?? "assessing")}</strong></div>
            </div>
            <div className="participant-stage">
              {(project?.participating_models.length ? project.participating_models : ["Participant A", "Participant B", "Verifier"]).map((model, index) => (
                <div className="participant" key={model}><div className={`participant-avatar avatar-${index % 3}`}>{model.charAt(0).toUpperCase()}</div><div><strong>{model}</strong><span>{activeRun?.status === "running" ? "Reasoning on active focus" : "Contribution synchronized"}</span></div><i className={activeRun?.status === "running" ? "active" : ""} /></div>
              ))}
            </div>
          </article>
          {project?.collaboration_progress?.adaptive_transitions && project.collaboration_progress.adaptive_transitions.length > 0 && (
            <article className="panel adaptive-transitions-panel">
              <div className="panel-heading">
                <div><span className="section-kicker">Adaptive Supervisor</span><h2>Mode transition history</h2></div>
                <span className="phase-badge">{project.collaboration_progress.adaptive_transitions.length} recorded transition(s)</span>
              </div>
              <div className="adaptive-transitions-timeline">
                {project.collaboration_progress.adaptive_transitions.map((item, idx) => (
                  <div className="adaptive-transition-card" key={`trans-${idx}-${item.phase_index}`}>
                    <div className="transition-header">
                      <span className="phase-pill">Phase {item.phase_index + 1}</span>
                      <strong className="transition-mode-flow">{humanize(item.from_mode || "Start")} → {humanize(item.selected_mode)}</strong>
                      <span className={`sync-pill sync-${item.selected_sync_mode}`}>{humanize(item.selected_sync_mode)} Sync</span>
                      {item.safety_override && <span className="safety-override-pill"><Icon name="shield" /> Safety Override</span>}
                    </div>
                    <div className="transition-details">
                      <div className="transition-rule"><span>Rule</span><code>{item.policy_rule}</code></div>
                      {item.phase_stop_reason && <div className="transition-stop"><span>Stop</span><strong>{humanize(item.phase_stop_reason)}</strong></div>}
                    </div>
                    {item.observed_triggers && item.observed_triggers.length > 0 && (
                      <div className="transition-triggers">
                        <span>Triggers:</span>
                        {item.observed_triggers.map((trig) => <span key={trig} className="trigger-chip">{trig}</span>)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </article>
          )}
          {project?.collaboration_progress?.parallel_progress && project.collaboration_progress.parallel_progress.total_units > 0 && (
            <article className="panel parallel-progress-panel">
              <div className="panel-heading">
                <div><span className="section-kicker">Parallel Decomposition</span><h2>DAG Work Units</h2></div>
                <StatusPill status={project.collaboration_progress.parallel_progress.integration_status === "passed" ? "succeeded" : project.collaboration_progress.parallel_progress.integration_status === "blocked" ? "blocked" : "running"} />
              </div>
              <div className="parallel-summary-bar">
                <div className="parallel-count">
                  <strong>{project.collaboration_progress.parallel_progress.completed_units} / {project.collaboration_progress.parallel_progress.total_units}</strong>
                  <span>units completed</span>
                </div>
                <div className="parallel-progress-meter">
                  <div className="meter-fill" style={{ width: `${project.collaboration_progress.parallel_progress.total_units > 0 ? (project.collaboration_progress.parallel_progress.completed_units / project.collaboration_progress.parallel_progress.total_units) * 100 : 0}%` }} />
                </div>
                <div className="parallel-status-counts">
                  {project.collaboration_progress.parallel_progress.blocked_units > 0 && <span className="count-blocked">{project.collaboration_progress.parallel_progress.blocked_units} blocked</span>}
                  {project.collaboration_progress.parallel_progress.active_units > 0 && <span className="count-active">{project.collaboration_progress.parallel_progress.active_units} active</span>}
                </div>
              </div>
              {project.collaboration_progress.parallel_progress.units && project.collaboration_progress.parallel_progress.units.length > 0 && (
                <div className="parallel-units-list">
                  {project.collaboration_progress.parallel_progress.units.map((unit) => (
                    <div className={`parallel-unit-row status-${unit.status}`} key={unit.id}>
                      <div className="unit-indicator"><i /></div>
                      <div className="unit-info">
                        <div className="unit-top">
                          <strong>{unit.focus || unit.id}</strong>
                          <span className={`status-tag status-${unit.status}`}>{humanize(unit.status)}</span>
                        </div>
                        <div className="unit-meta">
                          <span>Assignee: {unit.assignees.join(", ") || "Unassigned"}</span>
                          {unit.dependencies.length > 0 && <span>Deps: {unit.dependencies.join(", ")}</span>}
                        </div>
                        {unit.conflicts && unit.conflicts.length > 0 && (
                          <div className="unit-conflicts">
                            {unit.conflicts.map((c, i) => <p key={i} className="conflict-text">{c}</p>)}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {project.collaboration_progress.parallel_progress.integration_conflicts && project.collaboration_progress.parallel_progress.integration_conflicts.length > 0 && (
                <div className="integration-conflicts-box">
                  <strong>Integration Conflicts:</strong>
                  {project.collaboration_progress.parallel_progress.integration_conflicts.map((conf, ci) => <p key={ci}>{conf}</p>)}
                </div>
              )}
            </article>
          )}
          {decision && (
            <article className="panel setup-inspector-panel">
              <details className="setup-inspector-details">
                <summary className="setup-inspector-summary">
                  <div className="inspector-summary-left">
                    <span className="section-kicker">Decision Provenance</span>
                    <h2>Why this setup?</h2>
                  </div>
                  <div className="inspector-summary-right">
                    <span className="inspector-tag-mode">{humanize(decision.architecture?.mode ?? "Automatic")}</span>
                    <span className="inspector-tag-policy">{decision.architecture?.requested_by === "user_lock" ? "Manual Lock" : "Auto Intelligence"}</span>
                    <span className="inspector-expand-hint">Inspect rationale ▾</span>
                  </div>
                </summary>
                <div className="setup-inspector-body">
                  <div className="inspector-section architecture-decision-section">
                    <div className="section-heading-row">
                      <h3>1. Collaboration Architecture (HOW)</h3>
                      <StatusPill status={decision.architecture?.candidate_mode ? "degraded" : "authorized"} />
                    </div>
                    <div className="inspector-kv-grid">
                      <div>
                        <span>Effective Architecture</span>
                        <strong>{humanize(decision.architecture?.mode ?? "initializing")}</strong>
                      </div>
                      <div>
                        <span>Selected By</span>
                        <strong>{decision.architecture?.requested_by === "user_lock" ? "User Override (Lock)" : "Auto Intelligence"}</strong>
                      </div>
                      <div>
                        <span>Synchronization Mode</span>
                        <strong>{humanize(decision.architecture?.sync_mode ?? "default")}</strong>
                      </div>
                      <div>
                        <span>Bounded Rounds / Phases</span>
                        <strong>{decision.architecture?.expected_rounds ?? 1} round(s)</strong>
                      </div>
                      <div>
                        <span>Eligible Models Evaluated</span>
                        <strong>{decision.eligible_model_count ?? decision.model_set?.model_refs?.length ?? 1}</strong>
                      </div>
                      <div>
                        <span>Policy Rule</span>
                        <code>{decision.architecture?.policy_rule || "heuristic.default"}</code>
                      </div>
                    </div>
                    {decision.architecture?.reasons && decision.architecture.reasons.length > 0 && (
                      <div className="inspector-reasons-list">
                        <span>Audited Reasons:</span>
                        <ul>
                          {decision.architecture.reasons.map((r: string, ri: number) => (
                            <li key={ri}>{r}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {decision.architecture?.triggers && decision.architecture.triggers.length > 0 && (
                      <div className="inspector-triggers-row">
                        <span>Observed Triggers:</span>
                        <div className="triggers-chips">
                          {decision.architecture.triggers.map((trig: string) => (
                            <span key={trig} className="trigger-chip">{trig}</span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="inspector-section model-set-section">
                    <div className="section-heading-row">
                      <h3>2. Model Selection (WHO)</h3>
                      <small className="section-subnote">Model-set selection (WHO) is strictly distinct from Architecture (HOW)</small>
                    </div>
                    <div className="inspector-kv-grid">
                      <div>
                        <span>Selection Policy</span>
                        <strong>{humanize(decision.model_set?.policy ?? "standard")}</strong>
                      </div>
                      <div>
                        <span>Selected Logical Models</span>
                        <strong>{decision.model_set?.model_refs?.length ?? 0} model(s)</strong>
                      </div>
                      {decision.model_set?.confidence !== undefined && (
                        <div>
                          <span>Selection Confidence</span>
                          <strong>{String(decision.model_set.confidence)}</strong>
                        </div>
                      )}
                      {decision.model_set?.expected_cost_usd !== undefined && decision.model_set?.expected_cost_usd !== null && (
                        <div>
                          <span>Estimated Cost</span>
                          <strong>${Number(decision.model_set.expected_cost_usd).toFixed(4)} USD</strong>
                        </div>
                      )}
                    </div>
                    {decision.model_set?.model_refs && decision.model_set.model_refs.length > 0 && (
                      <div className="inspector-models-chips">
                        <span>Selected Models:</span>
                        <div className="models-chips-row">
                          {decision.model_set.model_refs.map((m: string) => (
                            <span key={m} className="model-chip"><Icon name="models" /> {m}</span>
                          ))}
                        </div>
                      </div>
                    )}
                    {decision.model_set?.rationale && (
                      <div className="inspector-rationale-box">
                        <span>Selection Rationale:</span>
                        <p>{decision.model_set.rationale}</p>
                      </div>
                    )}
                  </div>

                  {decision.task_shape && (
                    <div className="inspector-section task-shape-section">
                      <div className="section-heading-row">
                        <h3>3. Deterministic Task Shape</h3>
                        <small className="section-subnote">Lexical cues & structural properties classified from goal contract</small>
                      </div>
                      <div className="task-shape-grid">
                        <div>
                          <span>Complexity</span>
                          <strong>{humanize(String(decision.task_shape.complexity ?? "assessing"))}</strong>
                        </div>
                        <div>
                          <span>Coupling Signal</span>
                          <strong className={decision.task_shape.coupled ? "signal-coupled" : "signal-independent"}>
                            {decision.task_shape.coupled ? "Coupled" : "Independent"}
                          </strong>
                        </div>
                        <div>
                          <span>Decomposable</span>
                          <strong className={decision.task_shape.decomposable ? "signal-yes" : "signal-no"}>
                            {decision.task_shape.decomposable ? "Yes" : "No"}
                          </strong>
                        </div>
                        <div>
                          <span>Verification Heavy</span>
                          <strong className={decision.task_shape.verification_heavy ? "signal-yes" : "signal-no"}>
                            {decision.task_shape.verification_heavy ? "Yes" : "No"}
                          </strong>
                        </div>
                        <div>
                          <span>Novelty Required</span>
                          <strong className={decision.task_shape.novelty_required ? "signal-yes" : "signal-no"}>
                            {decision.task_shape.novelty_required ? "Yes" : "No"}
                          </strong>
                        </div>
                      </div>
                    </div>
                  )}

                  {(decision.architecture?.candidate_mode || decision.architecture?.degradation_reason) && (
                    <div className="inspector-section degradation-section">
                      <div className="section-heading-row">
                        <h3>4. Feasibility & Fallback Degradation</h3>
                        <span className="degradation-pill">Degraded for Feasibility</span>
                      </div>
                      <div className="degradation-grid">
                        <div>
                          <span>Pre-Feasibility Candidate</span>
                          <strong>{humanize(decision.architecture.candidate_mode ?? "unknown")}</strong>
                        </div>
                        <div>
                          <span>Executed Fallback</span>
                          <strong>{humanize(decision.architecture.mode ?? "solo")}</strong>
                        </div>
                        <div className="degradation-reason-col">
                          <span>Degradation Reason</span>
                          <p>{decision.architecture.degradation_reason || "Insufficient eligible models to satisfy candidate multi-model architecture."}</p>
                        </div>
                      </div>
                    </div>
                  )}

                  {decision.architecture?.mode === "adaptive_hybrid" && (
                    <div className="inspector-section adaptive-explanation-section">
                      <div className="section-heading-row">
                        <h3>5. Adaptive Hybrid Execution Rationale</h3>
                        <span className="adaptive-badge">Dynamic Supervisor</span>
                      </div>
                      <div className="adaptive-explanation-flow">
                        <div className="adaptive-step">
                          <div className="step-num">Setup</div>
                          <div className="step-content">
                            <strong>Initial Architecture: Adaptive Hybrid</strong>
                            <p>Initial alignment in Joint mode before evaluating decomposition feasibility. Rule: <code>{decision.architecture.policy_rule}</code>.</p>
                          </div>
                        </div>
                        {project?.collaboration_progress?.adaptive_transitions?.map((item) => (
                          <div className="adaptive-step" key={`adapt-step-${item.phase_index}`}>
                            <div className="step-num">Phase {item.phase_index + 1}</div>
                            <div className="step-content">
                              <strong>{humanize(item.from_mode || "Start")} → {humanize(item.selected_mode)} ({humanize(item.selected_sync_mode)} Sync)</strong>
                              <p>Rule: <code>{item.policy_rule}</code> · Triggers: {item.observed_triggers?.join(", ") || "None"} · Stop: {humanize(item.phase_stop_reason ?? "active")}</p>
                              {item.safety_override && <small className="safety-note"><Icon name="shield" /> Safety override enforced based on conflict detection.</small>}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </details>
            </article>
          )}
          {activeOrganization && (
            <article className="panel live-organization-panel">
              <div className="panel-heading"><div><span className="section-kicker">Authorized execution fabric</span><h2>Models, agents, and policy</h2></div><StatusPill status={activeOrganization.lifecycle ?? "authorized"} /></div>
              <div className="live-organization-grid">
                <div><span>Model routing</span><strong>{humanize(activeModelSelection?.policy ?? "manual")}</strong><small>{activeModelSelection?.rationale ?? "The selected logical model set is preserved."}</small></div>
                <div><span>Participants</span><strong>{activeModelSelection?.participant_ids?.length ?? project?.participating_models.length ?? 0}</strong><small>{activeModelSelection?.participant_ids?.join(" · ") || project?.participating_models.join(" · ") || "Resolving"}</small></div>
                <div><span>Auto task class</span><strong>{humanize(activeModelSelection?.task_complexity ?? "user selected")}</strong><small>Target {activeModelSelection?.requested_participant_target ?? activeModelSelection?.participant_ids?.length ?? 1} participant(s) · {activeModelSelection?.collaboration_rounds ?? 1} bounded round(s)</small></div>
                <div><span>Agent organization</span><strong>{humanize(activeOrganization.economy?.organization_size ?? "bounded")}</strong><small>{activeOrganization.economy?.logical_workers ?? activeOrganization.selected_agents?.length ?? 0} logical workers · manager depth {activeOrganization.economy?.manager_depth ?? 0}</small></div>
                <div><span>Governor</span><strong>{activeOrganization.governor?.authorized ? "Authorized" : "Review required"}</strong><small>{activeOrganization.governor?.reason ?? "Deterministic resource envelope applied."}</small></div>
              </div>
              <div className="agent-role-chips">{activeOrganization.selected_agents?.map((agent) => <span key={agent} title={String(agent).startsWith("custom.") ? "Your custom agent: it shapes this run's instructions and its tool grants." : "Selected by the task planner for this goal."}>{humanize(agent.split(".").pop())}</span>)}</div>
              {/* v1.1.8: state honestly what a selected role does, so the chips
                  are not mistaken for separately executed agents. */}
              <p className="agent-role-note">
                Roles are selected by the task planner from the goal. The research role drives evidence retrieval, and the governor/permission/tool-router roles are the governed tool chain that actually executes local file operations. Other specialist roles shape the run's instructions; they are not separate executing processes in this build.
              </p>
            </article>
          )}
          <article className="panel event-stream">
            <div className="panel-heading"><div><span className="section-kicker">Live state changes</span><h2>Collaboration stream</h2></div><span>v{project?.version ?? 0}</span></div>
            <div className="timeline">
              {events.length ? events.slice(-12).reverse().map((event) => (
                <div className="timeline-event" key={event.id}><div className={`event-dot severity-${event.severity}`} /><div className="event-copy"><div><strong>{humanize(event.event_type)}</strong><span>{event.source}</span><time>{relativeTime(event.created_at)}</time></div><p>{eventSummary(event) || "Canonical state updated."}</p></div></div>
              )) : <p className="muted-copy">Events will appear as participants contribute.</p>}
            </div>
          </article>
        </div>
        <aside className="room-aside">
          <article className="panel progress-panel"><div className="panel-heading"><div><span className="section-kicker">Goal contract</span><h2>Verification</h2></div></div><ProgressRing value={progress} /><div className="criteria-list">{criteria.map((criterion) => <div key={criterion.id} className={criterion.satisfied ? "criterion satisfied" : "criterion"}><i>{criterion.satisfied ? "✓" : ""}</i><span>{criterion.description}</span></div>)}</div></article>
          <article className="panel objection-panel"><div className="panel-heading"><div><span className="section-kicker">Epistemic safety</span><h2>Open objections</h2></div><strong>{unresolved.length}</strong></div>{unresolved.length ? unresolved.map((item) => <div className="objection" key={item.id}><StatusPill status={item.severity} /><p>{item.description}</p><small>Raised by {item.raised_by}</small></div>) : <div className="all-clear"><Icon name="shield" /><div><strong>No unresolved blocker</strong><span>Critical dissent gate is clear.</span></div></div>}</article>
        </aside>
      </section>
    </>
  );

  const whiteboard = !project ? <EmptyState title="No whiteboard yet" detail="Open a Project to see the shared thinking board for its work." /> : (
    <section className="whiteboard-grid">
      {[
        ["Accepted facts", project.accepted_facts, "fact"],
        ["Working hypotheses", project.hypotheses, "hypothesis"],
        ["Assumptions", project.assumptions, "assumption"],
        ["Equations", project.equations, "equation"],
        ["Open questions", project.open_questions, "question"],
        ["Pending ideas", project.pending_ideas, "idea"],
      ].map(([title, items, kind]) => (
        <article className={`board-column board-${kind}`} key={title as string}><div className="board-heading"><h2>{title as string}</h2><span>{(items as string[]).length}</span></div><div className="board-cards">{(items as string[]).length ? (items as string[]).map((item, index) => <div className="board-card" key={`${kind}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><p>{item}</p></div>) : <p className="board-empty">Nothing recorded yet.</p>}</div></article>
      ))}
      <article className="board-column board-wide"><div className="board-heading"><h2>Disagreements & resolutions</h2><span>{project.active_disagreements.length}</span></div><div className="disagreement-grid">{project.active_disagreements.map((item) => <div className={`disagreement-card ${item.resolved ? "resolved" : "open"}`} key={item.id}><div><StatusPill status={item.resolved ? "resolved" : item.severity} /><small>{item.raised_by}</small></div><h3>{item.topic || "Disputed claim"}</h3><p>{item.description}</p>{item.resolution && <blockquote>{item.resolution}</blockquote>}</div>)}</div></article>
    </section>
  );

  const files = !project ? <EmptyState title="No files selected" detail="Choose a Project to browse the documents and results it produced." /> : (
    <section className="split-grid">
      <article className="panel"><div className="panel-heading"><div><span className="section-kicker">Canonical inputs & outputs</span><h2>Project artifacts</h2></div><span>{assets.length} versions</span></div>{assets.length ? <div className="artifact-list">{assets.slice().reverse().map((asset) => <div className="artifact-row" key={asset.record_id}><div className="file-icon">{asset.filename.split(".").pop()?.slice(0, 3).toUpperCase() || "DOC"}</div><div><strong>{asset.filename} <small>v{asset.version}</small></strong><span>{formatBytes(asset.size_bytes)} · {humanize(asset.modality)} · {humanize(asset.extraction_status)} · {asset.chunks.length} chunks · <code>{asset.sha256.slice(0, 12)}</code></span></div></div>)}</div> : <EmptyState title="No files yet" detail="Attach a document or media file in Chat and it will be stored safely with this Project." />}</article>
      <article className="panel"><div className="panel-heading"><div><span className="section-kicker">Local workspace</span><h2>Import evidence</h2></div><Icon name="shield" /></div><div className="storage-card"><strong>{profile?.operating_system ?? "Local machine"}</strong><p>Selected files stay on this device. AMCA stores immutable source bytes, SHA-256 provenance, and bounded local extraction. Images, audio, and video are labeled metadata-only when no truthful local semantic extraction exists.</p><label className={`file-upload ${uploading ? "disabled" : ""}`}><Icon name="files" /><span>{uploading ? "Importing…" : "Choose document, Office, image, audio, or video"}</span><input type="file" accept={CHAT_ATTACHMENT_ACCEPT_ATTRIBUTE} disabled={uploading} onChange={(event) => { void uploadAsset(event.target.files?.[0]); event.currentTarget.value = ""; }} /></label><dl><div><dt>Privacy</dt><dd>{humanize(project.privacy_mode)}</dd></div><div><dt>State version</dt><dd>{project.version}</dd></div><div><dt>Updated</dt><dd>{relativeTime(project.updated_at)}</dd></div></dl></div></article>
    </section>
  );

  const cost = !project ? <EmptyState title="No cost ledger selected" detail="Choose a project to inspect API, local compute, tokens, and latency." /> : (
    <><section className="metrics-grid"><MetricCard label="Total spend" value={formatMoney(project.cost_state.api_cost_usd + project.cost_state.tool_cost_usd + project.cost_state.local_compute_cost_usd)} detail="API + tools + local estimate" tone="green" /><MetricCard label="Project Cumulative Tokens" value={(project.cost_state.input_tokens + project.cost_state.output_tokens).toLocaleString()} detail={`${project.cost_state.input_tokens.toLocaleString()} in · ${project.cost_state.output_tokens.toLocaleString()} out (all runs in project)`} /><MetricCard label="Model calls" value={String(project.cost_state.model_calls)} detail={`${project.cost_state.local_model_calls} local · ${project.cost_state.api_model_calls} API`} /><MetricCard label="Latency" value={`${project.cost_state.latency_seconds.toFixed(2)}s`} detail="Accumulated model latency" /></section><section className="split-grid"><article className="panel"><div className="panel-heading"><div><span className="section-kicker">Allocation</span><h2>Per-model cost</h2></div></div><div className="cost-bars">{Object.entries(project.cost_state.per_model_cost_usd).length ? Object.entries(project.cost_state.per_model_cost_usd).map(([model, amount]) => <div key={model}><div><strong>{model}</strong><span>{formatMoney(amount)}</span></div><i><b style={{ width: `${Math.max(3, Math.min(100, amount / Math.max(project.cost_state.api_cost_usd, 0.0001) * 100))}%` }} /></i></div>) : <p className="muted-copy">No paid model calls were made.</p>}</div></article><article className="panel"><div className="panel-heading"><div><span className="section-kicker">Local resources</span><h2>Compute profile</h2></div></div><dl className="detail-list"><div><dt>Recommended tier</dt><dd>{humanize(profile?.recommended_local_tier)}</dd></div><div><dt>Logical cores</dt><dd>{profile?.logical_cores ?? "—"}</dd></div><div><dt>System RAM</dt><dd>{formatBytes(profile?.total_ram_bytes)}</dd></div><div><dt>Free disk</dt><dd>{formatBytes(profile?.free_disk_bytes)}</dd></div></dl></article></section></>
  );

  const benchmarkView = (
    <section className="panel benchmark-panel"><div className="panel-heading"><div><span className="section-kicker">Evaluation laboratory</span><h2>Benchmark fixtures</h2></div><span>{benchmarks.length} tasks</span></div><p className="panel-intro">These contracts test mathematics, science, coding, and research-planning behavior without hiding failures behind a single aggregate score.</p><div className="benchmark-grid">{benchmarks.map((task, index) => <article key={task.id}><div><span>{String(index + 1).padStart(2, "0")}</span><StatusPill status={String(task.domain ?? "fixture")} /></div><h3>{String(task.title ?? task.goal ?? task.id)}</h3><p>{String(task.prompt ?? task.goal ?? "Machine-readable evaluation contract")}</p><footer><code>{task.id}</code><span>Ready</span></footer></article>)}</div></section>
  );

  const approvalView = !project ? (
    <EmptyState title="No approval queue selected" detail="Choose a project to review its exact-scope permission requests." />
  ) : (
    <section className="panel approval-panel">
      <div className="panel-heading">
        <div><span className="section-kicker">Human authority</span><h2>Exact-scope approvals</h2></div>
        <span>{approvals.filter((item) => item.status === "pending").length} pending</span>
      </div>
      <p className="panel-intro">Cloud transmission, network access, code execution, deletion, and irreversible actions remain blocked until a matching request is approved. Each approval is bound to the displayed scope digest and use count.</p>
      {pendingChat?.project_id === project.project_id && (
        <div className="pending-chat-run">
          <div><strong>Pending {humanize(pendingChat.mode)} Chat run</strong><span>{pendingChat.participant_ids.length} participants · exact transmission scope · one round</span></div>
          <button className="primary-button" onClick={() => void executePendingChat()} disabled={starting || !pendingChat.approval_ids.every((id) => approvals.some((item) => item.id === id && item.status === "approved"))}>
            <Icon name="play" /> Run approved team
          </button>
        </div>
      )}
      <div className="approval-list">
        {approvals.length ? approvals.map((approval) => (
          <article className={`approval-card risk-${approval.risk}`} key={approval.id}>
            <div className="approval-card-heading"><div><span className={"approval-kind " + (approval.action === "transmit_excerpt" || approval.action === "call_cloud_model" || approval.action === "network_request" ? "kind-security-network" : "kind-security-permission")}>{approval.action === "transmit_excerpt" || approval.action === "call_cloud_model" || approval.action === "network_request" ? "Security · Network permission" : "Security · Permission approval"}</span><StatusPill status={approval.status} /><StatusPill status={approval.risk} /></div><time>{relativeTime(approval.created_at)}</time></div>
            <h3>{humanize(approval.action)}</h3>
            <p>{approval.reason}</p>
            <dl>
              {Object.entries(approval.scope).map(([key, value]) => <div key={key}><dt>{humanize(key)}</dt><dd>{valueText(value)}</dd></div>)}
              <div><dt>Scope digest</dt><dd><code>{approval.scope_sha256.slice(0, 16)}</code></dd></div>
              <div><dt>Use limit</dt><dd>{approval.uses} / {approval.max_uses}</dd></div>
              <div><dt>Expires</dt><dd>{relativeTime(approval.expires_at)}</dd></div>
            </dl>
            {approval.status === "pending" && <div className="approval-actions"><button className="secondary-button danger" onClick={() => void decideApproval(approval.id, false)}>Deny</button><button className="primary-button" onClick={() => void decideApproval(approval.id, true)}>Approve exact scope</button></div>}
          </article>
        )) : <EmptyState title="No permission requests" detail="AMCA has not asked for any consequential action in this project." />}
      </div>
    </section>
  );

  const logs = !project ? <EmptyState title="No decision log selected" detail="Choose a project to inspect its auditable event history." /> : (
    <section className="panel log-panel"><div className="panel-heading"><div><span className="section-kicker">Replayable history</span><h2>Canonical decision log</h2></div><span>{events.length} events</span></div><div className="log-table"><div className="log-header"><span>Version</span><span>Event</span><span>Source</span><span>Summary</span><span>Time</span></div>{events.slice().reverse().map((event) => <div className="log-row" key={event.id}><code>v{event.state_version}</code><span><StatusPill status={event.event_type} /></span><strong>{event.source}</strong><p>{eventSummary(event) || "State transition"}</p><time>{relativeTime(event.created_at)}</time></div>)}</div></section>
  );


  const computerAccess = !permissionProjectId ? (
    <EmptyState title="No chat or Project selected" detail="Open a chat to review and change AMCA's local access." />
  ) : (
    <section className="workspace-view permission-workspace">
      <div className="view-heading"><div><span className="section-kicker">Governed local tools</span><h1>Computer access</h1><p>Choose a policy, grant exact folders, review every consequential action, and revoke access at any time.</p></div><StatusPill status={localTools?.profile.configured ? localTools.profile.profile : "not configured"} /></div>
      <div className="permission-grid">
        <article className="panel permission-profile-panel">
          <div className="panel-heading"><div><span className="section-kicker">Global policy</span><h2>Permission profile</h2></div><Icon name="shield" /></div>
          <div className="profile-choice-grid">
            {ACCESS_MODES.map((mode) => <button key={mode.id} className={permissionProfileDraft === mode.id ? "selected" : ""} onClick={() => setPermissionProfileDraft(mode.id)}><strong>{mode.title}</strong><small>{mode.detail}</small></button>)}
          </div>
          <button className="primary-button" disabled={permissionBusy} onClick={() => void savePermissionProfile()}>{permissionBusy ? "Saving…" : "Save profile"}</button>
          <p className="settings-note">Applies to every chat, Project, and model. Full access reaches every fixed drive attached right now; removable and network drives are never included. Overwriting, moving, deleting, or restoring an existing file still asks first, and running commands or controlling apps remains separately approved.</p>
          {localTools?.full_access && <div className="full-access-scope">
            <div className="panel-heading"><div><span className="section-kicker">Full access coverage</span><h2>Drives in scope</h2></div><button type="button" className="secondary-button" disabled={permissionBusy} onClick={() => void refreshFullAccessScope()}>Refresh drives</button></div>
            <p className="settings-note">{localTools.full_access.drives_granted.length
              ? `In scope: ${localTools.full_access.drives_granted.join(", ")}`
              : "Full access is not active."}{localTools.full_access.drives_missing.length
                ? ` A drive attached after Full access was switched on is not covered until you refresh: ${localTools.full_access.drives_missing.join(", ")}.`
                : ""}</p>
          </div>}
        </article>

        <article className="panel">
          <div className="panel-heading"><div><span className="section-kicker">Active authority</span><h2>Folder grants</h2></div><span>{localTools?.grants.length ?? 0}</span></div>
          <p className="settings-note">Older per-folder grants (created before this simple view) are listed here so you can revoke them. New grants are not needed: the three modes above cover access.</p>
          <div className="grant-list">{localTools?.grants.length ? localTools.grants.map((grant) => <article key={grant.grant_id} className={grant.active ? "" : "revoked"}><div><StatusPill status={grant.active ? "active" : "revoked"} /><time>{grant.expires_at ? `Expires ${relativeTime(grant.expires_at)}` : "No expiry"}</time></div><strong>{grant.canonical_root}</strong><p>{grant.reason}</p>{grant.active && <button className="secondary-button danger" disabled={permissionBusy} onClick={() => void revokePermissionGrant(grant.grant_id)}>Revoke</button>}</article>) : <EmptyState title="No folder grants" detail="The three modes above cover access; nothing else is needed." />}</div>
        </article>
      </div>

      <div className="permission-grid">
        <article className="panel">
          <div className="panel-heading"><div><span className="section-kicker">Human decisions</span><h2>Local approvals</h2></div><span>{localApprovals.filter((item) => item.status === "pending").length} pending</span></div>
          <div className="compact-approval-list">{localApprovals.filter((item) => ["local_file_modify", "local_file_move", "local_file_trash", "run_command", "control_application"].includes(item.action)).slice(0, 20).map((approval) => <article key={approval.id}><div><StatusPill status={approval.status} /><StatusPill status={approval.risk} /></div><strong>{humanize(approval.action)}</strong><p>{approval.reason}</p>{approval.status === "pending" && <div className="approval-actions"><button className="secondary-button danger" onClick={() => void decideApproval(approval.id, false)}>Deny</button><button className="primary-button" onClick={() => void decideApproval(approval.id, true)}>Approve exact scope</button></div>}</article>)}</div>
        </article>
        <article className="panel">
          <div className="panel-heading"><div><span className="section-kicker">Audit provenance</span><h2>Tool activity</h2></div><span>{localTools?.events.length ?? 0}</span></div>
          <div className="tool-event-list">{localTools?.events.slice(0, 30).map((event) => <article key={event.event_id}><div><StatusPill status={event.outcome} /><time>{relativeTime(event.created_at)}</time></div><strong>{humanize(event.action)}</strong><code>{event.target || "Project policy"}</code><small>{event.actor}</small></article>)}</div>
        </article>
      </div>
    </section>
  );

  const settings = (
    <section className="settings-grid">
      <article className="panel">
        <div className="panel-heading"><div><span className="section-kicker">Appearance</span><h2>Theme</h2></div></div>
        <div className="theme-picker" role="radiogroup" aria-label="Appearance">
          {([["system", "System", "Follow OS theme"], ["light", "Light", "Bright surfaces"], ["dark", "Dark", "Current default"]] as Array<[ThemePreference, string, string]>).map(([value, label, detail]) => (
            <button key={value} className={`theme-option ${themePreference === value ? "active" : ""}`} role="radio" aria-checked={themePreference === value} onClick={() => setThemePreference(value)}>
              <strong>{label}</strong><small>{detail}</small>
            </button>
          ))}
        </div>
        <p className="settings-note">Selection is remembered on this device. System mode follows the OS theme.</p>
      </article>
      <IntelligencePanel />
      <CostBudgetPanel />
      <AboutUpdatesPanel />
      <article className="panel">
        <div className="panel-heading"><div><span className="section-kicker">Service</span><h2>Local connection</h2></div><StatusPill status={online ? "connected" : "offline"} /></div>
        <dl className="detail-list"><div><dt>Endpoint</dt><dd><code>{API_LABEL}</code></dd></div><div><dt>Binding</dt><dd>Loopback only</dd></div><div><dt>Canonical state</dt><dd>Python + SQLite</dd></div><div><dt>UI authority</dt><dd>Command client only</dd></div></dl>
      </article>
      <article className="panel">
        <div className="panel-heading"><div><span className="section-kicker">Privacy policy</span><h2>Transmission boundary</h2></div><Icon name="shield" /></div>
        <div className="policy-card"><strong>Local-first with exact approval</strong><p>Ollama stays offline. A cloud model creates an exact-scope approval before the first network request, and every desktop Chat run is limited to one round.</p></div>
      </article>
      <ModelHub
        onRefreshLocal={refreshModels}
        onUseModels={(modelRefs) => {
          setSelectedTeam(modelRefs);
          setModelSelectionMode("manual");
          setChatMode(modelRefs.length > 1 ? "joint" : "solo");
          if (modelRefs.length === 1) setSelectedModel(modelRefs[0]);
          setView("chat");
        }}
      />
      <article className="panel settings-wide">
        <div className="panel-heading"><div><span className="section-kicker">Attachment intelligence</span><h2>Local media processing</h2></div><span>No network</span></div>
        <p className="settings-note">Printed image text and supported speech are extracted on this computer before model context is built. Missing or failed processors remain metadata-only and are never presented as understood content.</p>
        <div className="hardware-grid">
          <div><span>Image OCR</span><strong>{mediaRuntimes.ocr.available ? `${mediaRuntimes.ocr.runtime} ready` : "Runtime unavailable"}</strong><small>{mediaRuntimes.ocr.scope}</small></div>
          <div><span>Audio transcription</span><strong>{mediaRuntimes.transcription.available ? `${mediaRuntimes.transcription.runtime} ready` : "Runtime unavailable"}</strong><small>{mediaRuntimes.transcription.model ?? mediaRuntimes.transcription.scope}</small></div>
        </div>
      </article>
      <article className="panel settings-wide">
        <div className="panel-heading"><div><span className="section-kicker">Cloud model vault</span><h2>API models</h2></div><button className="primary-button" type="button" onClick={() => setShowAddModel((value) => !value)} disabled={!credentialStatus?.write_available_in_ui}><span aria-hidden="true">＋</span> {showAddModel ? "Close" : "Add model"}</button></div>
        <p className="settings-note">Add OpenAI, Anthropic/Claude, Kimi, Tabitoken, Groq, or another OpenAI-compatible endpoint. Model metadata goes to SQLite; API credentials are write-only and protected by Windows. Adding or selecting a model never starts a request.</p>
        {showAddModel && (
          <form className="model-profile-form" onSubmit={(event) => void addModelProfile(event)}>
            <div className="model-profile-form-heading"><div><strong>Add an API model</strong><span>One model and one credential per profile</span></div><StatusPill status="no network call" /></div>
            <div className="model-profile-fields">
              <label><span>Provider</span><select value={newModel.provider_kind} onChange={(event) => changeProviderPreset(event.target.value as NewModelProfile["provider_kind"])}>{Object.entries(PROVIDER_PRESETS).map(([id, preset]) => <option value={id} key={id}>{preset.label}</option>)}</select></label>
              <label><span>Display name</span><input required maxLength={160} value={newModel.label} onChange={(event) => setNewModel((current) => ({ ...current, label: event.target.value }))} placeholder="My GPT model" /></label>
              <label><span>Model ID</span><input required maxLength={160} value={newModel.model} onChange={(event) => setNewModel((current) => ({ ...current, model: event.target.value }))} placeholder="gpt-5-mini" /></label>
              <label className="profile-endpoint"><span>HTTPS API base URL</span><input required type="url" value={newModel.endpoint} onChange={(event) => setNewModel((current) => ({ ...current, endpoint: event.target.value }))} placeholder="https://provider.example/v1" /></label>
              <label><span>Pricing basis</span><select value={newModel.pricing_basis} onChange={(event) => setNewModel((current) => ({ ...current, pricing_basis: event.target.value as NewModelProfile["pricing_basis"], input_cost_per_million: 0, output_cost_per_million: 0, price_per_request_usd: 0 }))}><option value="token" disabled={newModel.provider_kind === "tabitoken"}>Per 1M tokens</option><option value="request" disabled={["anthropic", "kimi"].includes(newModel.provider_kind)}>Fixed per request</option><option value="free" disabled={newModel.provider_kind === "tabitoken"}>Free tier</option></select></label>
              {newModel.pricing_basis === "token" && <><label><span>Input USD / 1M</span><input required type="number" min="0" step="0.000001" value={newModel.input_cost_per_million} onChange={(event) => setNewModel((current) => ({ ...current, input_cost_per_million: Number(event.target.value) }))} /></label><label><span>Output USD / 1M</span><input required type="number" min="0" step="0.000001" value={newModel.output_cost_per_million} onChange={(event) => setNewModel((current) => ({ ...current, output_cost_per_million: Number(event.target.value) }))} /></label></>}
              {newModel.pricing_basis === "request" && <label><span>USD / request</span><input required type="number" min="0.000001" step="0.000001" value={newModel.price_per_request_usd} onChange={(event) => setNewModel((current) => ({ ...current, price_per_request_usd: Number(event.target.value) }))} /></label>}
              <label className="profile-secret"><span>API credential</span><input required type="password" autoComplete="new-password" value={newModel.secret} onChange={(event) => setNewModel((current) => ({ ...current, secret: event.target.value }))} placeholder="Stored in Windows protection, never shown again" /></label>
            </div>
            <div className="model-profile-form-footer"><span><Icon name="shield" /> HTTPS only · redirects blocked · no test call</span><button className="primary-button" type="submit" disabled={credentialBusy !== null || !newModel.secret.trim()}>{credentialBusy === "new-model" ? "Saving…" : "Save model securely"}</button></div>
          </form>
        )}
        <div className="credential-grid">
          {credentialStatus?.credentials.map((credential) => (
            <article className={`credential-card ${credential.configured ? "configured" : ""}`} key={credential.credential_id}>
              <div className="credential-heading"><div><strong>{credential.label}</strong><small>{humanize(credential.provider)} · {modelPriceLabel(credential)}</small></div><StatusPill status={credential.configured ? "configured" : "not configured"} /></div>
              <code>{credential.model} · {credential.endpoint}</code>
              <input
                type="password"
                autoComplete="new-password"
                placeholder={credential.configured ? "Enter a replacement API key" : "Paste API key"}
                value={credentialDrafts[credential.credential_id] ?? ""}
                disabled={!credentialStatus?.write_available_in_ui || credentialBusy === credential.credential_id}
                onChange={(event) => setCredentialDrafts((current) => ({ ...current, [credential.credential_id]: event.target.value }))}
              />
              <div className="credential-actions">
                <button className="primary-button" disabled={!credentialDrafts[credential.credential_id]?.trim() || credentialBusy !== null} onClick={() => void saveCredential(credential.credential_id)}>{credential.configured ? "Replace" : "Save securely"}</button>
                {credential.configured && <button className="secondary-button danger" disabled={credentialBusy !== null} onClick={() => void removeCredential(credential.credential_id)}>Remove</button>}
                {!credential.is_builtin && <button className="secondary-button danger" disabled={credentialBusy !== null} onClick={() => void deleteModelProfile(credential.profile_id, credential.label)}>Delete model</button>}
              </div>
              <label className="team-toggle"><input type="checkbox" checked={selectedTeam.includes(credential.credential_id)} disabled={!credential.configured} onChange={() => toggleTeamModel(credential.credential_id)} /> Include in Joint team</label>
            </article>
          )) ?? <p className="muted-copy">Credential catalog unavailable.</p>}
        </div>
        <p className="settings-note">Selected team: {selectedTeam.length ? selectedTeam.join(" · ") : "none"}. Local-only Joint runs start immediately; any cloud participant creates exact-scope approvals before network access.</p>
      </article>
      <article className="panel settings-wide"><div className="panel-heading"><div><span className="section-kicker">This computer</span><h2>Hardware-aware routing</h2></div></div>{profile ? <div className="hardware-grid"><div><span>Operating system</span><strong>{profile.operating_system}</strong></div><div><span>Processor</span><strong>{profile.cpu}</strong></div><div><span>Memory</span><strong>{formatBytes(profile.total_ram_bytes)}</strong></div><div><span>Local model tier</span><strong>{humanize(profile.recommended_local_tier)}</strong></div>{profile.gpus.map((gpu) => <div key={gpu.name}><span>GPU · {gpu.vendor}</span><strong>{gpu.name} · {formatBytes(gpu.total_vram_bytes)}</strong></div>)}</div> : <p className="muted-copy">Hardware profile unavailable.</p>}</article>
    </section>
  );

  const workbenchAsset = assets.find((asset) => asset.record_id === selectedWorkbenchAsset) ?? assets[0] ?? null;
  const projectRuns = project ? dashboard.runs.filter((run) => run.project_id === project.project_id) : [];
  const projectChanges = events.filter((event) => ["artifact_update", "result_update", "task_cost_finalized"].includes(event.event_type));
  const pendingReviewApprovals = approvals.filter((approval) => approval.status === "pending");
  const workbench = !project ? (
    <EmptyState title="No engineering Project selected" detail="Open a Project or attach a repository folder in Chat. Engineering tools stay inside the same AMCA conversation." />
  ) : (
    <section className="workbench-shell">
      <div className="view-heading"><div><span className="section-kicker">Unified engineering workspace</span><h1>Project workbench</h1><p>Files, changes, execution output, tests, and repository state remain attached to the canonical Project and conversation.</p></div><StatusPill status={project.status} /></div>
      <nav className="workbench-tabs" aria-label="Engineering workbench">
        {(["files", "changes", "terminal", "tests", "git"] as const).map((tab) => (
          <button key={tab} className={workbenchTab === tab ? "active" : ""} onClick={() => setWorkbenchTab(tab)}>{tab.toUpperCase()}<small>{tab === "files" ? assets.length : tab === "changes" ? projectChanges.length : tab === "tests" ? projectRuns.length : ""}</small></button>
        ))}
      </nav>
      <div className="workbench-surface">
        {workbenchTab === "files" && <div className="workbench-files">
          <aside><div className="workbench-pane-title"><strong>Project files</strong><span>{assets.length}</span></div>{assets.length ? assets.map((asset) => <button key={asset.record_id} className={workbenchAsset?.record_id === asset.record_id ? "active" : ""} onClick={() => setSelectedWorkbenchAsset(asset.record_id)}><Icon name="files" /><span><strong>{asset.filename}</strong><small>{humanize(asset.modality)} · v{asset.version}</small></span></button>) : <p>No files attached.</p>}
            <div className="workbench-pane-title" style={{ marginTop: 12 }}><strong>Editable workspace</strong><span>{workspaceFileList.length}</span></div>
            {workspaceFileList.length ? workspaceFileList.map((file) => <button key={file.path} className={workspaceEditor?.path === file.path ? "active" : ""} onClick={() => void openWorkspaceFile(file.path)}><Icon name="files" /><span><strong>{file.path}</strong><small>v{file.version} · {formatBytes(file.bytes)}</small></span></button>) : <p>No workspace files yet.</p>}
            <div className="workbench-pane-title" style={{ marginTop: 12 }}><strong>Artifacts</strong><span>{artifactList.length}</span></div>
            {artifactList.length ? artifactList.map((meta) => <button key={meta.artifact_id} className={artifactView?.meta.artifact_id === meta.artifact_id ? "active" : ""} onClick={() => void openArtifact(meta)}><Icon name="brain" /><span><strong>{meta.title}</strong><small>{meta.artifact_type} · v{meta.current_version}</small></span></button>) : <p>No artifacts authored yet.</p>}
            <button className="secondary-button" style={{ marginTop: 10 }} disabled={localBusy || !project?.project_id} onClick={() => { const name = window.prompt("New workspace file path", "notes/idea.md"); if (name) void (async () => { try { await amca.workspaceSave(project!.project_id, name, ""); setWorkspaceNotice(`Created ${name}.`); await refreshWorkspaceData(project!.project_id); } catch (caught) { setWorkspaceNotice(caught instanceof Error ? caught.message : String(caught)); } })(); }}><Icon name="files" /> New workspace file</button>
          </aside>
          <article>
            {workspaceEditor && (
              <div style={{ marginBottom: 16 }}>
                <div className="workbench-pane-title"><div><strong>{workspaceEditor.path}</strong><small>Workspace v{workspaceEditor.version}</small></div><button className="secondary-button" onClick={() => void saveWorkspaceFile()} disabled={localBusy}><Icon name="files" /> Save</button></div>
                {workspaceNotice && <p className="inline-warning">{workspaceNotice}</p>}
                <textarea aria-label={`Edit ${workspaceEditor.path}`} value={workspaceDraft} onChange={(event) => setWorkspaceDraft(event.target.value)} spellCheck={false} style={{ width: "100%", minHeight: 200, fontFamily: "ui-monospace,monospace", fontSize: 11, background: "rgba(0,0,0,.25)", color: "#c8d5cd", border: "1px solid var(--line)", borderRadius: 8, padding: 10 }} />
              </div>
            )}
            {artifactView && (
              <div style={{ marginBottom: 16 }}>
                <div className="workbench-pane-title"><div><strong>{artifactView.meta.title}</strong><small>{artifactView.meta.artifact_type} · v{artifactView.source.version}</small><small>by {artifactView.meta.created_by}</small></div>
                  <div style={{ display: "flex", gap: 6 }}>
                    {!artifactView.editing && <button className="secondary-button" onClick={() => setArtifactView({ ...artifactView, editing: true, draft: artifactView.source.content })}>Edit</button>}
                    {["html", "zip", "docx", "pdf", "xlsx", "pptx"].map((format) => <button key={format} className="secondary-button" onClick={() => void exportArtifactAs(format)} disabled={localBusy}>{format}</button>)}
                    {/* v1.1.8: a bibliography keeps its native BibTeX source,
                        so it must be downloadable as .bib from here too - the
                        separate bibliography was previously not retrievable. */}
                    {artifactView.meta.artifact_type === "bib" && <button className="secondary-button" disabled={localBusy} onClick={() => void saveFile(`${(artifactView.meta.title || "Bibliography").replace(/[<>:"/\|?*]/g, "_").replace(/\.bib$/i, "")}.bib`, artifactView.source.content)}>Download .bib</button>}
                    {(artifactView.meta.versions ?? []).filter((v) => v.version < artifactView.source.version).slice(-3).map((v) => (
                      <button key={v.version} className="secondary-button" onClick={() => void restoreArtifactVersion(v.version)}>Restore v{v.version}</button>
                    ))}
                  </div>
                </div>
                {artifactView.notice && <p className="inline-warning">{artifactView.notice}</p>}
                <dl className="detail-list"><div><dt>Provenance</dt><dd>{JSON.stringify(artifactView.source.provenance)}</dd></div></dl>
                {artifactView.editing ? (
                  <>
                    <textarea aria-label={`Edit artifact ${artifactView.meta.title}`} value={artifactView.draft} onChange={(event) => setArtifactView({ ...artifactView, draft: event.target.value })} spellCheck={false} style={{ width: "100%", minHeight: 200, fontFamily: "ui-monospace,monospace", fontSize: 11, background: "rgba(0,0,0,.25)", color: "#c8d5cd", border: "1px solid var(--line)", borderRadius: 8, padding: 10 }} />
                    <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                      <button className="secondary-button" onClick={() => void saveArtifactEdit()}>Save new version</button>
                      <button className="secondary-button" onClick={() => setArtifactView({ ...artifactView, editing: false })}>Cancel</button>
                    </div>
                  </>
                ) : artifactView.meta.artifact_type === "pdf" ? (
                  // Manager decision (PDF preview): classified unavailable for
                  // v1.1.8 — state it explicitly instead of rendering an
                  // incoherent binary dump; Download still delivers the file.
                  <p className="inline-warning">{PDF_PREVIEW_UNAVAILABLE}</p>
                ) : (
                  <pre style={{ whiteSpace: "pre-wrap", fontSize: 10, color: "#a4b3aa", background: "rgba(0,0,0,.2)", padding: 10, borderRadius: 8 }}>{artifactView.source.content}</pre>
                )}
              </div>
            )}
            {workbenchAsset ? <><div className="workbench-pane-title"><div><strong>{workbenchAsset.filename}</strong><small><code>{workbenchAsset.sha256}</code></small></div><StatusPill status={workbenchAsset.extraction_status} /></div><dl className="detail-list"><div><dt>Media type</dt><dd>{workbenchAsset.media_type}</dd></div><div><dt>Size</dt><dd>{formatBytes(workbenchAsset.size_bytes)}</dd></div><div><dt>Extraction</dt><dd>{workbenchAsset.extraction_method}</dd></div><div><dt>Chunks</dt><dd>{workbenchAsset.chunks.length}</dd></div><div><dt>Source</dt><dd>{humanize(workbenchAsset.source)}</dd></div></dl><p className="settings-note">{workbenchAsset.extraction_note}</p><div className="locator-list">{workbenchAsset.chunks.slice(0, 30).map((chunk) => <span key={`${chunk.index}-${chunk.locator}`}><b>{chunk.locator}</b><code>{chunk.sha256.slice(0, 12)}</code></span>)}</div></> : <EmptyState title="No file selected" detail="Drop files or a repository into Chat." />}
          </article>
        </div>}
        {workbenchTab === "changes" && <div className="workbench-feed">
          <div className="workbench-pane-title"><strong>Workspace diffs</strong><span>{workspaceChangesList.length}</span></div>
          {workspaceChangesList.length ? workspaceChangesList.map((entry) => (
            <article key={`${entry.path}-v${entry.version}`}>
              <div><StatusPill status="workspace diff" /><time>{relativeTime(new Date(entry.saved_at * 1000).toISOString())}</time></div>
              <strong>{entry.path} · v{entry.version}</strong>
              <pre style={{ whiteSpace: "pre-wrap", fontSize: 10, color: "#9fb3a8", margin: "6px 0 0" }}>{entry.diff}</pre>
              {workspaceEditor?.path === entry.path && (
                <button className="secondary-button" style={{ marginTop: 6 }} onClick={() => void revertWorkspaceFile(entry.path, entry.version - 1)} disabled={localBusy || entry.version < 2}>
                  Revert to v{entry.version - 1}
                </button>
              )}
            </article>
          )) : <p>No workspace file edits recorded yet.</p>}
          <div className="workbench-pane-title" style={{ marginTop: 12 }}><strong>Canonical changes</strong><span>{projectChanges.length}</span></div>{projectChanges.length ? projectChanges.slice().reverse().map((event) => <article key={event.id}><div><StatusPill status={event.event_type} /><time>{relativeTime(event.created_at)}</time></div><strong>{event.source}</strong><p>{eventSummary(event) || "Project state changed."}</p></article>) : <EmptyState title="No recorded changes" detail="File, artifact, result, and cost changes appear here with provenance." />}
        </div>}
        {workbenchTab === "terminal" && <div className="terminal-pane">
          <div className="workbench-pane-title"><div><strong>Approved command execution</strong><small>Deterministic allowlist · approval required · audited</small></div></div>
          {commandGrants.length ? (
            <>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", margin: "8px 0" }}>
                <select aria-label="Command grant" value={selectedCommandGrant?.grant_id ?? ""} onChange={(event) => setSelectedCommandGrant(commandGrants.find((grant) => grant.grant_id === event.target.value) ?? null)} style={{ background: "rgba(0,0,0,.3)", color: "#c8d5cd", border: "1px solid var(--line)", borderRadius: 6, padding: "5px 8px", fontSize: 10 }}>
                  {commandGrants.map((grant) => <option key={grant.grant_id} value={grant.grant_id}>{grant.canonical_root.split(/[\\/]/).pop()} · {grant.grant_id.slice(0, 8)}</option>)}
                </select>
                <input aria-label="Command argv (comma separated)" value={argvDraft} onChange={(event) => setArgvDraft(event.target.value)} placeholder="git, --version" style={{ flex: 1, minWidth: 180, background: "rgba(0,0,0,.3)", color: "#c8d5cd", border: "1px solid var(--line)", borderRadius: 6, padding: "5px 8px", fontSize: 10, fontFamily: "ui-monospace,monospace" }} />
                <button className="secondary-button" onClick={() => void proposeArgvCommand()} disabled={commandBusy || !selectedCommandGrant}><Icon name="files" /> Propose</button>
                <input aria-label="Approval id" value={commandApprovalId} onChange={(event) => setCommandApprovalId(event.target.value)} placeholder="approval id (after Review Queue)" style={{ width: 220, background: "rgba(0,0,0,.3)", color: "#c8d5cd", border: "1px solid var(--line)", borderRadius: 6, padding: "5px 8px", fontSize: 10, fontFamily: "ui-monospace,monospace" }} />
                <button className="secondary-button" onClick={() => void runApprovedArgvCommand()} disabled={commandBusy || !commandApprovalId.trim() || !selectedCommandGrant}><Icon name="brain" /> Run approved</button>
              </div>
              <pre aria-label="Command output" style={{ minHeight: 90 }}>{commandRunLog || latestRun?.log || "No AMCA-issued command or execution output has been recorded for this Project."}</pre>
              <p>Commands run only as exact reviewed argv under an explicit approval, inside the granted path, with hard timeout and output caps. Shells and destructive executables are refused by policy even with approval.</p>
            </>
          ) : (
            <>
              <pre>{latestRun?.log || "No AMCA-issued command or execution output has been recorded for this Project."}</pre>
              <p>Create a Computer-access grant with the propose-command capability to enable bounded approved commands. Interactive shell execution remains permission-gated; AMCA does not present a decorative terminal that bypasses command approvals.</p>
            </>
          )}
        </div>}
        {workbenchTab === "tests" && <div className="test-pane"><div className="workbench-pane-title"><strong>Verification runs</strong><span>{projectRuns.length}</span></div>{projectRuns.length ? projectRuns.map((run) => <article key={run.run_id}><div><StatusPill status={run.status} /><time>{relativeTime(run.finished_at ?? run.created_at)}</time></div><strong>{run.goal}</strong><dl><div><dt>Mode</dt><dd>{humanize(run.mode)}</dd></div><div><dt>Model</dt><dd>{run.model}</dd></div><div><dt>Result</dt><dd>{run.error || (run.result ? "Canonical result available" : "Pending")}</dd></div></dl></article>) : <EmptyState title="No verification runs" detail="Tests and evaluated runs linked to this Project appear here." />}</div>}
        {workbenchTab === "git" && <div className="git-pane"><div className="workbench-pane-title"><div><strong>Repository boundary</strong><small>Project-scoped and approval controlled</small></div><StatusPill status={assets.some((asset) => /(^|__)(\.gitignore|package\.json|pyproject\.toml|Cargo\.toml)$/i.test(asset.filename)) ? "repository detected" : "not mounted"} /></div><div className="git-summary"><MetricCard label="Tracked project assets" value={String(assets.length)} detail="Hashed canonical versions" /><MetricCard label="Recorded changes" value={String(projectChanges.length)} detail="Event-sourced provenance" /><MetricCard label="Runs" value={String(projectRuns.length)} detail="Linked execution records" /></div><p>Repository files can be dropped into Chat now. Branches, worktrees, commits, conflicts, and writable diffs will be enabled only through the repository permission profile; AMCA does not infer write access from possession of files.</p></div>}
      </div>
    </section>
  );

  const saveAutomationDraft = async () => {
    if (!project || !automationDraft.name.trim() || !automationDraft.goal.trim()) return;
    setStarting(true);
    setError(null);
    try {
      await amca.saveAutomation({ ...automationDraft, project_id: project.project_id });
      setAutomationDefinitions((await amca.automations()).automations);
      setAutomationDraft({ name: "", goal: "", automation_type: "once", schedule: "manual", enabled: false });
      setShowAutomationForm(false);
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setStarting(false); }
  };
  const toggleAutomation = async (automation: AutomationDefinition) => {
    try {
      await amca.saveAutomation({ ...automation, enabled: !automation.enabled });
      setAutomationDefinitions((await amca.automations()).automations);
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
  };
  const removeAutomation = async (automationId: string) => {
    try {
      await amca.deleteAutomation(automationId);
      setAutomationDefinitions((await amca.automations()).automations);
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
  };
  const runAutomationNow = async (automation: AutomationDefinition) => {
    setStarting(true);
    setError(null);
    try {
      const result = await amca.startChat(automation.goal, [], "joint", "auto_intelligence", automation.project_id);
      if (result.status === "waiting_for_approval") {
        setPendingChat(result);
        await loadProject(result.project_id);
        setView("review");
      } else {
        setActiveRun(result);
        setDashboard(await amca.dashboard());
        setView("review");
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setStarting(false); }
  };
  const automations = (
    <section className="workspace-view">
      <div className="view-heading"><div><span className="section-kicker">Background work</span><h1>Automations</h1><p>Durable task definitions use Auto Intelligence, Project boundaries, cost telemetry, Agent Governor, and Review Queue.</p></div><button className="primary-button" onClick={() => setShowAutomationForm((value) => !value)} disabled={!project}>+ New automation</button></div>
      {showAutomationForm && <article className="panel automation-form"><div className="panel-heading"><div><span className="section-kicker">Project-scoped definition</span><h2>Create automation</h2></div><StatusPill status="disabled by default" /></div><div className="automation-fields"><label><span>Name</span><input value={automationDraft.name} onChange={(event) => setAutomationDraft((current) => ({ ...current, name: event.target.value }))} placeholder="Weekly project review" /></label><label><span>Type</span><select value={automationDraft.automation_type} onChange={(event) => setAutomationDraft((current) => ({ ...current, automation_type: event.target.value as AutomationDefinition["automation_type"] }))}><option value="once">Once</option><option value="recurring">Recurring</option><option value="conditional">Conditional</option><option value="monitor">Monitor</option><option value="event_triggered">Event-triggered</option></select></label><label><span>Schedule or trigger</span><input value={automationDraft.schedule} onChange={(event) => setAutomationDraft((current) => ({ ...current, schedule: event.target.value }))} placeholder="Every Monday at 09:00" /></label><label className="automation-goal"><span>Task</span><textarea value={automationDraft.goal} onChange={(event) => setAutomationDraft((current) => ({ ...current, goal: event.target.value }))} placeholder="Describe the outcome AMCA should produce…" /></label></div><div className="form-actions"><button onClick={() => setShowAutomationForm(false)}>Cancel</button><button className="primary-button" onClick={() => void saveAutomationDraft()} disabled={starting || !automationDraft.name.trim() || !automationDraft.goal.trim()}>Save definition</button></div><p className="settings-note">Saving does not start network work. Enabling records intent; execution still passes exact provider and privacy authorization, with cost recorded as telemetry only.</p></article>}
      <p className="muted-copy">{AUTOMATION_READINESS_NOTE}</p>
      <article className="panel settings-wide">
                  <div className="panel-heading">
                    <div>
                      <span className="section-kicker">Automation manager</span>
                      <h2>{automationDefinitions.length ? "Saved definitions" : AUTOMATION_EMPTY_STATES.no_records}</h2>
                    </div>
                    <span className="status-pill">
                      <i />
                      {automationSchedulerStatus === null
                        ? "Scheduler unavailable"
                        : automationSchedulerStatus.running && automationSchedulerStatus.worker_alive
                          ? "Background scheduler: Running"
                          : "Scheduler stopped"}
                    </span>
                  </div>
                  <AutostartToggle />
                  {automationDefinitions.length ? (
                    <div className="automation-list">
                      {automationDefinitions.map((automation) => (
                        <AutomationCard
                          key={automation.id}
                          automation={automation}
                          schedulerRunning={automationSchedulerStatus ? automationSchedulerStatus.running && automationSchedulerStatus.worker_alive : null}
                          starting={starting}
                          onRunNow={(item) => void runAutomationNow(item)}
                          onToggle={(item) => void toggleAutomation(item)}
                          onDelete={(id) => void removeAutomation(id)}
                        />
                      ))}
                    </div>
                  ) : (
                    <p className="settings-note">Create a disabled-by-default definition for the selected Project. No background model activity starts merely by opening this page.</p>
                  )}
                </article>
    </section>
  );
  const plugins = (
    <section className="workspace-view"><div className="view-heading"><div><span className="section-kicker">Extensibility</span><h1>Plugins & skills</h1><p>Connect permission-reviewed tools, application integrations, skills, and installable agent packages.</p></div></div><article className="panel settings-wide"><div className="panel-heading"><div><span className="section-kicker">Trust boundary</span><h2>No installed packages</h2></div><span className="status-pill status-ready"><i />Protected</span></div><p className="settings-note">Marketplace packages are treated as untrusted until their manifests, versions, dependencies, permissions, integrity, and publisher information are reviewed. The external marketplace itself is not part of this release.</p></article></section>
  );

  const conversationsByProject = new Map(
    studioProjects.map((item) => [
      item.project_id,
      conversations.filter((conversation) => conversation.workspace_project_id === item.project_id),
    ]),
  );
  const standaloneConversations = conversations.filter((conversation) => !conversation.workspace_project_id);

  const projectChatsForActive = activeStudioProject
    ? conversationsByProject.get(activeStudioProject.project_id) ?? []
    : [];

  const projectWorkspace = !activeStudioProject ? (
    <div className="empty-state" style={{ margin: "auto" }}>
      <div className="empty-glyph"><Icon name="files" /></div>
      <h3>No Project open</h3>
      <p>Pick a Project from the sidebar to see its chats, research, and files here.</p>
    </div>
  ) : (
    <section className="workspace-view project-workspace">
      <div className="view-heading">
        <div>
          <span className="section-kicker">Project workspace</span>
          <h1>{activeStudioProject.name}</h1>
          <p>{activeStudioProject.description || "Everything for this Project - chats, research, files, and history - lives here and stays on this device."}</p>
        </div>
        <button className="primary-button" onClick={() => void createNewConversation()}>New chat in Project</button>
      </div>
      <nav className="project-tabs" aria-label="Project sections">
        {([["overview", "Overview"], ["history", "History"], ["research", "Research"], ["artifacts", "Artifacts"], ["settings", "Settings"]] as const).map(([tabId, label]) => (
          <button key={tabId} className={projectTab === tabId ? "active" : ""} onClick={() => {
            setProjectTab(tabId);
            if (tabId === "settings" && activeStudioProject) {
              setProjectSettingsDraft({
                name: activeStudioProject.name,
                description: activeStudioProject.description ?? "",
                instructions: activeStudioProject.instructions ?? "",
              });
            }
          }}>{label}</button>
        ))}
      </nav>

      {projectTab === "overview" && <section className="project-sources"><div className="panel-heading"><h2>Sources & Files</h2><label className="secondary-button">Add files<input type="file" multiple accept={CHAT_ATTACHMENT_ACCEPT_ATTRIBUTE} disabled={uploading} onChange={(event) => { void addStudioProjectFiles(Array.from(event.target.files || [])); event.currentTarget.value=""; }} /></label></div>
        {activeStudioProject.files.map(asset => <article className="file-row" key={asset.record_id}><div><strong>{asset.filename}</strong><small>{formatBytes(asset.size_bytes)} · {humanize(asset.extraction_status)}</small></div><button onClick={() => {setPreviewAsset(asset);inspect("files");}}>Open / Download</button><button onClick={async()=>{if(!window.confirm(`Remove ${asset.filename} from Project sources? Saved provenance is retained.`))return;try{await amca.removeStudioAsset(activeStudioProject.project_id,asset.asset_id);setActiveStudioProject(await amca.studioProject(activeStudioProject.project_id));}catch(e){setError(String(e));}}}>Remove</button></article>)}
        {!activeStudioProject.files.length && <p>Add sources to make them available to every chat in this Project.</p>}
        <div className="panel-heading"><h2>Chats</h2><button onClick={() => void createNewConversation()}>New chat in Project</button></div>{projectChatsForActive.map(item => <div className="file-row" key={item.conversation_id}><button onClick={() => void chooseConversation(item.conversation_id)}>{item.title}</button><button onClick={() => void renameConversation(item)}>Rename</button><select aria-label={`Move ${item.title}`} value={item.workspace_project_id ?? ""} onChange={e=>void moveConversationToProject(item,e.target.value||null)}><option value="">No Project</option>{studioProjects.map(p=><option key={p.project_id} value={p.project_id}>{p.name}</option>)}</select><button onClick={()=>void deleteConversation(item)}>Delete</button></div>)}
      </section>}
      {projectTab === "history" && (
        <div className="project-overview-grid">
          {(() => {
            // v0.8.0 collaboration workspace: overview, activity feed,
            // reusable workflow patterns, review summary — recorded
            // records only; single-operator, no collaboration service.
            const projectId = activeStudioProject?.project_id ?? null;
            const projectRuns = projectId ? dashboard.runs.filter((run) => run.project_id === projectId) : [];
            const overview = projectCollaborationOverview({
              purpose: activeStudioProject?.description ?? null,
              runs: projectRuns,
              artifactCount: projectAssets?.length ?? 0,
              knowledgeAttachmentCount: knowledgeSelection.length,
              agentIds: projectRuns.flatMap((run) => (run as unknown as { agentIds?: string[] }).agentIds ?? []),
            });
            const feed = projectActivityFeed({
              runs: projectRuns,
              artifacts: (projectAssets ?? []).map((item) => ({ artifact_id: item.asset_id, title: item.filename, created_at: (item as unknown as { created_at?: string | null }).created_at ?? null })),
              approvals: approvals.map((item) => ({ id: item.id, action: item.action, status: item.status, created_at: item.created_at })),
              automationOccurrences: automationDefinitions.map((item) => ({ automationId: item.id, status: item.latest_occurrence_status ?? "Not recorded", scheduled_for: item.last_run_at })),
            });
            const patterns = reusableWorkflowPatterns(projectRuns);
            const review = projectReviewSummary({
              runs: projectRuns,
              pendingGovernanceActions: pendingReviewApprovals.length,
              availableArtifacts: projectAssets?.length ?? 0,
              availableKnowledgeReferences: knowledgeSelection.length,
            });
            return (
              <article className="panel project-collab-panel">
                <div className="panel-heading"><div><span className="section-kicker">Collaboration workspace</span><h2>Project collaboration overview</h2></div></div>
                <dl className="detail-list">
                  <div><dt>Purpose / context</dt><dd>{overview.purpose ?? "Not recorded"}</dd></div>
                  <div><dt>Workflows executed</dt><dd>{overview.workflowsExecuted}</dd></div>
                  <div><dt>Artifacts generated</dt><dd>{overview.artifactsGenerated}</dd></div>
                  <div><dt>Knowledge used</dt><dd>{overview.knowledgeUsed}</dd></div>
                  <div><dt>Participating models</dt><dd>{overview.participatingModels.length ? overview.participatingModels.join(", ") : "Not recorded"}</dd></div>
                  <div><dt>Participating agents</dt><dd>{overview.participatingAgents.length ? overview.participatingAgents.join(", ") : "Not recorded"}</dd></div>
                </dl>
                <p className="muted-copy">{overview.boundary}</p>
                <small className="muted-copy">{COLLABORATION_BOUNDARIES.noMultiUser}</small>

                <h5>Project activity feed</h5>
                {feed.length ? (
                  <div className="ops-timeline">
                    {feed.slice(0, 12).map((event, index) => (
                      <div key={`${event.kind}-${index}`} className="ops-timeline-row">
                        <span className="status-pill status-muted"><i />{event.label}</span>
                        <span>{event.detail}</span>
                        <small>{event.timestamp ?? "undated record"}</small>
                      </div>
                    ))}
                  </div>
                ) : <p className="muted-copy">{COLLABORATION_NO_WORKFLOWS}</p>}

                <h5>Reusable workflow patterns</h5>
                {patterns.patterns.length ? (
                  <div className="hub-list">
                    {patterns.patterns.map((pattern) => (
                      <article key={pattern.taskType}>
                        <div><strong>{pattern.taskType}</strong><span>{pattern.workflows.length} workflow(s) recorded</span><small>models: {pattern.models.join(", ") || "Not recorded"}</small></div>
                        <b>{pattern.artifactCount} artifact(s) · {pattern.agentCount} agent participation(s)</b>
                      </article>
                    ))}
                  </div>
                ) : <p className="muted-copy">{patterns.emptyReason}</p>}
                <p className="muted-copy">{patterns.note}</p>

                <h5>Review summary</h5>
                <dl className="detail-list">
                  <div><dt>Completed workflows</dt><dd>{review.completedWorkflows}</dd></div>
                  <div><dt>Failed workflows</dt><dd>{review.failedWorkflows}</dd></div>
                  <div><dt>Pending governance actions</dt><dd>{review.pendingGovernanceActions}</dd></div>
                  <div><dt>Available artifacts</dt><dd>{review.availableArtifacts}</dd></div>
                  <div><dt>Available knowledge references</dt><dd>{review.availableKnowledgeReferences}</dd></div>
                </dl>
                <p className="muted-copy">{review.boundary}</p>

                <h5>Adaptive workflow readiness</h5>
                {(() => {
                  const latestRun = projectRuns[0] ?? null;
                  const view = adaptiveReadinessView({
                    run: latestRun,
                    modelRefs: overview.participatingModels,
                    agentIds: overview.participatingAgents,
                    knowledgeAttachmentCount: overview.knowledgeUsed,
                  });
                  return (
                    <div className="adaptive-readiness">
                      <dl className="detail-list">
                        <div><dt>Recorded task type</dt><dd>{view.indicators.recordedTaskType ?? "Not recorded"}</dd></div>
                        <div><dt>Recorded mode</dt><dd>{view.indicators.recordedMode ?? "Not recorded"}</dd></div>
                        <div><dt>Models / agents / knowledge</dt><dd>{view.indicators.participatingModelCount} / {view.indicators.participatingAgentCount} / {view.indicators.knowledgeAttachmentCount}</dd></div>
                        <div><dt>Workflow strategy</dt><dd>{view.strategy}</dd></div>
                      </dl>
                      <div className="adaptive-capability-rows">
                        {view.capabilityRows.map((row) => (
                          <small key={`${row.kind}-${row.ref}`}>{row.kind}: <code>{row.ref}</code> · {row.locality} · {row.governanceStatus}{row.recordedCapability ? ` · capability: ${row.recordedCapability}` : ""}</small>
                        ))}
                      </div>
                      <div className="adaptive-boundary">
                        <strong>{ADAPTIVE_BOUNDARY.headline}</strong>
                        <small>Future capability could include: {ADAPTIVE_BOUNDARY.futureCapabilities.join(" · ")}.</small>
                        <small>This build: {ADAPTIVE_BOUNDARY.thisBuildDoesNot.join("; ")}.</small>
                      </div>
                    </div>
                  );
                })()}
              </article>
            );
          })()}
          {(() => {
            // v0.8.0 workflow reuse foundation: recorded workflow examples
            // with detail, reuse preparation, and side-by-side comparison.
            // Preparation opens the existing composer — nothing is created
            // or executed automatically.
            const projectId = activeStudioProject?.project_id ?? null;
            const projectRuns = projectId ? dashboard.runs.filter((run) => run.project_id === projectId) : [];
            const templates = workflowTemplates(projectRuns);
            const [compareA, compareB] = templates;
            const comparison = compareA && compareB ? templateComparison([compareA, compareB]) : null;
            const prep = compareA ? reusePreparation(compareA, []) : null;
            return (
              <article className="panel project-templates-panel">
                <div className="panel-heading"><div><span className="section-kicker">Workflow reuse</span><h2>Workflow templates</h2></div></div>
                <p className="muted-copy">{TEMPLATE_BOUNDARY}</p>
                {templates.length ? (
                  <div className="hub-list">
                    {templates.map((template) => {
                      const sourceRun = projectRuns.find((run) => run.run_id === template.sourceWorkflowId);
                      const detail = templateDetail(sourceRun ?? ({ run_id: template.sourceWorkflowId, status: "Not recorded", goal: "", project_id: template.projectId, created_at: null, started_at: null, finished_at: null, error: null, log: "", provider: "", mode: "", privacy: "", conversation_id: null, result: null } as unknown as RunJob));
                      return (
                        <details key={template.sourceWorkflowId} className="project-template">
                          <summary>
                            <strong>{template.taskType}</strong>
                            <span>{template.sourceWorkflowId.slice(0, 14)}… · {template.artifactCount} artifact(s) · {template.knowledgeCount} knowledge</span>
                            <small>{template.completedAt ?? "undated record"}</small>
                          </summary>
                          <dl className="detail-list">
                            <div><dt>Source workflow</dt><dd><code>{detail.sourceWorkflowId}</code></dd></div>
                            <div><dt>Task type (recorded)</dt><dd>{detail.taskType}</dd></div>
                            <div><dt>Recorded stages</dt><dd>{detail.recordedStages.join("; ")}</dd></div>
                            <div><dt>Resources used</dt><dd>{detail.resources.join(", ") || TEMPLATE_EMPTY_STATES.no_detail}</dd></div>
                            <div><dt>Knowledge attachments</dt><dd>{detail.knowledgeAttachments > 0 ? `${detail.knowledgeAttachments} recorded` : TEMPLATE_EMPTY_STATES.no_detail}</dd></div>
                            <div><dt>Outputs produced</dt><dd>{detail.outputs.join(", ") || TEMPLATE_EMPTY_STATES.no_detail}</dd></div>
                            {detail.missing.length > 0 && <div><dt>Missing</dt><dd>{detail.missing.join(", ")}</dd></div>}
                          </dl>
                          {prep && (
                            <small className="muted-copy">Reuse preparation: {prep.boundary}</small>
                          )}
                        </details>
                      );
                    })}
                    {comparison && (
                      <div className="template-comparison">
                        <strong>Side-by-side comparison (first two recorded examples)</strong>
                        <table>
                          <thead><tr><th>Field</th><th>{compareA.sourceWorkflowId.slice(0, 10)}…</th><th>{compareB.sourceWorkflowId.slice(0, 10)}…</th></tr></thead>
                          <tbody>
                            {comparison.map((row) => (
                              <tr key={row.field}><td>{row.field}</td>{row.values.map((value, index) => <td key={index}>{value}</td>)}</tr>
                            ))}
                          </tbody>
                        </table>
                        <small className="muted-copy">{COMPARISON_NOTE}</small>
                      </div>
                    )}
                  </div>
                ) : <p className="muted-copy">{TEMPLATE_EMPTY_STATES.no_templates}</p>}
              </article>
            );
          })()}
          <article className="panel">
            <div className="panel-heading"><div><span className="section-kicker">At a glance</span><h2>Status</h2></div></div>
            <dl className="detail-list">
              <div><dt>Chats</dt><dd>{projectChatsForActive.length}</dd></div>
              <div><dt>Files</dt><dd>{projectAssets === null ? "\u2026" : projectAssets.length}</dd></div>
              <div><dt>Research sessions</dt><dd>{projectSessions === null ? "\u2026" : projectSessions.length}</dd></div>
              <div><dt>Created</dt><dd>{relativeTime(activeStudioProject.created_at)}</dd></div>
              <div><dt>Last activity</dt><dd>{relativeTime(activeStudioProject.updated_at)}</dd></div>
            </dl>
          </article>
          <article className="panel">
            <div className="panel-heading"><div><span className="section-kicker">Recent activity</span><h2>Latest chats</h2></div></div>
            {projectChatsForActive.length ? (
              <div className="project-chat-list">
                {projectChatsForActive.slice(0, 8).map((item) => (
                  <button key={item.conversation_id} onClick={() => void chooseConversation(item.conversation_id)}>
                    <span><strong>{item.title}</strong><small>{item.last_message_preview || "Empty chat"}</small></span>
                    <time>{relativeTime(item.updated_at)}</time>
                  </button>
                ))}
              </div>
            ) : <EmptyState title="No activity yet" detail="Start a chat from the sidebar. Everything you do stays inside this Project and on this device." />}
          </article>
        </div>
      )}

      {projectTab === "history" && (
        <article className="panel">
          <div className="panel-heading"><div><span className="section-kicker">History</span><h2>All chats in this Project</h2></div><span>{projectChatsForActive.length}</span></div>
          {projectChatsForActive.length ? (
            <div className="project-chat-list">
              {projectChatsForActive.map((item) => (
                <button key={item.conversation_id} onClick={() => void chooseConversation(item.conversation_id)}>
                  <span><strong>{item.title}</strong><small>{item.last_message_preview || "Empty chat"}</small></span>
                  <time>{relativeTime(item.updated_at)}</time>
                </button>
              ))}
            </div>
          ) : <EmptyState title="No chats yet" detail="Chats you start while this Project is open appear here." />}
        </article>
      )}

        {projectTab === "history" && (() => {
          // v0.8.0 continuity: project cognitive history from the recorded
          // run registry — records only, no ranking, no performance score.
          const history = projectCognitiveHistory(
            activeStudioProject?.project_id ?? project?.project_id ?? "unassigned",
            dashboard.runs,
          );
          return (
            <article className="panel">
              <div className="panel-heading"><div><span className="section-kicker">Cognitive continuity</span><h2>Project workflow history</h2></div><small>{history.totals.workflows} workflow(s)</small></div>
              <p className="muted-copy">{history.note}</p>
              {history.entries.length ? (
                <div className="hub-list">
                  {history.entries.map((entry) => (
                    <article key={entry.workflowId}>
                      <div><strong><code>{entry.workflowId.slice(0, 18)}…</code></strong><span>{entry.status}{entry.recordedFailure ? ` — ${entry.recordedFailure}` : ""}</span><small>{entry.createdAt ?? "no timestamp recorded"}</small></div>
                      <b>{entry.artifactCount} artifact(s) · {entry.knowledgeCount} knowledge · {entry.modelRefs.length} model ref(s)</b>
                    </article>
                  ))}
                </div>
              ) : <p className="muted-copy">{history.totals.workflows === 0 ? "No workflow records available yet." : ""}</p>}
              <small className="muted-copy">Totals: {history.totals.artifacts} artifact(s) · {history.totals.knowledgeAttachments} knowledge attachment(s) · {history.totals.recordedFailures} recorded failure(s)</small>
            </article>
          );
        })()}

        {projectTab === "history" && (() => {
          // v0.8.0 operational timeline: workflow + artifact + approval +
          // knowledge events, newest first, recorded timestamps only.
          const events = projectOperationalEvents({
            projectId: activeStudioProject?.project_id ?? project?.project_id ?? "unassigned",
            runs: dashboard.runs,
            artifacts: assets.map((item) => ({ artifact_id: item.asset_id, title: item.filename, created_at: (item as unknown as { created_at?: string | null }).created_at ?? null })),
            approvals: approvals.map((item) => ({ id: item.id, action: item.action, status: item.status, created_at: item.created_at })),
            knowledgeEvents: knowledgeSelection.map((item) => ({ artifactId: item.artifactId, timestamp: null })),
          });
          return (
            <article className="panel">
              <div className="panel-heading"><div><span className="section-kicker">Operational continuity</span><h2>Project operational timeline</h2></div><small>{events.length} event(s)</small></div>
              {events.length ? (
                <div className="ops-timeline">
                  {events.map((event, index) => (
                    <div key={`${event.kind}-${index}`} className="ops-timeline-row">
                      <span className="status-pill status-muted"><i />{event.label}</span>
                      <span>{event.detail}</span>
                      <small>{event.timestamp ?? "undated record"}</small>
                    </div>
                  ))}
                </div>
              ) : <p className="muted-copy">{OPERATIONAL_EMPTY_STATES.no_timeline}</p>}
              <small className="muted-copy">Recorded timestamps only — undated records are preserved and rendered as "undated record".</small>
            </article>
          );
        })()}

      {projectTab === "research" && <ResearchWorkspace projectId={activeStudioProject.project_id} onInspect={(title,value)=>{setResearchInspection({title,value});inspect("evidence");}} />}

      {projectTab === "artifacts" && <ArtifactInspector projectId={activeStudioProject.project_id} relatedProjectIds={activeStudioProject.chats.flatMap(chat=>chat.project_id ? [chat.project_id] : [])} onSource={text=>void addStudioProjectFiles([new File([text],"artifact-source.md",{type:"text/markdown"})])}/>}

      {projectTab === "settings" && (
        <form className="panel project-settings-form" onSubmit={(event) => { event.preventDefault(); void saveProjectSettings(); }}>
          <div className="panel-heading"><div><span className="section-kicker">Settings</span><h2>Project details</h2></div></div>
          <div className="studio-project-fields">
            <label><span>Name</span><input value={projectSettingsDraft?.name ?? ""} onChange={(event) => setProjectSettingsDraft((current) => current ? { ...current, name: event.target.value } : current)} /></label>
            <label className="wide"><span>Description</span><input value={projectSettingsDraft?.description ?? ""} onChange={(event) => setProjectSettingsDraft((current) => current ? { ...current, description: event.target.value } : current)} placeholder="What this Project is for" /></label>
            <label className="wide"><span>Project instructions</span><textarea value={projectSettingsDraft?.instructions ?? ""} onChange={(event) => setProjectSettingsDraft((current) => current ? { ...current, instructions: event.target.value } : current)} rows={4} placeholder="Shared context applied to chats in this Project" /></label>
          </div>
          <div className="form-actions"><button type="submit" className="primary-button" disabled={!projectSettingsDraft?.name?.trim()}>Save changes</button></div>
        </form>
      )}
    </section>
  );

  const content: Record<View, React.ReactNode> = {
    project: projectWorkspace,
    chat, overview, room: researchRoom, whiteboard, files, workbench, cost,
    history: executionHistory,
    dashboard: (
      <WorkspaceDashboard
        studioProjects={studioProjects}
        preferredProjectId={activeStudioProject?.project_id ?? null}
        onOpenProject={(id) => {
          if (id) void openStudioProject(id);
          setView("overview");
        }}
        onOpenProjects={() => setView("overview")}
        onOpenChat={(id) => {
          setView("chat");
          void chooseConversation(id);
        }}
        onOpenAgents={() => setView("agents")}
        onOpenModels={() => setView("settings")}
        onOpenArtifacts={() => setView("artifacts")}
        runs={dashboard.runs}
        onOpenArtifactsFiltered={(projectId, artifactType) => {
          // W1 deep link: carry the recorded artifact type into the
          // Artifact Center's existing filter tabs. No inferred relations —
          // the filter simply preselects the same recorded type.
          setArtifactFocus(artifactCenterFocus(projectId, artifactType));
          if (projectId && projectId !== project?.project_id) void loadProject(projectId);
          setView("artifacts");
        }}
        composeBusy={composeBusy}
        onComposeTask={composeTask}
      />
    ),
    research: (
      <ResearchCenter
        projectId={activeStudioProject?.project_id || project?.project_id || "default"}
        onNavigateToApprovals={() => setView("approvals")}
      />
    ),
    artifacts: (
      <ArtifactCenter
        projectId={project?.project_id || "default"}
        initialTypeFilter={artifactFocus?.filter ?? null}
        onOpenProjects={() => setView("overview")}
      />
    ),
    engineering: <EngineeringWorkspaceView projectId={project?.project_id || "default"} />,
    benchmarks: benchmarkView, approvals: approvalView, permissions: computerAccess, logs, settings,

    workspace: (
      <section className="workspace-view workspace-command-view">
        <div className="view-heading">
          <div><span className="section-kicker">Professional workspace</span><h1>Workspace Command Center</h1>
          <p>One surface over existing workspace records — visualization, navigation, and transparency only. No analytics service, no scores.</p></div>
        </div>
        {(() => {
          const scopedApprovals = project?.project_id ? approvals : [];
          const input = {
            studioProjects: studioProjects.length,
            runs: dashboard.runs,
            agentParticipants: dashboard.runs.length
              ? new Set(dashboard.runs.flatMap((run) => participantsFromRun(run).map((participant) => participant.ref))).size
              : null,
            availableModels: models.models.filter((item) => item.fit !== 2).length
              + (credentialStatus?.credentials.filter((item) => item.configured).length ?? 0)
              + hubProviders.filter((item) => item.configured).flatMap((item) => item.enabled_model_ids ?? []).length,
            artifacts: artifactList,
            automations: { total: automationDefinitions.length, enabled: automationDefinitions.filter((item) => item.enabled).length },
            approvals: scopedApprovals,
          };
          const cards = commandCenterCards(input);
          const health = workspaceHealth(input);
          // v0.8.0 operational intelligence: connect existing records into
          // operational metrics + drill-down (records only, no scoring).
          const knowledgeItemsForOps = knowledgeSelection;
          const operational = operationalMetrics({
            runs: dashboard.runs,
            pendingApprovals: scopedApprovals.filter((item) => item.status === "pending").length,
            artifactCount: artifactList.length,
            knowledgeItems: knowledgeItemsForOps,
            localModels: models.models.filter((item) => item.fit !== 2).length,
            providerModels: (credentialStatus?.credentials.filter((item) => item.configured).length ?? 0)
              + hubProviders.filter((item) => item.configured).length,
          });
          const operationalCards = operationalHealthCards({
            runs: dashboard.runs,
            pendingApprovals: scopedApprovals.filter((item) => item.status === "pending").length,
            availableResources: input.availableModels,
          });
          const workflowDrill = drillLink("workflow", dashboard.runs.length > 0);
          const artifactDrill = drillLink("artifact", artifactList.length > 0);
          const modelDrill = drillLink("model", input.availableModels > 0);
          const approvalDrill = drillLink("approval", scopedApprovals.length > 0);
          const timeline = unifiedTimeline({
            runs: dashboard.runs,
            artifacts: artifactList,
            approvals: scopedApprovals,
            automations: automationDefinitions.map((item) => ({ id: item.id, name: item.name, last_run_at: item.last_run_at })),
            limit: 40,
          });
          const latestRun = dashboard.runs.length
            ? dashboard.runs.slice().sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0]
            : null;
          const trace = crossModuleTrace({
            project: activeStudioProject ? { project_id: activeStudioProject.project_id, name: activeStudioProject.name } : null,
            run: latestRun,
            artifact: artifactList.length ? artifactList[artifactList.length - 1] : null,
            agentParticipants: [],
          });
          const emptyWorkspace = !studioProjects.length && !dashboard.runs.length && !artifactList.length;
          return (
            <>
              {emptyWorkspace && (
                <div className="panel settings-wide workspace-empty-state">
                  <p className="settings-note">{COMMAND_CENTER_EMPTY_STATES.workspace}</p>
                  <button className="secondary-button" onClick={() => setView("overview")}>Go to Projects</button>
                </div>
              )}
              <div className="metrics-grid workspace-overview">
                {cards.map((card) => (
                  <article key={card.id} className={`metric-card workspace-card${card.value === null ? " unrecorded" : ""}`}>
                    <span>{card.label}</span>
                    <strong>{card.displayValue}</strong>
                    {card.value === null ? <small>{card.reason}</small> : <small>{card.evidenceSource}</small>}
                    {card.navView && (
                      <button className="secondary-button" onClick={() => setView(card.navView as View)}>Open</button>
                    )}
                  </article>
                ))}
              </div>
              <article className="panel settings-wide workspace-trace">
                <div className="panel-heading"><div><span className="section-kicker">Cross-module trace</span><h2>Project → Run → Agents → Artifact → Memory</h2></div><span className="status-pill status-ready"><i />Evidence links only</span></div>
                <ol className="workspace-trace-list">
                  {trace.map((step) => (
                    <li key={step.stage} className={step.state}>
                      <strong>{step.stage}</strong>
                      <span>{step.detail}</span>
                      {step.targetView ? (
                        <button className="secondary-button" onClick={() => setView(step.targetView as View)}>Open</button>
                      ) : (
                        <small>not recorded</small>
                      )}
                    </li>
                  ))}
                </ol>
              </article>
              <article className="panel settings-wide workspace-operational">
                <div className="panel-heading"><div><span className="section-kicker">Operational intelligence</span><h2>Operational state</h2></div><small>existing records only</small></div>
                <p className="muted-copy">{OPERATIONAL_STATE_NOTE}</p>
                <div className="ops-metrics">
                  <span><b>{operational.activeWorkflows}</b> active workflow(s)</span>
                  <span><b>{operational.completedWorkflows}</b> completed</span>
                  <span><b>{operational.pendingApprovals}</b> pending approval(s)</span>
                  <span><b>{operational.artifactsGenerated}</b> artifact(s) generated</span>
                  <span><b>{operational.knowledgeAttachments}</b> knowledge attachment(s)</span>
                  <span>{operational.localExecutionVisible}</span>
                  <span>{operational.providerExecutionVisible}</span>
                </div>
                <div className="ops-health-cards">
                  {operationalCards.map((card) => (
                    <div key={card.label} className="ops-health-card">
                      <b>{card.value}</b>
                      <span>{card.label}</span>
                      <small>{card.evidence}</small>
                    </div>
                  ))}
                </div>
                <div className="ops-drill">
                  {[workflowDrill, artifactDrill, modelDrill, approvalDrill].map((link) => (
                    <button
                      key={link.label}
                      type="button"
                      disabled={!link.available}
                      title={link.available ? `Navigate to ${link.target}` : link.unavailableReason ?? undefined}
                      onClick={() => link.target && setView(link.target as View)}
                    >
                      {link.label}
                    </button>
                  ))}
                </div>
              </article>
              <article className="panel settings-wide workspace-timeline">
                <div className="panel-heading"><div><span className="section-kicker">Unified activity</span><h2>Workspace activity timeline</h2></div><small>{timeline.length} record{timeline.length === 1 ? "" : "s"}</small></div>
                {timeline.length ? (
                  <ul className="workspace-timeline-list">
                    {timeline.map((entry) => (
                      <li key={entry.key}>
                        <time>{entry.createdAt ?? "undated record"}</time>
                        <strong>{entry.label}</strong>
                        <span>{entry.detail}</span>
                        {entry.navView && <button className="secondary-button" onClick={() => setView(entry.navView as View)}>Open</button>}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="settings-note">{COMMAND_CENTER_EMPTY_STATES.workspace}</p>
                )}
              </article>
              <article className="panel settings-wide workspace-health">
                <div className="panel-heading"><div><span className="section-kicker">Workspace health</span><h2>Recorded counts only</h2></div><span className="status-pill status-ready"><i />No scores</span></div>
                <dl className="detail-list op-metrics">
                  <div><dt>Active projects</dt><dd>{health.activeProjects}</dd></div>
                  <div><dt>Running tasks</dt><dd>{health.runningTasks}</dd></div>
                  <div><dt>Completed workflows</dt><dd>{health.completedWorkflows}</dd></div>
                  <div><dt>Available models</dt><dd>{health.availableModels === 0 ? `0 — ${COMMAND_CENTER_EMPTY_STATES.models}` : health.availableModels}</dd></div>
                  <div><dt>Artifacts generated</dt><dd>{health.artifactsGenerated === 0 ? COMMAND_CENTER_EMPTY_STATES.artifacts : health.artifactsGenerated}</dd></div>
                </dl>
                <p className="settings-note">{health.line}</p>
              </article>
            </>
          );
        })()}
      </section>
    ),
    feedback: (
      <section className="workspace-view feedback-refinement-view">
        <div className="view-heading">
          <div><span className="section-kicker">Beta refinement</span><h1>Feedback Center</h1>
          <p>Close the loop between your feedback, known limitations, and improvement tracking — from local records only.</p></div>
        </div>
        <div className="panel settings-wide feedback-privacy-banner">
          <span className="status-pill status-ready"><i />{FEEDBACK_PRIVACY.headline}</span>
          <p className="settings-note">{FEEDBACK_PRIVACY.detail}</p>
        </div>
        {(() => {
          const modules = feedbackModules(LIMITATION_RECORDS);
          const moduleValue = feedbackModule || modules[0];
          const chains = limitationRefinementChains(LIMITATION_RECORDS);
          const summary = improvementSummary(sessionFeedback, LIMITATION_RECORDS.length);
          return (
            <>
              <div className="metrics-grid feedback-summary">
                <article className="metric-card"><span>Feedback records</span><strong>{summary.feedbackCount}</strong><small>session-local documents</small></article>
                <article className="metric-card"><span>Open items</span><strong>{summary.openItems}</strong><small>status you set</small></article>
                <article className="metric-card"><span>Resolved items</span><strong>{summary.resolvedItems}</strong><small>resolved locally</small></article>
                <article className="metric-card"><span>Recorded limitations</span><strong>{summary.limitationCount}</strong><small>accepted records</small></article>
              </div>
              <article className="panel settings-wide feedback-compose">
                <div className="panel-heading"><div><span className="section-kicker">New feedback record</span><h2>Add a record to this session</h2></div><span className="status-pill status-ready"><i />Local only</span></div>
                <form className="dash-feedback-form" onSubmit={(event) => event.preventDefault()}>
                  <div className="feedback-pickers">
                    <label><span>Category (you choose — no automatic classification)</span>
                      <select value={feedbackCategory} onChange={(event) => setFeedbackCategory(event.target.value)} aria-label="Feedback category">
                        {FEEDBACK_CATEGORIES.map((category) => <option key={category.id} value={category.id}>{category.label}</option>)}
                      </select>
                    </label>
                    <label><span>Affected module (recorded modules)</span>
                      <select value={moduleValue} onChange={(event) => setFeedbackModule(event.target.value)} aria-label="Affected module">
                        {modules.map((module) => <option key={module} value={module}>{module}</option>)}
                      </select>
                    </label>
                  </div>
                  <label><span>What happened, or what would you improve?</span>
                    <textarea rows={4} value={feedbackText} placeholder="Describe the issue or idea — the record stays in this session." onChange={(event) => { setFeedbackText(event.target.value); setFeedbackNotice(null); }} />
                  </label>
                  <div className="form-actions">
                    <button
                      className="secondary-button"
                      disabled={!feedbackText.trim()}
                      onClick={() => {
                        const record = addFeedbackRecord({ categoryId: feedbackCategory, module: moduleValue, text: feedbackText, createdAt: new Date().toISOString() });
                        if (!record) { setFeedbackNotice("A record needs text and a module."); return; }
                        setSessionFeedback((current) => [record, ...current]);
                        setFeedbackText("");
                        setFeedbackNotice("Record added to this session. It is stored locally and cleared when Studio closes.");
                      }}
                    >
                      Add session record
                    </button>
                    <button type="button" className="secondary-button" disabled={!feedbackText} onClick={() => setFeedbackText("")}>Clear</button>
                    {feedbackNotice && <small className="feedback-notice">{feedbackNotice}</small>}
                  </div>
                </form>
              </article>
              <article className="panel settings-wide feedback-records">
                <div className="panel-heading"><div><span className="section-kicker">Feedback records</span><h2>{FEEDBACK_PRIVACY.sessionScope}</h2></div><small>{sessionFeedback.length}</small></div>
                {sessionFeedback.length ? (
                  <ul className="feedback-record-list">
                    {sessionFeedback.map((record) => {
                      const chain = feedbackRecordChain(record);
                      return (
                        <li key={record.id}>
                          <div className="feedback-record-head">
                            <strong>{record.categoryLabel}</strong>
                            <span className={`status-pill pill-${record.status}`}>{record.status === "resolved" ? "Resolved" : "Open"}</span>
                            <time>{record.createdAt}</time>
                          </div>
                          <span>{record.text}</span>
                          <small>Affected module: {record.module} · Resolution: {record.resolution} · {record.storage}</small>
                          <small>Refinement path — Known limitation: {chain.knownLimitation} · Target release: {chain.targetRelease}</small>
                          {record.status === "open" && (
                            <button className="secondary-button" onClick={() => setSessionFeedback((current) => resolveFeedbackRecord(current, record.id))}>Mark resolved</button>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <p className="settings-note">No feedback records this session. Use the form above, or the Dashboard's Beta feedback panel to compose a copy-draft.</p>
                )}
              </article>
              <article className="panel settings-wide feedback-categories">
                <div className="panel-heading"><div><span className="section-kicker">Categories</span><h2>Feedback categories</h2></div><small>{FEEDBACK_CATEGORIES.length}</small></div>
                <ul className="feedback-category-list">
                  {FEEDBACK_CATEGORIES.map((category) => {
                    const count = sessionFeedback.filter((record) => record.categoryId === category.id).length;
                    return (
                      <li key={category.id}>
                        <strong>{category.label}</strong>
                        <small>{count} record{count === 1 ? "" : "s"} this session</small>
                      </li>
                    );
                  })}
                </ul>
              </article>
              <article className="panel settings-wide feedback-refinement">
                <div className="panel-heading"><div><span className="section-kicker">Refinement tracking</span><h2>Feedback → Module → Limitation → Status → Target</h2></div><span className="status-pill status-ready"><i />Recorded links only</span></div>
                <ul className="feedback-chain-list">
                  {chains.map((chain) => (
                    <li key={chain.key}>
                      <strong>{chain.knownLimitation}</strong>
                      <span>Affected module: {chain.affectedModule} · Resolution status: {chain.resolutionStatus} · Target release: {chain.targetRelease}</span>
                      <small>Source: {chain.feedback}</small>
                    </li>
                  ))}
                </ul>
                <p className="settings-note">Session feedback records show their module but no limitation/target link — no linkage backend exists between feedback and limitation records in this build, and none is inferred.</p>
              </article>
            </>
          );
        })()}
      </section>
    ),
    help: (
      <section className="workspace-view help-resources-view">
        <div className="view-heading">
          <div><span className="section-kicker">Documentation hub</span><h1>Help &amp; Resources</h1>
          <p>{BETA_STATUS.documentationNote}</p></div>
        </div>
        {(() => {
          const audit = auditNavigation({
            sidebarGroups: [
              { group: "Work", items: NAV_WORK },
              { group: "Resources", items: NAV_RESOURCES },
              { group: "Advanced", items: NAV_ADVANCED },
            ],
            palette: NAVIGABLE_VIEWS,
          });
          return (
            <>
              <article className="panel settings-wide help-beta-status">
                <div className="panel-heading"><div><span className="section-kicker">Release information</span><h2>Beta status</h2></div><span className="status-pill status-ready"><i />Pre-release</span></div>
                <dl className="detail-list">
                  <div><dt>Application version</dt><dd>{BETA_STATUS.appVersion}</dd></div>
                  <div><dt>Release channel</dt><dd>{BETA_STATUS.channel}</dd></div>
                  <div><dt>Status</dt><dd>{BETA_STATUS.statusLine}</dd></div>
                  <div><dt>Navigation audit</dt><dd>{audit.findings[0]?.detail ?? "Recorded audit available in the milestone report."}</dd></div>
                </dl>
                <div className="form-actions">
                  <button className="secondary-button" onClick={() => setView("validation")}>Known limitations</button>
                  <button className="secondary-button" onClick={() => setView("feedback")}>Beta feedback</button>
                  <button className="secondary-button" onClick={() => setView("validation")}>Validation Center</button>
                </div>
              </article>
              <article className="panel settings-wide help-docs">
                <div className="panel-heading"><div><span className="section-kicker">Documentation</span><h2>Getting started, guides &amp; troubleshooting</h2></div><small>{HELP_RESOURCES.length} entries</small></div>
                <ul className="help-resource-list">
                  {HELP_RESOURCES.map((resource) => (
                    <li key={resource.title}>
                      <strong>{resource.title}</strong>
                      <span>{resource.detail}</span>
                      <small>{resource.targetView ? "Opens in Studio" : `Local document · ${resource.docsPath}`}</small>
                      {resource.targetView && (
                        <button className="secondary-button" onClick={() => setView(resource.targetView as View)}>Open</button>
                      )}
                    </li>
                  ))}
                </ul>
                <p className="help-cwf-note">Cognitive workflow documentation entries link to recorded reports under software/docs/validation — this build has no in-app documentation reader.</p>
                <div className="help-search-entries">
                  <strong>Workspace search entry points</strong>
                  <small>Search over existing records only — no semantic recommendation, no hidden retrieval.</small>
                  <ul>
                    {(SEARCH_ENTRY_POINTS as ReadonlyArray<{ label: string; target: string; recordKind: string }>).map((entry) => (
                      <li key={entry.label}>
                        <button className="secondary-button" onClick={() => setView(entry.target as View)}>{entry.label}</button>
                        <small>{entry.recordKind}</small>
                      </li>
                    ))}
                  </ul>
                </div>
              </article>
            </>
          );
        })()}
      </section>
    ),
    validation: (
      <section className="workspace-view validation-view">
        <div className="view-heading">
          <div><span className="section-kicker">Beta transparency</span><h1>Validation Center</h1>
          <p>{RELEASE_RECORD.track} · {RELEASE_RECORD.appVersion} — {RELEASE_RECORD.note}</p></div>
        </div>
        {(() => {
          const summary = betaHealthSummary({ feedbackRecords: null });
          return (
            <div className="metrics-grid beta-health">
              <article className="metric-card"><span>Validated scenarios</span><strong>{summary.scenariosValidated}</strong><small>validated records only</small></article>
              <article className="metric-card"><span>Pending validation</span><strong>{summary.scenariosPending}</strong><small>no validation-record endpoint</small></article>
              <article className="metric-card"><span>Failed validations</span><strong>{summary.scenariosFailed}</strong><small>none recorded</small></article>
              <article className="metric-card"><span>Recorded limitations</span><strong>{summary.openLimitations}</strong><small>accepted scope records</small></article>
            </div>
          );
        })()}
        {(() => {
          // v0.8.0 governance transparency: recorded fields only — no
          // security score, no compliance score, no automatic decisions.
          const knowledgeVerdicts = pendingReviewApprovals.map(() => ({ shareability: "shareable", shareabilityReason: "Locally produced output." }));
          const localCount = models.models.length;
          const providerCount = (credentialStatus?.credentials.length ?? 0) + hubProviders.filter((provider) => provider.model_ids.length > 0).length;
          const summaryPanel = governanceSummary({
            knowledgeVerdicts,
            providerApprovalState: credentialStatus ? "Provider approval required before any network call" : null,
            localModelCount: localCount,
            providerModelCount: providerCount,
          });
          const approvalRecords = approvals.slice(0, 10).map(approvalContextFromRequest);
          return (
            <>
              <article className="panel settings-wide governance-summary-panel">
                <div className="panel-heading"><div><span className="section-kicker">Governance</span><h2>Unified governance summary</h2></div></div>
                <dl className="detail-list">
                  <div><dt>Knowledge permission state</dt><dd>{summaryPanel.knowledgePermissionState}</dd></div>
                  <div><dt>Provider approval state</dt><dd>{summaryPanel.providerApprovalState}</dd></div>
                  <div><dt>Local models</dt><dd>{summaryPanel.localModelStatus} <span className="status-pill status-ready"><i />{privacyIndicator("local_only").label}</span></dd></div>
                  <div><dt>Provider models</dt><dd>{summaryPanel.providerModelStatus} <span className="status-pill status-active"><i />{privacyIndicator("approval_required").label}</span></dd></div>
                  <div><dt>Workflow governance</dt><dd>{summaryPanel.workflowGovernanceState}</dd></div>
                </dl>
                <p className="muted-copy">{summaryPanel.note}</p>
                {approvalRecords.length > 0 && (
                  <div className="gov-approval-list">
                    <strong>Approval context (recorded requests)</strong>
                    {approvalRecords.map((record, index) => (
                      <div key={index} className="gov-approval-row">
                        <small><strong>{record.whatRequiresApproval}</strong> — {record.affectedResource} · state: {record.currentState} · {record.recordedTimestamp ?? "no timestamp recorded"}</small>
                      </div>
                    ))}
                  </div>
                )}
              </article>
              <article className="panel settings-wide governance-boundary-panel">
                <div className="panel-heading"><div><span className="section-kicker">User control boundary</span><h2>What you control — what AMCA never does</h2></div></div>
                <div className="gov-boundary-grid">
                  <div>
                    <strong>Users control</strong>
                    <ul>{USER_CONTROL_BOUNDARY.usersControl.map((line, index) => <li key={index}>{line}</li>)}</ul>
                  </div>
                  <div>
                    <strong>AMCA does not</strong>
                    <ul>{USER_CONTROL_BOUNDARY.amcaDoesNot.map((line, index) => <li key={index}>{line}</li>)}</ul>
                  </div>
                </div>
                <p className="muted-copy">{USER_CONTROL_BOUNDARY.note}</p>
                <small className="muted-copy">
                  Indicators: {privacyIndicator("local_only").label} · {privacyIndicator("approval_required").label} · {privacyIndicator("explicitly_attached").label} · {privacyIndicator("not_recorded").label} — e.g. run governance line: {runGovernanceLine({ privacy: "local-only", mode: "solo" })}
                </small>
              </article>
            </>
          );
        })()}
        <article className="panel settings-wide beta-scenarios">
          <div className="panel-heading"><div><span className="section-kicker">Validation scenarios</span><h2>Beta workflow scenarios</h2></div></div>
          {VALIDATION_SCENARIOS.map((scenario) => (
            <details key={scenario.id} className="beta-scenario">
              <summary><strong>{scenario.name}</strong><span className={`status-pill status-${scenario.status}`}><i />{scenario.status === "not_recorded" ? "Not recorded" : scenario.status}</span></summary>
              <div className="beta-scenario-body">
                <dl className="detail-list">
                  <div><dt>Purpose</dt><dd>{scenario.purpose}</dd></div>
                  <div><dt>Modules involved</dt><dd>{scenario.modules.join(" · ")}</dd></div>
                  <div><dt>Status</dt><dd>{scenario.statusReason}</dd></div>
                  <div><dt>Evidence references</dt><dd>{scenario.evidenceReferences.join(" · ")}</dd></div>
                </dl>
                {scenario.knownLimitations.length > 0 && (
                  <p className="settings-note">Known limitations: {scenario.knownLimitations.join("; ")}.</p>
                )}
              </div>
            </details>
          ))}
        </article>
        <article className="panel settings-wide beta-limitations">
          <div className="panel-heading"><div><span className="section-kicker">Known limitations</span><h2>Recorded limitation records</h2></div><small>{LIMITATION_RECORDS.length}</small></div>
          <ul className="beta-limitation-list">
            {LIMITATION_RECORDS.map((record) => (
              <li key={record.title}>
                <strong>{record.title}</strong>
                <span>{record.detail}</span>
                <small>Module: {record.module} · Workaround: {record.workaround} · Target: {record.targetRelease}</small>
              </li>
            ))}
          </ul>
        </article>
        <article className="panel settings-wide beta-feedback">
          <div className="panel-heading"><div><span className="section-kicker">Feedback</span><h2>Local feedback pathways</h2></div><span className="status-pill status-ready"><i />Local only</span></div>
          <ul className="beta-feedback-list">
            {FEEDBACK_PATHS.map((path) => (
              <li key={path.id}>
                <strong>{path.label}</strong>
                <span>{path.detail}</span>
                <button className="secondary-button" onClick={() => setView(path.view as View)}>Open {path.view === "dashboard" ? "Dashboard feedback panel" : path.view}</button>
              </li>
            ))}
          </ul>
        </article>
      </section>
    ),
    tools: (
      <ToolFabric
        projects={studioProjects}
        selectedProjectId={activeStudioProject?.project_id || project?.project_id || null}
        onSelectProject={(id) => void openStudioProject(id)}
      />
    ),
    evidence: null,
    skills: <SkillsWorkspace onUse={(name, text) => { useSkillInComposer(name, text); }} />,
    agents: <AgentLibrary initialTab={agentsTab} />, automations, plugins,
    review: (
      <ReviewCenter
        studioProjects={studioProjects}
        onOpenProject={(id) => {
          setView("overview");
          void openStudioProject(id);
        }}
        onOpenChat={(id) => {
          setView("chat");
          void chooseConversation(id);
        }}
      />
    ),
  };


  content.evidence = researchInspection ? <section><h2>{researchInspection.title}</h2><RecordedDetails value={researchInspection.value}/></section> : <p>Select a research source or evidence item.</p>;
  content.research = <ResearchWorkspace projectId={activeStudioProject?.project_id || project?.project_id || "default"} onInspect={(title,value)=>{setResearchInspection({title,value});inspect("evidence");}} />;
  content.artifacts = <ArtifactInspector projectId={project?.project_id || activeStudioProject?.project_id || "default"} focusArtifactId={focusedArtifactId}/>;
  content.workspace = <section className="command-centre">
    {/* A persistent exit. Previously the only way out was to open a specific
        run, so a user with no runs was stuck here. */}
    <header className="command-centre-header">
      <div><h1>Command Centre</h1><p>{online ? "Runtime connected" : "Runtime offline"} · {dashboard.runs.filter(run=>["running","queued","cancelling"].includes(run.status)).length} active runs</p></div>
      <nav className="response-actions" aria-label="Leave Command Centre">
        <button onClick={()=>setView(previousView || "chat")}>← Back</button>
        <button onClick={()=>setView("chat")}>Close</button>
      </nav>
    </header>
    <nav className="response-actions"><button onClick={()=>inspect("approvals")}>Pending Approvals ({approvals.filter(item=>item.status==="pending").length})</button><button onClick={()=>inspect("agents")}>Agents / Nano Agents</button><button onClick={()=>inspect("tools")}>Browser</button><button onClick={()=>setView("automations")}>Automations ({automationDefinitions.filter(item=>item.enabled).length} enabled)</button><button onClick={()=>inspect("logs")}>Activity / Provenance</button><button onClick={()=>setView("tools")}>Tools</button></nav>
    <h2>Active Runs</h2>{dashboard.runs.filter(run=>["running","queued","cancelling"].includes(run.status)).map(run=><article className="file-row" key={run.run_id}><div><strong>{run.goal}</strong><small>{humanize(run.mode)} · {run.status} · {run.model || "Auto Intelligence"}</small></div><button onClick={()=>{void loadProject(run.project_id);inspect("room");}}>Open run details</button><button onClick={async()=>{try{await amca.cancelRun(run.run_id);await refresh();}catch(e){setError(String(e));}}}>Stop</button></article>)}
    <h2>Recent Runs / Errors</h2>{dashboard.runs.filter(run=>!["running","queued","cancelling"].includes(run.status)).slice(0,20).map(run=><article className="file-row" key={run.run_id}><div><strong>{run.goal}</strong><small>{run.status} · {humanize(run.mode)}</small></div><button onClick={()=>{void loadProject(run.project_id);inspect("room");}}>Details</button><button onClick={()=>{setPrompt(run.goal);setView("chat");}}>Retry</button></article>)}
    {!dashboard.runs.length&&<p>No recorded runs yet.</p>}
  </section>;
  const renderThreadRow = (conversation: ConversationSummary, nested = false) => {
    const isMenuOpen = openThreadMenuId === conversation.conversation_id;
    return (
      <div className={`conversation-row ${nested ? "nested" : ""} ${activeConversationId === conversation.conversation_id ? "active" : ""}`} key={conversation.conversation_id} data-conversation-id={conversation.conversation_id}>
        <button className="conversation-open" onClick={() => void chooseConversation(conversation.conversation_id)}>
          <strong>{conversation.title}</strong>
        </button>
        <div className="thread-menu">
          <button
            type="button"
            className="thread-menu-trigger"
            aria-label={`More actions for ${conversation.title}`}
            title="Chat actions"
            aria-expanded={isMenuOpen}
            onClick={(e) => {
              e.stopPropagation();
              setOpenThreadMenuId(isMenuOpen ? null : conversation.conversation_id);
            }}
          >
            ⋯
          </button>
          {isMenuOpen && (
            <div className="thread-menu-dropdown" role="menu">
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpenThreadMenuId(null);
                  void renameConversation(conversation);
                }}
              >
                Rename
              </button>
              <label>
                Move to Project
                <select
                  aria-label={`Project for ${conversation.title}`}
                  value={conversation.workspace_project_id ?? ""}
                  onChange={(event) => {
                    setOpenThreadMenuId(null);
                    void moveConversationToProject(conversation, event.target.value || null);
                  }}
                >
                  <option value="">No Project</option>
                  {studioProjects.map((item) => (
                    <option key={item.project_id} value={item.project_id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                role="menuitem"
                className="danger"
                onClick={() => {
                  setOpenThreadMenuId(null);
                  void deleteConversation(conversation);
                }}
              >
                Delete
              </button>
            </div>
          )}
        </div>
      </div>
    );
  };
  /**
   * Settings are organised as five goal-oriented groups.
   *
   * The previous flat list of thirteen categories made every option equally
   * prominent, so nothing was findable.  No section was removed — each one is
   * rendered inside its group, and the group is what the navigation shows.
   */
  const SETTINGS_GROUPS: Record<string, string[]> = {
    General: ["General", "Usage", "Diagnostics / Advanced", "About & Updates"],
    Appearance: ["Appearance"],
    // Skills and Plugins are their own settings destinations, and the Tool
    // Fabric panel is not part of this page: stacking four sections under one
    // heading was exactly the clutter the operator removed.
    "Models & Providers": ["Models & Providers"],
    Skills: ["Skills"],
    Plugins: ["Plugins"],
    "Auto Intelligence": ["Auto Intelligence"],
    "Agents": ["Agents"],
    Permissions: ["Local PC & Permissions"],
  };
  const settingsCategories = Object.keys(SETTINGS_GROUPS);
  /** Map a section name (as passed by openSettings callers) to its group. */
  const settingsGroupFor = (section: string) =>
    settingsCategories.find((group) => SETTINGS_GROUPS[group].includes(section)) ?? "General";
  const settingsSectionBody = (name: string) => (
        name === "General" ? (
          <div className="settings-section">
            <h2>General Preferences</h2>
            <p className="muted-copy">Configure startup options, desktop behaviors, and workspace preferences.</p>
            <AutostartToggle />
          </div>
        ) : name === "Appearance" ? (
          <div className="settings-section">
            <h2>Appearance</h2>
            <p className="muted-copy">Choose how AMCA Studio looks on your display.</p>
            <div className="theme-picker">
              {(["light", "dark"] as ThemePreference[]).map((theme) => (
                // The .theme-option styling exists for exactly this control; without
                // the class these render as native buttons, which in dark mode are
                // dark text on a dark background and effectively unreadable.
                <button
                  key={theme}
                  className={"theme-option" + (themePreference === theme ? " active" : "")}
                  aria-pressed={themePreference === theme}
                  onClick={() => setThemePreference(theme)}
                >
                  {humanize(theme)}
                </button>
              ))}
            </div>
            <h2>Text size</h2>
            <p className="muted-copy">Applies to the sidebar, lists, and interface labels.</p>
            <div className="theme-picker">
              {(["compact", "standard", "large"] as const).map((scale) => (
                <button
                  key={scale}
                  className={"theme-option" + (uiFontScale === scale ? " active" : "")}
                  aria-pressed={uiFontScale === scale}
                  onClick={() => void chooseUiFontScale(scale)}
                >
                  {humanize(scale)}
                </button>
              ))}
            </div>
          </div>
        ) : name === "Models & Providers" ? (
          Children.toArray(settings.props.children).filter((child) => isValidElement(child) && child.type === ModelHub)
        ) : name === "Auto Intelligence" ? (
          <IntelligencePanel />
        ) : name === "Local PC & Permissions" ? (
          <>
            <div className="settings-section">
              <h2>Cloud transmission approval</h2>
              <p className="muted-copy">
                Exact scope shows an approve/deny card for every cloud model call.
                Automatic applies your standing owner policy: cloud model calls are
                pre-authorized (provenance &quot;owner-auto-policy&quot;) and no card appears.
                The single-use and scope checks stay enforced either way.
              </p>
              <div className="theme-picker">
                {([
                  { mode: false, label: "Exact scope" },
                  { mode: true, label: "Automatic (owner policy)" },
                ] as const).map((option) => (
                  <button
                    key={option.label}
                    className={"theme-option" + (cloudApprovalAutomatic === option.mode ? " active" : "")}
                    aria-pressed={cloudApprovalAutomatic === option.mode}
                    onClick={() => void chooseCloudApprovalAutomatic(option.mode)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
            {computerAccess}
          </>
        ) : name === "Agents" ? (
          <AgentLibrary initialTab="monitor" />
        ) : name === "Plugins" ? (
          plugins
        ) : name === "Skills" ? (
          content.skills
        ) : name === "Usage" ? (
          cost
        ) : name === "Diagnostics / Advanced" || name === "Diagnostics" ? (
          <>
            <p>Local service: {API_LABEL} · {online ? "Connected" : "Offline"}</p>
            {logs}
            <details>
              <summary>Advanced workspaces</summary>
              {NAVIGATION.map((item) => (
                <button key={item.id} onClick={() => setView(item.id)}>
                  {item.label}
                </button>
              ))}
            </details>
          </>
        ) : name === "About & Updates" ? (
          <AboutUpdatesPanel />
        ) : (
          <>
            <p>AMCA Studio {BETA_STATUS.appVersion}</p>
            <p>Updates are installed manually. No automatic update service is configured.</p>
            {content.help}
            <AutostartToggle />
          </>
        )
  );

  const consolidatedSettings = (
    <section className="consolidated-settings">
      <div className="settings-detail">
        {/* These categories' bodies carry their own page headings; a second h1
            above them is the duplication the operator removed. */}
        {!["Models & Providers", "Skills", "Plugins"].includes(settingsCategory) && <h1>{settingsCategory}</h1>}
        {SETTINGS_GROUPS[settingsCategory].map((section) => (
          <div className="settings-group-section" key={section}>
            {SETTINGS_GROUPS[settingsCategory].length > 1 && (
              <h2 className="settings-group-heading">{section}</h2>
            )}
            {settingsSectionBody(section)}
          </div>
        ))}
      </div>
    </section>
  );

  return (
    <div className={`app-shell studio-shell ${leftOpen ? "left-open" : "left-closed"} ${inspectorOpen ? "right-open" : ""}`} style={{ "--inspector-width": `${inspectorWidth}px` } as React.CSSProperties}>
      {threadDialog&&<ThreadDialog action={threadDialog.action} title={threadDialog.conversation.title} onClose={()=>setThreadDialog(null)} onConfirm={title=>threadDialog.action==="rename"?saveConversationTitle(threadDialog.conversation,title):confirmDeleteConversation(threadDialog.conversation)}/>}
      {projectPendingDelete && <ProjectDeleteDialog project={projectPendingDelete} chatCount={(conversationsByProject.get(projectPendingDelete.project_id) ?? []).length} busy={projectDeleteBusy} onCancel={()=>setProjectPendingDelete(null)} onConfirm={()=>void confirmDeleteStudioProject(projectPendingDelete)}/>}
      {viewJumpOpen && (
        <div className="view-jump-overlay" role="dialog" aria-label="Jump to view" onClick={() => setViewJumpOpen(false)}>
          <div className="view-jump" onClick={(event) => event.stopPropagation()} onKeyDown={(event)=>{if(event.key==="Tab"){const nodes=Array.from(event.currentTarget.querySelectorAll<HTMLElement>("input,button,[tabindex='0']"));const first=nodes[0],last=nodes[nodes.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}}}>
            <input
              autoFocus
              value={viewJumpQuery}
              placeholder="Search chats, Projects, sources and commands…"
              aria-label="Jump to view"
              onChange={(event) => setViewJumpQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  const first = event.currentTarget.parentElement?.querySelector<HTMLButtonElement>("li button");
                  first?.click();
                }
              }}
            />
            <ul>
              {studioProjects.filter(item=>item.name.toLowerCase().includes(viewJumpQuery.toLowerCase())).map(item=><li key={item.project_id}><button onClick={()=>{void openStudioProject(item.project_id);setViewJumpOpen(false);}}>Project · {item.name}</button></li>)}
              {conversations.filter(item=>`${item.title} ${item.last_message_preview}`.toLowerCase().includes(viewJumpQuery.toLowerCase())).slice(0,20).map(item=><li key={item.conversation_id}><button onClick={()=>{void chooseConversation(item.conversation_id);setViewJumpOpen(false);}}>Chat · {item.title}</button></li>)}
              {activeStudioProject?.files.filter(item=>item.filename.toLowerCase().includes(viewJumpQuery.toLowerCase())).map(item=><li key={item.record_id}><button onClick={()=>{setPreviewAsset(item);inspect("files");setViewJumpOpen(false);}}>Source · {item.filename}</button></li>)}
              {filterViewJumps(NAVIGABLE_VIEWS, viewJumpQuery).map((entry) => (
                <li key={entry.id}>
                  <button onClick={() => { setView(entry.id as View); setViewJumpOpen(false); }}>
                    <span>{entry.label}</span><code>{entry.id}</code>
                  </button>
                </li>
              ))}
              {!filterViewJumps(NAVIGABLE_VIEWS, viewJumpQuery).length && <li className="muted-copy">No view matches “{viewJumpQuery}”.</li>}
            </ul>
            <small>Enter opens the first match · Esc closes</small>
          </div>
        </div>
      )}
      {leftOpen && <button className="pane-scrim left-scrim" aria-label="Close navigation drawer" onClick={() => setLeftOpen(false)} />}
      <aside className="sidebar" aria-label="Primary Navigation Sidebar">
        <div className="brand"><span className="brand-identity"><img className="brand-symbol" src={amcaSymbol} alt="AMCA" /><strong>AMCA Studio</strong></span>{leftOpen ? <button className="icon-button" title="Collapse sidebar" aria-label="Collapse sidebar" onClick={() => { setLeftOpen(false); localStorage.setItem("amca.left", "false"); }}>‹</button> : null}</div>
        {view === "settings" ? (
          <>
            <div className="settings-sidebar-header">
              <button
                className="settings-back-button"
                onClick={() => setView(previousView || "chat")}
                title="Return to chats"
              >
                ← Back to Chats
              </button>
            </div>
            <nav className="studio-settings-nav" aria-label="Settings navigation">
              {settingsCategories.map((category) => (
                <button
                  key={category}
                  className={category === settingsCategory ? "active" : ""}
                  onClick={() => setSettingsCategory(category)}
                >
                  {category}
                </button>
              ))}
            </nav>
            <div className="sidebar-footer sidebar-footer-compact">
              <span className="footer-avatar" aria-hidden="true">U</span>
              <span className="profile-row" title="Local workspace">User</span>
              <button className={`icon-button${updateAvailable ? " update-available" : ""}`} aria-label="Check for Updates" title={updateAvailable ? "Update available — check for details" : "Check for Updates"} onClick={() => openSettings("About & Updates")}><Icon name="refresh" /></button>
              <button className="icon-button" aria-label="Settings" title="Settings" onClick={() => openSettings()}><Icon name="settings" /></button>
            </div>
          </>
        ) : (
          <>
            <nav className="studio-primary-nav" aria-label="AMCA Studio">
              {/* The destination is stated before creation. With a project open the
                  user previously had no way to start a chat outside it. */}
              <div className="new-chat-control">
                {/* One control, one destination: New Chat is always a
                    standalone chat, whichever chat or project is open, so the
                    sidebar behaves identically everywhere. Chats INSIDE a
                    project are created from that project's page. */}
                <button onClick={() => void createNewConversation("standalone")} disabled={conversationBusy}>
                  ＋ <span>New Chat</span>
                  <small>standalone</small>
                </button>
              </div>
              <button onClick={() => { setViewJumpQuery(""); setViewJumpOpen(true); }}>⌕ <span>Search</span></button>
              {/* 19B1 Amendment 4 (7): secondary destinations behind a real More disclosure */}
              <button aria-expanded={moreNavOpen} onClick={() => setMoreNavOpen((value) => !value)}>{moreNavOpen ? "▾" : "⋯"} <span>More</span></button>
              {moreNavOpen && <>
                <button className={view === "automations" ? "more-nav-active" : ""} onClick={() => setView("automations")}><Icon name="automation" /><span>Automations</span></button>
                <button className={view === "plugins" ? "more-nav-active" : ""} onClick={() => setView("plugins")}><Icon name="plugins" /><span>Plugins</span></button>
                <button className={view === "skills" ? "more-nav-active" : ""} onClick={() => { setView("skills"); }}><Icon name="brain" /><span>Skills</span></button>
              </>}
            </nav>
            <section className="sidebar-project-section" aria-label="Projects">
              {/* 19B1 Amendment 4 (8): the Projects heading is a real disclosure control */}
              <div className="conversation-section-heading"><button className="section-disclosure" aria-expanded={projectsSectionOpen} onClick={() => setProjectsSectionOpen((value) => !value)}><span>Projects {projectsSectionOpen ? "▾" : "▸"}</span></button><button title="New Project" aria-label="New Project" onClick={() => { setShowProjectForm(true); setView("overview"); }}>＋</button></div>
              {projectsSectionOpen ? <>
              <div className="sidebar-project-list">
              {/* 19B1 D2/D3: bounded scroll region; the active project auto-reveals
                  into view when selected or newly created — never hidden behind
                  the pinned footer, no horizontal scrollbar. */}
              {/* 19B1 D4: the project header is a real toggle — clicking an expanded
                  project collapses it (chats stay intact), clicking a collapsed one
                  expands it and opens the project. Chevron reflects real state. */}
              {/* 19B1 Amendment 4 (9): compact initial set (first 5) with Show more/less */}
              {(projectsShowAll ? studioProjects : studioProjects.slice(0, 5)).map((item) => {
                const expanded = expandedProjectId === item.project_id && activeStudioProject?.project_id === item.project_id;
                return <div key={item.project_id} data-project-id={item.project_id}>
                <div className={`sidebar-project-row ${expanded ? "active" : ""}`}>
                  {/* The project row is the only open/close control: opening a
                      chat under the project never collapses it. */}
                  <button className="sidebar-project-open" aria-expanded={expanded} onClick={() => { if (expanded) { setExpandedProjectId(null); } else { void openStudioProject(item.project_id); } }}><Icon name={expanded ? "folderOpen" : "folder"} /><strong>{item.name}</strong></button>
                  <button className="sidebar-project-delete" aria-label={`Delete project ${item.name}`} title="Delete project" onClick={() => setProjectPendingDelete(item)}>Delete</button>
                </div>
                {/* Project chats are nested under their project, and each carries the
                    indentation class so the hierarchy is visible, not implied. */}
                {expanded && (conversationsByProject.get(item.project_id) ?? []).map((conversation) => renderThreadRow(conversation, true))}
              </div>;
              })}
              </div>
              {studioProjects.length > 5 && <button className="sidebar-show-more" onClick={() => setProjectsShowAll((value) => !value)}>{projectsShowAll ? "Show less" : `Show more (${studioProjects.length - 5} more)`}</button>}
              </> : <p className="conversation-empty">Projects hidden — selecting a project reveals it.</p>}
            </section>
            <section className="conversation-section" aria-label="Chats">
              {/* 19B1 Amendment 4 (10): Chats heading is a real disclosure control */}
              <div className="conversation-section-heading"><button className="section-disclosure" aria-expanded={chatsSectionOpen} onClick={() => setChatsSectionOpen((value) => !value)}><span>Chats {chatsSectionOpen ? "▾" : "▸"}</span></button></div>
              {chatsSectionOpen ? <div className="conversation-list">{standaloneConversations.map((conversation) => renderThreadRow(conversation))}</div> : <p className="conversation-empty">Chats hidden — selecting a chat reveals it here.</p>}
            </section>
            <div className="sidebar-footer sidebar-footer-compact"><span className="footer-avatar" aria-hidden="true">U</span><span className="profile-row" title="Local workspace">User</span><button className={`icon-button${updateAvailable ? " update-available" : ""}`} aria-label="Check for Updates" title={updateAvailable ? "Update available — check for details" : "Check for Updates"} onClick={() => openSettings("About & Updates")}><Icon name="refresh" /></button><button className="icon-button" aria-label="Settings" title="Settings" onClick={() => openSettings()}><Icon name="settings" /></button></div>
          </>
        )}
      </aside>
      <main>
        <header className="topbar"><div><button className="icon-button" aria-label="Toggle navigation" title="Toggle navigation" aria-expanded={leftOpen} onClick={() => setLeftOpen(!leftOpen)}>☰</button><strong>{view === "project" ? activeStudioProject?.name ?? "Project" : view === "skills" ? "Skills" : view === "workspace" ? "Command Centre" : view === "chat" ? conversations.find((item) => item.conversation_id === activeConversationId)?.title ?? "New Chat" : view === "settings" ? `Settings · ${settingsCategory}` : pageTitle}</strong></div><div className="topbar-actions"><button title="Command Centre" onClick={() => { if (view !== "workspace") setPreviousView(view); setView("workspace"); }}>Command Centre{pendingReviewApprovals.length ? ` · ${pendingReviewApprovals.length}` : ""}</button><button className="icon-button" title="Toggle inspector" aria-label="Toggle inspector" aria-expanded={inspectorOpen} onClick={() => setInspectorOpen(!inspectorOpen)}>◫</button></div></header>
        <div className="content-area">
          {error && <div className="error-banner"><div><strong>{online ? "AMCA reported a problem" : "Desktop service is not running"}</strong><span>{error}</span>{!online && <code>$env:PYTHONPATH="src"; python -m amca.service</code>}</div><button onClick={() => void retryFailure()} disabled={retrying}>{retrying ? "Retrying…" : "Retry"}</button></div>}
          {view === "settings" ? consolidatedSettings : content[view]}
        </div>
      </main>
      {inspectorOpen && <>
        <button className="pane-scrim right-scrim" aria-label="Close inspector drawer" onClick={() => setInspectorOpen(false)} />
        <aside className="context-inspector" aria-label="Contextual Inspector Sidebar">
          <div className="inspector-resize" role="separator" aria-label="Inspector width" aria-orientation="vertical" aria-valuenow={inspectorWidth} aria-valuemin={320} aria-valuemax={680} tabIndex={0}
            onKeyDown={(event) => { if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); setInspectorWidth((width) => Math.max(320, Math.min(680, width + (event.key === "ArrowLeft" ? 20 : -20)))); } }}
            onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); }}
            onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) setInspectorWidth(Math.max(320, Math.min(680, window.innerWidth - event.clientX))); }}
            onPointerUp={(event) => event.currentTarget.releasePointerCapture(event.pointerId)} />
          {/* Round 25: chat/project render the ZCode-style panel chrome inside
             StudioPanel itself - the old inspector header stays only for the
             classic views. */}
          {view !== "chat" && view !== "project" && (
            <header><strong>Inspector</strong><button aria-label="Hide inspector" title="Hide inspector" onClick={() => setInspectorOpen(false)}>×</button></header>
          )}
          {(view === "chat" || view === "project") ? (
            <StudioPanel
              tabs={panelTabs}
              activeKey={activePanelKey}
              onSelect={(key) => setActivePanelKey(key === "__newtab__" ? null : key)}
              onClose={closePanelTab}
              onOpenFacility={openPanelFacility}
              onSideConversation={() => void createNewConversation("standalone")}
              onCollapse={() => setInspectorOpen(false)}
              onWiden={() => setInspectorWidth((width) => Math.min(680, width + 140))}
              projectId={activeStudioProject?.project_id ?? null}
              workspaceLabel={activeStudioProject?.name ?? "AMCA"}
              reviewView={content.review}
            />
          ) : (
          <>
          <nav aria-label="Inspector content">{([["room", "Run"], ["files", "Sources"], ["workbench", "Files"], ["artifacts", "Artifacts"], ["tools", "Browser"], ["approvals", "Tool Approval"], ["history", "Execution History"]] as [View, string][]).map(([id, label]) => <button key={id} className={inspectorView === id ? "active" : ""} onClick={() => setInspectorView(id)}>{label}</button>)}</nav>
          <div className="inspector-body">{workflowDetails}<details><summary>Agents / Nano workflow</summary><NanoAgentsPanel /></details>{inspectorView === "files" && previewAsset ? <SourcePreview asset={previewAsset}/> : inspectorView === "tools" ? <BrowserInspector projectId={permissionProjectId || ""} grants={localTools?.grants ?? []} initialUrl={browserUrl}/> : content[inspectorView]}</div>
          </>
          )}
        </aside>
      </>}
    </div>
  );
}
