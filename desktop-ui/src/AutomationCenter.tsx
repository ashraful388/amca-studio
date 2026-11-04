import React, { useCallback, useEffect, useMemo, useState } from "react";
import { amca } from "./api";
import { Icon } from "./icons";
import type {
  StudioProjectSummary,
  StudioWorkflowDetail,
  StudioWorkflowSummary,
} from "./types";

interface AutomationCenterProps {
  studioProjects: StudioProjectSummary[];
  activeProjectId?: string | null;
  onSelectProject?: (projectId: string) => void;
  onOpenChat?: (conversationId: string) => void;
}

const WORKFLOW_TEMPLATES = [
  {
    id: "code_review",
    name: "Automated Code & PR Review",
    goal: "Review recent code diffs in the project workspace, verify boundary conditions, check linting standards, and summarize risk areas.",
    automation_type: "recurring",
    schedule: "0 9 * * 1-5",
    steps: ["fetch_git_diffs", "run_linter_checks", "generate_risk_summary"],
    mode: "solo",
    participant_ids: ["ollama:llama3.2"],
  },
  {
    id: "security_audit",
    name: "Security & Vulnerability Audit",
    goal: "Inspect project dependencies and codebase for known CVEs, unvetted API calls, and unprotected secret storage patterns.",
    automation_type: "once",
    schedule: "manual",
    steps: ["scan_dependencies", "analyze_secrets", "compile_audit_report"],
    mode: "joint",
    participant_ids: ["ollama:llama3.2"],
  },
  {
    id: "research_synthesis",
    name: "Research Literature & Evidence Digest",
    goal: "Synthesize latest findings on topic: {research_topic} from project research sessions and extract verified claims.",
    automation_type: "once",
    schedule: "manual",
    steps: ["query_research_corpus", "extract_claims", "synthesize_digest"],
    mode: "adaptive_hybrid",
    participant_ids: ["ollama:llama3.2"],
  },
  {
    id: "dataset_validation",
    name: "Dataset Quality & Schema Validation",
    goal: "Validate project dataset {dataset_path} against expected schema, check for null ratios and statistical anomalies.",
    automation_type: "conditional",
    schedule: "*/10 * * * *",
    condition: { version: 1, kind: "file_exists", path: "dataset.csv" },
    steps: ["inspect_file_schema", "compute_statistics", "report_anomalies"],
    mode: "solo",
    participant_ids: ["ollama:llama3.2"],
  },
];

export const AutomationCenter: React.FC<AutomationCenterProps> = ({
  studioProjects,
  activeProjectId,
  onSelectProject,
}) => {
  const [selectedProjectId, setSelectedProjectId] = useState<string>(
    activeProjectId || (studioProjects[0]?.project_id ?? "")
  );
  const [workflows, setWorkflows] = useState<StudioWorkflowSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Search & Filter
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [typeFilter, setTypeFilter] = useState<"all" | "once" | "recurring" | "conditional">("all");

  // Selected Workflow for Detail/History Drawer
  const [detailWorkflowId, setDetailWorkflowId] = useState<string | null>(null);
  const [detailWorkflow, setDetailWorkflow] = useState<StudioWorkflowDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState<boolean>(false);

  // Builder Modal State
  const [builderOpen, setBuilderOpen] = useState<boolean>(false);
  const [editingWorkflowId, setEditingWorkflowId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState<string>("");
  const [draftGoal, setDraftGoal] = useState<string>("");
  const [draftType, setDraftType] = useState<"once" | "recurring" | "conditional">("once");
  const [draftSchedule, setDraftSchedule] = useState<string>("manual");
  const [draftTemplate, setDraftTemplate] = useState<string>("custom");
  const [draftConditionPath, setDraftConditionPath] = useState<string>("schema.sql");
  const [savingBuilder, setSavingBuilder] = useState<boolean>(false);

  // Run Parameter Modal State
  const [runModalWorkflow, setRunModalWorkflow] = useState<StudioWorkflowSummary | null>(null);
  const [runParamInputs, setRunParamInputs] = useState<Record<string, string>>({});
  const [dispatchingRun, setDispatchingRun] = useState<boolean>(false);

  // Delete Confirmation Modal
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [deletingWorkflow, setDeletingWorkflow] = useState<boolean>(false);

  // Synchronize with external active project if changed
  useEffect(() => {
    if (activeProjectId && activeProjectId !== selectedProjectId) {
      setSelectedProjectId(activeProjectId);
    }
  }, [activeProjectId]);

  // Load workflows for current project
  const loadWorkflows = useCallback(async () => {
    if (!selectedProjectId) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await amca.studioWorkflows(selectedProjectId);
      setWorkflows(resp.workflows || []);
    } catch (err: any) {
      setError(err?.message || "Failed to load project workflows");
    } finally {
      setLoading(false);
    }
  }, [selectedProjectId]);

  useEffect(() => {
    loadWorkflows();
  }, [loadWorkflows]);

  // Load detail drawer when detailWorkflowId changes
  useEffect(() => {
    if (!detailWorkflowId || !selectedProjectId) {
      setDetailWorkflow(null);
      return;
    }
    let cancelled = false;
    const loadDetail = async () => {
      setLoadingDetail(true);
      try {
        const resp = await amca.studioWorkflow(detailWorkflowId, selectedProjectId);
        if (!cancelled) setDetailWorkflow(resp.workflow);
      } catch (err: any) {
        console.error("Failed to load workflow detail", err);
      } finally {
        if (!cancelled) setLoadingDetail(false);
      }
    };
    loadDetail();
    return () => {
      cancelled = true;
    };
  }, [detailWorkflowId, selectedProjectId]);

  // Extract placeholder variables from goal text, e.g. {var_name}
  const extractPlaceholders = (text: string): string[] => {
    const matches = text.match(/\{([a-zA-Z0-9_-]+)\}/g);
    if (!matches) return [];
    return Array.from(new Set(matches.map((m) => m.slice(1, -1))));
  };

  // Open Run Trigger Modal or execute directly
  const handleInitiateRun = (wf: StudioWorkflowSummary) => {
    const placeholders = extractPlaceholders(wf.goal);
    if (placeholders.length > 0) {
      const initialInputs: Record<string, string> = {};
      placeholders.forEach((p) => {
        initialInputs[p] = "";
      });
      setRunParamInputs(initialInputs);
      setRunModalWorkflow(wf);
    } else {
      executeWorkflow(wf.id, {});
    }
  };

  const executeWorkflow = async (workflowId: string, inputs: Record<string, string>) => {
    if (!selectedProjectId) return;
    setDispatchingRun(true);
    setError(null);
    try {
      const resp = await amca.runStudioWorkflow(workflowId, selectedProjectId, inputs);
      setNotice(`Workflow run dispatched successfully (Job: ${resp.run.run_id})`);
      setRunModalWorkflow(null);
      await loadWorkflows();
      if (detailWorkflowId === workflowId) {
        const updated = await amca.studioWorkflow(workflowId, selectedProjectId);
        setDetailWorkflow(updated.workflow);
      }
    } catch (err: any) {
      setError(err?.message || "Failed to dispatch workflow run");
    } finally {
      setDispatchingRun(false);
    }
  };

  // Duplicate Workflow
  const handleDuplicate = async (workflowId: string) => {
    if (!selectedProjectId) return;
    try {
      const resp = await amca.duplicateStudioWorkflow(workflowId, selectedProjectId);
      setNotice(`Duplicated workflow as "${resp.workflow.name}"`);
      await loadWorkflows();
    } catch (err: any) {
      setError(err?.message || "Failed to duplicate workflow");
    }
  };

  // Delete Workflow
  const handleDelete = async (workflowId: string) => {
    if (!selectedProjectId) return;
    setDeletingWorkflow(true);
    try {
      await amca.deleteStudioWorkflow(workflowId, selectedProjectId);
      setNotice("Workflow deleted successfully");
      setDeleteConfirmId(null);
      if (detailWorkflowId === workflowId) setDetailWorkflowId(null);
      await loadWorkflows();
    } catch (err: any) {
      setError(err?.message || "Failed to delete workflow");
    } finally {
      setDeletingWorkflow(false);
    }
  };

  // Open Builder for Creation or Editing
  const handleOpenCreateBuilder = (templateKey?: string) => {
    setEditingWorkflowId(null);
    if (templateKey && templateKey !== "custom") {
      const tmpl = WORKFLOW_TEMPLATES.find((t) => t.id === templateKey);
      if (tmpl) {
        setDraftName(tmpl.name);
        setDraftGoal(tmpl.goal);
        setDraftType(tmpl.automation_type as any);
        setDraftSchedule(tmpl.schedule);
        setDraftTemplate(tmpl.id);
        if (tmpl.condition) {
          setDraftConditionPath((tmpl.condition as any).path || "schema.sql");
        }
      }
    } else {
      setDraftName("");
      setDraftGoal("");
      setDraftType("once");
      setDraftSchedule("manual");
      setDraftTemplate("custom");
      setDraftConditionPath("schema.sql");
    }
    setBuilderOpen(true);
  };

  const handleOpenEditBuilder = (wf: StudioWorkflowSummary) => {
    setEditingWorkflowId(wf.id);
    setDraftName(wf.name);
    setDraftGoal(wf.goal);
    setDraftType(wf.automation_type);
    setDraftSchedule(wf.schedule);
    setDraftTemplate(String(wf.metadata?.template || "custom"));
    if (wf.condition && (wf.condition as any).path) {
      setDraftConditionPath((wf.condition as any).path);
    }
    setBuilderOpen(true);
  };

  const handleSaveWorkflow = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedProjectId || !draftName.trim() || !draftGoal.trim()) return;
    setSavingBuilder(true);
    setError(null);
    try {
      const payload: any = {
        project_id: selectedProjectId,
        name: draftName.trim(),
        goal: draftGoal.trim(),
        automation_type: draftType,
        schedule: draftType === "once" ? "manual" : draftSchedule.trim(),
        enabled: true,
        metadata: {
          template: draftTemplate,
          // v1.1.8 Phase 3: automations run through Auto Intelligence, which
          // owns the collaboration mode; nothing reads a stored override.
          mode: "auto",
          participant_ids: ["ollama:llama3.2"],
        },
      };
      if (editingWorkflowId) {
        payload.workflow_id = editingWorkflowId;
      }
      if (draftType === "conditional") {
        payload.condition = {
          version: 1,
          kind: "file_exists",
          path: draftConditionPath.trim() || "schema.sql",
        };
      }
      await amca.saveStudioWorkflow(payload);
      setNotice(editingWorkflowId ? "Workflow updated successfully" : "Workflow created successfully");
      setBuilderOpen(false);
      await loadWorkflows();
    } catch (err: any) {
      setError(err?.message || "Failed to save workflow");
    } finally {
      setSavingBuilder(false);
    }
  };

  // Filtered workflows
  const filteredWorkflows = useMemo(() => {
    return workflows.filter((wf) => {
      const matchesSearch =
        !searchQuery.trim() ||
        wf.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        wf.goal.toLowerCase().includes(searchQuery.toLowerCase()) ||
        String(wf.metadata?.template || "").toLowerCase().includes(searchQuery.toLowerCase());
      const matchesType = typeFilter === "all" || wf.automation_type === typeFilter;
      return matchesSearch && matchesType;
    });
  }, [workflows, searchQuery, typeFilter]);

  // Statistics
  const totalCount = workflows.length;
  const recurringCount = workflows.filter((w) => w.automation_type === "recurring").length;
  const totalRunsCount = workflows.reduce((acc, w) => acc + (w.execution_history_count || 0), 0);

  const getStatusBadge = (wf: StudioWorkflowSummary) => {
    const latest = wf.latest_occurrence;
    if (!latest) {
      return <span className="status-pill status-ready"><i />Ready</span>;
    }
    switch (latest.status) {
      case "running":
        return <span className="status-pill status-running"><i />Running</span>;
      case "succeeded":
        return <span className="status-pill status-ready"><i />Success</span>;
      case "failed":
        return <span className="status-pill status-error"><i />Failed</span>;
      default:
        return <span className="status-pill status-pending"><i />Claimed</span>;
    }
  };

  return (
    <section className="workspace-view automation-center">
      {/* Header View */}
      <div className="view-heading">
        <div>
          <span className="section-kicker">Autonomous Intelligence</span>
          <h1>Automation & Workflow Center</h1>
          <p>
            Define reusable multi-step automation pipelines, configure deterministic triggers, and dispatch
            auditable runs governed by AMCA runtime safety boundaries.
          </p>
        </div>
        <div className="view-actions">
          <button className="primary-button" onClick={() => handleOpenCreateBuilder()}>
            <Icon name="automation" /> + New Workflow
          </button>
        </div>
      </div>

      {/* Notifications and Alerts */}
      {error && (
        <div className="error-banner">
          <div>
            <strong>Action Failed</strong>
            <span>{error}</span>
          </div>
          <button onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}
      {notice && (
        <div className="success-banner" style={{ background: "rgba(34, 197, 94, 0.1)", border: "1px solid rgba(34, 197, 94, 0.3)", padding: "10px 14px", borderRadius: 8, marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>{notice}</span>
          <button className="secondary-button" style={{ padding: "4px 8px" }} onClick={() => setNotice(null)}>✕</button>
        </div>
      )}

      {/* Overview Stats Bar */}
      <div className="stats-row" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16, marginBottom: 20 }}>
        <article className="panel stat-card" style={{ padding: "14px 18px" }}>
          <span style={{ fontSize: 12, color: "var(--muted-foreground, #888)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Total Workflows</span>
          <h2 style={{ fontSize: 28, margin: "4px 0 0 0" }}>{totalCount}</h2>
        </article>
        <article className="panel stat-card" style={{ padding: "14px 18px" }}>
          <span style={{ fontSize: 12, color: "var(--muted-foreground, #888)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Recurring Schedules</span>
          <h2 style={{ fontSize: 28, margin: "4px 0 0 0" }}>{recurringCount}</h2>
        </article>
        <article className="panel stat-card" style={{ padding: "14px 18px" }}>
          <span style={{ fontSize: 12, color: "var(--muted-foreground, #888)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Recorded Executions</span>
          <h2 style={{ fontSize: 28, margin: "4px 0 0 0" }}>{totalRunsCount}</h2>
        </article>
        <article className="panel stat-card" style={{ padding: "14px 18px" }}>
          <span style={{ fontSize: 12, color: "var(--muted-foreground, #888)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Scheduler Status</span>
          <div style={{ marginTop: 8 }}>
            <span className="status-pill status-ready"><i />Durable In-Process</span>
          </div>
        </article>
      </div>

      {/* Project Selector & Search Controls */}
      <div className="panel" style={{ padding: 16, marginBottom: 20 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", gap: 12, alignItems: "center", flex: "1 1 300px" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 500 }}>
              <span>Project:</span>
              <select
                value={selectedProjectId}
                onChange={(e) => {
                  setSelectedProjectId(e.target.value);
                  if (onSelectProject) onSelectProject(e.target.value);
                }}
                style={{ padding: "6px 12px", borderRadius: 6, border: "1px solid var(--border-color, #444)", background: "var(--input-bg, #222)", color: "inherit" }}
              >
                {studioProjects.map((p) => (
                  <option key={p.project_id} value={p.project_id}>
                    {p.name} ({p.project_id})
                  </option>
                ))}
              </select>
            </label>

            <div style={{ position: "relative", flex: 1 }}>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search workflows by name, goal, or template..."
                style={{ width: "100%", padding: "6px 12px 6px 32px", borderRadius: 6, border: "1px solid var(--border-color, #444)", background: "var(--input-bg, #222)", color: "inherit" }}
              />
              <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "#888" }}>⌕</span>
            </div>
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            {(["all", "once", "recurring", "conditional"] as const).map((t) => (
              <button
                key={t}
                className={typeFilter === t ? "primary-button" : "secondary-button"}
                style={{ padding: "4px 10px", fontSize: 12, textTransform: "capitalize" }}
                onClick={() => setTypeFilter(t)}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Workflow Cards Grid */}
      {loading ? (
        <div style={{ padding: 40, textAlign: "center", color: "#888" }}>Loading project workflows...</div>
      ) : filteredWorkflows.length === 0 ? (
        <div className="panel empty-state" style={{ padding: 48, textAlign: "center" }}>
          <div className="empty-glyph" style={{ fontSize: 32, marginBottom: 12 }}><Icon name="automation" /></div>
          <h3>No Workflows Configured</h3>
          <p style={{ maxWidth: 480, margin: "8px auto 20px auto", color: "#888" }}>
            Create reusable multi-agent workflows with deterministic triggers, templated parameters, and tracked execution history.
          </p>
          <div style={{ display: "flex", gap: 10, justifyContent: "center", flexWrap: "wrap" }}>
            <button className="primary-button" onClick={() => handleOpenCreateBuilder()}>
              + Create Custom Workflow
            </button>
            {WORKFLOW_TEMPLATES.map((tmpl) => (
              <button
                key={tmpl.id}
                className="secondary-button"
                onClick={() => handleOpenCreateBuilder(tmpl.id)}
              >
                Use {tmpl.name}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: 16 }}>
          {filteredWorkflows.map((wf) => (
            <article
              key={wf.id}
              className="panel workflow-card"
              style={{
                padding: 18,
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                borderRadius: 8,
                border: "1px solid var(--border-color, #333)",
              }}
            >
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8, marginBottom: 8 }}>
                  <div>
                    <span style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em", color: "#888" }}>
                      {wf.automation_type} · {wf.schedule}
                    </span>
                    <h3 style={{ margin: "4px 0 6px 0", fontSize: 16 }}>{wf.name}</h3>
                  </div>
                  {getStatusBadge(wf)}
                </div>

                <p style={{ fontSize: 13, color: "var(--muted-foreground, #aaa)", margin: "0 0 12px 0", lineHeight: 1.4 }}>
                  {wf.goal.length > 130 ? `${wf.goal.slice(0, 130)}…` : wf.goal}
                </p>

                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 14 }}>
                  {wf.metadata?.template && (
                    <span style={{ fontSize: 11, background: "rgba(255,255,255,0.06)", padding: "2px 8px", borderRadius: 4 }}>
                      Template: {String(wf.metadata.template)}
                    </span>
                  )}
                  <span style={{ fontSize: 11, background: "rgba(255,255,255,0.06)", padding: "2px 8px", borderRadius: 4 }}>
                    Mode: {String(wf.metadata?.mode || "solo")}
                  </span>
                  {wf.execution_history_count !== undefined && (
                    <span style={{ fontSize: 11, background: "rgba(255,255,255,0.06)", padding: "2px 8px", borderRadius: 4 }}>
                      {wf.execution_history_count} Runs
                    </span>
                  )}
                </div>
              </div>

              <div style={{ borderTop: "1px solid var(--border-color, #333)", paddingTop: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", gap: 6 }}>
                  <button
                    className="primary-button"
                    style={{ padding: "4px 12px", fontSize: 12 }}
                    onClick={() => handleInitiateRun(wf)}
                    title="Dispatch Workflow Execution"
                  >
                    Run
                  </button>
                  <button
                    className="secondary-button"
                    style={{ padding: "4px 10px", fontSize: 12 }}
                    onClick={() => setDetailWorkflowId(wf.id)}
                    title="Inspect Occurrences & Details"
                  >
                    History
                  </button>
                </div>

                <div style={{ display: "flex", gap: 4 }}>
                  <button
                    className="icon-button"
                    style={{ padding: 4 }}
                    onClick={() => handleOpenEditBuilder(wf)}
                    title="Edit Workflow"
                  >
                    <Icon name="whiteboard" />
                  </button>
                  <button
                    className="icon-button"
                    style={{ padding: 4 }}
                    onClick={() => handleDuplicate(wf.id)}
                    title="Duplicate Workflow"
                  >
                    <Icon name="files" />
                  </button>
                  <button
                    className="icon-button danger"
                    style={{ padding: 4, color: "#ef4444" }}
                    onClick={() => setDeleteConfirmId(wf.id)}
                    title="Delete Workflow"
                  >
                    ✕
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}

      {/* Guided Workflow Builder Modal */}
      {builderOpen && (
        <div className="modal-backdrop" style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, padding: 20 }}>
          <div className="panel modal-dialog" style={{ width: "100%", maxWidth: 600, maxHeight: "90vh", overflowY: "auto", padding: 24, borderRadius: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <h2 style={{ margin: 0 }}>{editingWorkflowId ? "Edit Workflow Definition" : "Create New Workflow"}</h2>
              <button className="secondary-button" onClick={() => setBuilderOpen(false)}>✕</button>
            </div>

            <form onSubmit={handleSaveWorkflow} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <label>
                <span style={{ fontSize: 13, fontWeight: 500 }}>Workflow Name</span>
                <input
                  type="text"
                  required
                  value={draftName}
                  onChange={(e) => setDraftName(e.target.value)}
                  placeholder="e.g. Daily Security Dependency Audit"
                  style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border-color, #444)", background: "var(--input-bg, #222)", color: "inherit" }}
                />
              </label>

              <label>
                <span style={{ fontSize: 13, fontWeight: 500 }}>Objective / Goal Prompt (Supports &#123;param&#125; Placeholders)</span>
                <textarea
                  required
                  rows={4}
                  value={draftGoal}
                  onChange={(e) => setDraftGoal(e.target.value)}
                  placeholder="e.g. Review recent commits on branch {target_branch} and summarize changes..."
                  style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border-color, #444)", background: "var(--input-bg, #222)", color: "inherit", resize: "vertical" }}
                />
              </label>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <label>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>Automation Type</span>
                  <select
                    value={draftType}
                    onChange={(e) => setDraftType(e.target.value as any)}
                    style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border-color, #444)", background: "var(--input-bg, #222)", color: "inherit" }}
                  >
                    <option value="once">Once (Manual Dispatch)</option>
                    <option value="recurring">Recurring (Cron Schedule)</option>
                    <option value="conditional">Conditional (File/State Trigger)</option>
                  </select>
                </label>

                <label>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>Collaboration Mode</span>
                  {/* v1.1.8 Phase 3: automations always dispatch through Auto
                      Intelligence, which owns the collaboration mode.  The
                      select that used to sit here stored a value the run
                      never read, so it is reported as a fixed state. */}
                  <p style={{ margin: 0, fontSize: 12, color: "var(--muted, #999)" }}>
                    Auto · AMCA decides — each run's collaboration mode is chosen from the goal.
                  </p>
                </label>
              </div>

              {draftType === "recurring" && (
                <label>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>Cron Schedule Expression</span>
                  <input
                    type="text"
                    required
                    value={draftSchedule}
                    onChange={(e) => setDraftSchedule(e.target.value)}
                    placeholder="e.g. 0 9 * * 1-5"
                    style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border-color, #444)", background: "var(--input-bg, #222)", color: "inherit" }}
                  />
                  <small style={{ color: "#888", display: "block", marginTop: 4 }}>Standard 5-part cron syntax: minute hour day month day-of-week</small>
                </label>
              )}

              {draftType === "conditional" && (
                <label>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>Condition Trigger File Path</span>
                  <input
                    type="text"
                    required
                    value={draftConditionPath}
                    onChange={(e) => setDraftConditionPath(e.target.value)}
                    placeholder="e.g. schema.sql"
                    style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border-color, #444)", background: "var(--input-bg, #222)", color: "inherit" }}
                  />
                  <small style={{ color: "#888", display: "block", marginTop: 4 }}>Deterministic containment-verified path inside the project workspace.</small>
                </label>
              )}

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 10 }}>
                <button type="button" className="secondary-button" onClick={() => setBuilderOpen(false)}>
                  Cancel
                </button>
                <button type="submit" className="primary-button" disabled={savingBuilder}>
                  {savingBuilder ? "Saving..." : editingWorkflowId ? "Update Workflow" : "Create Workflow"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Parameter Input Run Modal */}
      {runModalWorkflow && (
        <div className="modal-backdrop" style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, padding: 20 }}>
          <div className="panel modal-dialog" style={{ width: "100%", maxWidth: 500, padding: 24, borderRadius: 10 }}>
            <h3 style={{ margin: "0 0 8px 0" }}>Dispatch Workflow: {runModalWorkflow.name}</h3>
            <p style={{ fontSize: 13, color: "#888", margin: "0 0 16px 0" }}>
              Provide parameter values for placeholder variables defined in this workflow.
            </p>

            <form
              onSubmit={(e) => {
                e.preventDefault();
                executeWorkflow(runModalWorkflow.id, runParamInputs);
              }}
              style={{ display: "flex", flexDirection: "column", gap: 12 }}
            >
              {Object.keys(runParamInputs).map((param) => (
                <label key={param}>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>{param}</span>
                  <input
                    type="text"
                    required
                    value={runParamInputs[param]}
                    onChange={(e) =>
                      setRunParamInputs((prev) => ({
                        ...prev,
                        [param]: e.target.value,
                      }))
                    }
                    placeholder={`Value for {${param}}`}
                    style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border-color, #444)", background: "var(--input-bg, #222)", color: "inherit" }}
                  />
                </label>
              ))}

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 10 }}>
                <button type="button" className="secondary-button" onClick={() => setRunModalWorkflow(null)}>
                  Cancel
                </button>
                <button type="submit" className="primary-button" disabled={dispatchingRun}>
                  {dispatchingRun ? "Dispatching..." : "Execute Run"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deleteConfirmId && (
        <div className="modal-backdrop" style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, padding: 20 }}>
          <div className="panel modal-dialog" style={{ width: "100%", maxWidth: 420, padding: 24, borderRadius: 10 }}>
            <h3 style={{ margin: "0 0 8px 0" }}>Delete Workflow</h3>
            <p style={{ fontSize: 13, color: "#888", margin: "0 0 16px 0" }}>
              Are you sure you want to delete this workflow? This action cannot be undone.
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button className="secondary-button" onClick={() => setDeleteConfirmId(null)} disabled={deletingWorkflow}>
                Cancel
              </button>
              <button className="primary-button danger" onClick={() => handleDelete(deleteConfirmId)} disabled={deletingWorkflow}>
                {deletingWorkflow ? "Deleting..." : "Confirm Delete"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Execution History & Detail Drawer */}
      {detailWorkflowId && (
        <div className="modal-backdrop" style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", display: "flex", alignItems: "center", justifyContent: "flex-end", zIndex: 1000 }}>
          <div className="panel detail-drawer" style={{ width: "100%", maxWidth: 580, height: "100%", overflowY: "auto", padding: 24, borderLeft: "1px solid var(--border-color, #444)", display: "flex", flexDirection: "column" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <div>
                <span style={{ fontSize: 11, textTransform: "uppercase", color: "#888" }}>Workflow Detail & History</span>
                <h2 style={{ margin: "2px 0 0 0", fontSize: 20 }}>{detailWorkflow?.name || "Loading..."}</h2>
              </div>
              <button className="secondary-button" onClick={() => setDetailWorkflowId(null)}>✕</button>
            </div>

            {loadingDetail || !detailWorkflow ? (
              <p style={{ color: "#888", textAlign: "center", padding: 30 }}>Loading occurrences and history...</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
                {/* Definition Specs */}
                <article className="panel" style={{ padding: 14 }}>
                  <h4 style={{ margin: "0 0 8px 0" }}>Configuration</h4>
                  <dl className="detail-list" style={{ margin: 0 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0" }}>
                      <dt style={{ color: "#888" }}>Type</dt>
                      <dd style={{ margin: 0 }}>{detailWorkflow.automation_type}</dd>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0" }}>
                      <dt style={{ color: "#888" }}>Schedule</dt>
                      <dd style={{ margin: 0 }}>{detailWorkflow.schedule}</dd>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0" }}>
                      <dt style={{ color: "#888" }}>Revision</dt>
                      <dd style={{ margin: 0 }}>v{detailWorkflow.revision}</dd>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0" }}>
                      <dt style={{ color: "#888" }}>Last Run ID</dt>
                      <dd style={{ margin: 0 }}><code>{detailWorkflow.last_run_id || "Never executed"}</code></dd>
                    </div>
                  </dl>
                </article>

                {/* Execution Occurrences History */}
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                    <h4 style={{ margin: 0 }}>Execution Occurrences ({detailWorkflow.occurrences?.length || 0})</h4>
                    <button className="primary-button" style={{ padding: "4px 10px", fontSize: 12 }} onClick={() => handleInitiateRun(detailWorkflow)}>
                      + Run Now
                    </button>
                  </div>

                  {!detailWorkflow.occurrences || detailWorkflow.occurrences.length === 0 ? (
                    <p style={{ color: "#888", fontSize: 13 }}>No execution occurrences recorded yet.</p>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                      {detailWorkflow.occurrences.map((occ) => (
                        <div
                          key={occ.id}
                          style={{
                            padding: 12,
                            borderRadius: 6,
                            background: "rgba(255,255,255,0.03)",
                            border: "1px solid var(--border-color, #333)",
                            fontSize: 13,
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                            <span style={{ fontWeight: 600 }}>{occ.scheduled_for}</span>
                            <span className={`status-pill status-${occ.status === "succeeded" ? "ready" : occ.status === "failed" ? "error" : "running"}`}>
                              <i />
                              {occ.status}
                            </span>
                          </div>
                          {occ.run_id && (
                            <div style={{ color: "#888", fontSize: 12 }}>
                              Run ID: <code>{occ.run_id}</code>
                            </div>
                          )}
                          {occ.error && (
                            <div style={{ color: "#ef4444", fontSize: 12, marginTop: 4 }}>
                              Error: {occ.error}
                            </div>
                          )}
                          {occ.check && (
                            <div style={{ fontSize: 11, color: "#888", marginTop: 4 }}>
                              Condition evidence: {JSON.stringify(occ.check)}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
};

