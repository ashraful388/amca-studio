import { CognitiveWorkflowPanel } from "./CognitiveWorkflowPanel";
import { useCallback, useEffect, useState } from "react";
import { amca } from "./api";
import {
  buildMonitorRows,
  buildNanoTree,
  canCancel,
  countStates,
  type MonitorState,
  type NanoTreeNode,
} from "./agentMonitorModel";
import { groupAgentsByTask } from "./composerModel";
import { agentUxState } from "./agentControlModel";
import {
  architectureLabel,
  architectureMode,
  collaborationTimeline,
  costInsight,
  decisionTrace,
  insightsAvailable,
  participantsFromRun,
} from "./collaborationInsightsModel";
import {
  costTransparencyLines,
  decisionPanel,
  routingExplanations,
} from "./orchestrationUxModel";
import type { RunJob } from "./types";

// Z1 Part 2 — Agent Control Panel. Live view over the EXISTING run
// registry (/api/dashboard runs, /api/runs/{id}, /api/runs/{id}/cancel)
// and nano-agent registry (/api/nano-agents). Actions are limited to the
// safe existing ones: inspect (expand), cancel (run registry), refresh.

const STATE_LABELS: Record<MonitorState, string> = {
  created: "Created",
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

const STATE_PILL: Record<MonitorState, string> = {
  created: "status-pending",
  queued: "status-queued",
  running: "status-running",
  completed: "status-ready",
  failed: "status-failed",
  cancelled: "status-cancelled",
};

function statePill(state: string): string {
  return STATE_PILL[(state as MonitorState) in STATE_PILL ? (state as MonitorState) : "created"];
}

function relativeTime(value: string | null): string {
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

function NanoTreeBranch({ node, last, depth }: { node: NanoTreeNode; last: boolean; depth: number }) {
  return (
    <>
      <div className={`dash-tree-row state-${node.state}`} style={{ paddingLeft: `${depth * 18}px` }}>
        <span className="dash-tree-branch">{depth === 0 ? (last ? "└" : "├") : last ? "└" : "├"}</span>
        <strong>{node.purpose || node.taskId}</strong>
        <small>{node.statusLabel}</small>
        <span className="dash-tree-id">{node.nanoId}</span>
      </div>
      {node.children.map((child, index) => (
        <NanoTreeBranch key={child.nanoId} node={child} last={index === node.children.length - 1} depth={depth + 1} />
      ))}
    </>
  );
}

export function AgentMonitor() {
  const [runs, setRuns] = useState<RunJob[]>([]);
  const [nano, setNano] = useState<Awaited<ReturnType<typeof amca.nanoAgents>> | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [inspect, setInspect] = useState<Record<string, RunJob>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [dashboardDoc, nanoState] = await Promise.all([amca.dashboard(), amca.nanoAgents()]);
      setRuns(dashboardDoc.runs);
      setNano(nanoState);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const nanoDocument = nano ?? { assignments: [], statuses: {} };
  const rows = buildMonitorRows(runs, nanoDocument);
  const stateCounts = countStates(rows);
  const tree = buildNanoTree(nanoDocument);
  const taskGroups = groupAgentsByTask(runs, nanoDocument);

  const cancelRun = async (runId: string) => {
    setBusy(true);
    setError(null);
    try {
      const updated = await amca.cancelRun(runId);
      setRuns((current) => current.map((run) => (run.run_id === updated.run_id ? updated : run)));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const inspectRun = async (runId: string) => {
    setBusy(true);
    setError(null);
    try {
      const job = await amca.run(runId);
      setInspect((current) => ({ ...current, [runId]: job }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="panel settings-wide agent-monitor">
      <div className="panel-heading">
        <div><span className="section-kicker">Live execution</span><h2>Agent monitor</h2></div>
        <button className="secondary-button" onClick={() => void refresh()} disabled={busy}>Refresh</button>
      </div>
      <p className="settings-note">Live runs and nano workers with their current state. Cancel uses the existing run-registry cancellation; inspection reads stored run state only. Internal agent messages stay collapsed per the activity policy.</p>

      {error && <div className="error-banner"><div><strong>Monitor reported a problem</strong><span>{error}</span></div><button onClick={() => void refresh()}>Retry</button></div>}

      <div className="monitor-state-strip">
        {(Object.keys(STATE_LABELS) as MonitorState[]).map((state) => (
          <span key={state} className={`status-pill ${STATE_PILL[state]}`}>{STATE_LABELS[state]} <b>{stateCounts[state]}</b></span>
        ))}
      </div>

      {/* Z5: one honest UX-state banner over the real counts (order scope 5).
          The agent count is >0 whenever the monitor renders (catalog rows
          exist independently); the states derive from live run/nano counts. */}
      {(() => {
        const view = agentUxState(1, {
          failed: stateCounts.failed,
          cancelled: stateCounts.cancelled,
          running: stateCounts.running,
          queued: stateCounts.queued,
        });
        return (
          <div className={`agent-ux-banner ux-${view.state}`} role="status">
            <strong>{view.title}</strong>
            <span>{view.detail}</span>
          </div>
        );
      })()}

      {rows.length ? (
        <div className="monitor-table">
          <div className="monitor-row monitor-head">
            <span>Agent</span><span>Type</span><span>State</span><span>Task</span><span>Model</span><span>Started</span><span>Actions</span>
          </div>
          {rows.map((row) => (
            <div key={row.key}>
              <div className="monitor-row">
                <span className="monitor-id" title={row.agentId}>{row.agentId.length > 22 ? `${row.agentId.slice(0, 22)}…` : row.agentId}</span>
                <span>{row.agentType}</span>
                <span><span className={`status-pill ${STATE_PILL[row.state]}`}>{STATE_LABELS[row.state]}</span></span>
                <span className="monitor-focus" title={row.focus}>{row.focus.length > 60 ? `${row.focus.slice(0, 60)}…` : row.focus}</span>
                <span>{row.model}</span>
                <span>{relativeTime(row.startedAt)}</span>
                <span className="monitor-actions">
                  <button
                    onClick={() => {
                      setExpanded(expanded === row.key ? null : row.key);
                      if (row.runId && !inspect[row.runId]) void inspectRun(row.runId);
                    }}
                    disabled={busy}
                  >Inspect</button>
                  {row.runId && canCancel(row.state) && (
                    <button className="danger-link" disabled={busy} onClick={() => void cancelRun(row.runId!)}>Cancel</button>
                  )}
                </span>
              </div>
              {expanded === row.key && (
                <div className="monitor-detail">
                  <dl>
                    <div><dt>Agent ID</dt><dd><code>{row.agentId}</code></dd></div>
                    {row.runId && inspect[row.runId] && (
                      <>
                        <div><dt>Status</dt><dd>{inspect[row.runId].status}</dd></div>
                        <div><dt>Created</dt><dd>{inspect[row.runId].created_at}</dd></div>
                        <div><dt>Finished</dt><dd>{inspect[row.runId].finished_at ?? "—"}</dd></div>
                        <div><dt>Rounds</dt><dd>{inspect[row.runId].collaboration_rounds ?? "—"}</dd></div>
                        {inspect[row.runId].error && <div><dt>Error</dt><dd className="monitor-error">{inspect[row.runId].error}</dd></div>}
                        {inspect[row.runId].log && (
                          <div><dt>Log tail</dt><dd><pre className="monitor-log">{inspect[row.runId].log.split("\n").slice(-14).join("\n")}</pre></dd></div>
                        )}
                      </>
                    )}
                    {row.kind === "nano" && (() => {
                      const assignment = nanoDocument.assignments.find((item) => item.nano_id === row.agentId);
                      if (!assignment) return null;
                      return (
                        <>
                          <div><dt>Task ID</dt><dd><code>{assignment.task_id}</code></dd></div>
                          <div><dt>Parent task</dt><dd><code>{assignment.parent_task_id}</code></dd></div>
                          <div><dt>Depth</dt><dd>{assignment.depth}</dd></div>
                          <div><dt>Type</dt><dd>{assignment.task_type}</dd></div>
                          <div><dt>Run</dt><dd><code>{assignment.run_ref}</code></dd></div>
                          <div><dt>Executor</dt><dd>{assignment.executor_kind ?? "Unknown"}</dd></div>
                        </>
                      );
                    })()}
                  </dl>
                  {/* Z6: collaboration insights from the run's own evidence. */}
                  {row.runId && inspect[row.runId] && insightsAvailable(inspect[row.runId]) && (
                    <CollaborationInsights run={inspect[row.runId]} />
                  )}
                  {row.runId && inspect[row.runId] && (
                    <CognitiveWorkflowPanel
                      run={inspect[row.runId]}
                      knowledgeItems={[]}
                      agentIds={row.kind === "nano" ? [row.agentId] : []}
                    />
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="muted-copy">No live agent activity. Runs and nano workers appear here the moment a task starts.</p>
      )}

      <div className="panel-heading"><div><span className="section-kicker">By task</span><h2>Agents grouped by task</h2></div><small>{taskGroups.length}</small></div>
      <p className="settings-note">Each governed run coordinates its own nano workers; assignments without a run stay visible under the background group. Read-only view over the existing registries.</p>
      {taskGroups.length ? (
        <div className="task-groups">
          {taskGroups.map((group) => (
            <div key={group.taskKey} className="task-group">
              <div className="task-group-heading">
                <strong>{group.goal.length > 70 ? `${group.goal.slice(0, 70)}…` : group.goal}</strong>
                {group.runState && <span className={`status-pill ${statePill(group.runState)}`}>{group.runState}</span>}
              </div>
              <ul className="task-group-agents">
                {group.agents.map((agent) => (
                  <li key={`${agent.kind}-${agent.id}`}>
                    <span className="task-agent-role">{agent.role}</span>
                    <span className={`status-pill ${statePill(agent.state)}`}>{agent.state}</span>
                    <span className="dash-tree-id" title={agent.id}>{agent.id.length > 18 ? `${agent.id.slice(0, 18)}…` : agent.id}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted-copy">No task groupings yet — start a run and its coordinator plus workers will appear here.</p>
      )}

      <div className="panel-heading"><div><span className="section-kicker">Hierarchy</span><h2>Nano agent execution tree</h2></div><small>{nanoDocument.assignments.length}</small></div>
      <p className="settings-note">Parent and child agents with status propagation. Authority only shrinks from parent to child; cancellations cascade through the tree.</p>
      {tree.length ? (
        <div className="dash-tree">
          {tree.map((root, index) => (
            <NanoTreeBranch key={root.nanoId} node={root} last={index === tree.length - 1} depth={0} />
          ))}
        </div>
      ) : (
        <p className="muted-copy">0 tasks. No Nano agents assigned in the current registry.</p>
      )}
    </article>
  );
}

// Z6 Collaboration Insights — renders the run's own evidence: timeline,
// architecture + participants, decision trace, cost insight. Stages and
// trace lines without evidence show "not recorded"; nothing is invented.
function CollaborationInsights({ run }: { run: RunJob }) {
  const stages = collaborationTimeline(run);
  const trace = decisionTrace(run);
  const recordedTrace = trace.filter((row) => row.recorded);
  const missingTrace = trace.filter((row) => !row.recorded);
  const cost = costInsight(run);
  const participants = participantsFromRun(run);
  const mode = architectureMode(run);
  return (
    <section className="collab-insights" aria-label="Collaboration insights">
      <h4>Collaboration insights</h4>

      <div className="collab-insight-block">
        <h5>Timeline — {architectureLabel(mode)}</h5>
        <ol className="collab-timeline">
          {stages.map((stage) => (
            <li key={stage.key} className={stage.state === "recorded" ? "recorded" : "missing"}>
              <strong>{stage.label}</strong>
              <span>{stage.detail}</span>
              {stage.state === "not_recorded" && <small>not recorded</small>}
            </li>
          ))}
        </ol>
      </div>

      <div className="collab-insight-block">
        <h5>Participants & models</h5>
        <ul className="collab-participants">
          {participants.map((participant) => (
            <li key={`${participant.ref}-${participant.provider}-${participant.model}`}>
              <code>{participant.provider || "unrecorded"} / {participant.model || "unrecorded"}</code>
              <span>{participant.calls > 0 ? `${participant.calls} measured call${participant.calls === 1 ? "" : "s"}` : "assignments decided at task time"}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="collab-insight-block">
        <h5>Decision trace — why this approach</h5>
        {recordedTrace.length ? (
          <dl className="collab-trace">
            {recordedTrace.map((row) => (
              <div key={row.label}><dt>{row.label}</dt><dd>{row.value}</dd></div>
            ))}
          </dl>
        ) : (
          <p className="muted-copy">Evidence not recorded.</p>
        )}
        {recordedTrace.length > 0 && missingTrace.length > 0 && (
          <p className="settings-note">Not recorded on this run: {missingTrace.map((row) => row.label).join(", ")}.</p>
        )}
      </div>

      <div className="collab-insight-block">
        <h5>Cost & efficiency</h5>
        <p className={`collab-cost ${cost.basis}`}>{cost.line}</p>
        {cost.inputTokens !== null && cost.outputTokens !== null && (
          <p className="settings-note">Tokens: {cost.inputTokens} in · {cost.outputTokens} out (recorded by the existing runtime accounting).</p>
        )}
        {cost.breakdown.length > 0 && (
          <ul className="collab-cost-breakdown">
            {cost.breakdown.map((entry) => (
              <li key={entry.model}><code>{entry.model}</code><span>${entry.costUsd.toFixed(4)}</span></li>
            ))}
          </ul>
        )}
      </div>

      {/* BETA-B4: model decision transparency + routing explanations +
          per-ref cost transparency, from the run's own decision document. */}
      {(() => {
        const decision = decisionPanel(run);
        const explanations = routingExplanations(run);
        const costLines = costTransparencyLines(decision.selectedRefs);
        if (!decision.hasDecision && explanations.length === 1 && explanations[0].evidence === "none") return null;
        return (
          <div className="collab-insight-block">
            <h5>Model decision — {decision.policy.replaceAll("_", " ")} ({decision.locality})</h5>
            {decision.selectedRefs.length > 0 && (
              <ul className="orch-selected-refs">
                {decision.selectedRefs.map((ref) => <li key={ref}><code>{ref}</code></li>)}
              </ul>
            )}
            {decision.factors.length > 0 && (
              <dl className="orch-factors">
                {decision.factors.map((factor) => (
                  <div key={factor.label}><dt>{factor.label}</dt><dd>{factor.value}</dd></div>
                ))}
              </dl>
            )}
            <ul className="orch-explanations">
              {explanations.map((explanation, index) => (
                <li key={index}>
                  <span>{explanation.sentence}</span>
                  <small>source: {explanation.evidence}</small>
                </li>
              ))}
            </ul>
            {decision.expectedCostUsd !== null && (
              <p className="settings-note">Recorded expected cost for the selected set: ${decision.expectedCostUsd.toFixed(4)}.</p>
            )}
            {costLines.length > 0 && (
              <ul className="orch-cost-lines">
                {costLines.map((line) => <li key={line.ref}>{line.line}</li>)}
              </ul>
            )}
          </div>
        );
      })()}
    </section>
  );
}
