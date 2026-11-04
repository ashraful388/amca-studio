import { useEffect, useState } from "react";
import { amca } from "./api";

type LoadPhase = "loading" | "ready" | "error";

const NANO_STATUS_LABELS: Record<string, string> = {
  proposed: "Proposed",
  authorized: "Authorized",
  queued: "Queued",
  running: "Running",
  waiting: "Waiting",
  verifying: "Verifying",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  retired: "Retired",
};

function statusBadge(status: string) {
  const color =
    status === "completed"
      ? "status-ready"
      : status === "running"
      ? "status-running"
      : status === "failed"
      ? "status-failed"
      : status === "cancelled"
      ? "status-cancelled"
      : "status-pending";
  return (
    <span className={`status-pill ${color}`}>
      {NANO_STATUS_LABELS[status] ?? status}
    </span>
  );
}

function costDisplay(cost: number | null | undefined) {
  if (cost === null || cost === undefined) return "Unknown";
  if (cost === 0) return "$0.0000";
  return `$${cost.toFixed(4)}`;
}

interface NanoAssignment {
  nano_id: string;
  task_id: string;
  run_ref: string;
  parent_task_id: string;
  depth: number;
  purpose: string;
  task_type: string;
  capabilities: string[];
  permissions: string[];
  expected_cost: number | null;
  correlation_id: string;
  proposal_ref: string;
  assignment_version: number;
  executor_kind?: string | null;
  actual_cost?: number | null;
}

interface NanoResult {
  disposition: string;
  summary: string;
  output_digest: string;
  capabilities_used: string[];
  evidence_refs: string[];
}

interface NanoMessage {
  sender_id: string;
  recipient_id: string;
  kind: string;
  correlation_id: string;
  summary: string;
}

interface NanoEscalation {
  nano_id: string;
  reason: string;
  requested_authority: string;
  granted: boolean;
}

interface NanoEnvelope {
  max_logical_agents: number;
  max_concurrent_agents: number;
  max_manager_depth: number;
  max_nano_tasks: number;
  max_cloud_calls: number;
  max_frontier_calls: number;
  max_api_cost_usd: number;
  max_runtime_seconds: number;
}

interface NanoAgentsPanelData {
  assignments: NanoAssignment[];
  statuses: Record<string, string>;
  results: Record<string, NanoResult>;
  cancelled: string[];
  messages: NanoMessage[];
  escalations: NanoEscalation[];
  envelope: NanoEnvelope;
  run_cancelled: boolean;
  gate_enabled: boolean;
}

function renderResult(result: NanoResult) {
  return (
    <>
      <dt>Result</dt>
      <dd>
        <div className="nano-result">
          <div className={`result-badge ${result.disposition.toLowerCase()}`}>
            {result.disposition}
          </div>
          <p className="result-summary">{result.summary}</p>
          <small>Digest: {result.output_digest}</small>
          {result.capabilities_used.length > 0 && (
            <div>
              <strong>Capabilities Used:</strong>
              {result.capabilities_used.map((c) => (
                <span key={c} className="nano-capability">{c}</span>
              ))}
            </div>
          )}
          {result.evidence_refs.length > 0 && (
            <div>
              <strong>Evidence:</strong>
              {result.evidence_refs.map((e) => (
                <span key={e} className="evidence-ref">{e}</span>
              ))}
            </div>
          )}
        </div>
      </dd>
    </>
  );
}

function renderAssignment(
  assignment: NanoAssignment,
  statuses: Record<string, string>,
  results: Record<string, NanoResult>,
  selectedId: string | null,
  onSelect: (id: string | null) => void
) {
  const isSelected = selectedId === assignment.nano_id;
  return (
    <article
      key={assignment.nano_id}
      className={`nano-agent-card ${isSelected ? "selected" : ""}`}
      onClick={() => onSelect(isSelected ? null : assignment.nano_id)}
    >
      <div className="nano-agent-header">
        <div className="nano-agent-title">
          <strong>{assignment.purpose}</strong>
          <span className="nano-agent-id">{assignment.nano_id}</span>
        </div>
        <div className="nano-agent-meta">
          {statusBadge(statuses[assignment.nano_id] ?? "unknown")}
          <span className="nano-cost">{costDisplay(assignment.expected_cost)}</span>
          <span className="nano-depth">Depth: {assignment.depth}</span>
        </div>
      </div>

      <div className="nano-agent-details">
        <dl>
          <div>
            <dt>Task ID</dt>
            <dd>{assignment.task_id}</dd>
          </div>
          <div>
            <dt>Parent</dt>
            <dd>{assignment.parent_task_id}</dd>
          </div>
          <div>
            <dt>Run</dt>
            <dd>{assignment.run_ref}</dd>
          </div>
          <div>
            <dt>Depth</dt>
            <dd>{assignment.depth}</dd>
          </div>
          <div>
            <dt>Executor</dt>
            <dd>{assignment.executor_kind ?? "Unknown"}</dd>
          </div>
          <div>
            <dt>Actual cost</dt>
            <dd>{costDisplay(assignment.actual_cost)}</dd>
          </div>
          <div>
            <dt>Type</dt>
            <dd>{assignment.task_type}</dd>
          </div>
          <div>
            <dt>Proposal Ref</dt>
            <dd>{assignment.proposal_ref}</dd>
          </div>
          <div>
            <dt>Version</dt>
            <dd>{assignment.assignment_version}</dd>
          </div>

          {results[assignment.nano_id] && renderResult(results[assignment.nano_id])}
        </dl>

        <div className="nano-capabilities">
          <dt>Capabilities</dt>
          <dd>
            {assignment.capabilities.length > 0 ? (
              assignment.capabilities.map((c) => (
                <span key={c} className="nano-capability">
                  {c}
                </span>
              ))
            ) : (
              <span className="muted-copy">None</span>
            )}
          </dd>
        </div>

        <div className="nano-permissions">
          <dt>Permissions</dt>
          <dd>
            {assignment.permissions.length > 0 ? (
              assignment.permissions.map((p) => (
                <span key={p} className="nano-permission">
                  {p}
                </span>
              ))
            ) : (
              <span className="muted-copy">None</span>
            )}
          </dd>
        </div>
      </div>
    </article>
  );
}

function renderMessages(messages: NanoMessage[]) {
  if (messages.length === 0) return null;
  return (
    <div className="nano-messages-panel">
      <div className="panel-heading">
        <h3>Messages</h3>
      </div>
      <div className="nano-messages-list">
        {messages.map((msg, idx) => (
          <article key={idx} className="nano-message">
            <div className="nano-message-header">
              <span className="nano-kind">{msg.kind}</span>
              <span className="nano-actor">
                {msg.sender_id} → {msg.recipient_id}
              </span>
            </div>
            <p className="nano-message-summary">{msg.summary}</p>
            <small>Correlation: {msg.correlation_id}</small>
          </article>
        ))}
      </div>
    </div>
  );
}

function renderEscalations(escalations: NanoEscalation[]) {
  if (escalations.length === 0) return null;
  return (
    <div className="nano-escalations-panel">
      <div className="panel-heading">
        <h3>Escalations</h3>
      </div>
      <div className="nano-escalations-list">
        {escalations.map((esc, idx) => (
          <article key={idx} className="nano-escalation">
            <div className="escalation-header">
              <strong>{esc.nano_id}</strong>
              <span className={esc.granted ? "granted" : "pending"}>
                {esc.granted ? "Granted" : "Pending"}
              </span>
            </div>
            <p>Reason: {esc.reason}</p>
            <p>Requested Authority: {esc.requested_authority}</p>
          </article>
        ))}
      </div>
    </div>
  );
}

function renderEnvelope(envelope: NanoEnvelope, runCancelled: boolean, gateEnabled: boolean) {
  return (
    <>
      <div className="panel-heading">
        <h3>Governance Envelope</h3>
      </div>
      <dl className="envelope-details">
        <div><dt>Max Logical Agents</dt><dd>{envelope.max_logical_agents}</dd></div>
        <div><dt>Max Concurrent</dt><dd>{envelope.max_concurrent_agents}</dd></div>
        <div><dt>Max Manager Depth</dt><dd>{envelope.max_manager_depth}</dd></div>
        <div><dt>Max Nano Tasks</dt><dd>{envelope.max_nano_tasks}</dd></div>
        <div><dt>Max Cloud Calls</dt><dd>{envelope.max_cloud_calls}</dd></div>
        <div><dt>Max Frontier Calls</dt><dd>{envelope.max_frontier_calls}</dd></div>
        <div><dt>Max API Cost (USD)</dt><dd>${envelope.max_api_cost_usd}</dd></div>
        <div><dt>Max Runtime (s)</dt><dd>{envelope.max_runtime_seconds}</dd></div>
        <div><dt>Run Cancelled</dt><dd>{runCancelled ? "Yes" : "No"}</dd></div>
        <div><dt>Gate Enabled</dt><dd>{gateEnabled ? "Yes" : "No"}</dd></div>
      </dl>
    </>
  );
}

export function NanoAgentsPanel() {
  const [phase, setPhase] = useState<LoadPhase>("loading");
  const [loadError, setError] = useState<string | null>(null);
  const [data, setData] = useState<NanoAgentsPanelData | null>(null);
  const [selectedAssignmentId, setSelectedAssignmentId] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = () => {
    setPhase("loading");
    setError(null);
    amca
      .nanoAgents()
      .then((value) => {
        if (!value || !value.assignments) {
          throw new Error("unsupported nano agents response");
        }
        setData(value);
        setPhase("ready");
      })
      .catch((error: unknown) => {
        setError(
          error instanceof Error ? error.message : "Nano agents status unavailable",
        );
        setPhase("error");
      });
  };

  useEffect(() => {
    refresh();
  }, [refreshKey]);

  if (phase === "loading") {
    return (
      <article className="panel settings-wide intelligence-panel">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">M3 Governed</span>
            <h2>Nano Agents</h2>
          </div>
        </div>
        <p className="muted-copy">Loading nano agents status…</p>
      </article>
    );
  }

  if (phase === "error") {
    return (
      <article className="panel settings-wide intelligence-panel">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">M3 Governed</span>
            <h2>Nano Agents</h2>
          </div>
        </div>
        <p className="settings-note intelligence-error" role="alert">
          Nano agents status unavailable: {loadError}
        </p>
        <div className="form-actions">
          <button
            className="secondary-button"
            onClick={() => setRefreshKey((k) => k + 1)}
          >
            Retry
          </button>
        </div>
      </article>
    );
  }

  if (!data) {
    return (
      <article className="panel settings-wide intelligence-panel">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">M3 Governed</span>
            <h2>Nano Agents</h2>
          </div>
        </div>
        <p className="muted-copy">No data available.</p>
      </article>
    );
  }

  const messagesPanel = renderMessages(data.messages);
  const escalationsPanel = renderEscalations(data.escalations);

  return (
    <article className="panel settings-wide intelligence-panel nano-agents-panel">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">M3 Governed</span>
          <h2>Nano Agents</h2>
        </div>
        <div className="intelligence-heading-actions">
          <span className="status-pill">
            {data.gate_enabled ? "Enabled" : "Disabled"}
          </span>
          <button
            className="secondary-button"
            onClick={() => setRefreshKey((k) => k + 1)}
          >
            Refresh
          </button>
        </div>
      </div>

      <p className="settings-note">
        Governed Nano agents are bounded, scope-checked, and fully audited.
        Lifecycle is managed by the M3 NanoRunRegistry; cancellation propagates
        through the hierarchy. Model-call history is separate from executed Nano tasks.
      </p>

      <div className="nano-agents-summary">
        <div className="summary-card">
          <strong>Total</strong>
          <span>{data.assignments.length}</span>
        </div>
        <div className="summary-card">
          <strong>Active</strong>
          <span>
            {Object.values(data.statuses).filter(
              (s) => ["running", "waiting", "verifying"].includes(s)
            ).length}
          </span>
        </div>
        <div className="summary-card">
          <strong>Completed</strong>
          <span>
            {Object.values(data.statuses).filter((s) => ["completed", "merged", "retired"].includes(s))
              .length}
          </span>
        </div>
        <div className="summary-card">
          <strong>Cancelled</strong>
          <span>
            {Object.values(data.statuses).filter((s) => s === "cancelled")
              .length}
          </span>
        </div>
        <div className="summary-card">
          <strong>Failed</strong>
          <span>
            {Object.values(data.statuses).filter((s) => s === "failed")
              .length}
          </span>
        </div>
        <div className="summary-card">
          <strong>Gate</strong>
          <span>{data.gate_enabled ? "ON" : "OFF"}</span>
        </div>
      </div>

      <div className="nano-agents-list">
        {data.assignments.length === 0 ? (
          <p className="muted-copy">
            0 tasks. No Nano assignments have executed in this registry.
          </p>
        ) : (
          <>{data.assignments.map((assignment) =>
            renderAssignment(
              assignment,
              data.statuses,
              data.results,
              selectedAssignmentId,
              setSelectedAssignmentId
            )
          )}</>
        )}
      </div>

      {messagesPanel}
      {escalationsPanel}
      {renderEnvelope(data.envelope, data.run_cancelled, data.gate_enabled)}
    </article>
  );
}
