// Z5 Advanced Agent Control — pure presentation model (no fetch, no DOM).
// Builds the commercial Agent Control experience on the EXISTING documents
// only: /api/agent-fabric (AgentTemplate catalog + settings + hierarchy),
// /api/nano-agents (assignments, statuses, results), and the run registry.
// Honesty rule carried over from Z3/Z4: the runtime does not track
// per-template task history, performance, or cost, so those detail
// sections render "not tracked" instead of invented metrics.

// ---------- 1. Model assignment visibility (order scope 1 + 2) ----------
// AgentTemplate carries a model POLICY, never a pinned model: executor
// choice happens at task time via the existing router. Labels say so.

export function modelAssignmentLabel(policy: string): string {
  switch (policy) {
    case "auto_intelligence":
      return "Auto Intelligence — the router picks the lowest-cost qualified model at task time";
    case "manual_only":
      return "Manual only — an exact model is chosen explicitly at task time";
    case "local_preferred":
      return "Local preferred — favors eligible on-device models when available";
    case "local_only":
      return "Local only — never selects network models";
    default:
      return `${policy.replaceAll("_", " ")} — policy applied by the existing router at task time`;
  }
}

// ---------- 2. Capability summary for enhanced library cards (scope 1) ----------

export interface AgentTemplateLike {
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

export interface AgentCardSummary {
  originLabel: string;
  classLabel: string;
  policyLabel: string;
  toolCount: number;
  permissionCount: number;
  spawnLabel: string;
}

export function agentCardSummary(agent: AgentTemplateLike): AgentCardSummary {
  return {
    originLabel: agent.origin === "built_in" ? "Built-in" : agent.origin === "custom" ? "Custom" : "Installed",
    classLabel: agent.agent_class.replaceAll("_", " "),
    policyLabel: modelAssignmentLabel(agent.preferred_model_policy),
    toolCount: agent.allowed_tools.length,
    permissionCount: agent.permissions.length,
    spawnLabel: agent.can_spawn ? `Can spawn · max ${agent.max_children}` : "No spawning",
  };
}

// ---------- 3. Catalog statistics (order scope 1) ----------

export interface AgentCatalogStats {
  total: number;
  builtIn: number;
  custom: number;
  installed: number;
  disabled: number;
  withTools: number;
  withPermissions: number;
  spawnCapable: number;
  categories: Array<{ name: string; count: number }>;
}

export function agentCatalogStats(agents: AgentTemplateLike[]): AgentCatalogStats {
  const categories = new Map<string, number>();
  const stats: AgentCatalogStats = {
    total: agents.length,
    builtIn: 0, custom: 0, installed: 0, disabled: 0,
    withTools: 0, withPermissions: 0, spawnCapable: 0,
    categories: [],
  };
  for (const agent of agents) {
    if (agent.origin === "built_in") stats.builtIn += 1;
    else if (agent.origin === "custom") stats.custom += 1;
    else stats.installed += 1;
    if (!agent.enabled) stats.disabled += 1;
    if (agent.allowed_tools.length > 0) stats.withTools += 1;
    if (agent.permissions.length > 0) stats.withPermissions += 1;
    if (agent.can_spawn) stats.spawnCapable += 1;
    categories.set(agent.category, (categories.get(agent.category) ?? 0) + 1);
  }
  stats.categories = [...categories.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  return stats;
}

// ---------- 4. Safety profile (order scope 4) ----------
// Every line is either a field from the template or a factual statement
// about the existing governor/approval flow. No invented levels.

export interface AgentSafetyProfile {
  allowedTools: string[];
  permissions: string[];
  toolsLabel: string;
  permissionsLabel: string;
  spawnLabel: string;
  approvalsLabel: string;
}

const APPROVALS_LABEL =
  "Consequential task-time actions flow through the deterministic Governor and the existing Approvals view.";

export function agentSafetyProfile(agent: AgentTemplateLike): AgentSafetyProfile {
  return {
    allowedTools: agent.allowed_tools,
    permissions: agent.permissions,
    toolsLabel: agent.allowed_tools.length
      ? agent.allowed_tools.join(" · ")
      : "No explicit tool grants — default governor limits apply",
    permissionsLabel: agent.permissions.length
      ? agent.permissions.join(" · ")
      : "No explicit permissions — default governor limits apply",
    spawnLabel: agent.can_spawn
      ? `Spawning allowed, bounded to ${agent.max_children} child${agent.max_children === 1 ? "" : "ren"} (proposals still gated by the Governor)`
      : "This agent cannot spawn child agents",
    approvalsLabel: APPROVALS_LABEL,
  };
}

// ---------- 5. Agent detail view (order scope 2) ----------
// Sections in the manager's order: Identity, Purpose, Model, Permissions,
// Recent Tasks, Performance, Cost Usage. Where the runtime has no
// per-agent data, the section carries an honest "not tracked" line.

export interface DetailRow {
  label: string;
  value: string;
}

export interface DetailSection {
  id: "identity" | "purpose" | "model" | "permissions" | "recent_tasks" | "performance" | "cost";
  title: string;
  rows: DetailRow[];
  note: string | null;
}

export function agentDetailSections(agent: AgentTemplateLike): DetailSection[] {
  const safety = agentSafetyProfile(agent);
  return [
    {
      id: "identity",
      title: "Identity",
      rows: [
        { label: "Name", value: agent.name },
        { label: "Category", value: agent.category },
        { label: "Class", value: agent.agent_class.replaceAll("_", " ") },
        { label: "Origin", value: agentCardSummary(agent).originLabel },
        { label: "Status", value: agent.enabled ? "Enabled" : "Disabled" },
      ],
      note: null,
    },
    {
      id: "purpose",
      title: "Purpose",
      rows: [
        { label: "Description", value: agent.description || "No description recorded." },
        { label: "Instructions", value: agent.instructions || "No instructions recorded." },
        { label: "Success criteria", value: agent.success_criteria || "Not specified — the Governor's generic verification applies." },
        { label: "Output schema", value: agent.output_schema || "Unstructured" },
      ],
      note: null,
    },
    {
      id: "model",
      title: "Model",
      rows: [
        { label: "Assignment policy", value: modelAssignmentLabel(agent.preferred_model_policy) },
        { label: "Concrete model", value: "Chosen at task time by the existing router — never pinned to this template" },
      ],
      note: null,
    },
    {
      id: "permissions",
      title: "Permissions & safety",
      rows: [
        { label: "Allowed tools", value: safety.toolsLabel },
        { label: "Permissions", value: safety.permissionsLabel },
        { label: "Spawning", value: safety.spawnLabel },
        { label: "Approvals", value: safety.approvalsLabel },
      ],
      note: null,
    },
    {
      id: "recent_tasks",
      title: "Recent tasks",
      rows: [],
      note: "Not tracked per agent: the run registry records runs and nano tasks carry purposes, not template identities. Live work appears in the Agent Monitor and Nano panels.",
    },
    {
      id: "performance",
      title: "Performance",
      rows: [],
      note: "Not tracked per agent — no per-template latency or success-rate document exists in the runtime. Run-level state is visible in the Agent Monitor.",
    },
    {
      id: "cost",
      title: "Cost usage",
      rows: [],
      note: "Not tracked per agent — cost lands on the task (run), not the template. Task costs stay in the existing Cost & compute panel.",
    },
  ];
}

// ---------- 6. Agent UX states (order scope 5) ----------
// The five states the order names, derived from real documents.

export type AgentUxState =
  | "no_agents"
  | "no_active_tasks"
  | "has_failures"
  | "has_cancellations"
  | "healthy";

export interface AgentUxView {
  state: AgentUxState;
  title: string;
  detail: string;
  action: { label: string; target: "compose" | "create_agent" } | null;
}

export function agentUxState(totalAgents: number, states: { failed: number; cancelled: number; running: number; queued: number }): AgentUxView {
  if (totalAgents === 0) {
    return {
      state: "no_agents",
      title: "No agents available",
      detail: "The Agent Fabric catalog is empty. Create a custom specialist or check the service.",
      action: { label: "Create agent", target: "create_agent" },
    };
  }
  if (states.failed > 0) {
    return {
      state: "has_failures",
      title: `${states.failed} task${states.failed === 1 ? "" : "s"} failed`,
      detail: "Failed tasks keep their error state for inspection; nothing auto-retries.",
      action: null,
    };
  }
  if (states.cancelled > 0) {
    return {
      state: "has_cancellations",
      title: `${states.cancelled} task${states.cancelled === 1 ? "" : "s"} cancelled`,
      detail: "Cancelled tasks stopped by request; their partial work stays recorded.",
      action: null,
    };
  }
  if (states.running === 0 && states.queued === 0) {
    return {
      state: "no_active_tasks",
      title: "No active tasks",
      detail: "Start a new AMCA workflow.",
      action: { label: "Open composer", target: "compose" },
    };
  }
  return {
    state: "healthy",
    title: "Agents working",
    detail: `${states.running} running · ${states.queued} queued.`,
    action: null,
  };
}

// ---------- 7. Nano lifecycle visibility (order scope 3) ----------
// One canonical stage sequence for nano tasks; a status maps to the
// furthest stage it has reached. Failed/cancelled are terminal branches.

export interface LifecycleStage {
  key: string;
  label: string;
}

export const NANO_LIFECYCLE: LifecycleStage[] = [
  { key: "created", label: "Created" },
  { key: "queued", label: "Queued" },
  { key: "running", label: "Running" },
  { key: "verifying", label: "Verifying" },
  { key: "completed", label: "Completed" },
];

const NANO_STAGE_INDEX: Record<string, number> = {
  proposed: 0,
  authorized: 0,
  queued: 1,
  running: 2,
  waiting: 2,
  verifying: 3,
  completed: 4,
  retired: 4,
};

export function nanoLifecycle(status: string | undefined): {
  stages: LifecycleStage[];
  reachedIndex: number;
  terminal: "failed" | "cancelled" | null;
} {
  const reachedIndex = NANO_STAGE_INDEX[status ?? ""] ?? 0;
  const terminal = status === "failed" ? "failed" : status === "cancelled" ? "cancelled" : null;
  return { stages: NANO_LIFECYCLE, reachedIndex, terminal };
}

// ---------- 8. Nano relationship labels (order scope 3) ----------

export function nanoRelationshipLabel(depth: number, parentTaskId: string): string {
  if (depth === 0 || !parentTaskId || parentTaskId === "root") return "Root worker — spawned for the task directly";
  return `Child worker at depth ${depth} — parent task ${parentTaskId}`;
}

// ---------- 9. Agent-facing run identity (order scope 2, honest) ----------
// Runs expose collaboration decisions, not agent-template bindings. The
// detail view therefore links "where to look" instead of inventing an
// assignment list.

export function agentWorkLink(agentId: string): { label: string; detail: string } {
  return {
    label: "Agent Monitor",
    detail: `The runtime organizes agents per task; ${agentId} appears there only while a task that needs it is live.`,
  };
}

// ---------- 10. Library filtering (order scope 1) ----------
// Search covers name, description, and category; category filter is exact
// with the conventional "all" wildcard. Case-insensitive throughout.

export function filterAgents(
  agents: AgentTemplateLike[],
  query: string,
  category: string,
): AgentTemplateLike[] {
  const needle = query.trim().toLowerCase();
  return agents.filter((agent) =>
    (category === "all" || agent.category === category) &&
    (!needle || `${agent.name} ${agent.description} ${agent.category}`.toLowerCase().includes(needle)),
  );
}
