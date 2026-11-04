import React from "react";
import {
  DecisionEvidenceRecord,
  formatDecisionTransparencyCard,
  formatEvidenceProvenanceViewer,
  getHumanControlPlaceholders,
} from "./decisionTransparencyModel";

export interface DecisionTransparencyPanelProps {
  decisionRecord?: DecisionEvidenceRecord | null;
}

export const DecisionTransparencyPanel: React.FC<DecisionTransparencyPanelProps> = ({
  decisionRecord,
}) => {
  const card = formatDecisionTransparencyCard(decisionRecord);
  const provenanceEntries = formatEvidenceProvenanceViewer(decisionRecord);
  const humanControls = getHumanControlPlaceholders();

  if (!card.hasRecord) {
    return (
      <div className="decision-transparency-card empty">
        <div className="card-header">
          <h3>Decision Transparency</h3>
          <span className="badge badge-neutral">No Records</span>
        </div>
        <div className="empty-state-notice">
          <p>{card.emptyPlaceholder}</p>
          <small>
            Decision evidence records will be populated when approved coordination proposals are
            converted into execution plans.
          </small>
        </div>
        <div className="future-controls-section">
          <h4>Future Human Control Actions (Disabled)</h4>
          <div className="controls-grid">
            {humanControls.map((ctrl) => (
              <div key={ctrl.id} className="control-item">
                <button type="button" className="btn btn-secondary" disabled>
                  {ctrl.label}
                </button>
                <span className="control-status">{ctrl.statusText}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="decision-transparency-card active">
      <div className="card-header">
        <div>
          <span className="section-kicker">Governance Audit</span>
          <h3>Decision Transparency</h3>
          <small className="decision-id-label">ID: {card.decisionId}</small>
        </div>
        <span className={`badge badge-${card.statusBadge.variant}`}>
          {card.statusBadge.label}
        </span>
      </div>

      <div className="card-body">
        <div className="detail-row">
          <strong>Approval Reference:</strong>
          <span>{card.approvalReference || "None recorded"}</span>
        </div>
        <div className="detail-row">
          <strong>Operator Override Available:</strong>
          <span>{card.overrideAvailable ? "Yes" : "No"}</span>
        </div>
        <div className="detail-row">
          <strong>Source Category:</strong>
          <span>{card.sourceCategory}</span>
        </div>
        <div className="detail-row">
          <strong>Timestamp:</strong>
          <span>{card.timestamp}</span>
        </div>

        <div className="section-block">
          <h4>Selected Resources</h4>
          {card.selectedResources.length === 0 ? (
            <p className="subtext">No resources explicitly assigned.</p>
          ) : (
            <ul className="resource-list">
              {card.selectedResources.map((res, i) => (
                <li key={i}>{res}</li>
              ))}
            </ul>
          )}
        </div>

        <div className="section-block">
          <h4>Evidence Provenance Viewer (Read-Only)</h4>
          {provenanceEntries.length === 0 ? (
            <p className="subtext">No evidence references recorded.</p>
          ) : (
            <div className="provenance-table-wrapper">
              <table className="provenance-table">
                <thead>
                  <tr>
                    <th>Evidence ID</th>
                    <th>Category</th>
                    <th>Linked Workflow</th>
                    <th>Artifacts</th>
                    <th>Timestamp</th>
                  </tr>
                </thead>
                <tbody>
                  {provenanceEntries.map((entry) => (
                    <tr key={entry.evidenceId}>
                      <td className="mono">{entry.evidenceId}</td>
                      <td>{entry.sourceCategory}</td>
                      <td className="mono">{entry.linkedWorkflowId}</td>
                      <td>{entry.linkedArtifactsCount}</td>
                      <td className="timestamp">{entry.timestamp}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="future-controls-section">
          <h4>Future Human Control Actions (Disabled)</h4>
          <div className="controls-grid">
            {humanControls.map((ctrl) => (
              <div key={ctrl.id} className="control-item">
                <button type="button" className="btn btn-secondary" disabled>
                  {ctrl.label}
                </button>
                <span className="control-status">{ctrl.statusText}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

