export type RunStatus = "queued" | "running" | "cancelling" | "cancelled" | "succeeded" | "partial" | "failed";

export interface ProjectSummary {
  project_id: string;
  goal: string;
  status: string;
  version: number;
  updated_at: string;
  mode: string;
  collaboration_rounds: number;
  sync_mode: string;
  goal_progress: number;
  total_cost_usd: number;
}

export interface RunJob {
  run_id: string;
  project_id: string;
  goal: string;
  status: RunStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  log: string;
  provider: string;
  model: string;
  mode: string;
  privacy: string;
  conversation_id: string | null;
  collaboration_rounds?: number;
  collaboration_decision?: CollaborationDecisionDocument | null;
}

/** Live activity snapshot for one run (response-UX R5 feed). */
export interface RunActivity {
  run_id: string;
  status: string;
  model: string;
  rounds_completed: number;
  elapsed?: string;
  lines: string[];
}

export type CollaborationArchitecture =
  | "solo"
  | "joint"
  | "independent_discuss"
  | "parallel"
  | "adaptive_hybrid";

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  mode: CollaborationArchitecture;
  participant_ids: string[];
  workspace_project_id: string | null;
  project_id: string | null;
  active_run_id: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  last_message_at: string | null;
  message_count: number;
  last_message_preview: string;
}

export interface StudioProjectSummary {
  project_id: string;
  name: string;
  description: string;
  instructions: string;
  template: string;
  created_at: string;
  updated_at: string;
  archived: boolean;
  chat_count: number;
  /**
   * v1.2.0: the Project's folder, set once at creation. `null` only for
   * Projects created before v1.2.0, which are deliberately NOT migrated -
   * AMCA does not create directories on the user's disk unasked.
   */
  root_path: string | null;
}

/** v1.2.0: which drives Full Access currently covers, read from the backend. */
export interface FullAccessScope {
  project_id: string;
  profile: string;
  active: boolean;
  drives_detected: string[];
  drives_granted: string[];
  drives_missing: string[];
}

export interface StudioProjectDocument extends StudioProjectSummary {
  chats: ConversationSummary[];
  files: AssetRecord[];
}

export interface ConversationMessage {
  message_id: string;
  conversation_id: string;
  ordinal: number;
  role: "user" | "assistant" | "system";
  content: string;
  meta: string | null;
  run_id: string | null;
  project_id: string | null;
  created_at: string;
}

export interface ConversationDocument {
  conversation: ConversationSummary;
  messages: ConversationMessage[];
}

export interface LocalModel {
  provider: "ollama";
  model: string;
  digest: string;
  size_bytes: number;
  family: string | null;
  parameter_size: string | null;
  quantization_level: string | null;
  model_format: string | null;
  observed_at: string;
  fit: number;
  measurement: {
    wall_seconds: number;
    tokens_per_second: number | null;
    completed_at: string;
  } | null;
}

export interface LocalModelCatalog {
  provider: "ollama";
  refreshed: boolean;
  runtime_reachable?: boolean | null;
  runtime_error?: string | null;
  models: LocalModel[];
}

export interface MediaRuntimeCapability {
  available: boolean;
  runtime: string | null;
  model?: string | null;
  scope: string;
}

export interface MediaRuntimeStatus {
  ocr: MediaRuntimeCapability;
  transcription: MediaRuntimeCapability;
  network_used: false;
}

export interface CredentialStatus {
  persistent_store: string;
  write_available_in_ui: boolean;
  configured: Record<string, boolean>;
  credentials: CredentialModelStatus[];
}

export interface CredentialModelStatus {
  credential_id: string;
  profile_id: string;
  provider: string;
  model: string;
  label: string;
  endpoint: string;
  price_per_request_usd: number;
  pricing_basis: "token" | "request" | "free";
  input_cost_per_million: number;
  output_cost_per_million: number;
  team_selectable: boolean;
  is_builtin: boolean;
  configured: boolean;
}

export interface NewModelProfile {
  provider_kind: "openai" | "anthropic" | "kimi" | "openai_compatible" | "tabitoken";
  label: string;
  model: string;
  endpoint: string;
  pricing_basis: "token" | "request" | "free";
  input_cost_per_million: number;
  output_cost_per_million: number;
  price_per_request_usd: number;
  secret: string;
}

export interface ModelSetDecisionDoc {
  policy?: string;
  model_refs?: string[];
  rationale?: string;
  confidence?: string | number;
  expected_cost_usd?: number | null;
}

export interface ArchitectureDecisionDoc {
  mode?: string;
  sync_mode?: string;
  policy_rule?: string;
  reasons?: string[];
  triggers?: string[];
  requested_by?: string;
  expected_rounds?: number;
  candidate_mode?: string | null;
  degradation_reason?: string | null;
}

export interface TaskShapeDoc {
  complexity?: string;
  coupled?: boolean;
  decomposable?: boolean;
  verification_heavy?: boolean;
  novelty_required?: boolean;
  [key: string]: unknown;
}

export interface CollaborationDecisionDocument {
  contract_version?: number;
  model_set?: ModelSetDecisionDoc;
  architecture?: ArchitectureDecisionDoc;
  eligible_model_count?: number;
  task_shape?: TaskShapeDoc;
  decomposition?: { basis?: string; work_unit_count?: number; levels?: number };
  signals?: Record<string, unknown>;
}

export interface WorkspaceFileInfo {
  path: string;
  bytes: number;
  version: number;
  updated_at: number;
}

export interface WorkspaceOpenFile {
  path: string;
  content: string;
  version: number;
}

export interface WorkspaceSaveResult {
  path: string;
  version: number;
  created: boolean;
  diff: string;
}

export interface WorkspaceChangeEntry {
  path: string;
  version: number;
  saved_at: number;
  diff: string;
}

export interface CommandRunSnapshot {
  run_id: string;
  approval_id: string;
  argv: string[];
  status: "running" | "completed" | "failed" | "timeout" | "cancelled";
  output: string;
  truncated: boolean;
  exit_code: number | null;
  started_at: string;
  finished_at: string | null;
  error?: string;
}

/** Flat receipt returned by POST /api/local-tools/commands/cancel. */
export interface CommandCancelReceipt {
  run_id: string;
  status: string;
  process_killed: boolean;
}

// Round 24 ZCode-style panel workspaces: file explorer + terminal.
export interface ExplorerEntry {
  name: string;
  type: "dir" | "file";
  bytes: number;
  updated_at: number;
}

export interface ExplorerListing {
  root: string;
  path: string;
  entries: ExplorerEntry[];
  truncated: boolean;
}

export interface ExplorerFile {
  path: string;
  media_type: string;
  encoding: "text" | "base64";
  content: string;
  bytes: number;
  truncated: boolean;
}

export interface PanelPage {
  url: string;
  html: string;
}

export interface TerminalSessionInfo {
  session_id: string;
  project_id: string;
  cwd: string;
  alive: boolean;
}

export interface TerminalOutputPage {
  text: string;
  next_offset: number;
  cwd: string;
  alive: boolean;
}

export interface ArtifactVersionInfo {
  version: number;
  operation: string;
  created_by: string;
  created_at: string;
}

export interface ArtifactMeta {
  artifact_id: string;
  project_id: string;
  artifact_type: string;
  title: string;
  created_by: string;
  current_version: number;
  versions?: ArtifactVersionInfo[];
}

export interface ArtifactSourceDoc {
  version: number;
  content: string;
  provenance?: Record<string, unknown>;
}

export interface ArtifactExportResult {
  data_base64?: string;
  success?: boolean;
  status: string;
  output_path?: string;
  format?: string;
  byte_count?: number;
  error?: string | null;
  warnings?: string[];
  converter?: string;
  artifact_id?: string;
  version_number?: number;
  created_at?: string;
  reason?: string;
  target_format?: string;
  bytes?: number;
  version?: number;
}


export interface ChatApprovalPlan {
  status: "waiting_for_approval";
  project_id: string;
  mode: CollaborationArchitecture;
  participant_ids: string[];
  approval_ids: string[];
  approvals: ApprovalRequest[];
  max_api_cost_usd: number;
  network_started: false;
  conversation_id: string;
  selection_policy: "auto_intelligence" | "manual_model_set";
  selection_rationale?: string;
  selection_confidence?: string;
  agent_plan?: Record<string, unknown>;
}

export interface Dashboard {
  projects: ProjectSummary[];
  runs: RunJob[];
}

export interface GoalCriterion {
  id: string;
  description: string;
  required: boolean;
  satisfied: boolean;
  status?: string;
  evidence?: string[];
  evidence_ids?: string[];
}

export interface Disagreement {
  id: string;
  topic?: string;
  description: string;
  severity: string;
  raised_by: string;
  resolved: boolean;
  resolution?: string | null;
  evidence?: string[];
}

export interface CostState {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  api_cost_usd: number;
  tool_cost_usd: number;
  local_compute_cost_usd: number;
  latency_seconds: number;
  model_calls: number;
  api_model_calls: number;
  local_model_calls: number;
  per_model_cost_usd: Record<string, number>;
}

export interface CognitiveEvent {
  id: string;
  event_type: string;
  source: string;
  payload: Record<string, unknown>;
  state_version: number;
  severity: string;
  created_at: string;
  target?: string | null;
}

export interface AssetRecord {
  asset_id: string;
  record_id: string;
  project_id: string;
  version: number;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  stored_relative_path: string;
  source: string;
  created_at: string;
  modality: "document" | "spreadsheet" | "presentation" | "image" | "audio" | "video";
  extraction_status: "text_extracted" | "metadata_only";
  extraction_method: string;
  extraction_note: string;
  metadata: Array<{ name: string; value: string; locator: string }>;
  chunks: Array<{ index: number; locator: string; sha256: string }>;
}

export interface ApprovalRequest {
  id: string;
  project_id: string;
  action: string;
  scope: Record<string, unknown>;
  scope_sha256: string;
  risk: string;
  reason: string;
  requested_by: string;
  status: string;
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  decided_by: string | null;
  decision_reason: string | null;
  max_uses: number;
  uses: number;
  consumed_at: string | null;
}

export type LocalPermissionProfile = "restricted" | "standard" | "extended" | "full_computer";
export type LocalFileCapability =
  | "list" | "stat" | "search" | "read" | "mkdir" | "create"
  | "write" | "append" | "copy" | "move" | "trash" | "restore"
  | "propose_command" | "propose_application";

export interface LocalPermissionProfileState {
  project_id: string;
  profile: LocalPermissionProfile;
  workspace_root: string | null;
  capabilities: LocalFileCapability[];
  configured: boolean;
  updated_at?: string;
  updated_by?: string;
}

export interface LocalPathGrant {
  grant_id: string;
  project_id: string;
  requested_path: string;
  canonical_root: string;
  capabilities: LocalFileCapability[];
  created_at: string;
  expires_at: string | null;
  created_by: string;
  reason: string;
  revoked_at: string | null;
  revoked_by: string | null;
  revoke_reason: string | null;
  active: boolean;
}

export interface LocalToolEvent {
  event_id: string;
  project_id: string;
  grant_id: string | null;
  action: string;
  target: string | null;
  outcome: string;
  detail: Record<string, unknown>;
  created_at: string;
  actor: string;
}

export interface LocalToolSnapshot {
  profile: LocalPermissionProfileState;
  grants: LocalPathGrant[];
  events: LocalToolEvent[];
  /** v1.2.0: which drives Full Access currently covers. */
  full_access?: FullAccessScope;
  limits: {
    max_read_bytes: number;
    max_write_bytes: number;
    max_list_entries: number;
    max_search_results: number;
    max_search_depth: number;
    max_search_seconds: number;
  };
  command_execution: "inspection_only_exact_approval";
  application_control: "proposal_only";
}

export interface AutomationDefinition {
  id: string;
  name: string;
  project_id: string;
  goal: string;
  automation_type: "once" | "recurring" | "conditional" | "monitor" | "event_triggered";
  schedule: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  last_run_at: string | null;
  last_run_id: string | null;
  metadata: Record<string, unknown>;
  condition?: Record<string, unknown> | null;
  revision?: number;
  last_check_at?: string | null;
  latest_occurrence_status?: "claimed" | "running" | "succeeded" | "failed" | "skipped" | null;
  latest_error?: string | null;
  next_scheduled_check_at?: string | null;
}

export interface AutomationSchedulerStatus {
  running: boolean;
  worker_alive: boolean;
  started_at: string | null;
  poll_interval_seconds: number | null;
  last_tick_at: string | null;
  last_tick_error: string | null;
}

export interface AutomationConditionCheck {
  kind: string;
  version?: number;
  checked_at: string;
  result: "true" | "false" | "error";
  evidence?: Record<string, unknown>;
  error?: string;
}

export interface AutomationOccurrence {
  id: string;
  automation_id: string;
  scheduled_for: string;
  status: "claimed" | "running" | "succeeded" | "failed" | "skipped";
  claimed_at: string;
  run_id?: string | null;
  completed_at?: string | null;
  error?: string | null;
  check?: AutomationConditionCheck | null;
}

export interface ProjectState {
  project_id: string;
  goal: string;
  status: string;
  version: number;
  cognitive_revision: number;
  created_at: string;
  updated_at: string;
  current_focus: string;
  active_subproblem: string;
  current_collaboration_mode: string;
  current_sync_mode: string;
  complexity: string;
  privacy_mode: string;
  compute_policy: string;
  progress: number;
  accepted_facts: string[];
  assumptions: string[];
  hypotheses: string[];
  equations: string[];
  evidence: unknown[];
  experimental_results: unknown[];
  open_questions: string[];
  pending_ideas: string[];
  rejected_ideas: string[];
  participating_models: string[];
  active_disagreements: Disagreement[];
  artifacts: Record<string, unknown>;
  cost_state: CostState;
  goal_contract?: {
    id: string;
    goal: string;
    criteria: GoalCriterion[];
  } | null;
  collaboration_progress?: CollaborationProgress;
}

export interface AdaptiveTransitionItem {
  phase_index: number;
  from_mode: string;
  selected_mode: string;
  selected_sync_mode: string;
  policy_rule: string;
  observed_triggers: string[];
  phase_stop_reason?: string | null;
  state_version_before: number;
  state_version_after?: number | null;
  timestamp?: string;
  safety_override?: boolean;
}

export interface ParallelUnitProgress {
  id: string;
  focus: string;
  status: "completed" | "active" | "blocked" | "pending" | string;
  assignees: string[];
  dependencies: string[];
  conflicts?: string[];
}

export interface ParallelProgressSummary {
  total_units: number;
  completed_units: number;
  active_units: number;
  blocked_units: number;
  units: ParallelUnitProgress[];
  integration_status: "passed" | "blocked" | "pending" | "none" | string;
  integration_conflicts?: string[];
}

export interface CollaborationProgress {
  top_level_architecture: string;
  current_base_mode: string;
  current_sync_mode: string;
  phase_index?: number;
  max_phases?: number;
  adaptive_transitions: AdaptiveTransitionItem[];
  parallel_progress?: ParallelProgressSummary | null;
  collaboration_decision?: CollaborationDecisionDocument | null;
  live_organization?: Record<string, unknown> | null;
}

export interface HardwareProfile {
  operating_system: string;
  architecture: string;
  cpu: string;
  logical_cores: number;
  total_ram_bytes: number | null;
  available_ram_bytes: number | null;
  free_disk_bytes: number;
  gpus: Array<{
    name: string;
    vendor: string;
    total_vram_bytes: number | null;
  }>;
  accelerators: string[];
  recommended_local_tier: string;
  warnings: string[];
}

export interface BenchmarkTask {
  id: string;
  title?: string;
  domain?: string;
  prompt?: string;
  goal?: string;
  [key: string]: unknown;
}

export interface ModelHubProvider {
  id: string;
  provider_kind: string;
  name: string;
  endpoint: string;
  status: string;
  pricing_mode: string;
  model_ids: string[];
  /** The subset of model_ids the user published into the model selectors. */
  enabled_model_ids: string[];
  configured: boolean;
  created_at: string;
  updated_at: string;
  /** The operator's stored include/exclude decision. Absent on stale
   * documents means included, matching the backend DEFAULT 1. */
  enabled?: boolean;
  /** Operator-recorded per-model metadata: what the operator typed about
   * their own provider, never a measurement. Keyed by model id. */
  model_meta?: Record<string, { context_window: number | null; max_output_tokens: number | null }>;
}

export interface ModelTeam {
  id: string;
  name: string;
  description: string;
  model_refs: string[];
  quality_policy: string;
  default_budget: number | null;
  fallback_permissions: Record<string, boolean>;
  created_at: string;
  updated_at: string;
}

export interface LocalCompatibility {
  status: "green" | "red";
  explanation: string;
  required_ram_bytes: number;
  required_vram_bytes: number;
  installed_ram_bytes: number | null;
  installed_vram_bytes: number;
  expected_offload: string;
  storage_bytes: number;
  performance_class: string;
}

export type ModelHealthState =
  | "READY"
  | "AUTH_REQUIRED"
  | "MODEL_NOT_FOUND"
  | "PROVIDER_UNAVAILABLE"
  | "LOCAL_RUNTIME_OFFLINE"
  | "QUOTA_EXHAUSTED"
  | "UNVERIFIED";

export interface ModelHealthRecord {
  config_key: string;
  kind: string;
  ref: string;
  endpoint: string;
  model: string;
  state: ModelHealthState;
  detail: string;
  verified_at: string;
  evidence: string;
  runnable: boolean;
  http_status?: number | null;
}

export interface ModelHealthReport {
  schema: string;
  results: ModelHealthRecord[];
  runnable_count: number;
  any_runnable: boolean;
}

export interface ModelHubState {
  model_health?: Record<string, ModelHealthRecord>;
  amca_api: {
    status: string;
    credential: string | null;
    credit_balance_usd: number | null;
    subscription: string | null;
    managed_backbone: string;
    models: unknown[];
    commercial_status: string;
  };
  byok_providers: ModelHubProvider[];
  direct_models: CredentialStatus["credentials"];
  local_models: Omit<LocalModelCatalog, "models"> & {
    models: Array<LocalModel & { compatibility: LocalCompatibility }>;
    recommended: Array<{
      model: string;
      provider: "ollama";
      parameter_size: string;
      size_bytes: number;
      summary: string;
      installed: boolean;
      compatibility: LocalCompatibility;
      install_command: string;
    }>;
  };
  model_teams: ModelTeam[];
  pricing: Array<Record<string, unknown>>;
  maia: { status: string; normally_active: boolean; nano_agents: Array<Record<string, unknown>> };
  selection: {
    primary_options: ["auto_intelligence", "manual"];
    auto_objective: string;
    manual_sources: string[];
  };
}

export interface NewHubProvider {
  provider_kind: string;
  name: string;
  endpoint: string;
  pricing_mode: "auto" | "manual";
  secret: string;
  model_ids?: string[];
  /** Include/exclude the whole provider in chat model selection. */
  enabled?: boolean;
}

export interface AgentResourceEnvelope {
  max_logical_agents: number;
  max_concurrent_agents: number;
  max_manager_depth: number;
  max_nano_tasks: number;
  max_cloud_calls: number;
  max_frontier_calls: number;
  max_api_cost_usd: number;
  max_runtime_seconds: number;
}

export interface AgentFabricSettings {
  auto_agents: boolean;
  dynamic_agents: boolean;
  nano_agents: boolean;
  recursive_spawning: boolean;
  speculative_execution: boolean;
  scaling: "conservative" | "balanced" | "aggressive" | "custom";
  activity_detail: "compact" | "detailed";
  envelope: AgentResourceEnvelope;
}

export interface AgentTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  agent_class: string;
  origin: "built_in" | "custom" | "installed";
  enabled: boolean;
  preferred_model_policy: string;
  allowed_tools: string[];
  permissions: string[];
  instructions: string;
  output_schema: string;
  success_criteria: string;
  can_spawn: boolean;
  max_children: number;
}

export interface AgentFabricState {
  agents: AgentTemplate[];
  counts: {
    core: number;
    built_in_specialists: number;
    custom: number;
    total_available: number;
    active: number;
  };
  categories: Record<string, number>;
  settings: AgentFabricSettings;
  hierarchy: string[];
  status: string;
  default_policy: string;
  active_organization: null | Record<string, unknown>;
}

export interface NewCustomAgent {
  name: string;
  description: string;
  instructions: string;
  category: string;
  preferred_model_policy?: string;
  allowed_tools?: string[];
  permissions?: string[];
  output_schema?: string;
  success_criteria?: string;
  can_spawn?: boolean;
  max_children?: number;
  enabled?: boolean;
}

export type ReviewItemStatus = "unread" | "read" | "dismissed";
export type ReviewItemSeverity = "info" | "warning" | "error" | "critical" | "attention";

export interface ReviewItem {
  id: string;
  source_key: string;
  source_type: string;
  project_id: string;
  title: string;
  summary: string;
  severity: ReviewItemSeverity | string;
  status: ReviewItemStatus;
  run_id: string | null;
  occurrence_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReviewQueueStatus {
  unread_count: number;
}

export interface NotificationPreferences {
  enabled: boolean;
  notify_attention: boolean;
  notify_info: boolean;
  enabled_at: string | null;
  updated_at: string;
}

export interface PendingNotification {
  review_item_id: string;
  title: string;
  body: string;
  severity: string;
  project_id: string;
  created_at: string;
}

// --- Artifact Center Types (AG-W2.5) ---

export interface ArtifactItem {
  artifact_id: string;
  project_id: string;
  artifact_type: "document" | "spreadsheet" | "presentation" | "text" | string;
  title: string;
  current_version_number: number;
  status: string;
  created_at: string;
  updated_at: string;
  provenance: Record<string, unknown>;
}

export interface ArtifactVersionItem {
  version_id: string;
  artifact_id: string;
  version_number: number;
  content: Record<string, unknown>;
  content_digest: string;
  created_at: string;
  created_by: string;
  provenance: Record<string, unknown>;
}


export interface ArtifactExportRecord {
  export_id: string;
  artifact_id: string;
  version_number: number;
  format: string;
  status: string;
  output_path: string;
  byte_count: number;
  warnings: string[];
  converter: string;
  error: string | null;
  created_at: string;
}

export interface ExportCapabilityFormat {
  status: "supported" | "unavailable" | "available_with_warning";
  warnings: string[];
  converter: string;
}

export interface ArtifactExportCapabilities {
  artifact_id: string;
  artifact_type: string;
  formats: Record<string, ExportCapabilityFormat>;
}

/** One rendition this build can or cannot produce, with the reason when it cannot. */
export interface ArtifactRenderCapability {
  artifact_type: string;
  target_format: string;
  converter: string;
  available: boolean;
  reason: string;
}

export interface ArtifactPreview {
  artifact_id: string;
  project_id: string;
  artifact_type: string;
  title: string;
  version_number: number;
  created_at: string;
  created_by: string;
  provenance: Record<string, unknown>;
  content_digest: string;
  blocks_summary?: Array<{ type: string; text: string; level?: number; item_count?: number; row_count?: number }>;
  total_blocks?: number;
  snippet?: string;
  sheets?: Array<{ sheet_id: string; name: string; headers: string[]; row_count: number; sample_rows: unknown[][] }>;
  sheet_count?: number;
  slides?: Array<{ slide_id: string; layout: string; title: string; element_count: number; element_types: string[]; has_notes: boolean }>;
  total_slides?: number;
}

// --- Engineering Workspace Foundation (AG-E1.1) ---

export type RepositoryKind = "git" | "filesystem";
export type DiffState = "proposed" | "applied" | "committed" | "rejected";
export type FileChangeKind = "added" | "modified" | "deleted" | "renamed" | "unmodified";

export interface FileHunk {
  old_start: number;
  old_lines: number;
  new_start: number;
  new_lines: number;
  lines: string[];
  header: string;
}

export interface FileDiff {
  path: string;
  change_kind: FileChangeKind;
  additions: number;
  deletions: number;
  hunks: FileHunk[];
  old_path?: string;
  binary: boolean;
}

export interface EngineeringDiff {
  diff_id: string;
  workspace_id: string;
  base_revision: string;
  state: DiffState;
  files: FileDiff[];
  total_additions: number;
  total_deletions: number;
  created_at: string;
  provenance: Record<string, unknown>;
}

export interface FileTreeEntry {
  path: string;
  name: string;
  is_dir: boolean;
  size: number;
  modified_at: string;
  file_type: string;
  sensitive: boolean;
  restricted: boolean;
  children?: FileTreeEntry[];
}

export interface FileTreeResponse {
  workspace_id: string;
  root_path: string;
  subpath: string;
  max_depth: number;
  entries_count: number;
  truncated: boolean;
  tree: FileTreeEntry[];
}

export interface FileInspectResponse {
  workspace_id: string;
  path: string;
  total_lines: number;
  start_line: number;
  end_line: number;
  lines: string[];
  content: string;
  size_bytes: number;
  truncated: boolean;
  sensitive: boolean;
  encoding: string;
}

export interface SearchMatch {
  path: string;
  line_number: number;
  snippet: string;
}

export interface WorkspaceSearchResponse {
  workspace_id: string;
  query: string;
  regex: boolean;
  files_scanned: number;
  total_matches: number;
  truncated: boolean;
  matches: SearchMatch[];
}

export interface RepositoryStatus {
  repository_kind: RepositoryKind;
  root_path: string;
  current_branch?: string;
  head_commit?: string;
  is_clean: boolean;
  tracked_count: number;
  untracked_count: number;
  modified_count: number;
  staged_count: number;
  status_summary: Record<string, unknown>;
}

export interface EngineeringWorkspace {
  workspace_id: string;
  project_id: string;
  name: string;
  root_path: string;
  repository_kind: RepositoryKind;
  current_branch?: string;
  head_commit?: string;
  created_at: string;
  updated_at: string;
  status: string;
  provenance: Record<string, unknown>;
}

// --- Safe Mutation & Patch Application (AG-E1.2) ---

export type ApplyStatus =
  | "prepared"
  | "applying"
  | "applied"
  | "rolled_back"
  | "conflict"
  | "failed"
  | "failed_partial"
  | "recovery_required";

export interface FileAffectedRecord {
  path: string;
  change_kind: string;
  base_sha256?: string | null;
  applied_sha256?: string | null;
  backup_path?: string | null;
  status: string;
}

export interface ApplyRecord {
  apply_id: string;
  diff_id: string;
  workspace_id: string;
  status: ApplyStatus;
  files_affected: FileAffectedRecord[];
  error_message?: string | null;
  created_at: string;
  applied_at?: string | null;
  rolled_back_at?: string | null;
  provenance: Record<string, unknown>;
}



// --- Permissioned Command, Test & Build Runner (AG-E1.3) ---

export type ExecutionPlanKind = "TEST" | "BUILD" | "CHECK" | "LINT";

export type ExecutionStatus =
  | "PREPARED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "TIMED_OUT"
  | "CANCELLED"
  | "START_FAILED"
  | "INTERRUPTED";

export type TestStatus = "passed" | "failed" | "error" | "skipped";

export interface RunnerProfile {
  runner_id: string;
  name: string;
  kind: ExecutionPlanKind;
  executable: string;
  default_args: string[];
  available: boolean;
  unavailability_reason?: string | null;
  version?: string | null;
  description: string;
}

export interface ExecutionPlan {
  execution_plan_id: string;
  workspace_id: string;
  kind: ExecutionPlanKind;
  runner_profile: string;
  executable: string;
  arguments: string[];
  working_directory: string;
  timeout_seconds: number;
  environment_policy: string;
  created_at: string;
  status: ExecutionStatus;
  provenance: Record<string, unknown>;
}

export interface CommandResult {
  command_id: string;
  execution_plan_id: string;
  workspace_id: string;
  kind: string;
  executable: string;
  arguments: string[];
  cwd: string;
  started_at: string;
  completed_at: string;
  duration_ms: number;
  status: ExecutionStatus;
  exit_code?: number | null;
  stdout: string;
  stderr: string;
  stdout_reference?: string | null;
  stderr_reference?: string | null;
  stdout_truncated: boolean;
  stderr_truncated: boolean;
  timeout_triggered: boolean;
  cancellation_triggered: boolean;
  provenance: Record<string, unknown>;
}

export interface TestRun {
  test_run_id: string;
  command_id: string;
  execution_plan_id: string;
  workspace_id: string;
  framework: string;
  command: string[];
  started_at: string;
  completed_at: string;
  status: TestStatus;
  passed?: number | null;
  failed?: number | null;
  errors?: number | null;
  skipped?: number | null;
  total?: number | null;
  duration_ms: number;
  output_reference?: string | null;
  provenance: Record<string, unknown>;
}

export interface BuildResult {
  build_id: string;
  command_id: string;
  execution_plan_id: string;
  workspace_id: string;
  builder: string;
  command: string[];
  started_at: string;
  completed_at: string;
  status: string;
  exit_code?: number | null;
  duration_ms: number;
  artifacts_produced: string[];
  output_reference?: string | null;
  provenance: Record<string, unknown>;
}


// --- Git, Branch, Worktree & Safe Commit Types (AG-E1.4) ---

export type WorktreeStatus = "PLANNED" | "CREATING" | "READY" | "FAILED" | "REMOVED";
export type CommitPreparationStatus = "PREPARED" | "COMMITTED" | "ABORTED" | "CONFLICT" | "INTERRUPTED_REQUIRES_REFRESH";

export interface GitWorkspaceState {
  workspace_id: string;
  repository_root: string;
  current_branch: string | null;
  head_commit: string | null;
  upstream: string | null;
  worktree_kind: "primary" | "amca_worktree" | "user_worktree" | "non_git";
  dirty: boolean;
  staged_count: number;
  unstaged_count: number;
  untracked_count: number;
  ahead?: number;
  behind?: number;
  updated_at: string;
}

export interface GitBranch {
  name: string;
  head_commit: string;
  is_current: boolean;
  upstream?: string | null;
  ahead?: number;
  behind?: number;
}

export interface GitWorktree {
  worktree_id: string;
  workspace_id: string;
  path: string;
  branch: string;
  base_commit: string;
  head_commit: string;
  status: WorktreeStatus;
  amca_managed: boolean;
  created_at: string;
  provenance?: Record<string, any>;
}

export interface StagedFile {
  path: string;
  status_code: string;
  additions: number;
  deletions: number;
}

export interface CommitPreparation {
  preparation_id: string;
  workspace_id: string;
  branch: string;
  prepared_head: string;
  staged_diff_digest: string;
  staged_files: Array<{ path: string; change_kind: string; additions: number; deletions: number }>;
  message: string;
  status: CommitPreparationStatus;
  test_run_ids: string[];
  test_evidence_stale: boolean;
  created_at: string;
  provenance?: Record<string, any>;
}

export interface GitCommitResult {
  commit_id: string;
  preparation_id: string;
  workspace_id: string;
  branch: string;
  parent_head: string;
  new_commit_sha: string;
  staged_diff_digest: string;
  files_committed: string[];
  message: string;
  created_at: string;
  provenance?: Record<string, any>;
}


// --- Integrated Engineering Workflow Pipeline (AG-E1.5) ---

export type WorkflowStage =
  | "inspect"
  | "propose"
  | "review"
  | "applied"
  | "testing"
  | "evidence"
  | "staged"
  | "review_staged"
  | "committed"
  | "rolled_back"
  | "failed";

export type WorkflowPipelineStatus = "in_progress" | "completed" | "rolled_back" | "aborted";

export interface EngineeringWorkflowPipeline {
  pipeline_id: string;
  workspace_id: string;
  project_id: string;
  title: string;
  current_stage: WorkflowStage;
  status: WorkflowPipelineStatus;
  diff_id?: string | null;
  apply_id?: string | null;
  test_run_ids: string[];
  test_passed: boolean;
  staged_paths: string[];
  staged_diff_digest?: string | null;
  preparation_id?: string | null;
  commit_id?: string | null;
  commit_sha?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  provenance: Record<string, any>;
}


// --- Tool Fabric & Approval Center (AG-T1.2) ---

export type RiskClass = "read_only" | "compute_only" | "reversible_write" | "irreversible_or_external" | "sensitive";
export type SideEffectClass = "none" | "local_read" | "local_write" | "process_execution" | "network" | "external_side_effect";
export type ToolDecision = "allow" | "deny" | "require_approval";
export type ToolExecutionStatus = "requested" | "authorized" | "denied" | "running" | "succeeded" | "failed" | "cancelled" | "timed_out" | "interrupted_after_start";

export interface ToolCapability {
  tool_id: string;
  version: string;
  category: string;
  description: string;
  risk_class: RiskClass;
  side_effect_class: SideEffectClass;
  supported_operations: string[];
  resource_scopes: string[];
  approval_policy: ToolDecision;
  project_policy: ToolDecision;
  execution_backend?: string | null;
}

export interface ToolRequest {
  request_id: string;
  organization_id: string;
  project_id: string;
  tool_id: string;
  operation: string;
  normalized_arguments: Record<string, any>;
  requested_resource_scope?: string | null;
  actor?: string | null;
  created_at: string;
}

export interface ToolExecutionRecord {
  execution_id: string;
  request_id: string;
  tool_id: string;
  operation: string;
  decision: ToolDecision;
  status: ToolExecutionStatus;
  approval_reference?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  resource_scope?: string | null;
  result_summary?: string | null;
  side_effect_summary?: string | null;
  outcome_uncertain: number;
  duration_ms: number;
}

export interface ToolApprovalRecord {
  approval_id: string;
  request_id: string;
  project_id: string;
  execution_id: string;
  approver_actor: string;
  decision: ToolDecision;
  request_digest: string;
  created_at: string;
  expires_at?: string | null;
  consumed: number;
  note?: string | null;
}

export interface ToolTelemetry {
  requests_count: number;
  allowed_count: number;
  denied_count: number;
  approval_required_count: number;
  approved_count: number;
  executed_count: number;
  succeeded_count: number;
  failed_count: number;
  timed_out_count: number;
  interrupted_count: number;
}


// --- Research Center (AG-R1.5B) ---

export interface ResearchSession {
  session_id: string;
  project_id: string;
  title: string;
  purpose: string;
  status: string;
  provenance: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface ResearchSource {
  source_id: string;
  project_id: string;
  session_id: string;
  source_type: "local_file" | "user_text" | "artifact" | "web_url";
  title: string;
  location: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ResearchSnapshot {
  snapshot_id: string;
  project_id: string;
  source_id: string;
  session_id: string;
  byte_count: number;
  sha256: string;
  content_type: string;
  status: string;
  created_at: string;
}

export interface ResearchSegment {
  locator: Record<string, any>;
  text: string;
}

export interface ResearchExtraction {
  extraction_id: string;
  project_id: string;
  snapshot_id: string;
  status: "ok" | "failed" | "unsupported_format" | "empty";
  error?: string | null;
  segment_count: number;
  segments: ResearchSegment[];
  created_at: string;
}

export interface ResearchEvidence {
  evidence_id: string;
  project_id: string;
  snapshot_id: string;
  locator: Record<string, any>;
  excerpt: string;
  created_at: string;
}

export interface ResearchCitation {
  citation_id: string;
  project_id: string;
  evidence_id: string;
  display_label: string;
  created_at: string;
}

export interface ResearchQuestion {
  question_id: string;
  project_id: string;
  session_id: string;
  question: string;
  parent_question_id?: string | null;
  status: string;
  created_at: string;
}

export type ClaimType = "factual" | "inferential" | "procedural";
export type ClaimRelation = "supports" | "contradicts" | "context" | "qualifies";
export type CoverageState = "no_evidence" | "support_only" | "contradiction_only" | "mixed" | "context_only";

export interface ResearchClaim {
  claim_id: string;
  project_id: string;
  session_id: string;
  claim_type: ClaimType;
  claim_text: string;
  question_id?: string | null;
  scope_qualifier?: string | null;
  status: string;
  created_at: string;
}

export interface ResearchClaimEvidenceLink {
  link_id: string;
  project_id: string;
  claim_id: string;
  evidence_id: string;
  relation: ClaimRelation;
  assessor: string;
  rationale?: string | null;
  created_at: string;
}

export interface ResearchCoverage {
  claim_id: string;
  coverage: CoverageState;
  supporting_count: number;
  contradicting_count: number;
  context_count: number;
  qualifying_count: number;
  distinct_source_count: number;
  distinct_chunk_count: number;
}

export interface ResearchSynthesisPlan {
  plan_id: string;
  project_id: string;
  session_id: string;
  title: string;
  goal: string;
  status: string;
  question_ids: string[];
  claim_ids: string[];
  created_at: string;
}

export type GroundingStatus = "grounded" | "incomplete" | "invalid_citation" | "unsupported_claims" | "contested" | "draft" | "stale";

export interface ResearchSynthesisVersion {
  version_id: string;
  project_id: string;
  session_id: string;
  title: string;
  status: string;
  grounding_status: GroundingStatus;
  summary_text: string;
  structured_sections: Array<{ heading: string; body: string; citation_ids?: string[] }>;
  citation_map: Record<string, string>;
  claim_ids: string[];
  created_at: string;
}

export interface ResearchSearchEvent {
  event_id: string;
  project_id: string;
  session_id: string;
  provider: string;
  query: string;
  result_count: number;
  results: Array<{ title: string; url: string; snippet?: string; score?: number }>;
  created_at: string;
}

export interface ResearchWebAcquisition {
  acquisition_id: string;
  project_id: string;
  session_id: string;
  requested_url: string;
  final_url: string;
  status: string;
  http_status: number;
  content_type: string;
  byte_count: number;
  snapshot_id?: string | null;
  created_at: string;
}

// --- Nano Agent Types (M3 Nano Agent Affordance) ---

export type NanoAgentStatus =
  | "proposed"
  | "authorized"
  | "queued"
  | "running"
  | "waiting"
  | "verifying"
  | "completed"
  | "failed"
  | "cancelled"
  | "retired";

export interface NanoAssignment {
  nano_id: string;
  task_id: string;
  run_ref: string;
  parent_task_id: string;
  depth: number;
  purpose: string;
  task_type: string;
  capabilities: string[];
  permissions: string[];
  expected_cost: number;
  correlation_id: string;
  proposal_ref: string;
  assignment_version: number;
  executor_kind?: string | null;
  actual_cost?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  source?: string;
}

export interface NanoResult {
  disposition: string;
  summary: string;
  output_digest: string;
  capabilities_used: string[];
  evidence_refs: string[];
}

export interface NanoMessage {
  sender_id: string;
  recipient_id: string;
  kind: string;
  correlation_id: string;
  summary: string;
}

export interface NanoEscalation {
  nano_id: string;
  reason: string;
  requested_authority: string;
  granted: boolean;
}

export interface NanoAgentEnvelope {
  max_logical_agents: number;
  max_concurrent_agents: number;
  max_manager_depth: number;
  max_nano_tasks: number;
  max_cloud_calls: number;
  max_frontier_calls: number;
  max_api_cost_usd: number;
  max_runtime_seconds: number;
}

export interface NanoAgentsState {
  assignments: NanoAssignment[];
  statuses: Record<string, string>;
  results: Record<string, NanoResult>;
  cancelled: string[];
  messages: Array<{
    sender_id: string;
    recipient_id: string;
    kind: string;
    correlation_id: string;
    summary: string;
  }>;
  escalations: Array<{
    nano_id: string;
    reason: string;
    requested_authority: string;
    granted: boolean;
  }>;
  envelope: NanoAgentEnvelope;
  run_cancelled: boolean;
  gate_enabled: boolean;
}

export interface NanoAgentsResponse {
  assignments: NanoAssignment[];
  statuses: Record<string, string>;
  results: Record<string, NanoResult>;
  cancelled: string[];
  messages: Array<{
    sender_id: string;
    recipient_id: string;
    kind: string;
    correlation_id: string;
    summary: string;
  }>;
  escalations: Array<{
    nano_id: string;
    reason: string;
    requested_authority: string;
    granted: boolean;
  }>;
  envelope: NanoAgentEnvelope;
  run_cancelled: boolean;
  gate_enabled: boolean;
}

export interface StudioWorkflowOccurrence {
  id: string;
  automation_id: string;
  scheduled_for: string;
  status: "claimed" | "running" | "succeeded" | "failed";
  claimed_at: string;
  run_id: string | null;
  completed_at: string | null;
  error: string | null;
  check?: Record<string, unknown> | null;
}

export interface StudioWorkflowSummary {
  id: string;
  name: string;
  project_id: string;
  goal: string;
  automation_type: "once" | "recurring" | "conditional";
  schedule: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  last_run_at: string | null;
  last_run_id: string | null;
  metadata: {
    template?: string;
    steps?: string[];
    participant_ids?: string[];
    mode?: string;
    inputs?: Record<string, string>;
    [key: string]: unknown;
  };
  revision: number;
  condition?: Record<string, unknown> | null;
  last_check_at?: string | null;
  execution_history_count?: number;
  latest_occurrence?: StudioWorkflowOccurrence | null;
}

export interface StudioWorkflowDetail extends StudioWorkflowSummary {
  occurrences: StudioWorkflowOccurrence[];
}

