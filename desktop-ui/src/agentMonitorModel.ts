// Z1 Agent Control Panel — pure presentation model for the live Agent
// Monitor tab. Maps the EXISTING run-registry and nano-agent documents
// onto the monitor states (Created / Queued / Running / Completed /
// Failed / Cancelled) and builds the nano parent/child execution tree.
// Safe actions only: inspect, cancel (run registry), refresh.

import type { RunJob } from "./types";

export type MonitorState = "created" | "queued" | "running" | "completed" | "failed" | "cancelled";

const RUN_STATE_MAP: Record<string, MonitorState> = {
  queued: "queued",
  running: "running",
  cancelling: "running",
  succeeded: "completed",
  partial: "completed",
  failed: "failed",
  cancelled: "cancelled",
};

export function runMonitorState(status: string): MonitorState {
  return RUN_STATE_MAP[status] ?? "created";
}

const NANO_STATE_MAP: Record<string, MonitorState> = {
  proposed: "created",
  authorized: "created",
  queued: "queued",
  running: "running",
  waiting: "running",
  verifying: "running",
  completed: "completed",
  retired: "completed",
  failed: "failed",
  cancelled: "cancelled",
};

export function nanoMonitorState(status: string | undefined): MonitorState {
  return NANO_STATE_MAP[status ?? ""] ?? "created";
}

export function canCancel(state: MonitorState): boolean {
  return state === "queued" || state === "running";
}

export interface NanoAssignmentLike {
  nano_id: string;
  task_id: string;
  parent_task_id: string;
  purpose: string;
  task_type: string;
  depth: number;
  expected_cost: number | null;
}

export interface NanoDocumentLike {
  assignments: NanoAssignmentLike[];
  statuses: Record<string, string>;
}

export interface MonitorTaskRow {
  key: string;
  kind: "run" | "nano";
  agentId: string;
  agentType: string;
  focus: string;
  state: MonitorState;
  model: string;
  startedAt: string | null;
  runId: string | null;
}

// Nano assignments carry no model identity (executor choice is decided at
// task time by the existing router), so the column stays honest: "router".
export function buildMonitorRows(runs: RunJob[], nano: NanoDocumentLike): MonitorTaskRow[] {
  const rows: MonitorTaskRow[] = runs.map((run) => ({
    key: `run:${run.run_id}`,
    kind: "run" as const,
    agentId: run.run_id,
    agentType: "Run",
    focus: run.goal,
    state: runMonitorState(run.status),
    model: [run.provider, run.model].filter(Boolean).join(" / ") || "unassigned",
    startedAt: run.started_at,
    runId: run.run_id,
  }));
  for (const assignment of nano.assignments) {
    rows.push({
      key: `nano:${assignment.nano_id}`,
      kind: "nano",
      agentId: assignment.nano_id,
      agentType: "Nano worker",
      focus: assignment.purpose || assignment.task_type,
      state: nanoMonitorState(nano.statuses[assignment.nano_id]),
      model: "router-selected",
      startedAt: null,
      runId: null,
    });
  }
  return rows;
}

export interface NanoTreeNode {
  nanoId: string;
  taskId: string;
  parentTaskId: string;
  purpose: string;
  depth: number;
  state: MonitorState;
  statusLabel: string;
  children: NanoTreeNode[];
}

export function buildNanoTree(nano: NanoDocumentLike): NanoTreeNode[] {
  const byTaskId = new Map(nano.assignments.map((assignment) => [assignment.task_id, assignment]));
  const nodes = new Map<string, NanoTreeNode>();
  for (const assignment of nano.assignments) {
    const status = nano.statuses[assignment.nano_id] ?? "unknown";
    nodes.set(assignment.nano_id, {
      nanoId: assignment.nano_id,
      taskId: assignment.task_id,
      parentTaskId: assignment.parent_task_id,
      purpose: assignment.purpose,
      depth: assignment.depth,
      state: nanoMonitorState(status),
      statusLabel: status,
      children: [],
    });
  }
  const roots: NanoTreeNode[] = [];
  for (const assignment of nano.assignments) {
    const node = nodes.get(assignment.nano_id);
    if (!node) continue;
    const parent = byTaskId.get(assignment.parent_task_id);
    const parentNode = parent ? nodes.get(parent.nano_id) : undefined;
    // Depth guards against malformed chains: a child never attaches to a
    // shallower-or-equal node, so cycles degrade to visible roots.
    if (parentNode && parentNode.nanoId !== node.nanoId && parentNode.depth < node.depth) {
      parentNode.children.push(node);
    } else {
      roots.push(node);
    }
  }
  const sortNodes = (list: NanoTreeNode[]) => {
    list.sort((a, b) => a.purpose.localeCompare(b.purpose));
    for (const node of list) sortNodes(node.children);
  };
  sortNodes(roots);
  return roots;
}

export function countStates(rows: MonitorTaskRow[]): Record<MonitorState, number> {
  const counts: Record<MonitorState, number> = {
    created: 0, queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0,
  };
  for (const row of rows) counts[row.state] += 1;
  return counts;
}
