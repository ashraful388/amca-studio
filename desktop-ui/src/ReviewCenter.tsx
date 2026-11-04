import { useCallback, useEffect, useMemo, useState } from "react";
import { amca } from "./api";
import { Icon } from "./icons";
import { REVIEW_CENTER_EXPLANATION } from "./feUxBatchModel";
import {
  REVIEW_BOUNDARY,
  NO_DETAIL,
  classifyReviewItems,
  reviewDetail,
  reviewNavigation,
  NAVIGATION_MUTATION_NOTE,
  type ReviewNavTarget,
} from "./reviewCenterWorkflowUxModel";
import {
  DECISION_PANEL_SECTIONS,
  CONTROL_PLACEHOLDERS,
  DECISION_RECORDS_UNAVAILABLE,
  DESIGNED_CONTROL_LABEL,
  INTEGRATION_NOTE,
} from "./decisionTransparencyUxPreparationModel";
import type {
  NotificationPreferences,
  ReviewItem,
  ReviewItemStatus,
  StudioProjectSummary,
} from "./types";

interface ReviewCenterProps {
  studioProjects?: StudioProjectSummary[];
  onOpenProject?: (projectId: string) => void;
  onOpenChat?: (conversationId: string) => void;
  /** Existing records for the workflow classification groups. */
  runs?: Array<{ run_id: string; status: string; project_id: string | null; created_at?: string | null; finished_at?: string | null; error?: string | null; result?: Record<string, unknown> | null }>;
  artifacts?: Array<{ id: string; title?: string }>;
  /** Navigation-only jumps supplied by the host app. */
  onNavigate?: (target: ReviewNavTarget) => void;
}

// D2: the first-visit note shows once per session (module-scoped), not per
// remount — a user who has seen it keeps a calm surface on later visits.
let reviewCenterVisitedThisSession = false;

export function ReviewCenter({
  studioProjects = [],
  onOpenProject,
  runs = [],
  artifacts = [],
  onNavigate,
}: ReviewCenterProps) {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [actionBusyId, setActionBusyId] = useState<string | null>(null);
  const [selectedItem, setSelectedItem] = useState<ReviewItem | null>(null);

  // Notification Preferences (B1.4C)
  const [notifPrefs, setNotifPrefs] = useState<NotificationPreferences | null>(null);
  const [showNotifSettings, setShowNotifSettings] = useState<boolean>(false);
  const [savingNotif, setSavingNotif] = useState<boolean>(false);

  // Filters
  const [statusFilter, setStatusFilter] = useState<"unread" | "all" | "dismissed">("unread");
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [severityFilter, setSeverityFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");

  const fetchNotifPrefs = useCallback(async () => {
    try {
      const res = await amca.getNotificationPreferences();
      setNotifPrefs(res.preferences);
    } catch {
      // Quiet
    }
  }, []);

  const handleToggleNotif = async (enabled: boolean) => {
    setSavingNotif(true);
    try {
      const res = await amca.updateNotificationPreferences({
        enabled,
        notify_attention: notifPrefs?.notify_attention ?? true,
        notify_info: notifPrefs?.notify_info ?? false,
      });
      setNotifPrefs(res.preferences);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSavingNotif(false);
    }
  };

  const handleUpdateNotifFilter = async (field: "notify_attention" | "notify_info", val: boolean) => {
    if (!notifPrefs) return;
    setSavingNotif(true);
    try {
      const res = await amca.updateNotificationPreferences({
        enabled: notifPrefs.enabled,
        notify_attention: field === "notify_attention" ? val : notifPrefs.notify_attention,
        notify_info: field === "notify_info" ? val : notifPrefs.notify_info,
      });
      setNotifPrefs(res.preferences);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSavingNotif(false);
    }
  };

  const fetchItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await amca.listReviewItems({
        limit: 100,
        project_id: selectedProjectId || undefined,
        unread_only: statusFilter === "unread",
      });
      setItems(response.review_items ?? (response as any).items ?? []);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught);
      setError(`Review Queue backend unavailable: ${message}`);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, selectedProjectId]);

  useEffect(() => {
    void fetchItems();
    void fetchNotifPrefs();
  }, [fetchItems, fetchNotifPrefs]);

  const handleMarkRead = async (item: ReviewItem) => {
    if (actionBusyId) return;
    setActionBusyId(item.id);
    try {
      await amca.markReviewItemRead(item.id);
      setItems((current) =>
        current.map((it) =>
          it.id === item.id ? { ...it, status: "read" as ReviewItemStatus } : it
        )
      );
      if (selectedItem?.id === item.id) {
        setSelectedItem((curr) => curr ? { ...curr, status: "read" as ReviewItemStatus } : null);
      }
      if (statusFilter === "unread") {
        setItems((current) => current.filter((it) => it.id !== item.id));
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setActionBusyId(null);
    }
  };

  const handleDismiss = async (item: ReviewItem) => {
    if (actionBusyId) return;
    setActionBusyId(item.id);
    try {
      await amca.dismissReviewItem(item.id);
      setItems((current) =>
        current.map((it) =>
          it.id === item.id ? { ...it, status: "dismissed" as ReviewItemStatus } : it
        )
      );
      if (selectedItem?.id === item.id) {
        setSelectedItem((curr) => curr ? { ...curr, status: "dismissed" as ReviewItemStatus } : null);
      }
      if (statusFilter === "unread") {
        setItems((current) => current.filter((it) => it.id !== item.id));
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setActionBusyId(null);
    }
  };

  const handleMarkAllVisibleRead = async () => {
    const unreadItems = filteredItems.filter((it) => it.status === "unread");
    if (!unreadItems.length || actionBusyId) return;
    setActionBusyId("bulk");
    try {
      await Promise.all(unreadItems.map((it) => amca.markReviewItemRead(it.id)));
      await fetchItems();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setActionBusyId(null);
    }
  };

  const filteredItems = useMemo(() => {
    return items.filter((item) => {
      if (statusFilter === "dismissed" && item.status !== "dismissed") return false;
      if (statusFilter === "all" && item.status === "dismissed") return false;
      if (severityFilter !== "all" && item.severity.toLowerCase() !== severityFilter.toLowerCase()) return false;
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const matchTitle = item.title.toLowerCase().includes(q);
        const matchSummary = item.summary.toLowerCase().includes(q);
        const matchProject = item.project_id.toLowerCase().includes(q);
        const matchSource = item.source_type.toLowerCase().includes(q);
        if (!matchTitle && !matchSummary && !matchProject && !matchSource) return false;
      }
      return true;
    });
  }, [items, statusFilter, severityFilter, searchQuery]);

  const unreadCount = useMemo(() => {
    return items.filter((it) => it.status === "unread").length;
  }, [items]);

  // v0.8.0 Review Center workflow UX: workflow-level classification groups
  // over existing records (approvals are already review items above; the
  // groups below add runs and artifacts). Record state only — no priority,
  // urgency, or severity inference.
  const workflowGroups = useMemo(() => {
    const approvalRecords = items
      .filter((item) => item.status === "unread")
      .map((item) => ({ id: item.id, status: item.status, created_at: item.created_at ?? null, action: item.title }));
    return classifyReviewItems({
      approvals: approvalRecords,
      runs: runs.map((run) => ({
        run_id: run.run_id,
        status: run.status,
        project_id: run.project_id ?? null,
        created_at: run.created_at ?? null,
        finished_at: run.finished_at ?? null,
        error: run.error ?? null,
        result: run.result ?? null,
      })),
      artifactIds: artifacts,
    });
  }, [items, runs, artifacts]);

  const [expandedGroups, setExpandedGroups] = useState<ReadonlySet<string>>(new Set());
  const toggleGroup = (group: string) => {
    setExpandedGroups((current) => {
      const next = new Set(current);
      if (next.has(group)) next.delete(group); else next.add(group);
      return next;
    });
  };

  const formatRelativeTime = (iso: string): string => {
    try {
      const date = new Date(iso);
      const diffMs = Date.now() - date.getTime();
      const diffSec = Math.max(0, Math.floor(diffMs / 1000));
      if (diffSec < 60) return `${diffSec}s ago`;
      const diffMin = Math.floor(diffSec / 60);
      if (diffMin < 60) return `${diffMin}m ago`;
      const diffHr = Math.floor(diffMin / 60);
      if (diffHr < 24) return `${diffHr}h ago`;
      return `${Math.floor(diffHr / 24)}d ago`;
    } catch {
      return iso;
    }
  };

  const getSeverityPillClass = (sev: string): string => {
    const s = sev.toLowerCase();
    if (s === "critical" || s === "attention" || s === "error") return "severity-critical";
    if (s === "warning") return "severity-warning";
    return "severity-info";
  };

  const getProjectName = (projectId: string): string => {
    if (!projectId) return "Global / Unassigned";
    const found = studioProjects.find((p) => p.project_id === projectId);
    return found ? found.name : projectId;
  };

  // D2: one-line purpose explanation on first visit per session.
  const [showFirstVisitNote] = useState(() => {
    const hadVisited = reviewCenterVisitedThisSession;
    reviewCenterVisitedThisSession = true;
    return !hadVisited;
  });

  return (
    <section className="workspace-view review-center-shell">
      <div className="view-heading">
        <div>
          <span className="section-kicker">Human attention & triage</span>
          <h1>Review Center</h1>
          <p>
            Durable outcomes, background automations, and items requiring attention.
            Review actions update triage status without deleting underlying run data.
          </p>
          {showFirstVisitNote && <p className="review-first-visit-note">{REVIEW_CENTER_EXPLANATION}</p>}
        </div>
        <div className="review-heading-actions">
          <button
            className={`secondary-button ${notifPrefs?.enabled ? "notif-active-button" : ""}`}
            onClick={() => setShowNotifSettings((curr) => !curr)}
            aria-label="Windows notification preferences"
            title="Configure Windows native notifications"
          >
            <Icon name="shield" /> Notifications: {notifPrefs?.enabled ? "ON" : "OFF"}
          </button>
          <button
            className="secondary-button"
            onClick={() => void fetchItems()}
            disabled={loading}
            aria-label="Refresh reviews"
          >
            <Icon name="refresh" className={loading ? "spinning" : ""} /> Refresh
          </button>
        </div>
      </div>

      {showNotifSettings && (
        <div className="review-notification-settings-panel">
          <div className="settings-panel-header">
            <div>
              <h3>Windows Native Notifications (Opt-in)</h3>
              <p>Receive native Windows toast notifications when background tasks complete or require attention.</p>
            </div>
            <button
              className="ghost-button"
              onClick={() => setShowNotifSettings(false)}
              aria-label="Close notification settings"
            >
              Close
            </button>
          </div>

          <div className="settings-panel-body">
            <div className="setting-toggle-row">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  checked={notifPrefs?.enabled ?? false}
                  disabled={savingNotif}
                  onChange={(e) => void handleToggleNotif(e.target.checked)}
                />
                <strong>Enable Native Windows Notifications</strong>
              </label>
              <span className="setting-subtext">
                {notifPrefs?.enabled
                  ? `Active (ignoring items before cutoff: ${notifPrefs.enabled_at ? new Date(notifPrefs.enabled_at).toLocaleTimeString() : "N/A"})`
                  : "Disabled by default. No historical notification storm when enabled."}
              </span>
            </div>

            {notifPrefs?.enabled && (
              <div className="notification-sub-options">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={notifPrefs.notify_attention}
                    disabled={savingNotif}
                    onChange={(e) => void handleUpdateNotifFilter("notify_attention", e.target.checked)}
                  />
                  <span>Notify for Attention & Critical items (default ON)</span>
                </label>
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={notifPrefs.notify_info}
                    disabled={savingNotif}
                    onChange={(e) => void handleUpdateNotifFilter("notify_info", e.target.checked)}
                  />
                  <span>Notify for Info & Regular completion items (default OFF)</span>
                </label>
              </div>
            )}

            <div className="privacy-guard-notice">
              <Icon name="shield" />
              <span>
                <strong>Privacy Guard:</strong> Notifications use bounded generic messages without credentials, secrets, file paths, or run transcripts.
              </span>
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="error-banner review-error-banner">
          <div>
            <strong>Service Degraded</strong>
            <span>{error}</span>
          </div>
          <button onClick={() => void fetchItems()}>Retry Connection</button>
        </div>
      )}

      {/* Stats row */}
      <div className="review-stats-grid">
        <div className="review-stat-card">
          <span className="stat-label">Pending Unread</span>
          <strong className="stat-value">{unreadCount}</strong>
          <small className="stat-detail">Awaiting human review</small>
        </div>
        <div className="review-stat-card">
          <span className="stat-label">Visible Filtered</span>
          <strong className="stat-value">{filteredItems.length}</strong>
          <small className="stat-detail">Matching current view</small>
        </div>
        <div className="review-stat-card">
          <span className="stat-label">Total Loaded</span>
          <strong className="stat-value">{items.length}</strong>
          <small className="stat-detail">In local queue buffer</small>
        </div>
      </div>

      {/* Filter toolbar */}
      <div className="review-filter-toolbar">
        <div className="filter-tabs" role="tablist">
          <button
            className={`filter-tab ${statusFilter === "unread" ? "active" : ""}`}
            onClick={() => setStatusFilter("unread")}
            role="tab"
            aria-selected={statusFilter === "unread"}
          >
            Unread {unreadCount > 0 && <span className="tab-badge">{unreadCount}</span>}
          </button>
          <button
            className={`filter-tab ${statusFilter === "all" ? "active" : ""}`}
            onClick={() => setStatusFilter("all")}
            role="tab"
            aria-selected={statusFilter === "all"}
          >
            All Active
          </button>
          <button
            className={`filter-tab ${statusFilter === "dismissed" ? "active" : ""}`}
            onClick={() => setStatusFilter("dismissed")}
            role="tab"
            aria-selected={statusFilter === "dismissed"}
          >
            Dismissed
          </button>
        </div>

        <div className="filter-controls">
          <select
            value={selectedProjectId}
            onChange={(e) => setSelectedProjectId(e.target.value)}
            aria-label="Filter by project"
            className="filter-select"
          >
            <option value="">All Projects</option>
            {studioProjects.map((proj) => (
              <option key={proj.project_id} value={proj.project_id}>
                {proj.name}
              </option>
            ))}
          </select>

          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
            aria-label="Filter by severity"
            className="filter-select"
          >
            <option value="all">All Severities</option>
            <option value="critical">Attention / Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>

          <input
            type="search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search reviews…"
            className="filter-search"
            aria-label="Search reviews"
          />

          {statusFilter === "unread" && unreadCount > 0 && (
            <button
              className="secondary-button"
              onClick={() => void handleMarkAllVisibleRead()}
              disabled={actionBusyId !== null}
            >
              Mark all as read
            </button>
          )}
        </div>
      </div>

      {/* Review items list */}
      <div className="review-content-layout">
        <div className="review-items-container">
          {loading && !items.length ? (
            <div className="review-loading-state">
              <Icon name="refresh" className="spinning" />
              <span>Loading Review Queue…</span>
            </div>
          ) : filteredItems.length === 0 ? (
            <div className="review-empty-state">
              <Icon name="logs" />
              <h3>No items in Review Center</h3>
              <p>
                {statusFilter === "unread"
                  ? "All background automations and outcomes are caught up."
                  : "No review items match the selected filter criteria."}
              </p>
            </div>
          ) : (
            <div className="review-cards-list">
              {filteredItems.map((item) => (
                <article
                  key={item.id}
                  className={`review-card status-${item.status} ${selectedItem?.id === item.id ? "selected" : ""}`}
                  onClick={() => setSelectedItem(item)}
                >
                  <div className="review-card-top">
                    <div className="review-card-badges">
                      <span className={`severity-tag ${getSeverityPillClass(item.severity)}`}>
                        {item.severity.toUpperCase()}
                      </span>
                      <span className="source-tag">{item.source_type}</span>
                      <span className="project-tag">{getProjectName(item.project_id)}</span>
                    </div>
                    <time dateTime={item.created_at} title={item.created_at}>
                      {formatRelativeTime(item.created_at)}
                    </time>
                  </div>

                  <div className="review-card-body">
                    <h3 className="review-card-title">{item.title}</h3>
                    {item.summary && (
                      <p className="review-card-summary">{item.summary}</p>
                    )}
                  </div>

                  <div className="review-card-footer" onClick={(e) => e.stopPropagation()}>
                    <div className="review-identifiers">
                      {item.run_id && <small>Run: <code>{item.run_id.slice(0, 8)}</code></small>}
                      {item.occurrence_id && <small>Occurrence: <code>{item.occurrence_id.slice(0, 8)}</code></small>}
                    </div>

                    <div className="review-card-actions">
                      {item.status === "unread" && (
                        <button
                          className="action-btn"
                          onClick={() => void handleMarkRead(item)}
                          disabled={actionBusyId === item.id}
                          title="Mark as Read"
                        >
                          Mark read
                        </button>
                      )}
                      {item.status !== "dismissed" && (
                        <button
                          className="action-btn dismiss"
                          onClick={() => void handleDismiss(item)}
                          disabled={actionBusyId === item.id}
                          title="Dismiss item"
                        >
                          Dismiss
                        </button>
                      )}
                      {item.project_id && onOpenProject && (
                        <button
                          className="action-btn project-link"
                          onClick={() => onOpenProject(item.project_id)}
                          title="Open associated Project"
                        >
                          Open Project
                        </button>
                      )}
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>

        {/* Item Detail Inspector Drawer */}
        {selectedItem && (
          <aside className="review-detail-drawer">
            <div className="drawer-header">
              <div>
                <span className="section-kicker">Review Inspector</span>
                <h2>Item Details</h2>
              </div>
              <button
                className="close-drawer-btn"
                onClick={() => setSelectedItem(null)}
                aria-label="Close details"
              >
                ✕
              </button>
            </div>

            <div className="drawer-body">
              <div className="drawer-section">
                <h3>{selectedItem.title}</h3>
                <div className="drawer-badges">
                  <span className={`severity-tag ${getSeverityPillClass(selectedItem.severity)}`}>
                    {selectedItem.severity}
                  </span>
                  <span className={`status-tag status-${selectedItem.status}`}>
                    {selectedItem.status}
                  </span>
                </div>
              </div>

              {selectedItem.summary && (
                <div className="drawer-section">
                  <span className="drawer-field-label">Summary</span>
                  <div className="drawer-summary-box">{selectedItem.summary}</div>
                </div>
              )}

              <div className="drawer-section">
                <span className="drawer-field-label">Metadata & Provenance</span>
                <dl className="drawer-dl">
                  <div>
                    <dt>Item ID</dt>
                    <dd><code>{selectedItem.id}</code></dd>
                  </div>
                  <div>
                    <dt>Source Key</dt>
                    <dd><code>{selectedItem.source_key}</code></dd>
                  </div>
                  <div>
                    <dt>Source Type</dt>
                    <dd>{selectedItem.source_type}</dd>
                  </div>
                  <div>
                    <dt>Project</dt>
                    <dd>{getProjectName(selectedItem.project_id)}</dd>
                  </div>
                  {selectedItem.run_id && (
                    <div>
                      <dt>Run ID</dt>
                      <dd><code>{selectedItem.run_id}</code></dd>
                    </div>
                  )}
                  {selectedItem.occurrence_id && (
                    <div>
                      <dt>Occurrence ID</dt>
                      <dd><code>{selectedItem.occurrence_id}</code></dd>
                    </div>
                  )}
                  <div>
                    <dt>Created At</dt>
                    <dd>{new Date(selectedItem.created_at).toLocaleString()}</dd>
                  </div>
                  <div>
                    <dt>Updated At</dt>
                    <dd>{new Date(selectedItem.updated_at).toLocaleString()}</dd>
                  </div>
                </dl>
              </div>

              <div className="drawer-notice">
                <Icon name="shield" />
                <p>
                  <strong>Non-Mutation Invariant:</strong> Dismissing or marking this review item
                  only updates local triage state. Underlying automation run artifacts and execution logs
                  remain permanent and immutable.
                </p>
              </div>

              <div className="drawer-actions">
                {selectedItem.status === "unread" && (
                  <button
                    className="primary-button"
                    onClick={() => void handleMarkRead(selectedItem)}
                    disabled={actionBusyId === selectedItem.id}
                  >
                    Mark as Read
                  </button>
                )}
                {selectedItem.status !== "dismissed" && (
                  <button
                    className="secondary-button"
                    onClick={() => void handleDismiss(selectedItem)}
                    disabled={actionBusyId === selectedItem.id}
                  >
                    Dismiss Item
                  </button>
                )}
              </div>
            </div>
          </aside>
        )}

        {/* v0.8.0 Review Center workflow UX: classification groups over
            existing runs/artifacts/approvals — record state only, no
            priority/urgency/severity inference, navigation-only links. */}
        <div className="decision-prep-panel">
          <h3>Future decision transparency (preparation)</h3>
          <p className="muted-copy">{DECISION_RECORDS_UNAVAILABLE}</p>
          <dl className="cwf-fields">
            {DECISION_PANEL_SECTIONS.map((section) => (
              <div key={section.sectionId}>
                <dt>{section.title}</dt>
                <dd>{DECISION_RECORDS_UNAVAILABLE}</dd>
              </div>
            ))}
          </dl>
          <div className="decision-control-placeholders">
            {CONTROL_PLACEHOLDERS.map((placeholder) => (
              <button key={placeholder.control} type="button" disabled title={DESIGNED_CONTROL_LABEL}>
                {placeholder.label}
              </button>
            ))}
            <small className="muted-copy">{DESIGNED_CONTROL_LABEL}</small>
          </div>
          <small className="muted-copy">{INTEGRATION_NOTE}</small>
        </div>
        <div className="review-workflow-groups">
          <h3>Workflow review groups</h3>
          <p className="muted-copy">{REVIEW_BOUNDARY}</p>
          {workflowGroups.length === 0 && (
            <p className="muted-copy">No workflow records available yet.</p>
          )}
          {workflowGroups.map((group) => {
            const open = expandedGroups.has(group.group);
            return (
              <details key={group.group} className="review-workflow-group" open={open} onToggle={(event) => {
                const details = event.currentTarget as HTMLDetailsElement;
                if (details.open) toggleGroup(group.group); else toggleGroup(group.group);
              }}>
                <summary>
                  <strong>{group.label}</strong>
                  <span>{group.items.length} item(s)</span>
                </summary>
                {group.items.map((record) => {
                  const detail = reviewDetail({
                    kind: record.kind,
                    id: record.id,
                    status: record.status,
                    projectId: record.projectId,
                    timestamp: record.timestamp,
                    artifactIds: record.artifactIds,
                    governanceRecorded: record.status === "succeeded" || record.status === "partial" ? true : undefined,
                  });
                  const links = reviewNavigation({ kind: record.kind, hasProject: Boolean(record.projectId), hasArtifacts: record.artifactIds.length > 0 });
                  return (
                    <div key={record.id} className="review-workflow-item">
                      <div className="review-workflow-head">
                        <strong>{record.id.slice(0, 14)}…</strong>
                        <span className="review-workflow-status">{record.status}</span>
                      </div>
                      <dl className="cwf-fields">
                        <div><dt>Source record</dt><dd>{detail.sourceRecord}</dd></div>
                        <div><dt>Timestamp</dt><dd>{detail.timestamp ?? NO_DETAIL}</dd></div>
                        <div><dt>Related project</dt><dd>{detail.relatedProject ?? NO_DETAIL}</dd></div>
                        <div><dt>Workflow ID</dt><dd>{detail.workflowId ?? NO_DETAIL}</dd></div>
                        <div><dt>Artifacts</dt><dd>{detail.artifacts.length ? detail.artifacts.join(", ") : NO_DETAIL}</dd></div>
                        <div><dt>Governance state</dt><dd>{detail.governanceState}</dd></div>
                        {detail.missing.length > 0 && <div><dt>Missing</dt><dd>{detail.missing.join(", ")}</dd></div>}
                      </dl>
                      <div className="review-workflow-links">
                        {links.map((link) => (
                          <button
                            key={link.label}
                            type="button"
                            disabled={!link.available || !onNavigate}
                            title={link.available ? `Navigate to ${link.target}` : link.unavailableReason ?? undefined}
                            onClick={() => link.target && onNavigate?.(link.target)}
                          >
                            {link.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </details>
            );
          })}
          <small className="muted-copy">{NAVIGATION_MUTATION_NOTE}</small>
        </div>
      </div>
    </section>
  );
}
