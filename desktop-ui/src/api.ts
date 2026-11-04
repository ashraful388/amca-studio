import type {
  KnowledgeSearchRequest,
  KnowledgeSearchResponse,
} from "./knowledgeIntelligenceModel";
import type {
  AgentFabricSettings,
  StudioWorkflowDetail,
  StudioWorkflowSummary,
  AgentFabricState,
  AgentTemplate,
  AssetRecord,
  ApprovalRequest,
  AutomationDefinition,
  AutomationOccurrence,
  AutomationSchedulerStatus,
  BenchmarkTask,
  ChatApprovalPlan,
  CommandCancelReceipt,
  CommandRunSnapshot,
  ExplorerFile,
  PanelPage,
  ExplorerListing,
  TerminalOutputPage,
  TerminalSessionInfo,
  CognitiveEvent,
  CollaborationArchitecture,
  ConversationDocument,
  ConversationSummary,
  CredentialStatus,
  Dashboard,
  HardwareProfile,
  LocalModelCatalog,
  LocalFileCapability,
  LocalPermissionProfile,
  LocalPermissionProfileState,
  LocalPathGrant,
  LocalToolSnapshot,
  FullAccessScope,
  MediaRuntimeStatus,
  ModelHubState,
  ModelTeam,
  NewHubProvider,
  NewCustomAgent,
  NewModelProfile,
  ProjectState,
  RunActivity,
  RunJob,
  StudioProjectDocument,
  StudioProjectSummary,
  WorkspaceChangeEntry,
  WorkspaceFileInfo,
  WorkspaceOpenFile,
  WorkspaceSaveResult,
  ArtifactMeta,

  ArtifactSourceDoc,
  ArtifactExportResult,
  ArtifactItem,
  ArtifactVersionItem,
  ArtifactExportCapabilities,
  ArtifactRenderCapability,
  ArtifactExportRecord,
  ArtifactPreview,
  ReviewItem,
  NotificationPreferences,
  PendingNotification,
  EngineeringWorkspace,
  RepositoryStatus,
  FileTreeResponse,
  FileInspectResponse,
  WorkspaceSearchResponse,
  EngineeringDiff,
  ApplyRecord,
  RunnerProfile,
  ExecutionPlan,
  CommandResult,
  TestRun,
  BuildResult,
  GitWorkspaceState,
  GitBranch,
  GitWorktree,
  CommitPreparation,
  GitCommitResult,
  EngineeringWorkflowPipeline,
  ToolCapability,
  ToolRequest,
  ToolExecutionRecord,
  ToolApprovalRecord,
  ToolTelemetry,
  ResearchSession,
  ResearchSource,
  ResearchSnapshot,
  ResearchExtraction,
  ResearchEvidence,
  ResearchCitation,
  ResearchQuestion,
  ResearchClaim,
  ResearchClaimEvidenceLink,
  ResearchCoverage,
  ResearchSynthesisPlan,
  ResearchSynthesisVersion,
  ResearchSearchEvent,
  ResearchWebAcquisition,
  NanoAgentsResponse,
  ModelHealthRecord,
  ModelHealthReport,
} from "./types";



const isVitePreview =
  ["127.0.0.1", "localhost"].includes(window.location.hostname) &&
  window.location.port === "1420";
const API_BASE =
  import.meta.env.VITE_AMCA_API_URL ??
  (isVitePreview ? "" : "http://127.0.0.1:8765");
const API_LABEL = API_BASE || "same-origin /api proxy";
const DESKTOP_PROTOCOL_VERSION = 15;

export interface IntelligenceCapabilityState {
  implemented: boolean;
  enabled: boolean;
  runtime_ready: string;
  /** Why readiness is not "ready", when the backend states a reason. */
  readiness_reason?: string;
}

export interface IntelligenceStatus {
  contract_version: number;
  seam_id: string;
  version: number;
  gates: Record<string, boolean>;
  capability_contract_version: number;
  capabilities: Record<string, IntelligenceCapabilityState>;
  execution_authority: string;
}

export interface IntelligenceValidationDecision {
  requested: boolean;
  implemented?: boolean;
  enabled: boolean;
  runtime_ready?: string;
  available?: boolean;
  decision: string;
  reason?: string;
}

export interface IntelligenceValidation {
  contract_version: number;
  requested: string[];
  decisions: Record<string, IntelligenceValidationDecision>;
  execution_authority: string;
}

// M5.7 secure controlled enablement: response of the only intelligence
// mutation route (POST /api/intelligence/configure, hardened bootstrap).
export interface IntelligenceConfiguration {
  snapshot_version: number;
  gates: Record<string, boolean>;
  changes: Record<string, boolean>;
  correlation_id: string;
}

class AMCAClient {
  private token: string | null = null;

  private async session(): Promise<string> {
    if (this.token) return this.token;
    // M5.4: inside the Tauri shell the ephemeral session token is
    // transferred out-of-band via a Tauri command - it never travels over
    // unauthenticated HTTP. The HTTP bootstrap below remains only for
    // manual/dev starts where the backend generated its own token.
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      const injected = await invoke<string>("session_token");
      if (typeof injected === "string" && injected.length >= 32) {
        this.token = injected;
        return this.token;
      }
    } catch {
      // not running inside the Tauri shell; fall through to HTTP bootstrap
    }
    const response = await fetch(`${API_BASE}/api/session`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Desktop service returned ${response.status}`);
    const value = (await response.json()) as {
      session_token: string;
      protocol_version?: number;
    };
    if (value.protocol_version !== DESKTOP_PROTOCOL_VERSION) {
      throw new Error(
        `Incompatible AMCA backend on port 8765 (expected protocol ${DESKTOP_PROTOCOL_VERSION}). Close older AMCA versions and retry.`,
      );
    }
    this.token = value.session_token;
    return value.session_token;
  }

  private async send<T>(path: string, init?: RequestInit): Promise<T> {
    const token = await this.session();
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        "X-AMCA-Session": token,
        ...init?.headers,
      },
    });
    const value = (await response.json()) as T & { error?: string };
    if (!response.ok) {
      throw new Error(value.error ?? `AMCA service returned ${response.status}`);
    }
    return value;
  }

  // 28B: the desktop shell respawns a crashed backend within a few seconds
  // while the session token stays the same. Read-only requests may therefore
  // wait out that window instead of failing in the user's face. Mutations are
  // never retried automatically: a request that may already have been applied
  // must not run twice, so it reports the restart truthfully instead.
  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const method = (init?.method ?? "GET").toUpperCase();
    const attempts = method === "GET" ? 3 : 1;
    let lastError: unknown;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        return await this.send<T>(path, init);
      } catch (error) {
        lastError = error;
        const unreachable =
          error instanceof TypeError ||
          (error instanceof Error && /failed to fetch|networkerror|load failed/i.test(error.message));
        if (!unreachable) throw error;
        if (attempt < attempts - 1) {
          await new Promise((resolve) => setTimeout(resolve, 1_200 * (attempt + 1)));
          continue;
        }
      }
    }
    if (method === "GET") {
      throw new Error(
        "The AMCA background service is restarting. If this keeps happening, close AMCA and open it again.",
      );
    }
    throw new Error(
      "The AMCA background service is not responding, so this action may not have completed. Wait a moment and try again.",
      { cause: lastError },
    );
  }

  dashboard(): Promise<Dashboard> {
    return this.request<Dashboard>("/api/dashboard");
  }

  intelligenceStatus(): Promise<IntelligenceStatus> {
    return this.request<IntelligenceStatus>("/api/intelligence/status");
  }

  intelligenceValidate(
    requested: string[],
  ): Promise<IntelligenceValidation> {
    return this.request<IntelligenceValidation>("/api/intelligence/validate", {
      method: "POST",
      body: JSON.stringify({ contract_version: 2, requested }),
    });
  }

  intelligenceConfigure(
    changes: Record<string, boolean>,
  ): Promise<IntelligenceConfiguration> {
    return this.request<IntelligenceConfiguration>(
      "/api/intelligence/configure",
      {
        method: "POST",
        body: JSON.stringify({ contract_version: 1, changes }),
      },
    );
  }

  /**
   * v0.6.1 P1 readiness fix: explicit bounded LOCAL-ONLY readiness re-probe.
   * Refreshes the cached truthful readiness report the gate controller
   * consults for enablement. It never mutates gates and never contacts a
   * cloud/paid endpoint; generation only happens in the hardened desktop
   * bootstrap through the existing loopback Ollama path.
   */
  intelligenceReadinessRefresh(): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "/api/intelligence/readiness/refresh",
      { method: "POST", body: "{}" },
    );
  }

  nanoAgents(): Promise<NanoAgentsResponse> {
    return this.request<NanoAgentsResponse>("/api/nano-agents");
  }

  conversations(search = ""): Promise<{ conversations: ConversationSummary[] }> {
    const query = search.trim() ? `?q=${encodeURIComponent(search.trim())}` : "";
    return this.request<{ conversations: ConversationSummary[] }>(`/api/conversations${query}`);
  }

  conversation(conversationId: string): Promise<ConversationDocument> {
    return this.request<ConversationDocument>(
      `/api/conversations/${encodeURIComponent(conversationId)}`,
    );
  }

  createConversation(
    mode: CollaborationArchitecture,
    participantIds: string[],
    workspaceProjectId?: string,
  ): Promise<{ conversation: ConversationSummary }> {
    return this.request<{ conversation: ConversationSummary }>("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ mode, participant_ids: participantIds, workspace_project_id: workspaceProjectId }),
    });
  }

  updateConversation(
    conversationId: string,
    changes: { title?: string; mode?: CollaborationArchitecture; participant_ids?: string[]; workspace_project_id?: string | null },
  ): Promise<{ conversation: ConversationSummary }> {
    return this.request<{ conversation: ConversationSummary }>(
      `/api/conversations/${encodeURIComponent(conversationId)}/update`,
      { method: "POST", body: JSON.stringify(changes) },
    );
  }

  studioProjects(): Promise<{ projects: StudioProjectSummary[] }> {
    return this.request("/api/studio-projects");
  }

  studioProject(projectId: string): Promise<StudioProjectDocument> {
    return this.request(`/api/studio-projects/${encodeURIComponent(projectId)}`);
  }

  saveStudioProject(project: Partial<StudioProjectSummary> & Pick<StudioProjectSummary, "name">): Promise<{ project: StudioProjectSummary }> {
    return this.request("/api/studio-projects", { method: "POST", body: JSON.stringify(project) });
  }

  /**
   * v1.2.0: the exact folder a new Project of this name would get. The
   * backend owns the naming rule, so the form shows the real default instead
   * of a second implementation that could drift.
   */
  studioProjectDefaultFolder(name: string): Promise<{ default_folder_base: string; default_folder: string }> {
    return this.request(
      `/api/studio-projects-default-folder?name=${encodeURIComponent(name)}`,
    );
  }

  deleteStudioProject(projectId: string): Promise<{ deleted: string }> {
    return this.request(`/api/studio-projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
  }

  async uploadStudioAsset(workspaceProjectId: string, file: File): Promise<{ asset: AssetRecord }> {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    }
    return this.request<{ asset: AssetRecord }>("/api/studio-assets", {
      method: "POST",
      body: JSON.stringify({
        workspace_project_id: workspaceProjectId,
        filename: file.name,
        data_base64: window.btoa(binary),
      }),
    });
  }

  researchRuns(projectId:string): Promise<{runs:Array<Record<string,unknown>>}> { return this.request(`/api/research/runs?project_id=${encodeURIComponent(projectId)}`); }
  researchDetail(projectId:string,runId:string,section="status"): Promise<Record<string,unknown>> { return this.request(`/api/research/runs/${encodeURIComponent(runId)}/${section}?project_id=${encodeURIComponent(projectId)}`); }
  researchAction(projectId:string,runId:string,action:string): Promise<Record<string,unknown>> { return this.request(`/api/research/runs/${encodeURIComponent(runId)}/${action}`,{method:"POST",body:JSON.stringify({project_id:projectId})}); }
  createResearchRun(projectId:string,objective:string): Promise<{run_id:string}> { return this.request("/api/research/runs",{method:"POST",body:JSON.stringify({project_id:projectId,objective,mode:"deterministic"})}); }
  assetContent(projectId: string, recordId: string): Promise<{ asset: Omit<AssetRecord, "chunks"> & { chunks: Array<{ text?: string; locator: string }> }; data_base64: string }> {
    return this.request(`/api/studio-asset-content?project_id=${encodeURIComponent(projectId)}&record_id=${encodeURIComponent(recordId)}`);
  }
  removeStudioAsset(projectId: string, assetId: string): Promise<{ removed: string }> {
    return this.request(`/api/studio-assets/${encodeURIComponent(assetId)}?project_id=${encodeURIComponent(projectId)}`, { method: "DELETE" });
  }
  browserProposal(value: Record<string, string>): Promise<{ approval: ApprovalRequest }> {
    return this.request("/api/local-tools/browser-proposals", { method: "POST", body: JSON.stringify(value) });
  }
  browserExecute(value: Record<string, string>): Promise<Record<string, unknown>> {
    return this.request("/api/local-tools/browser/execute", { method: "POST", body: JSON.stringify(value) });
  }

  deleteConversation(conversationId: string): Promise<{ deleted: string }> {
    return this.request<{ deleted: string }>(
      `/api/conversations/${encodeURIComponent(conversationId)}`,
      { method: "DELETE" },
    );
  }

  profile(): Promise<HardwareProfile> {
    return this.request<HardwareProfile>("/api/profile");
  }

  benchmarks(): Promise<{ tasks: BenchmarkTask[] }> {
    return this.request<{ tasks: BenchmarkTask[] }>("/api/benchmarks");
  }

  project(projectId: string): Promise<ProjectState> {
    return this.request<ProjectState>(`/api/projects/${encodeURIComponent(projectId)}`);
  }

  events(projectId: string): Promise<{ events: CognitiveEvent[] }> {
    return this.request<{ events: CognitiveEvent[] }>(
      `/api/projects/${encodeURIComponent(projectId)}/events`,
    );
  }

  assets(projectId: string): Promise<{ assets: AssetRecord[] }> {
    return this.request<{ assets: AssetRecord[] }>(
      `/api/projects/${encodeURIComponent(projectId)}/assets`,
    );
  }

  approvals(projectId?: string): Promise<{ approvals: ApprovalRequest[] }> {
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
    return this.request<{ approvals: ApprovalRequest[] }>(`/api/approvals${query}`);
  }

  localTools(projectId: string): Promise<LocalToolSnapshot> {
    return this.request<LocalToolSnapshot>(
      `/api/local-tools?project_id=${encodeURIComponent(projectId)}`,
    );
  }

  setLocalPermissionProfile(
    projectId: string,
    profile: LocalPermissionProfile,
    workspaceRoot: string,
  ): Promise<{ profile: LocalPermissionProfileState }> {
    return this.request("/api/local-tools/profile", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, profile, workspace_root: workspaceRoot }),
    });
  }

  /** v1.2.0: read which drives Full Access currently covers. Never mutates. */
  localFullAccessScope(projectId: string): Promise<FullAccessScope> {
    return this.request<FullAccessScope>(
      `/api/local-tools/full-access?project_id=${encodeURIComponent(projectId)}`,
    );
  }

  /**
   * v1.2.0: re-enumerate drives and align whole-PC grants with the access
   * level. A drive attached after Full Access was switched on is deliberately
   * not covered until this runs.
   */
  refreshLocalFullAccess(projectId: string): Promise<FullAccessScope> {
    return this.request<FullAccessScope>("/api/local-tools/full-access/refresh", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId }),
    });
  }

  createLocalGrant(value: {
    project_id: string;
    root_path: string;
    capabilities: LocalFileCapability[];
    reason: string;
    ttl_seconds?: number;
    allow_system_root?: boolean;
  }): Promise<{ grant: LocalPathGrant }> {
    return this.request("/api/local-tools/grants", {
      method: "POST",
      body: JSON.stringify(value),
    });
  }

  revokeLocalGrant(grantId: string, reason: string): Promise<{ grant: LocalPathGrant }> {
    return this.request(`/api/local-tools/grants/${encodeURIComponent(grantId)}/revoke`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
  }

  requestInspectionCommand(value: {
    project_id: string;
    grant_id: string;
    argv: string[];
    cwd: string;
    timeout_seconds: number;
    reason: string;
  }): Promise<{ approval: ApprovalRequest; contract: Record<string, unknown>; execution_supported: true }> {
    return this.request("/api/local-tools/commands/request", {
      method: "POST",
      body: JSON.stringify(value),
    });
  }

  executeInspectionCommand(value: {
    project_id: string;
    grant_id: string;
    approval_id: string;
    argv: string[];
    cwd: string;
    timeout_seconds: number;
  }): Promise<{ result: { exit_code: number; stdout: string; stderr: string; elapsed_seconds: number; execution_scope: "inspection_only" } }> {
    return this.request("/api/local-tools/commands/execute", {
      method: "POST",
      body: JSON.stringify(value),
    });
  }

  automations(): Promise<{ automations: AutomationDefinition[] }> {
    return this.request("/api/automations");
  }

  schedulerStatus(): Promise<AutomationSchedulerStatus> {
    return this.request("/api/automations/status");
  }

  automationOccurrences(
    automationId: string,
    limit = 25,
  ): Promise<{ automation_id: string; occurrences: AutomationOccurrence[] }> {
    return this.request(`/api/automations/${encodeURIComponent(automationId)}/occurrences?limit=${limit}`);
  }

  saveAutomation(automation: Partial<AutomationDefinition> & Pick<AutomationDefinition, "name" | "project_id" | "goal" | "automation_type" | "schedule">): Promise<{ automation: AutomationDefinition }> {
    return this.request("/api/automations", { method: "POST", body: JSON.stringify(automation) });
  }

  deleteAutomation(automationId: string): Promise<{ deleted: string }> {
    return this.request(`/api/automations/${encodeURIComponent(automationId)}`, { method: "DELETE" });
  }

  decideApproval(
    requestId: string,
    approve: boolean,
    reason?: string,
  ): Promise<{ approval: ApprovalRequest }> {
    return this.request<{ approval: ApprovalRequest }>(
      `/api/approvals/${encodeURIComponent(requestId)}/decision`,
      { method: "POST", body: JSON.stringify({ approve, reason }) },
    );
  }

  async uploadAsset(projectId: string, file: File): Promise<{ asset: AssetRecord }> {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    }
    return this.request<{ asset: AssetRecord }>("/api/assets", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        filename: file.name,
        data_base64: window.btoa(binary),
      }),
    });
  }

  run(runId: string): Promise<RunJob> {
    return this.request<RunJob>(`/api/runs/${encodeURIComponent(runId)}`);
  }

  runActivity(runId: string): Promise<RunActivity> {
    return this.request<RunActivity>(`/api/runs/${encodeURIComponent(runId)}/activity`);
  }

  workspaceFiles(projectId: string): Promise<{ files: WorkspaceFileInfo[] }> {
    return this.request(
      `/api/projects/${encodeURIComponent(projectId)}/workspace`,
    );
  }

  workspaceOpen(
    projectId: string,
    path: string,
  ): Promise<{ file: WorkspaceOpenFile }> {
    return this.request(
      `/api/projects/${encodeURIComponent(projectId)}/workspace/open?path=${encodeURIComponent(path)}`,
    );
  }

  workspaceSave(
    projectId: string,
    path: string,
    content: string,
    baseVersion?: number,
  ): Promise<{ file: WorkspaceSaveResult }> {
    return this.request(
      `/api/projects/${encodeURIComponent(projectId)}/workspace/save`,
      {
        method: "POST",
        body: JSON.stringify({
          path,
          content,
          ...(baseVersion !== undefined ? { base_version: baseVersion } : {}),
        }),
      },
    );
  }

  workspaceRevert(
    projectId: string,
    path: string,
    version: number,
  ): Promise<{ file: WorkspaceSaveResult }> {
    return this.request(
      `/api/projects/${encodeURIComponent(projectId)}/workspace/revert`,
      {
        method: "POST",
        body: JSON.stringify({ path, version }),
      },
    );
  }

  workspaceChanges(
    projectId: string,
    limit = 20,
  ): Promise<{ changes: WorkspaceChangeEntry[] }> {
    return this.request(
      `/api/projects/${encodeURIComponent(projectId)}/workspace/changes?limit=${limit}`,
    );
  }

  proposeArgvCommand(body: {
    project_id: string;
    grant_id: string;
    argv: string[];
    cwd?: string;
    reason: string;
  }): Promise<{ approval: ApprovalRequest; execution_supported: boolean }> {
    return this.request("/api/local-tools/argv-commands/propose", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  runArgvCommand(body: {
    project_id: string;
    grant_id: string;
    approval_id: string;
    timeout_seconds?: number;
  }): Promise<CommandRunSnapshot> {
    return this.request("/api/local-tools/argv-commands/run", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  commandRunSnapshot(runId: string): Promise<{ run: CommandRunSnapshot }> {
    return this.request(
      `/api/local-tools/commands/runs/${encodeURIComponent(runId)}`,
    );
  }

  // Round 23 facilities panel: cancel a running bounded command.
  // The cancel route returns a flat receipt ({run_id, status, process_killed}),
  // NOT the {run: snapshot} envelope the snapshot route uses.
  cancelCommandRun(runId: string): Promise<CommandCancelReceipt> {
    return this.request("/api/local-tools/commands/cancel", {
      method: "POST",
      body: JSON.stringify({ run_id: runId }),
    });
  }

  artifactsList(projectId: string): Promise<{ artifacts: ArtifactMeta[] }> {
    return this.request(`/api/projects/${encodeURIComponent(projectId)}/artifacts`);
  }

  // Round 24 ZCode-style panel workspaces: live file explorer + terminal.
  explorerList(projectId: string, path: string, root?: string): Promise<ExplorerListing> {
    return this.request("/api/panel/files/list", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, path, root: root || undefined }),
    });
  }

  explorerRead(projectId: string, path: string, root?: string): Promise<ExplorerFile> {
    return this.request("/api/panel/files/read", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, path, root: root || undefined }),
    });
  }

  terminalCreate(projectId: string, cwd?: string): Promise<TerminalSessionInfo> {
    return this.request("/api/terminal/sessions", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, cwd: cwd || undefined }),
    });
  }

  // Round 26: reader fetch for sites that refuse embedding.
  panelFetchPage(url: string): Promise<PanelPage> {
    return this.request("/api/panel/browser/fetch", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
  }

  terminalOutput(sessionId: string, after: number): Promise<TerminalOutputPage> {
    return this.request(
      `/api/terminal/sessions/${encodeURIComponent(sessionId)}/output?after=${after}`,
    );
  }

  terminalInput(sessionId: string, line: string): Promise<{ sent: boolean }> {
    return this.request(
      `/api/terminal/sessions/${encodeURIComponent(sessionId)}/input`,
      { method: "POST", body: JSON.stringify({ line }) },
    );
  }

  terminalBreak(sessionId: string): Promise<{ sent: boolean; restarted?: boolean }> {
    return this.request(
      `/api/terminal/sessions/${encodeURIComponent(sessionId)}/break`,
      { method: "POST", body: JSON.stringify({}) },
    );
  }

  terminalDelete(sessionId: string): Promise<{ deleted: string }> {
    return this.request(
      `/api/terminal/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
    );
  }

  artifactSource(projectId: string, aid: string, version?: number): Promise<{ source: ArtifactSourceDoc }> {
    const suffix = version !== undefined ? `?version=${version}` : "";
    return this.request(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(aid)}/source${suffix}`);
  }

  artifactEdit(projectId: string, aid: string, content: string, baseVersion: number): Promise<{ artifact: ArtifactMeta }> {
    return this.request(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(aid)}/edit`, {
      method: "POST",
      body: JSON.stringify({ content, base_version: baseVersion }),
    });
  }

  artifactRestore(projectId: string, aid: string, version: number): Promise<{ artifact: ArtifactMeta }> {
    return this.request(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(aid)}/restore`, {
      method: "POST",
      body: JSON.stringify({ version }),
    });
  }

  /**
   * Which renditions this build can actually produce.
   *
   * Reported up front so the artifact UI can state that, for example, PDF
   * export is unavailable, instead of only surfacing it after a failed click.
   */
  artifactRenderCapabilities(): Promise<{ capabilities: ArtifactRenderCapability[] }> {
    return this.request<{ capabilities: ArtifactRenderCapability[] }>("/api/artifacts/render-capabilities");
  }

  artifactExport(projectId: string, aid: string, format: string): Promise<{ export: ArtifactExportResult }> {
    return this.request(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(aid)}/export`, {
      method: "POST",
      body: JSON.stringify({ format }),
    });
  }

  startDemo(goal: string): Promise<RunJob> {
    return this.request<RunJob>("/api/demo", {
      method: "POST",
      body: JSON.stringify({ goal }),
    });
  }

  localModels(): Promise<LocalModelCatalog> {
    return this.request<LocalModelCatalog>("/api/local-models");
  }

  mediaRuntimes(): Promise<MediaRuntimeStatus> {
    return this.request<MediaRuntimeStatus>("/api/media-runtimes");
  }

  refreshLocalModels(): Promise<LocalModelCatalog> {
    return this.request<LocalModelCatalog>("/api/local-models/refresh", {
      method: "POST",
      body: "{}",
    });
  }

  credentialStatus(): Promise<CredentialStatus> {
    return this.request<CredentialStatus>("/api/credentials/status");
  }

  saveCredential(credentialId: string, secret: string): Promise<CredentialStatus> {
    return this.request<CredentialStatus>("/api/credentials", {
      method: "POST",
      body: JSON.stringify({ credential_id: credentialId, secret }),
    });
  }

  deleteCredential(credentialId: string): Promise<CredentialStatus> {
    return this.request<CredentialStatus>(
      `/api/credentials/${encodeURIComponent(credentialId)}`,
      { method: "DELETE" },
    );
  }

  createModelProfile(profile: NewModelProfile): Promise<{
    profile: CredentialStatus["credentials"][number];
    status: CredentialStatus;
    network_started: false;
  }> {
    return this.request("/api/model-profiles", {
      method: "POST",
      body: JSON.stringify(profile),
    });
  }

  deleteModelProfile(profileId: string): Promise<{ deleted: string; status: CredentialStatus }> {
    return this.request(`/api/model-profiles/${encodeURIComponent(profileId)}`, {
      method: "DELETE",
    });
  }

  /** Desktop-wide preferences, including the Default Auto control. */
  preferences(): Promise<{ schema: string; preferences: { selection_policy: string; default_auto: boolean; ui_font_scale: string; cloud_approval_mode: string; cloud_approval_automatic: boolean } }> {
    return this.request("/api/preferences");
  }

  setPreferences(patch: { default_auto?: boolean; selection_policy?: string; ui_font_scale?: string; cloud_approval_mode?: string; cloud_approval_automatic?: boolean }): Promise<{ schema: string; preferences: { selection_policy: string; default_auto: boolean; ui_font_scale: string; cloud_approval_mode: string; cloud_approval_automatic: boolean } }> {
    return this.request("/api/preferences", { method: "POST", body: JSON.stringify(patch) });
  }

  modelHub(): Promise<ModelHubState> {
    return this.request<ModelHubState>("/api/model-hub");
  }

  // H1: model-health connection tests. A configured entry is usable only
  // after the backend has verified endpoint + credential + model id live.
  verifyModelConfiguration(input: { kind: string; ref: string; endpoint: string; model: string; local_runtime?: boolean }): Promise<ModelHealthRecord> {
    return this.request("/api/model-hub/verify", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  verifyAllConfiguredModels(): Promise<ModelHealthReport> {
    return this.request("/api/model-hub/verify-all", {
      method: "POST",
      body: "{}",
    });
  }

  createHubProvider(provider: NewHubProvider): Promise<{ provider: ModelHubState["byok_providers"][number]; network_started: false }> {
    return this.request("/api/model-hub/providers", {
      method: "POST",
      body: JSON.stringify(provider),
    });
  }

  updateHubProvider(providerId: string, provider: Partial<NewHubProvider>): Promise<{ provider: ModelHubState["byok_providers"][number]; network_started: false }> {
    return this.request(`/api/model-hub/providers/${encodeURIComponent(providerId)}/update`, {
      method: "POST",
      body: JSON.stringify(provider),
    });
  }

  discoverHubProvider(providerId: string): Promise<{ provider: ModelHubState["byok_providers"][number]; catalog_count: number; network_started: true; inference_started: false }> {
    return this.request(`/api/model-hub/providers/${encodeURIComponent(providerId)}/discover`, {
      method: "POST",
      body: "{}",
    });
  }

  /** Publish or hide one discovered model in the model selectors. */
  setHubModelVisibility(providerId: string, modelId: string, enabled: boolean): Promise<{ provider: ModelHubState["byok_providers"][number] }> {
    return this.request(`/api/model-hub/providers/${encodeURIComponent(providerId)}/models/visibility`, {
      method: "POST",
      body: JSON.stringify({ model_id: modelId, enabled }),
    });
  }

  /** Record operator-typed per-model metadata (context window, max output
   * tokens). Stored verbatim as operator-recorded values; read by no routing
   * or verification path. */
  setHubModelMeta(providerId: string, modelId: string, meta: { context_window?: number | null; max_output_tokens?: number | null }): Promise<{ provider: ModelHubState["byok_providers"][number] }> {
    return this.request(`/api/model-hub/providers/${encodeURIComponent(providerId)}/models/meta`, {
      method: "POST",
      body: JSON.stringify({ model_id: modelId, meta }),
    });
  }

  deleteHubProvider(providerId: string): Promise<{ deleted: string }> {
    return this.request(`/api/model-hub/providers/${encodeURIComponent(providerId)}`, { method: "DELETE" });
  }

  saveModelTeam(team: Pick<ModelTeam, "name" | "description" | "model_refs"> & Partial<ModelTeam>): Promise<{ team: ModelTeam }> {
    return this.request("/api/model-hub/teams", { method: "POST", body: JSON.stringify(team) });
  }

  deleteModelTeam(teamId: string): Promise<{ deleted: string }> {
    return this.request(`/api/model-hub/teams/${encodeURIComponent(teamId)}`, { method: "DELETE" });
  }

  agentFabric(): Promise<AgentFabricState> {
    return this.request<AgentFabricState>("/api/agent-fabric");
  }

  saveCustomAgent(agent: NewCustomAgent & { id?: string }): Promise<{ agent: AgentTemplate }> {
    return this.request("/api/agent-fabric/custom-agents", {
      method: "POST",
      body: JSON.stringify(agent),
    });
  }

  deleteCustomAgent(agentId: string): Promise<{ deleted: string }> {
    return this.request(`/api/agent-fabric/custom-agents/${encodeURIComponent(agentId)}`, {
      method: "DELETE",
    });
  }

  updateAgentSettings(settings: Partial<AgentFabricSettings>): Promise<{ settings: AgentFabricSettings }> {
    return this.request("/api/agent-fabric/settings", {
      method: "POST",
      body: JSON.stringify(settings),
    });
  }

  previewAgentPlan(goal: string, projectId?: string): Promise<{ plan: Record<string, unknown> }> {
    return this.request("/api/agent-fabric/plan", {
      method: "POST",
      body: JSON.stringify({ goal, project_id: projectId }),
    });
  }

  startLocal(goal: string, model: string): Promise<RunJob> {
    return this.request<RunJob>("/api/local-runs", {
      method: "POST",
      body: JSON.stringify({ goal, provider: "ollama", model, mode: "solo" }),
    });
  }

  startChat(
    goal: string,
    participantIds: string[],
    mode: CollaborationArchitecture,
    selectionPolicy: "auto_intelligence" | "manual_model_set" = "manual_model_set",
    projectId?: string,
    approvalIds: string[] = [],
    conversationId?: string,
    attachments: File[] = [],
    workspaceProjectId?: string,
    effort: "low" | "high" | "max" = "low",
  ): Promise<RunJob | ChatApprovalPlan> {
    return Promise.all(attachments.map(async (file) => {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
      }
      return { filename: file.name, data_base64: window.btoa(binary) };
    })).then((encodedAttachments) => this.request<RunJob | ChatApprovalPlan>("/api/chat-runs", {
      method: "POST",
      body: JSON.stringify({
        goal,
        participant_ids: participantIds,
        mode,
        selection_policy: selectionPolicy,
        project_id: projectId,
        approval_ids: approvalIds,
        conversation_id: conversationId,
        attachments: encodedAttachments,
        workspace_project_id: workspaceProjectId,
        effort,
      }),
    }));
  }

  cancelRun(runId: string): Promise<RunJob> {
    return this.request<RunJob>(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      body: "{}",
    });
  }

  listReviewItems(params?: {
    limit?: number;
    project_id?: string;
    unread_only?: boolean;
  }): Promise<{ review_items: ReviewItem[]; unread_count: number }> {
    const query = new URLSearchParams();
    if (params?.limit) query.set("limit", String(params.limit));
    if (params?.project_id) query.set("project_id", params.project_id);
    if (params?.unread_only) query.set("unread_only", "true");
    const qs = query.toString() ? `?${query.toString()}` : "";
    return this.request<{ review_items: ReviewItem[]; unread_count: number }>(`/api/review-queue${qs}`);
  }

  reviewQueueStatus(project_id?: string): Promise<{ unread: number }> {
    const qs = project_id ? `?project_id=${encodeURIComponent(project_id)}` : "";
    return this.request<{ unread: number }>(`/api/review-queue/unread-count${qs}`);
  }

  markReviewItemRead(itemId: string): Promise<ReviewItem> {
    return this.request<ReviewItem>(`/api/review-queue/${encodeURIComponent(itemId)}/read`, {
      method: "POST",
      body: "{}",
    });
  }

  dismissReviewItem(itemId: string): Promise<ReviewItem> {
    return this.request<ReviewItem>(`/api/review-queue/${encodeURIComponent(itemId)}/dismiss`, {
      method: "POST",
      body: "{}",
    });
  }

  getNotificationPreferences(): Promise<{ preferences: NotificationPreferences }> {
    return this.request<{ preferences: NotificationPreferences }>("/api/review-queue/notifications/preferences");
  }

  updateNotificationPreferences(prefs: {
    enabled: boolean;
    notify_attention?: boolean;
    notify_info?: boolean;
  }): Promise<{ preferences: NotificationPreferences }> {
    return this.request<{ preferences: NotificationPreferences }>("/api/review-queue/notifications/preferences", {
      method: "POST",
      body: JSON.stringify(prefs),
    });
  }

  listPendingNotifications(channel: string = "windows_native", limit: number = 10): Promise<{ pending_notifications: PendingNotification[] }> {
    const query = new URLSearchParams({ channel, limit: String(limit) });
    return this.request<{ pending_notifications: PendingNotification[] }>(`/api/review-queue/notifications/pending?${query.toString()}`);
  }

  claimNotification(reviewItemId: string, channel: string = "windows_native"): Promise<{ claimed: boolean; review_item_id: string }> {
    return this.request<{ claimed: boolean; review_item_id: string }>("/api/review-queue/notifications/claim", {
      method: "POST",
      body: JSON.stringify({ review_item_id: reviewItemId, channel }),
    });
  }

  recordNotificationResult(
    reviewItemId: string,
    success: boolean,
    channel: string = "windows_native",
    error?: string,
  ): Promise<{ recorded: boolean; review_item_id: string }> {
    return this.request<{ recorded: boolean; review_item_id: string }>("/api/review-queue/notifications/result", {
      method: "POST",
      body: JSON.stringify({ review_item_id: reviewItemId, success, channel, error }),
    });
  }

  // --- Artifact Center API Methods (W2.5) ---
  listArtifacts(projectId: string): Promise<{ artifacts: ArtifactItem[] }> {
    return this.request<{ artifacts: ArtifactItem[] }>(`/api/projects/${encodeURIComponent(projectId)}/artifacts`);
  }

  getArtifact(projectId: string, artifactId: string): Promise<{ artifact: ArtifactItem }> {
    return this.request<{ artifact: ArtifactItem }>(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}`);
  }

  getArtifactPreview(projectId: string, artifactId: string, version?: number): Promise<{ preview: ArtifactPreview }> {
    const qs = version !== undefined ? `?version=${version}` : "";
    return this.request<{ preview: ArtifactPreview }>(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/preview${qs}`);
  }

  listArtifactVersions(projectId: string, artifactId: string): Promise<{ versions: ArtifactVersionItem[] }> {
    return this.request<{ versions: ArtifactVersionItem[] }>(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/versions`);
  }

  getArtifactVersion(projectId: string, artifactId: string, versionNumber: number): Promise<{ version: ArtifactVersionItem }> {
    return this.request<{ version: ArtifactVersionItem }>(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/versions/${versionNumber}`);
  }

  restoreArtifactVersion(projectId: string, artifactId: string, targetVersionNumber: number, baseVersion: number): Promise<{ restored_version: ArtifactVersionItem }> {
    return this.request<{ restored_version: ArtifactVersionItem }>(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/versions/${targetVersionNumber}/restore`, {
      method: "POST",
      body: JSON.stringify({ base_version: baseVersion }),
    });
  }

  getArtifactExportCapabilities(projectId: string, artifactId: string): Promise<{ capabilities: ArtifactExportCapabilities }> {
    return this.request<{ capabilities: ArtifactExportCapabilities }>(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/export/capabilities`);
  }

  /**
   * v0.8.0 Knowledge Intelligence foundation: explicit, user-initiated
   * search against the recorded knowledge contract. The route may not be
   * bound in the running service yet — callers must treat absence as the
   * recorded "endpoint not connected" state (see knowledgeIntelligenceModel).
   */
  knowledgeSearch(request: KnowledgeSearchRequest): Promise<KnowledgeSearchResponse> {
    return this.request<KnowledgeSearchResponse>("/api/knowledge/search", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  exportArtifactVersion(projectId: string, artifactId: string, versionNumber: number, format: string, filename?: string): Promise<{ export: ArtifactExportResult }> {
    return this.request<{ export: ArtifactExportResult }>(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/versions/${versionNumber}/export`, {
      method: "POST",
      body: JSON.stringify({ format, filename }),
    });
  }

  listArtifactExports(projectId: string, artifactId: string): Promise<{ exports: ArtifactExportRecord[] }> {
    return this.request<{ exports: ArtifactExportRecord[] }>(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/export/history`);
  }

  // --- Engineering Workspace (AG-E1.1) ---

  listEngineeringWorkspaces(projectId: string): Promise<{ workspaces: EngineeringWorkspace[] }> {
    return this.request<{ workspaces: EngineeringWorkspace[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces`);
  }

  getEngineeringWorkspace(projectId: string, workspaceId: string): Promise<{ workspace: EngineeringWorkspace }> {
    return this.request<{ workspace: EngineeringWorkspace }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}`);
  }

  createOrOpenEngineeringWorkspace(projectId: string, payload: { root_path?: string; name?: string }): Promise<{ workspace: EngineeringWorkspace }> {
    return this.request<{ workspace: EngineeringWorkspace }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  getEngineeringRepositoryStatus(projectId: string, workspaceId: string): Promise<{ repository: RepositoryStatus }> {
    return this.request<{ repository: RepositoryStatus }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/repository`);
  }

  getEngineeringFileTree(projectId: string, workspaceId: string, subpath = "", maxDepth = 4, maxEntries = 500): Promise<{ tree: FileTreeResponse }> {
    const qs = `?subpath=${encodeURIComponent(subpath)}&max_depth=${maxDepth}&max_entries=${maxEntries}`;
    return this.request<{ tree: FileTreeResponse }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/tree${qs}`);
  }

  readEngineeringFile(projectId: string, workspaceId: string, path: string, startLine = 1, endLine?: number): Promise<{ file: FileInspectResponse }> {
    let qs = `?path=${encodeURIComponent(path)}&start_line=${startLine}`;
    if (endLine !== undefined) {
      qs += `&end_line=${endLine}`;
    }
    return this.request<{ file: FileInspectResponse }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/file${qs}`);
  }

  searchEngineeringWorkspace(projectId: string, workspaceId: string, query: string, regex = false): Promise<{ search: WorkspaceSearchResponse }> {
    const qs = `?q=${encodeURIComponent(query)}&regex=${regex}`;
    return this.request<{ search: WorkspaceSearchResponse }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/search${qs}`);
  }

  getEngineeringWorkingTreeDiff(projectId: string, workspaceId: string): Promise<{ diff: EngineeringDiff }> {
    return this.request<{ diff: EngineeringDiff }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/diff`);
  }

  recordEngineeringDiff(projectId: string, workspaceId: string, payload: { raw_diff: string; state?: string; base_revision?: string }): Promise<{ diff: EngineeringDiff }> {
    return this.request<{ diff: EngineeringDiff }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/diffs`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  getEngineeringDiff(projectId: string, workspaceId: string, diffId: string): Promise<{ diff: EngineeringDiff }> {
    return this.request<{ diff: EngineeringDiff }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/diffs/${encodeURIComponent(diffId)}`);
  }

  listEngineeringDiffs(projectId: string, workspaceId: string): Promise<{ diffs: Array<{ diff_id: string; workspace_id: string; base_revision: string; state: string; total_additions: number; total_deletions: number; created_at: string; provenance: Record<string, any> }> }> {
    return this.request<{ diffs: Array<{ diff_id: string; workspace_id: string; base_revision: string; state: string; total_additions: number; total_deletions: number; created_at: string; provenance: Record<string, any> }> }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/diffs`);
  }

  applyEngineeringDiff(projectId: string, workspaceId: string, diffId: string, payload: { confirm_apply?: boolean; provenance?: Record<string, any> } = { confirm_apply: true }): Promise<{ apply: ApplyRecord }> {
    return this.request<{ apply: ApplyRecord }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/diffs/${encodeURIComponent(diffId)}/apply`, {
      method: "POST",
      body: JSON.stringify({ confirm_apply: true, ...payload }),
    });
  }

  rejectEngineeringDiff(projectId: string, workspaceId: string, diffId: string): Promise<{ diff: EngineeringDiff }> {
    return this.request<{ diff: EngineeringDiff }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/diffs/${encodeURIComponent(diffId)}/reject`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  getEngineeringApply(projectId: string, workspaceId: string, applyId: string): Promise<{ apply: ApplyRecord }> {
    return this.request<{ apply: ApplyRecord }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/applies/${encodeURIComponent(applyId)}`);
  }

  listEngineeringApplies(projectId: string, workspaceId: string): Promise<{ applies: ApplyRecord[] }> {
    return this.request<{ applies: ApplyRecord[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/applies`);
  }

  rollbackEngineeringApply(projectId: string, workspaceId: string, applyId: string, payload: { provenance?: Record<string, any> } = {}): Promise<{ rollback: ApplyRecord }> {
    return this.request<{ rollback: ApplyRecord }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/applies/${encodeURIComponent(applyId)}/rollback`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  detectInterruptedEngineeringApplies(projectId: string, workspaceId: string): Promise<{ interrupted: ApplyRecord[] }> {
    return this.request<{ interrupted: ApplyRecord[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/interrupted`);
  }

  // --- Permissioned Command, Test & Build Runner (AG-E1.3) ---

  discoverEngineeringRunners(projectId: string, workspaceId: string): Promise<{ runners: RunnerProfile[] }> {
    return this.request<{ runners: RunnerProfile[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/runners`);
  }

  createEngineeringExecutionPlan(
    projectId: string,
    workspaceId: string,
    payload: {
      kind: string;
      runner_profile: string;
      executable: string;
      arguments: string[];
      working_directory?: string;
      timeout_seconds?: number;
      environment_policy?: string;
      provenance?: Record<string, any>;
    }
  ): Promise<{ plan: ExecutionPlan }> {
    return this.request<{ plan: ExecutionPlan }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/plans`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  getEngineeringExecutionPlan(projectId: string, workspaceId: string, planId: string): Promise<{ plan: ExecutionPlan }> {
    return this.request<{ plan: ExecutionPlan }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/plans/${encodeURIComponent(planId)}`);
  }

  listEngineeringExecutionPlans(projectId: string, workspaceId: string): Promise<{ plans: ExecutionPlan[] }> {
    return this.request<{ plans: ExecutionPlan[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/plans`);
  }

  runEngineeringExecutionPlan(projectId: string, workspaceId: string, planId: string): Promise<{ execution: CommandResult }> {
    return this.request<{ execution: CommandResult }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/plans/${encodeURIComponent(planId)}/run`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  cancelEngineeringExecution(projectId: string, workspaceId: string, execId: string): Promise<{ cancelled: boolean; workspace_id: string; execution_id: string }> {
    return this.request<{ cancelled: boolean; workspace_id: string; execution_id: string }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/executions/${encodeURIComponent(execId)}/cancel`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  getEngineeringCommandResult(projectId: string, workspaceId: string, commandId: string): Promise<{ execution: CommandResult }> {
    return this.request<{ execution: CommandResult }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/executions/${encodeURIComponent(commandId)}`);
  }

  listEngineeringCommandResults(projectId: string, workspaceId: string, limit = 50): Promise<{ executions: CommandResult[] }> {
    return this.request<{ executions: CommandResult[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/executions?limit=${limit}`);
  }

  getEngineeringTestRun(projectId: string, workspaceId: string, testRunId: string): Promise<{ test_run: TestRun }> {
    return this.request<{ test_run: TestRun }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/test-runs/${encodeURIComponent(testRunId)}`);
  }

  listEngineeringTestRuns(projectId: string, workspaceId: string): Promise<{ test_runs: TestRun[] }> {
    return this.request<{ test_runs: TestRun[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/test-runs`);
  }

  getEngineeringBuildResult(projectId: string, workspaceId: string, buildId: string): Promise<{ build: BuildResult }> {
    return this.request<{ build: BuildResult }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/builds/${encodeURIComponent(buildId)}`);
  }

  listEngineeringBuildResults(projectId: string, workspaceId: string): Promise<{ builds: BuildResult[] }> {
    return this.request<{ builds: BuildResult[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/builds`);
  }

  // --- Git, Branch, Worktree & Safe Commit (AG-E1.4) ---

  getEngineeringGitState(projectId: string, workspaceId: string): Promise<{ git_state: GitWorkspaceState }> {
    return this.request<{ git_state: GitWorkspaceState }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/status`);
  }

  listEngineeringBranches(projectId: string, workspaceId: string): Promise<{ branches: GitBranch[] }> {
    return this.request<{ branches: GitBranch[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/branches`);
  }

  createEngineeringBranch(projectId: string, workspaceId: string, payload: { branch_name: string; base_ref?: string }): Promise<{ branch: GitBranch }> {
    return this.request<{ branch: GitBranch }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/branches`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  switchEngineeringBranch(projectId: string, workspaceId: string, payload: { branch_name: string }): Promise<{ branch: GitBranch }> {
    return this.request<{ branch: GitBranch }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/switch`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  listEngineeringWorktrees(projectId: string, workspaceId: string): Promise<{ worktrees: GitWorktree[] }> {
    return this.request<{ worktrees: GitWorktree[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/worktrees`);
  }

  createEngineeringWorktree(projectId: string, workspaceId: string, payload: { branch_name: string; base_ref?: string }): Promise<{ worktree: GitWorktree }> {
    return this.request<{ worktree: GitWorktree }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/worktrees`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  removeEngineeringWorktree(projectId: string, workspaceId: string, worktreeId: string): Promise<{ removed: boolean }> {
    return this.request<{ removed: boolean }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/worktrees/${encodeURIComponent(worktreeId)}`, {
      method: "DELETE",
    });
  }

  stageEngineeringPaths(projectId: string, workspaceId: string, paths: string[]): Promise<{ staged_paths: string[] }> {
    return this.request<{ staged_paths: string[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/stage`, {
      method: "POST",
      body: JSON.stringify({ paths }),
    });
  }

  unstageEngineeringPaths(projectId: string, workspaceId: string, paths: string[]): Promise<{ unstaged_paths: string[] }> {
    return this.request<{ unstaged_paths: string[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/unstage`, {
      method: "POST",
      body: JSON.stringify({ paths }),
    });
  }

  getEngineeringStagedDiff(projectId: string, workspaceId: string): Promise<{ diff: EngineeringDiff; staged_diff_digest: string }> {
    return this.request<{ diff: EngineeringDiff; staged_diff_digest: string }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/staged-diff`);
  }

  prepareEngineeringCommit(projectId: string, workspaceId: string, payload: { message: string; test_run_ids?: string[] }): Promise<{ preparation: CommitPreparation }> {
    return this.request<{ preparation: CommitPreparation }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/commit/prepare`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  getEngineeringCommitPreparation(projectId: string, workspaceId: string, prepId: string): Promise<{ preparation: CommitPreparation }> {
    return this.request<{ preparation: CommitPreparation }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/commit/preparations/${encodeURIComponent(prepId)}`);
  }

  executeEngineeringCommit(projectId: string, workspaceId: string, prepId: string): Promise<{ commit: GitCommitResult }> {
    return this.request<{ commit: GitCommitResult }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/commit/preparations/${encodeURIComponent(prepId)}/execute`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  getEngineeringCommitResult(projectId: string, workspaceId: string, commitId: string): Promise<{ commit: GitCommitResult }> {
    return this.request<{ commit: GitCommitResult }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/commits/${encodeURIComponent(commitId)}`);
  }

  listEngineeringCommitResults(projectId: string, workspaceId: string, limit = 50): Promise<{ commits: GitCommitResult[] }> {
    return this.request<{ commits: GitCommitResult[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/commits?limit=${limit}`);
  }

  detectInterruptedEngineeringGit(projectId: string, workspaceId: string): Promise<{ reconciled_commit_preparations: string[] }> {
    return this.request<{ reconciled_commit_preparations: string[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/git/interrupted`);
  }

  // --- Integrated Engineering Workflow Pipeline (AG-E1.5) ---

  createEngineeringPipeline(projectId: string, workspaceId: string, payload: { title?: string; diff_id?: string }): Promise<{ pipeline: EngineeringWorkflowPipeline }> {
    return this.request<{ pipeline: EngineeringWorkflowPipeline }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/workflow/pipelines`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  getEngineeringPipeline(projectId: string, workspaceId: string, pipelineId: string): Promise<{ pipeline: EngineeringWorkflowPipeline }> {
    return this.request<{ pipeline: EngineeringWorkflowPipeline }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/workflow/pipelines/${encodeURIComponent(pipelineId)}`);
  }

  listEngineeringPipelines(projectId: string, workspaceId: string, limit = 50): Promise<{ pipelines: EngineeringWorkflowPipeline[] }> {
    return this.request<{ pipelines: EngineeringWorkflowPipeline[] }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/workflow/pipelines?limit=${limit}`);
  }

  attachEngineeringPipelineDiff(projectId: string, workspaceId: string, pipelineId: string, payload: { diff?: string; diff_id?: string }): Promise<{ pipeline: EngineeringWorkflowPipeline }> {
    return this.request<{ pipeline: EngineeringWorkflowPipeline }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/workflow/pipelines/${encodeURIComponent(pipelineId)}/diff`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  applyEngineeringPipelineDiff(projectId: string, workspaceId: string, pipelineId: string): Promise<{ pipeline: EngineeringWorkflowPipeline }> {
    return this.request<{ pipeline: EngineeringWorkflowPipeline }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/workflow/pipelines/${encodeURIComponent(pipelineId)}/apply`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  recordEngineeringPipelineTest(projectId: string, workspaceId: string, pipelineId: string, payload: { test_run_id: string }): Promise<{ pipeline: EngineeringWorkflowPipeline }> {
    return this.request<{ pipeline: EngineeringWorkflowPipeline }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/workflow/pipelines/${encodeURIComponent(pipelineId)}/test`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  stageEngineeringPipelineChanges(projectId: string, workspaceId: string, pipelineId: string, payload: { paths?: string[] }): Promise<{ pipeline: EngineeringWorkflowPipeline }> {
    return this.request<{ pipeline: EngineeringWorkflowPipeline }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/workflow/pipelines/${encodeURIComponent(pipelineId)}/stage`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  commitEngineeringPipeline(projectId: string, workspaceId: string, pipelineId: string, payload: { message: string }): Promise<{ pipeline: EngineeringWorkflowPipeline }> {
    return this.request<{ pipeline: EngineeringWorkflowPipeline }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/workflow/pipelines/${encodeURIComponent(pipelineId)}/commit`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  rollbackEngineeringPipeline(projectId: string, workspaceId: string, pipelineId: string): Promise<{ pipeline: EngineeringWorkflowPipeline }> {
    return this.request<{ pipeline: EngineeringWorkflowPipeline }>(`/api/projects/${encodeURIComponent(projectId)}/engineering/workspaces/${encodeURIComponent(workspaceId)}/workflow/pipelines/${encodeURIComponent(pipelineId)}/rollback`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  // --- Tool Fabric & Approval Center (AG-T1.2) ---

  getToolCapabilities(projectId: string): Promise<{ capabilities: ToolCapability[] }> {
    return this.request<{ capabilities: ToolCapability[] }>(`/api/projects/${encodeURIComponent(projectId)}/tools/capabilities`);
  }

  getToolPolicy(projectId: string): Promise<{ policy: Record<string, string> }> {
    return this.request<{ policy: Record<string, string> }>(`/api/projects/${encodeURIComponent(projectId)}/tools/policy`);
  }

  updateToolPolicy(projectId: string, policy: Record<string, string>): Promise<{ policy: Record<string, string> }> {
    return this.request<{ policy: Record<string, string> }>(`/api/projects/${encodeURIComponent(projectId)}/tools/policy`, {
      method: "PUT",
      body: JSON.stringify({ policy }),
    });
  }

  submitToolRequest(projectId: string, payload: {
    tool_id: string;
    operation: string;
    arguments: Record<string, any>;
    resource_scope?: string | null;
    actor?: string;
  }): Promise<{ decision: any; execution: ToolExecutionRecord; request: ToolRequest }> {
    return this.request<{ decision: any; execution: ToolExecutionRecord; request: ToolRequest }>(`/api/projects/${encodeURIComponent(projectId)}/tools/requests`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  getPendingToolApprovals(projectId: string): Promise<{ pending_approvals: Array<ToolExecutionRecord & { normalized_arguments: Record<string, any>; actor: string; requested_at: string }> }> {
    return this.request<{ pending_approvals: Array<ToolExecutionRecord & { normalized_arguments: Record<string, any>; actor: string; requested_at: string }> }>(`/api/projects/${encodeURIComponent(projectId)}/tools/approvals/pending`);
  }

  approveToolRequest(projectId: string, executionId: string, payload: { actor?: string; note?: string; source_surface?: string } = {}): Promise<{ execution: ToolExecutionRecord; approval: ToolApprovalRecord }> {
    return this.request<{ execution: ToolExecutionRecord; approval: ToolApprovalRecord }>(`/api/projects/${encodeURIComponent(projectId)}/tools/approvals/${encodeURIComponent(executionId)}/approve`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  denyToolRequest(projectId: string, executionId: string, payload: { actor?: string; reason?: string } = {}): Promise<ToolExecutionRecord> {
    return this.request<ToolExecutionRecord>(`/api/projects/${encodeURIComponent(projectId)}/tools/approvals/${encodeURIComponent(executionId)}/deny`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  executeToolRequest(projectId: string, executionId: string): Promise<ToolExecutionRecord> {
    return this.request<ToolExecutionRecord>(`/api/projects/${encodeURIComponent(projectId)}/tools/executions/${encodeURIComponent(executionId)}/execute`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  getToolExecution(projectId: string, executionId: string): Promise<{ execution: ToolExecutionRecord }> {
    return this.request<{ execution: ToolExecutionRecord }>(`/api/projects/${encodeURIComponent(projectId)}/tools/executions/${encodeURIComponent(executionId)}`);
  }

  listToolExecutions(projectId: string, filters: { tool_id?: string; status?: string; limit?: number } = {}): Promise<{ executions: ToolExecutionRecord[] }> {
    const params = new URLSearchParams();
    if (filters.tool_id) params.set("tool_id", filters.tool_id);
    if (filters.status) params.set("status", filters.status);
    if (filters.limit) params.set("limit", String(filters.limit));
    const qs = params.toString() ? `?${params.toString()}` : "";
    return this.request<{ executions: ToolExecutionRecord[] }>(`/api/projects/${encodeURIComponent(projectId)}/tools/executions${qs}`);
  }

  getToolTelemetry(projectId: string): Promise<{ telemetry: ToolTelemetry }> {
    return this.request<{ telemetry: ToolTelemetry }>(`/api/projects/${encodeURIComponent(projectId)}/tools/telemetry`);
  }

  // --- Research Center (AG-R1.5B) ---

  listResearchSessions(projectId: string): Promise<{ sessions: ResearchSession[] }> {
    return this.request<{ sessions: ResearchSession[] }>(`/api/projects/${encodeURIComponent(projectId)}/research/sessions`);
  }

  createResearchSession(projectId: string, payload: { title: string; purpose?: string; provenance?: any }): Promise<ResearchSession> {
    return this.request<ResearchSession>(`/api/projects/${encodeURIComponent(projectId)}/research/sessions`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  getResearchSession(projectId: string, sessionId: string): Promise<{ session: ResearchSession }> {
    return this.request<{ session: ResearchSession }>(`/api/projects/${encodeURIComponent(projectId)}/research/sessions/${encodeURIComponent(sessionId)}`);
  }

  listResearchSources(projectId: string, sessionId?: string): Promise<{ sources: ResearchSource[] }> {
    const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return this.request<{ sources: ResearchSource[] }>(`/api/projects/${encodeURIComponent(projectId)}/research/sources${qs}`);
  }

  getResearchSource(projectId: string, sourceId: string): Promise<{ source: ResearchSource }> {
    return this.request<{ source: ResearchSource }>(`/api/projects/${encodeURIComponent(projectId)}/research/sources/${encodeURIComponent(sourceId)}`);
  }

  listResearchSnapshots(projectId: string, sourceId: string): Promise<{ snapshots: ResearchSnapshot[] }> {
    return this.request<{ snapshots: ResearchSnapshot[] }>(`/api/projects/${encodeURIComponent(projectId)}/research/sources/${encodeURIComponent(sourceId)}/snapshots`);
  }

  getResearchSnapshot(projectId: string, snapshotId: string): Promise<{ snapshot: ResearchSnapshot }> {
    return this.request<{ snapshot: ResearchSnapshot }>(`/api/projects/${encodeURIComponent(projectId)}/research/snapshots/${encodeURIComponent(snapshotId)}`);
  }

  getResearchSnapshotContentUrl(projectId: string, snapshotId: string): string {
    return `${API_BASE}/api/projects/${encodeURIComponent(projectId)}/research/snapshots/${encodeURIComponent(snapshotId)}/content`;
  }

  listResearchExtractions(projectId: string, snapshotId: string): Promise<{ extractions: ResearchExtraction[] }> {
    return this.request<{ extractions: ResearchExtraction[] }>(`/api/projects/${encodeURIComponent(projectId)}/research/snapshots/${encodeURIComponent(snapshotId)}/extractions`);
  }

  ingestResearchLocal(projectId: string, sessionId: string, payload: { path: string; title?: string }): Promise<{ source: ResearchSource; snapshot: ResearchSnapshot }> {
    return this.request<{ source: ResearchSource; snapshot: ResearchSnapshot }>(`/api/projects/${encodeURIComponent(projectId)}/research/sessions/${encodeURIComponent(sessionId)}/ingest-local`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  ingestResearchText(projectId: string, sessionId: string, payload: { title: string; content: string; content_type?: string }): Promise<{ source: ResearchSource; snapshot: ResearchSnapshot }> {
    return this.request<{ source: ResearchSource; snapshot: ResearchSnapshot }>(`/api/projects/${encodeURIComponent(projectId)}/research/sessions/${encodeURIComponent(sessionId)}/ingest-text`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  ingestResearchArtifact(projectId: string, sessionId: string, payload: { artifact_id: string; version?: number }): Promise<{ source: ResearchSource; snapshot: ResearchSnapshot }> {
    return this.request<{ source: ResearchSource; snapshot: ResearchSnapshot }>(`/api/projects/${encodeURIComponent(projectId)}/research/sessions/${encodeURIComponent(sessionId)}/ingest-artifact`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  extractResearchSnapshot(projectId: string, snapshotId: string): Promise<ResearchExtraction> {
    return this.request<ResearchExtraction>(`/api/projects/${encodeURIComponent(projectId)}/research/snapshots/${encodeURIComponent(snapshotId)}/extract`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  searchResearchLexical(projectId: string, query: string, limit: number = 10): Promise<{ results: Array<{ source_id: string; snapshot_id: string; locator: any; excerpt: string; score?: number }> }> {
    return this.request<{ results: Array<{ source_id: string; snapshot_id: string; locator: any; excerpt: string; score?: number }> }>(`/api/projects/${encodeURIComponent(projectId)}/research/search/lexical?q=${encodeURIComponent(query)}&limit=${limit}`);
  }

  searchResearchWeb(projectId: string, sessionId: string | null, payload: { query: string; max_results?: number; approved?: boolean }): Promise<{ results: Array<{ title: string; url: string; snippet: string; authors: string[]; year: number | null; source: string }>; providers: string[]; errors: Array<{ provider: string; error: string }>; metadata_artifact_id: string; bib_artifact_id?: string; evidence_scope: string }> {
    return this.request<{ results: Array<{ title: string; url: string; snippet: string; authors: string[]; year: number | null; source: string }>; providers: string[]; errors: Array<{ provider: string; error: string }>; metadata_artifact_id: string; bib_artifact_id?: string; evidence_scope: string }>(`/api/projects/${encodeURIComponent(projectId)}/research/search/web`, {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, ...payload }),
    });
  }

  listResearchSearchEvents(projectId: string, sessionId?: string): Promise<{ search_events: ResearchSearchEvent[] }> {
    const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return this.request<{ search_events: ResearchSearchEvent[] }>(`/api/projects/${encodeURIComponent(projectId)}/research/search/events${qs}`);
  }

  getResearchSearchEvent(projectId: string, eventId: string): Promise<{ search_event: ResearchSearchEvent }> {
    return this.request<{ search_event: ResearchSearchEvent }>(`/api/projects/${encodeURIComponent(projectId)}/research/search/events/${encodeURIComponent(eventId)}`);
  }

  fetchResearchAcquisition(projectId: string, sessionId: string | null, payload: { url: string; approved?: boolean }): Promise<{ acquisition: ResearchWebAcquisition; source?: ResearchSource; snapshot?: ResearchSnapshot }> {
    return this.request<{ acquisition: ResearchWebAcquisition; source?: ResearchSource; snapshot?: ResearchSnapshot }>(`/api/projects/${encodeURIComponent(projectId)}/research/acquisitions`, {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, ...payload }),
    });
  }

  listResearchAcquisitions(projectId: string, sessionId?: string): Promise<{ acquisitions: ResearchWebAcquisition[] }> {
    const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return this.request<{ acquisitions: ResearchWebAcquisition[] }>(`/api/projects/${encodeURIComponent(projectId)}/research/acquisitions${qs}`);
  }

  listResearchEvidence(projectId: string, filters: { session_id?: string; snapshot_id?: string } = {}): Promise<{ evidence: ResearchEvidence[] }> {
    const params = new URLSearchParams();
    if (filters.session_id) params.set("session_id", filters.session_id);
    if (filters.snapshot_id) params.set("snapshot_id", filters.snapshot_id);
    const qs = params.toString() ? `?${params.toString()}` : "";
    return this.request<{ evidence: ResearchEvidence[] }>(`/api/projects/${encodeURIComponent(projectId)}/research/evidence${qs}`);
  }

  createResearchEvidence(projectId: string, payload: { snapshot_id: string; excerpt: string; locator?: any }): Promise<ResearchEvidence> {
    return this.request<ResearchEvidence>(`/api/projects/${encodeURIComponent(projectId)}/research/evidence`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  getResearchEvidence(projectId: string, evidenceId: string): Promise<{ evidence: ResearchEvidence }> {
    return this.request<{ evidence: ResearchEvidence }>(`/api/projects/${encodeURIComponent(projectId)}/research/evidence/${encodeURIComponent(evidenceId)}`);
  }

  listResearchCitations(projectId: string, sessionId?: string): Promise<{ citations: ResearchCitation[] }> {
    const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return this.request<{ citations: ResearchCitation[] }>(`/api/projects/${encodeURIComponent(projectId)}/research/citations${qs}`);
  }

  createResearchCitation(projectId: string, payload: { evidence_id: string; display_label: string }): Promise<ResearchCitation> {
    return this.request<ResearchCitation>(`/api/projects/${encodeURIComponent(projectId)}/research/citations`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  listResearchQuestions(projectId: string, sessionId?: string): Promise<{ questions: ResearchQuestion[] }> {
    const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return this.request<{ questions: ResearchQuestion[] }>(`/api/projects/${encodeURIComponent(projectId)}/research/questions${qs}`);
  }

  createResearchQuestion(projectId: string, sessionId: string, payload: { text: string; parent_question_id?: string | null }): Promise<ResearchQuestion> {
    return this.request<ResearchQuestion>(`/api/projects/${encodeURIComponent(projectId)}/research/questions`, {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, ...payload }),
    });
  }

  listResearchClaims(projectId: string, sessionId?: string): Promise<{ claims: ResearchClaim[] }> {
    const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return this.request<{ claims: ResearchClaim[] }>(`/api/projects/${encodeURIComponent(projectId)}/research/claims${qs}`);
  }

  createResearchClaim(projectId: string, sessionId: string, payload: { claim_type: string; text: string; question_id?: string | null; scope_qualifier?: string | null }): Promise<ResearchClaim> {
    return this.request<ResearchClaim>(`/api/projects/${encodeURIComponent(projectId)}/research/claims`, {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, ...payload }),
    });
  }

  linkResearchClaimEvidence(projectId: string, claimId: string, payload: { evidence_id: string; relation: string; assessor?: string; notes?: string }): Promise<ResearchClaimEvidenceLink> {
    return this.request<ResearchClaimEvidenceLink>(`/api/projects/${encodeURIComponent(projectId)}/research/claims/${encodeURIComponent(claimId)}/links`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  getResearchClaimCoverage(projectId: string, claimId: string): Promise<{ coverage: ResearchCoverage }> {
    return this.request<{ coverage: ResearchCoverage }>(`/api/projects/${encodeURIComponent(projectId)}/research/claims/${encodeURIComponent(claimId)}/coverage`);
  }

  createResearchSynthesisPlan(projectId: string, sessionId: string, payload: { title: string; goal: string; question_ids?: string[]; claim_ids?: string[] }): Promise<ResearchSynthesisPlan> {
    return this.request<ResearchSynthesisPlan>(`/api/projects/${encodeURIComponent(projectId)}/research/synthesis/plans`, {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, ...payload }),
    });
  }

  generateResearchSynthesis(projectId: string, sessionId: string, payload: { title?: string; question_id?: string; claim_ids?: string[] }): Promise<ResearchSynthesisVersion> {
    return this.request<ResearchSynthesisVersion>(`/api/projects/${encodeURIComponent(projectId)}/research/synthesis/generate`, {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, ...payload }),
    });
  }

  listResearchSynthesisVersions(projectId: string, sessionId?: string): Promise<{ versions: ResearchSynthesisVersion[] }> {
    const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return this.request<{ versions: ResearchSynthesisVersion[] }>(`/api/projects/${encodeURIComponent(projectId)}/research/synthesis/versions${qs}`);
  }

  getResearchSynthesisVersion(projectId: string, versionId: string): Promise<{ version: ResearchSynthesisVersion }> {
    return this.request<{ version: ResearchSynthesisVersion }>(`/api/projects/${encodeURIComponent(projectId)}/research/synthesis/versions/${encodeURIComponent(versionId)}`);
  }

  // v0.6.0 M2 cost/budget backend surface (AUTOCLAW-AMCA-V0.6.0-M2-COST-BUDGET-CORE-01).
  costState(): Promise<CostStateWire> {
    return this.request<CostStateWire>("/api/cost/state");
  }

  costGetBudget(): Promise<{ policy: BudgetPolicyWire | null }> {
    return this.request<{ policy: BudgetPolicyWire | null }>("/api/cost/budget");
  }

  costSetBudget(draft: {
    run_budget_usd: number | null;
    project_budget_usd: number | null;
    warning_threshold_usd: number | null;
    strict_monetary: boolean;
  }): Promise<{ policy: BudgetPolicyWire | null }> {
    return this.request<{ policy: BudgetPolicyWire | null }>("/api/cost/budget", {
      method: "POST",
      body: JSON.stringify(draft),
    });
  }

  // v1.0.0 model integration: studio workflow methods
  studioWorkflows(projectId: string): Promise<{ project_id: string; workflows: StudioWorkflowSummary[] }> {
    return this.request(`/api/studio/workflows?project_id=${encodeURIComponent(projectId)}`);
  }

  studioWorkflow(workflowId: string, projectId: string): Promise<{ workflow: StudioWorkflowDetail }> {
    return this.request(`/api/studio/workflows/${encodeURIComponent(workflowId)}?project_id=${encodeURIComponent(projectId)}`);
  }

  saveStudioWorkflow(body: {
    workflow_id?: string;
    id?: string;
    project_id: string;
    name: string;
    goal: string;
    automation_type?: string;
    schedule?: string;
    enabled?: boolean;
    metadata?: Record<string, unknown>;
    condition?: Record<string, unknown> | null;
  }): Promise<{ workflow: StudioWorkflowSummary }> {
    return this.request("/api/studio/workflows", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  duplicateStudioWorkflow(workflowId: string, projectId: string): Promise<{ workflow: StudioWorkflowSummary }> {
    return this.request(`/api/studio/workflows/${encodeURIComponent(workflowId)}/duplicate`, {
      method: "POST",
      body: JSON.stringify({ project_id: projectId }),
    });
  }

  runStudioWorkflow(
    workflowId: string,
    projectId: string,
    inputs?: Record<string, unknown>,
  ): Promise<{ workflow_id: string; run: RunJob }> {
    return this.request(`/api/studio/workflows/${encodeURIComponent(workflowId)}/run`, {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, inputs }),
    });
  }

  deleteStudioWorkflow(workflowId: string, projectId: string): Promise<{ deleted: string; success: boolean }> {
    return this.request(`/api/studio/workflows/${encodeURIComponent(workflowId)}?project_id=${encodeURIComponent(projectId)}`, {
      method: "DELETE",
    });
  }

  // Round 22 facilities panel: account-level user memories.
  userMemories(params?: { memory_type?: string; pinned_only?: boolean; limit?: number }): Promise<{ memories: UserMemoryRecord[] }> {
    const query = new URLSearchParams();
    if (params?.memory_type) query.set("memory_type", params.memory_type);
    if (params?.pinned_only) query.set("pinned_only", "1");
    if (params?.limit) query.set("limit", String(params.limit));
    const suffix = query.size ? `?${query}` : "";
    return this.request(`/api/user/memory${suffix}`);
  }

  userMemory(memoryId: string): Promise<{ memory: UserMemoryRecord }> {
    return this.request(`/api/user/memory/${encodeURIComponent(memoryId)}`);
  }

  userMemoryContext(queryText: string, budget = 2000): Promise<{ memories: UserMemoryRecord[]; selected_count: number }> {
    const query = new URLSearchParams({ query: queryText, budget: String(budget) });
    return this.request(`/api/user/memory/context?${query}`);
  }

  saveUserMemory(body: { memory_id?: string; title: string; content: string; memory_type?: string; source_ref?: string; pinned?: boolean }): Promise<{ memory: UserMemoryRecord }> {
    return this.request("/api/user/memory", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  toggleUserMemoryPin(memoryId: string, pinned?: boolean): Promise<{ memory: UserMemoryRecord }> {
    return this.request(`/api/user/memory/${encodeURIComponent(memoryId)}/pin`, {
      method: "POST",
      body: JSON.stringify({ pinned }),
    });
  }

  deleteUserMemory(memoryId: string): Promise<{ deleted: string; success: boolean }> {
    return this.request(`/api/user/memory/${encodeURIComponent(memoryId)}`, {
      method: "DELETE",
    });
  }

  // Round 22 facilities panel: reusable slash commands (composer-insert invoke).
  studioCommands(projectId?: string): Promise<{ commands: StudioCommandRecord[] }> {
    const suffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
    return this.request(`/api/studio/commands${suffix}`);
  }

  studioCommand(commandId: string): Promise<{ command: StudioCommandRecord }> {
    return this.request(`/api/studio/commands/${encodeURIComponent(commandId)}`);
  }

  saveStudioCommand(body: { command_id?: string; name: string; description?: string; instructions: string; scope?: string; project_id?: string; enabled?: boolean }): Promise<{ command: StudioCommandRecord }> {
    return this.request("/api/studio/commands", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  setStudioCommandEnabled(commandId: string, enabled: boolean): Promise<{ command: StudioCommandRecord }> {
    return this.request(`/api/studio/commands/${encodeURIComponent(commandId)}/enable`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
  }

  deleteStudioCommand(commandId: string): Promise<{ deleted: string; success: boolean }> {
    return this.request(`/api/studio/commands/${encodeURIComponent(commandId)}`, {
      method: "DELETE",
    });
  }

  // Round 22 facilities panel: local stdio MCP servers (list-only probe).
  mcpServers(): Promise<{ servers: McpServerRecord[] }> {
    return this.request("/api/mcp/servers");
  }

  mcpServer(serverId: string): Promise<{ server: McpServerRecord }> {
    return this.request(`/api/mcp/servers/${encodeURIComponent(serverId)}`);
  }

  saveMcpServer(body: { server_id?: string; name: string; command: string; args?: string[]; env?: Record<string, string>; transport?: string; enabled?: boolean }): Promise<{ server: McpServerRecord }> {
    return this.request("/api/mcp/servers", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  setMcpServerEnabled(serverId: string, enabled: boolean): Promise<{ server: McpServerRecord }> {
    return this.request(`/api/mcp/servers/${encodeURIComponent(serverId)}/enable`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
  }

  probeMcpServer(serverId: string): Promise<{ server: McpServerRecord }> {
    return this.request(`/api/mcp/servers/${encodeURIComponent(serverId)}/probe`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  deleteMcpServer(serverId: string): Promise<{ deleted: string; success: boolean }> {
    return this.request(`/api/mcp/servers/${encodeURIComponent(serverId)}`, {
      method: "DELETE",
    });
  }

  // Round 22 facilities panel: run-event hooks (runtime-driven firing).
  runHooks(): Promise<{ hooks: RunHookRecord[] }> {
    return this.request("/api/run-hooks");
  }

  runHook(hookId: string): Promise<{ hook: RunHookRecord }> {
    return this.request(`/api/run-hooks/${encodeURIComponent(hookId)}`);
  }

  runHookFirings(hookId?: string): Promise<{ firings: RunHookFiring[] }> {
    const suffix = hookId ? `?hook_id=${encodeURIComponent(hookId)}` : "";
    return this.request(`/api/run-hooks/firings${suffix}`);
  }

  saveRunHook(body: { hook_id?: string; name: string; event: string; action: string; match?: Record<string, unknown>; action_arg?: string; enabled?: boolean }): Promise<{ hook: RunHookRecord }> {
    return this.request("/api/run-hooks", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  setRunHookEnabled(hookId: string, enabled: boolean): Promise<{ hook: RunHookRecord }> {
    return this.request(`/api/run-hooks/${encodeURIComponent(hookId)}/enable`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
  }

  deleteRunHook(hookId: string): Promise<{ deleted: string; success: boolean }> {
    return this.request(`/api/run-hooks/${encodeURIComponent(hookId)}`, {
      method: "DELETE",
    });
  }

  // Round 22 facilities panel: server-side skills (composer-insert invoke).
  studioSkills(): Promise<{ skills: StudioSkillRecord[] }> {
    return this.request("/api/studio/skills");
  }

  studioSkill(skillId: string): Promise<{ skill: StudioSkillRecord }> {
    return this.request(`/api/studio/skills/${encodeURIComponent(skillId)}`);
  }

  saveStudioSkill(body: { skill_id?: string; name: string; instructions: string; enabled?: boolean }): Promise<{ skill: StudioSkillRecord }> {
    return this.request("/api/studio/skills", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  setStudioSkillEnabled(skillId: string, enabled: boolean): Promise<{ skill: StudioSkillRecord }> {
    return this.request(`/api/studio/skills/${encodeURIComponent(skillId)}/enable`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
  }

  deleteStudioSkill(skillId: string): Promise<{ deleted: string; success: boolean }> {
    return this.request(`/api/studio/skills/${encodeURIComponent(skillId)}`, {
      method: "DELETE",
    });
  }

}

export interface UserMemoryRecord {
  memory_id: string;
  memory_type: string;
  title: string;
  content: string;
  source_ref: string | null;
  pinned: boolean;
  confidence: number;
  classification: string;
  created_at: string;
  updated_at: string;
  estimated_tokens: number;
}

export interface StudioCommandRecord {
  command_id: string;
  name: string;
  description: string;
  instructions: string;
  scope: string;
  project_id: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface McpServerRecord {
  server_id: string;
  name: string;
  transport: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  enabled: boolean;
  last_tools: Array<{ name: string; description: string }> | null;
  last_probe_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface RunHookRecord {
  hook_id: string;
  name: string;
  event: string;
  match: Record<string, unknown>;
  action: string;
  action_arg: string;
  enabled: boolean;
  fire_count: number;
  last_fired_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RunHookFiring {
  firing_id: string;
  hook_id: string;
  event: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface StudioSkillRecord {
  skill_id: string;
  name: string;
  instructions: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}


export interface BudgetPolicyWire {
  run_budget_usd: number | null;
  project_budget_usd: number | null;
  warning_threshold_usd: number | null;
  strict_monetary: boolean;
}

export interface CostStateWire {
  run_id: string | null;
  project_id: string | null;
  currency: string;
  total_known_cost_usd: number;
  total_unknown_calls: number;
  total_calls: number;
  total_local_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  per_model_cost_usd: Record<string, number>;
  project_aggregate_known_cost_usd: number | null;
  project_aggregate_unknown_calls: number | null;
  run_budget_usd: number | null;
  project_budget_usd: number | null;
  remaining_run_budget_usd: number | null;
  remaining_project_budget_usd: number | null;
  warning_threshold_usd: number | null;
  strict_monetary: boolean;
  known: boolean;
}

export const amca = new AMCAClient();
export { API_BASE, API_LABEL };
