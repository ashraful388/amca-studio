import React, { useState } from "react";
import {
  EMPTY_FILTER,
  failureReview,
  filterHistory,
  knowledgeInteraction,
  recordedProjects,
  recordedStatuses,
  resourceNav,
  workflowActions,
  type HistoryFilter,
  type WorkflowNavTarget,
} from "./cognitiveWorkflowInteractionModel";
import {
  EMPTY_NO_INSIGHTS,
  EMPTY_NO_STAGE_DETAILS,
  EMPTY_NO_WORKFLOWS,
  historyNote,
  stageDetail,
  whyThisResource,
  workflowHistory,
  workflowSummaryCard,
  type RecordedResourceFields,
} from "./cognitiveWorkflowInsightsModel";
import {
  knowledgeUsage,
  resourceTransparency,
  verificationView,
  workflowPanelView,
  workflowPosition,
  workflowStages,
  WORKFLOW_PANEL_EMPTY,
  type WorkflowRunRecord,
} from "./cognitiveWorkflowUxModel";
import type { KnowledgeContextItem } from "./knowledgeContextModel";

const STAGE_PILL: Record<string, string> = {
  done: "status-ready",
  active: "status-active",
  failed: "status-error",
  not_recorded: "status-muted",
};

/**
 * v0.8.0 Cognitive Workflow UX foundation — transparency panel over
 * EXISTING desktop records (run registry + the explicit knowledge
 * selection). Stages without recorded evidence render "Not recorded";
 * no inferred progress, no scores, no hidden reasoning.
 */
export const CognitiveWorkflowPanel: React.FC<{
  run: WorkflowRunRecord & { goal?: string | null };
  knowledgeItems: KnowledgeContextItem[];
  agentIds?: string[];
  artifactTitles?: string[];
  auditEventRef?: string | null;
  /** Recorded resource fields for decision transparency (why lines). */
  recordedResources?: RecordedResourceFields[];
  /** Recorded runs of the same project for the history view. */
  historyRuns?: Array<WorkflowRunRecord & { project_id?: string; artifact_count?: number }>;
  /** Navigation-only interaction: jump to an existing view. Never mutates. */
  onNavigate?: (target: WorkflowNavTarget) => void;
  artifactIds?: string[];
  runLog?: string | null;
}> = ({
  run,
  knowledgeItems,
  agentIds = [],
  artifactTitles = [],
  auditEventRef = null,
  recordedResources = [],
  historyRuns = [],
  onNavigate,
  artifactIds = [],
  runLog = null,
}) => {
  const stages = workflowStages(run, knowledgeItems);
  const panel = workflowPanelView(run);
  const resources = resourceTransparency([], agentIds);
  const usage = knowledgeUsage(knowledgeItems, auditEventRef);
  const verification = verificationView(run, artifactTitles);
  const position = workflowPosition(stages);
  // Advanced insights (record-based only)
  const [expandedStages, setExpandedStages] = useState<ReadonlySet<string>>(new Set());
  const summary = workflowSummaryCard({
    stages,
    knowledgeItems,
    resourceCount: resources.models.length + resources.agents.length,
    artifactCount: artifactTitles.length,
  });
  const toggleStage = (id: string) => {
    setExpandedStages((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const actions = workflowActions({
    hasArtifacts: artifactTitles.length > 0,
    hasKnowledge: knowledgeItems.length > 0,
    hasModels: true,
    hasAgents: agentIds.length > 0,
  });
  const nav = (target: WorkflowNavTarget | null) => {
    if (target && onNavigate) onNavigate(target);
  };
  const failedStage = stages.find((stage) => stage.state === "failed");
  const review = failedStage && run.status === "failed"
    ? failureReview({ failedStageLabel: failedStage.label, error: run.error ?? null, artifactIds, log: runLog })
    : null;
  const [showFailureDetails, setShowFailureDetails] = useState(false);
  const [historyFilter, setHistoryFilter] = useState<HistoryFilter>(EMPTY_FILTER);
  const filteredHistory = filterHistory(historyRuns, historyFilter);
  const statusOptions = recordedStatuses(historyRuns);
  const projectOptions = recordedProjects(historyRuns);

  return (
    <div className="cwf-panel">
      <div className="cwf-head">
        <strong>Cognitive workflow — transparency layer</strong>
        <small>{WORKFLOW_PANEL_EMPTY.detail}</small>
      </div>
      <dl className="cwf-fields">
        <div><dt>Workflow ID</dt><dd><code>{panel.workflowId}</code></dd></div>
        <div><dt>User goal</dt><dd>{panel.userGoal.length > 140 ? `${panel.userGoal.slice(0, 140)}…` : panel.userGoal}</dd></div>
        <div><dt>Task type</dt><dd>{panel.taskType ?? "Not recorded"}</dd></div>
        <div><dt>Lifecycle state</dt><dd>{panel.lifecycleState}{panel.failureReason ? ` — ${panel.failureReason}` : ""}</dd></div>
        <div><dt>Last recorded stage</dt><dd>{position}</dd></div>
      </dl>

      <div className="cwf-summary" aria-label="Workflow summary">
        <span><b>{summary.completedStages}/{summary.totalStages}</b> stages recorded</span>
        <span><b>{summary.knowledgeCount}</b> knowledge attached</span>
        <span><b>{summary.resourceCount}</b> resources</span>
        <span><b>{summary.artifactCount}</b> artifacts</span>
        {summary.blockedState && <span className="cwf-blocked">blocked at: {summary.blockedState}</span>}
      </div>

      <div className="cwf-actions" aria-label="Workflow actions — navigation only">
        {actions.map((action) => (
          <button
            key={action.id}
            type="button"
            onClick={() => nav(action.target)}
            disabled={!action.available || !onNavigate}
            title={action.available ? `Navigate to ${action.target}` : action.unavailableReason ?? undefined}
          >
            {action.label}
          </button>
        ))}
        <small className="muted-copy">Navigation only — opens existing views; no execution bypass, no state modification.</small>
      </div>

      <h5>Workflow loop — recorded states only</h5>
      <ol className="cwf-timeline">
        {stages.map((stage) => {
          const detail = stageDetail(stage);
          const open = expandedStages.has(stage.id);
          return (
            <li key={stage.id} className={`cwf-stage cwf-${stage.state}`}>
              <button
                type="button"
                className="cwf-stage-toggle"
                onClick={() => toggleStage(stage.id)}
                aria-expanded={open}
              >
                <span className={`status-pill ${STAGE_PILL[stage.state]}`}>{stage.state === "not_recorded" ? "Not recorded" : stage.state}</span>
                <span className="cwf-stage-label">{stage.label}</span>
                {stage.timestamp && <small className="cwf-stage-ts">{stage.timestamp}</small>}
                {stage.detail && <small className="cwf-stage-detail">{stage.detail}</small>}
              </button>
              {open && (
                <dl className="cwf-stage-detail-box">
                  <div><dt>Stage</dt><dd>{detail.name}</dd></div>
                  <div><dt>Status</dt><dd>{detail.status}</dd></div>
                  <div><dt>Evidence source</dt><dd>{detail.evidenceSource}</dd></div>
                  <div><dt>Timestamp</dt><dd>{detail.timestamp ?? "Not recorded"}</dd></div>
                  {detail.error && <div><dt>Error</dt><dd className="monitor-error">{detail.error}</dd></div>}
                </dl>
              )}
            </li>
          );
        })}
      </ol>
      {!stages.some((stage) => stage.state !== "not_recorded") && (
        <p className="muted-copy">{EMPTY_NO_STAGE_DETAILS}</p>
      )}

      <h5>Resource selection</h5>
      <dl className="cwf-fields">
        <div><dt>Selected models</dt><dd>{resources.models.length ? resources.models.join(", ") : "Not recorded"}</dd></div>
        <div><dt>Selected agents</dt><dd>{resources.agents.length ? resources.agents.join(", ") : "Not recorded"}</dd></div>
        <div><dt>Locality</dt><dd>{resources.locality}</dd></div>
        <div><dt>Governance</dt><dd>{resources.governanceStatus}</dd></div>
      </dl>

      {recordedResources.length > 0 && (
        <div className="cwf-resource-nav">
          {recordedResources.map((resource) => {
            const line = resourceNav(resource.ref, resource.kind);
            return (
              <button
                key={`${resource.kind}-${line.ref}`}
                type="button"
                onClick={() => nav(line.target)}
                title={`Open ${line.target} — ${line.localityLabel}`}
              >
                {line.localityLabel}: <code>{line.ref}</code> → {line.target === "settings" ? "Model Hub details" : "Agent details"}
              </button>
            );
          })}
        </div>
      )}

      {recordedResources.length ? (
        <div className="cwf-why">
          {recordedResources.map((resource) => {
            const line = whyThisResource(resource);
            return (
              <div key={`${resource.kind}-${resource.ref}`} className="cwf-why-item">
                <strong><code>{line.ref}</code></strong>
                <ul>{line.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>
              </div>
            );
          })}
          <small className="muted-copy">Reasons restate recorded fields only — no generated explanations.</small>
        </div>
      ) : (
        <p className="muted-copy">{EMPTY_NO_INSIGHTS}</p>
      )}

      <h5>Knowledge usage</h5>
      {usage.length ? (
        <div className="cwf-knowledge">
          {usage.map((item) => (
            <div key={item.artifactId} className="cwf-knowledge-item">
              <strong>{item.title}</strong>
              <small>{item.provenance}</small>
              <small>{item.permissionStatus} · {item.retrievalMode}</small>
              {item.auditReference && <small className="cwf-audit">Audit ref: {item.auditReference}</small>}
              {(() => {
                const source = knowledgeItems.find((k) => k.artifactId === item.artifactId);
                if (!source) return null;
                const interaction = knowledgeInteraction({
                  artifactId: source.artifactId,
                  title: source.title,
                  provenanceLine: source.provenanceLine,
                  shareability: source.shareability,
                  shareabilityReason: source.shareabilityReason,
                });
                return (
                  <div className="cwf-knowledge-actions">
                    <button type="button" onClick={() => nav("knowledge")}>Open knowledge references</button>
                    <small>{interaction.boundary}</small>
                  </div>
                );
              })()}
            </div>
          ))}
        </div>
      ) : (
        <p className="muted-copy">No knowledge attached to this run — nothing retrieved automatically.</p>
      )}

      <h5>Verification</h5>
      <dl className="cwf-fields">
        <div><dt>Execution outcome</dt><dd>{verification.outcome}</dd></div>
        <div><dt>Generated artifacts</dt><dd>{verification.artifacts.length ? verification.artifacts.join(", ") : "Not recorded"}</dd></div>
        <div><dt>Validation evidence</dt><dd>{verification.validationEvidence}</dd></div>
        {verification.unresolvedIssues.length > 0 && (
          <div><dt>Unresolved issues</dt><dd className="monitor-error">{verification.unresolvedIssues.join("; ")}</dd></div>
        )}
      </dl>
      <h5>Project workflow history</h5>
      {historyRuns.length ? (
        (() => {
          const groups = workflowHistory(filteredHistory);
          return (
            <div className="cwf-history">
              <small className="muted-copy">{historyNote()}</small>
              <div className="cwf-history-filters">
                <select
                  value={historyFilter.projectId ?? ""}
                  aria-label="Filter history by project"
                  onChange={(event) => setHistoryFilter((current) => ({ ...current, projectId: event.target.value || null }))}
                >
                  <option value="">All projects</option>
                  {projectOptions.map((projectId) => <option key={projectId} value={projectId}>{projectId.slice(0, 12)}</option>)}
                </select>
                <select
                  value={historyFilter.status ?? ""}
                  aria-label="Filter history by status"
                  onChange={(event) => setHistoryFilter((current) => ({ ...current, status: event.target.value || null }))}
                >
                  <option value="">All statuses</option>
                  {statusOptions.map((status) => <option key={status} value={status}>{status}</option>)}
                </select>
                <input
                  type="date"
                  value={historyFilter.from ?? ""}
                  aria-label="Filter from recorded date"
                  onChange={(event) => setHistoryFilter((current) => ({ ...current, from: event.target.value || null }))}
                />
                <input
                  type="date"
                  value={historyFilter.to ?? ""}
                  aria-label="Filter to recorded date"
                  onChange={(event) => setHistoryFilter((current) => ({ ...current, to: event.target.value || null }))}
                />
                <small>{filteredHistory.length} of {historyRuns.length} shown — recorded timestamps only; no ranking</small>
              </div>
              {groups.map((group) => (
                <div key={group.projectId} className="cwf-history-group">
                  <strong>Project {group.projectId.slice(0, 12)} · {group.entries.length} workflow(s) · {group.failureCount} recorded failure(s)</strong>
                  {group.entries.map((entry) => (
                    <div key={entry.workflowId} className="cwf-history-row">
                      <code>{entry.workflowId.slice(0, 18)}…</code>
                      <span className={`status-pill ${entry.status === "succeeded" ? "status-ready" : entry.status === "failed" || entry.status === "cancelled" ? "status-error" : "status-muted"}`}>{entry.status}</span>
                      <small>{entry.createdAt ?? "no timestamp recorded"} · {entry.artifactsCreated} artifact(s){entry.recordedFailure ? ` · failure: ${entry.recordedFailure}` : ""}</small>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          );
        })()
      ) : (
        <p className="muted-copy">{EMPTY_NO_WORKFLOWS}</p>
      )}
      {review && (
        <div className="cwf-failure">
          <strong>Failure review — {review.failureStage}</strong>
          <p>{review.recordedError}</p>
          {review.relatedArtifacts.length > 0 && (
            <small>Related artifacts: {review.relatedArtifacts.join(", ")}</small>
          )}
          {showFailureDetails && (
            <div>
              {review.relatedLogs && <pre className="monitor-log">{review.relatedLogs}</pre>}
              <button type="button" className="secondary-button" onClick={() => nav("artifacts")}>Open artifacts view</button>
              {!review.relatedLogs && <small>No run log recorded — nothing more to show.</small>}
            </div>
          )}
          <button type="button" onClick={() => setShowFailureDetails((current) => !current)}>
            {showFailureDetails ? "Hide failure details" : review.headline}
          </button>
          <small className="muted-copy">No automatic diagnosis — the recorded error text is shown as-is.</small>
        </div>
      )}
      <p className="muted-copy cwf-note">No confidence scores, AI quality ratings, or subjective rankings — recorded evidence only.</p>
    </div>
  );
};
