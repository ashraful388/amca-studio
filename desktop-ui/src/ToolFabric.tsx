import React, { useEffect, useState } from "react";
import { amca } from "./api";
import type {
  StudioProjectSummary,
  ToolCapability,
  ToolExecutionRecord,
  ToolTelemetry,
} from "./types";

interface ToolFabricProps {
  projects: StudioProjectSummary[];
  selectedProjectId: string | null;
  onSelectProject: (id: string) => void;
}

export const ToolFabric: React.FC<ToolFabricProps> = ({
  projects,
  selectedProjectId,
  onSelectProject,
}) => {
  const [activeTab, setActiveTab] = useState<"approvals" | "capabilities" | "policy" | "executions" | "tester">("approvals");
  const [capabilities, setCapabilities] = useState<ToolCapability[]>([]);
  const [, setPolicy] = useState<Record<string, string>>({});
  const [pendingApprovals, setPendingApprovals] = useState<any[]>([]);
  const [executions, setExecutions] = useState<ToolExecutionRecord[]>([]);
  const [telemetry, setTelemetry] = useState<ToolTelemetry | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  // Policy editing state
  const [editingPolicy, setEditingPolicy] = useState<Record<string, string>>({});
  const [policySaving, setPolicySaving] = useState<boolean>(false);

  // Test Runner state
  const [testToolId, setTestToolId] = useState<string>("math.evaluate");
  const [testOp, setTestOp] = useState<string>("evaluate");
  const [testArgs, setTestArgs] = useState<string>('{\n  "expression": "sqrt(144) * 3 + 6"\n}');
  const [testScope, setTestScope] = useState<string>("");
  const [testSubmitting, setTestSubmitting] = useState<boolean>(false);
  const [testResult, setTestResult] = useState<any | null>(null);

  const effectiveProjectId = selectedProjectId || (projects.length > 0 ? projects[0].project_id : "");

  const refreshData = async () => {
    if (!effectiveProjectId) return;
    setLoading(true);
    setError(null);
    try {
      const [capsRes, polRes, apprRes, execRes, telemRes] = await Promise.all([
        amca.getToolCapabilities(effectiveProjectId).catch(() => ({ capabilities: [] })),
        amca.getToolPolicy(effectiveProjectId).catch(() => ({ policy: {} })),
        amca.getPendingToolApprovals(effectiveProjectId).catch(() => ({ pending_approvals: [] })),
        amca.listToolExecutions(effectiveProjectId, { limit: 50 }).catch(() => ({ executions: [] })),
        amca.getToolTelemetry(effectiveProjectId).catch(() => ({ telemetry: null })),
      ]);

      setCapabilities(capsRes.capabilities);
      setPolicy(polRes.policy);
      setEditingPolicy(polRes.policy);
      setPendingApprovals(apprRes.pending_approvals);
      setExecutions(execRes.executions);
      if (telemRes.telemetry) setTelemetry(telemRes.telemetry);
    } catch (err: any) {
      setError(err?.message || "Failed to load Tool Fabric state");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshData();
  }, [effectiveProjectId]);

  const handleSavePolicy = async () => {
    if (!effectiveProjectId) return;
    setPolicySaving(true);
    setError(null);
    setActionSuccess(null);
    try {
      const updated = await amca.updateToolPolicy(effectiveProjectId, editingPolicy);
      setPolicy(updated.policy);
      setActionSuccess("Project tool policy updated successfully.");
      refreshData();
    } catch (err: any) {
      setError(err?.message || "Failed to update project policy");
    } finally {
      setPolicySaving(false);
    }
  };

  const handleApproveAndExecute = async (executionId: string) => {
    if (!effectiveProjectId) return;
    setError(null);
    setActionSuccess(null);
    try {
      await amca.approveToolRequest(effectiveProjectId, executionId, {
        actor: "lead-engineer",
        source_surface: "Tool Fabric Approval Center",
      });
      const execRes = await amca.executeToolRequest(effectiveProjectId, executionId);
      setActionSuccess(`Request approved and executed: ${execRes.status.toUpperCase()}`);
      refreshData();
    } catch (err: any) {
      setError(err?.message || "Failed to approve and execute request");
    }
  };

  const handleApproveOnly = async (executionId: string) => {
    if (!effectiveProjectId) return;
    setError(null);
    setActionSuccess(null);
    try {
      await amca.approveToolRequest(effectiveProjectId, executionId, {
        actor: "lead-engineer",
        source_surface: "Tool Fabric Approval Center",
      });
      setActionSuccess("Request approved and moved to AUTHORIZED status.");
      refreshData();
    } catch (err: any) {
      setError(err?.message || "Failed to approve request");
    }
  };

  const handleDeny = async (executionId: string) => {
    if (!effectiveProjectId) return;
    setError(null);
    setActionSuccess(null);
    try {
      await amca.denyToolRequest(effectiveProjectId, executionId, {
        actor: "lead-engineer",
        reason: "User denied from Tool Fabric UI",
      });
      setActionSuccess("Request DENIED successfully.");
      refreshData();
    } catch (err: any) {
      setError(err?.message || "Failed to deny request");
    }
  };

  const handleRunAuthorized = async (executionId: string) => {
    if (!effectiveProjectId) return;
    setError(null);
    setActionSuccess(null);
    try {
      const res = await amca.executeToolRequest(effectiveProjectId, executionId);
      setActionSuccess(`Execution completed: ${res.status.toUpperCase()}`);
      refreshData();
    } catch (err: any) {
      setError(err?.message || "Execution failed");
    }
  };

  const handleTestSubmit = async () => {
    if (!effectiveProjectId) return;
    setTestSubmitting(true);
    setError(null);
    setTestResult(null);
    try {
      let parsedArgs = {};
      try {
        parsedArgs = JSON.parse(testArgs);
      } catch {
        throw new Error("Invalid JSON arguments format");
      }
      const res = await amca.submitToolRequest(effectiveProjectId, {
        tool_id: testToolId,
        operation: testOp,
        arguments: parsedArgs,
        resource_scope: testScope.trim() || undefined,
        actor: "test-console",
      });
      setTestResult(res);
      refreshData();
    } catch (err: any) {
      setError(err?.message || "Failed to submit test tool request");
    } finally {
      setTestSubmitting(false);
    }
  };

  const getRiskBadge = (risk: string) => {
    switch (risk) {
      case "read_only":
        return <span className="px-2 py-0.5 rounded text-xs bg-emerald-900/50 text-emerald-300 border border-emerald-700">READ-ONLY</span>;
      case "compute_only":
        return <span className="px-2 py-0.5 rounded text-xs bg-blue-900/50 text-blue-300 border border-blue-700">COMPUTE-ONLY</span>;
      case "reversible_write":
        return <span className="px-2 py-0.5 rounded text-xs bg-amber-900/50 text-amber-300 border border-amber-700">REVERSIBLE WRITE</span>;
      case "irreversible_or_external":
        return <span className="px-2 py-0.5 rounded text-xs bg-orange-900/50 text-orange-300 border border-orange-700">IRREVERSIBLE / EXTERNAL</span>;
      case "sensitive":
        return <span className="px-2 py-0.5 rounded text-xs bg-red-900/50 text-red-300 border border-red-700">SENSITIVE</span>;
      default:
        return <span className="px-2 py-0.5 rounded text-xs bg-zinc-800 text-zinc-300">{risk}</span>;
    }
  };

  const getStatusBadge = (status: string, outcomeUncertain: number = 0) => {
    if (outcomeUncertain === 1 || status === "interrupted_after_start") {
      return <span className="px-2 py-0.5 rounded text-xs bg-amber-950 text-amber-300 border border-amber-600">⚠️ INTERRUPTED (UNCERTAIN)</span>;
    }
    switch (status) {
      case "succeeded":
        return <span className="px-2 py-0.5 rounded text-xs bg-emerald-950 text-emerald-400 border border-emerald-800">✅ SUCCEEDED</span>;
      case "failed":
        return <span className="px-2 py-0.5 rounded text-xs bg-red-950 text-red-400 border border-red-800">❌ FAILED</span>;
      case "authorized":
        return <span className="px-2 py-0.5 rounded text-xs bg-blue-950 text-blue-400 border border-blue-800">🛡️ AUTHORIZED</span>;
      case "requested":
        return <span className="px-2 py-0.5 rounded text-xs bg-yellow-950 text-yellow-400 border border-yellow-800">⏳ PENDING APPROVAL</span>;
      case "denied":
        return <span className="px-2 py-0.5 rounded text-xs bg-zinc-800 text-zinc-400 border border-zinc-700">🚫 DENIED</span>;
      case "running":
        return <span className="px-2 py-0.5 rounded text-xs bg-indigo-950 text-indigo-300 border border-indigo-700 animate-pulse">⚡ RUNNING</span>;
      default:
        return <span className="px-2 py-0.5 rounded text-xs bg-zinc-800 text-zinc-400">{status}</span>;
    }
  };

  return (
    <div className="flex flex-col h-full bg-[#12141a] text-zinc-100 overflow-hidden font-sans">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-zinc-800 bg-[#161922]">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-xl shadow-lg shadow-indigo-500/20">
            🛠️
          </div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-white flex items-center gap-2">
              Permissioned Tool Fabric{" "}
              <span className="text-xs font-normal px-2 py-0.5 bg-indigo-950/80 text-indigo-400 border border-indigo-800/60 rounded-full">
                AG-T1.2 Canonical
              </span>
            </h1>
            <p className="text-xs text-zinc-400">
              Capability Registry, Policy Enforcement & Cryptographically-Bound Approval Center
            </p>
          </div>
        </div>

        {/* Project Selector & Refresh */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-xs text-zinc-400">Project:</span>
            <select
              value={effectiveProjectId}
              onChange={(e) => onSelectProject(e.target.value)}
              className="bg-zinc-900 border border-zinc-700 rounded-md px-3 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-indigo-500"
            >
              {projects.map((p) => (
                <option key={p.project_id} value={p.project_id}>
                  {p.name} ({p.project_id})
                </option>
              ))}
            </select>
          </div>

          <button
            onClick={refreshData}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700 rounded-md transition-colors disabled:opacity-50"
          >
            🔄 Refresh
          </button>
        </div>
      </div>

      {/* Telemetry Strip */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3 px-6 py-3 border-b border-zinc-800/80 bg-[#13161f] text-xs">
        <div className="bg-zinc-900/80 p-2.5 rounded-lg border border-zinc-800/60">
          <span className="text-zinc-400 block text-[11px]">Total Requests</span>
          <span className="text-base font-semibold text-zinc-100">{telemetry?.requests_count ?? 0}</span>
        </div>
        <div className="bg-zinc-900/80 p-2.5 rounded-lg border border-zinc-800/60">
          <span className="text-emerald-400/80 block text-[11px]">Direct Allowed</span>
          <span className="text-base font-semibold text-emerald-300">{telemetry?.allowed_count ?? 0}</span>
        </div>
        <div className="bg-zinc-900/80 p-2.5 rounded-lg border border-zinc-800/60">
          <span className="text-yellow-400/80 block text-[11px]">Approval Required</span>
          <span className="text-base font-semibold text-yellow-300">{telemetry?.approval_required_count ?? 0}</span>
        </div>
        <div className="bg-zinc-900/80 p-2.5 rounded-lg border border-zinc-800/60">
          <span className="text-blue-400/80 block text-[11px]">Approved Consents</span>
          <span className="text-base font-semibold text-blue-300">{telemetry?.approved_count ?? 0}</span>
        </div>
        <div className="bg-zinc-900/80 p-2.5 rounded-lg border border-zinc-800/60">
          <span className="text-indigo-400/80 block text-[11px]">Succeeded / Executed</span>
          <span className="text-base font-semibold text-indigo-300">
            {telemetry?.succeeded_count ?? 0} / {telemetry?.executed_count ?? 0}
          </span>
        </div>
        <div className="bg-zinc-900/80 p-2.5 rounded-lg border border-zinc-800/60">
          <span className="text-amber-400/80 block text-[11px]">Interrupted / Uncertain</span>
          <span className="text-base font-semibold text-amber-300">{telemetry?.interrupted_count ?? 0}</span>
        </div>
      </div>

      {/* Notifications / Alerts */}
      {error && (
        <div className="mx-6 mt-3 px-4 py-2 bg-red-950/80 border border-red-800 text-red-200 text-xs rounded-md flex items-center justify-between">
          <span>⚠️ {error}</span>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-white">✕</button>
        </div>
      )}
      {actionSuccess && (
        <div className="mx-6 mt-3 px-4 py-2 bg-emerald-950/80 border border-emerald-800 text-emerald-200 text-xs rounded-md flex items-center justify-between">
          <span>✅ {actionSuccess}</span>
          <button onClick={() => setActionSuccess(null)} className="text-emerald-400 hover:text-white">✕</button>
        </div>
      )}

      {/* Navigation Tabs */}
      <div className="flex items-center gap-1 px-6 pt-3 border-b border-zinc-800 bg-[#141720]">
        <button
          onClick={() => setActiveTab("approvals")}
          className={`flex items-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-all ${
            activeTab === "approvals"
              ? "border-indigo-500 text-indigo-300 bg-indigo-950/20"
              : "border-transparent text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
          }`}
        >
          <span>⚡ Approval Center</span>
          {pendingApprovals.length > 0 && (
            <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-yellow-500/20 text-yellow-300 border border-yellow-600 font-semibold">
              {pendingApprovals.length}
            </span>
          )}
        </button>

        <button
          onClick={() => setActiveTab("capabilities")}
          className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-all ${
            activeTab === "capabilities"
              ? "border-indigo-500 text-indigo-300 bg-indigo-950/20"
              : "border-transparent text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
          }`}
        >
          🧰 Capability Registry ({capabilities.length})
        </button>

        <button
          onClick={() => setActiveTab("policy")}
          className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-all ${
            activeTab === "policy"
              ? "border-indigo-500 text-indigo-300 bg-indigo-950/20"
              : "border-transparent text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
          }`}
        >
          🛡️ Project Policy Rules
        </button>

        <button
          onClick={() => setActiveTab("executions")}
          className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-all ${
            activeTab === "executions"
              ? "border-indigo-500 text-indigo-300 bg-indigo-950/20"
              : "border-transparent text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
          }`}
        >
          📜 Execution Log & Receipts ({executions.length})
        </button>

        <button
          onClick={() => setActiveTab("tester")}
          className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-all ${
            activeTab === "tester"
              ? "border-indigo-500 text-indigo-300 bg-indigo-950/20"
              : "border-transparent text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
          }`}
        >
          🧪 Interactive Tester
        </button>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 overflow-y-auto p-6">
        {/* Tab 1: Approval Center */}
        {activeTab === "approvals" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-zinc-200">Pending Execution Consents</h2>
                <p className="text-xs text-zinc-400">
                  Every tool requiring explicit user consent is bound to an immutable SHA-256 request digest.
                </p>
              </div>
            </div>

            {pendingApprovals.length === 0 ? (
              <div className="p-8 text-center bg-zinc-900/40 border border-zinc-800/80 rounded-xl">
                <span className="text-2xl block mb-2">🎉</span>
                <span className="text-xs text-zinc-400">No pending tool approvals for this project. All clear!</span>
              </div>
            ) : (
              <div className="space-y-3">
                {pendingApprovals.map((req) => {
                  const cap = capabilities.find((c) => c.tool_id === req.tool_id);
                  return (
                    <div
                      key={req.execution_id}
                      className="p-4 bg-zinc-900/70 border border-zinc-800 rounded-xl hover:border-zinc-700 transition-all shadow-sm"
                    >
                      <div className="flex items-start justify-between gap-4 mb-3">
                        <div>
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-sm font-semibold text-white">{req.tool_id}</span>
                            <span className="text-xs px-2 py-0.5 bg-zinc-800 text-zinc-300 rounded font-mono">
                              op: {req.operation}
                            </span>
                            {cap && getRiskBadge(cap.risk_class)}
                            {getStatusBadge(req.status)}
                          </div>
                          <p className="text-xs text-zinc-400">
                            Requested by <span className="text-zinc-200 font-medium">{req.actor || "agent"}</span> at{" "}
                            {new Date(req.requested_at || Date.now()).toLocaleTimeString()}
                          </p>
                        </div>

                        {/* Action Buttons */}
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => handleApproveAndExecute(req.execution_id)}
                            className="px-3 py-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg transition-colors shadow-sm"
                          >
                            ✅ Approve & Run
                          </button>
                          <button
                            onClick={() => handleApproveOnly(req.execution_id)}
                            className="px-3 py-1.5 text-xs font-medium bg-blue-600/80 hover:bg-blue-500 text-white rounded-lg transition-colors"
                          >
                            🛡️ Approve Only
                          </button>
                          <button
                            onClick={() => handleDeny(req.execution_id)}
                            className="px-3 py-1.5 text-xs font-medium bg-red-900/60 hover:bg-red-800 text-red-200 rounded-lg transition-colors"
                          >
                            ❌ Deny
                          </button>
                        </div>
                      </div>

                      {/* Scope & Arguments */}
                      <div className="bg-zinc-950/80 rounded-lg p-3 border border-zinc-800/80 text-xs font-mono">
                        {req.requested_resource_scope && (
                          <div className="mb-2 pb-2 border-b border-zinc-800 text-zinc-300">
                            <span className="text-zinc-500">Resource Scope: </span>
                            {req.requested_resource_scope}
                          </div>
                        )}
                        <span className="text-zinc-500 block mb-1">Normalized Arguments:</span>
                        <pre className="text-indigo-300 overflow-x-auto whitespace-pre-wrap">
                          {JSON.stringify(req.normalized_arguments, null, 2)}
                        </pre>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Tab 2: Capability Registry */}
        {activeTab === "capabilities" && (
          <div className="space-y-4">
            <div>
              <h2 className="text-sm font-semibold text-zinc-200">Registered Tool Capabilities</h2>
              <p className="text-xs text-zinc-400">
                Truthful registry of bounded capabilities governed by AMCA Tool Fabric.
              </p>
            </div>

            <div className="border border-zinc-800 rounded-xl overflow-hidden bg-zinc-900/50">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="bg-zinc-900 border-b border-zinc-800 text-zinc-400">
                    <th className="py-3 px-4">Tool ID & Version</th>
                    <th className="py-3 px-4">Category</th>
                    <th className="py-3 px-4">Risk Class</th>
                    <th className="py-3 px-4">Side-Effect Class</th>
                    <th className="py-3 px-4">Operations</th>
                    <th className="py-3 px-4">Default Policy</th>
                    <th className="py-3 px-4">Project Policy</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/60">
                  {capabilities.map((c) => (
                    <tr key={c.tool_id} className="hover:bg-zinc-800/30 transition-colors">
                      <td className="py-3 px-4 font-mono font-medium text-white">
                        {c.tool_id} <span className="text-zinc-500 text-[11px]">v{c.version}</span>
                      </td>
                      <td className="py-3 px-4 text-zinc-300 capitalize">{c.category}</td>
                      <td className="py-3 px-4">{getRiskBadge(c.risk_class)}</td>
                      <td className="py-3 px-4 text-zinc-400 font-mono text-[11px]">{c.side_effect_class}</td>
                      <td className="py-3 px-4 text-zinc-300 font-mono">
                        {c.supported_operations.join(", ") || "*"}
                      </td>
                      <td className="py-3 px-4 text-zinc-400 font-semibold uppercase">{c.approval_policy}</td>
                      <td className="py-3 px-4">
                        <span className="px-2 py-0.5 rounded text-xs bg-indigo-950 text-indigo-300 border border-indigo-800 font-semibold uppercase">
                          {c.project_policy || c.approval_policy}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Tab 3: Project Policy Rules */}
        {activeTab === "policy" && (
          <div className="space-y-4 max-w-3xl">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-zinc-200">Project Tool Policy Rules</h2>
                <p className="text-xs text-zinc-400">
                  Override default policies for this specific project. Changes are strictly project-isolated.
                </p>
              </div>
              <button
                onClick={handleSavePolicy}
                disabled={policySaving}
                className="px-4 py-2 text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition-colors disabled:opacity-50"
              >
                {policySaving ? "Saving..." : "💾 Save Policy Overrides"}
              </button>
            </div>

            <div className="border border-zinc-800 rounded-xl overflow-hidden bg-zinc-900/50 p-4 space-y-3">
              {capabilities.map((c) => {
                const currentDecision = editingPolicy[c.tool_id] || c.approval_policy;
                return (
                  <div
                    key={c.tool_id}
                    className="flex items-center justify-between p-3 bg-zinc-950/60 border border-zinc-800/80 rounded-lg"
                  >
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono font-medium text-white">{c.tool_id}</span>
                        {getRiskBadge(c.risk_class)}
                      </div>
                      <p className="text-[11px] text-zinc-400 mt-0.5">{c.description}</p>
                    </div>

                    <div className="flex items-center gap-2">
                      <select
                        value={currentDecision}
                        onChange={(e) =>
                          setEditingPolicy({
                            ...editingPolicy,
                            [c.tool_id]: e.target.value,
                          })
                        }
                        className="bg-zinc-900 border border-zinc-700 rounded px-2.5 py-1 text-xs text-zinc-200 focus:outline-none focus:border-indigo-500"
                      >
                        <option value="allow">ALLOW (Direct Execution)</option>
                        <option value="require_approval">REQUIRE APPROVAL (Ask Consent)</option>
                        <option value="deny">DENY (Blocked)</option>
                      </select>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Tab 4: Execution Log & Receipts */}
        {activeTab === "executions" && (
          <div className="space-y-4">
            <div>
              <h2 className="text-sm font-semibold text-zinc-200">Execution Log & Verifiable Receipts</h2>
              <p className="text-xs text-zinc-400">
                Complete durable history of tool invocations, duration, outcome summaries, and provenance links.
              </p>
            </div>

            {executions.length === 0 ? (
              <div className="p-8 text-center bg-zinc-900/40 border border-zinc-800/80 rounded-xl">
                <span className="text-xs text-zinc-400">No execution history recorded for this project yet.</span>
              </div>
            ) : (
              <div className="border border-zinc-800 rounded-xl overflow-hidden bg-zinc-900/50">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="bg-zinc-900 border-b border-zinc-800 text-zinc-400">
                      <th className="py-3 px-4">Status</th>
                      <th className="py-3 px-4">Tool & Op</th>
                      <th className="py-3 px-4">Execution ID</th>
                      <th className="py-3 px-4">Duration</th>
                      <th className="py-3 px-4">Result Summary</th>
                      <th className="py-3 px-4">Completed At</th>
                      <th className="py-3 px-4">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/60">
                    {executions.map((e) => (
                      <tr key={e.execution_id} className="hover:bg-zinc-800/30 transition-colors">
                        <td className="py-3 px-4">{getStatusBadge(e.status, e.outcome_uncertain)}</td>
                        <td className="py-3 px-4 font-mono text-white">
                          {e.tool_id} <span className="text-zinc-500">({e.operation})</span>
                        </td>
                        <td className="py-3 px-4 font-mono text-[11px] text-zinc-400">{e.execution_id}</td>
                        <td className="py-3 px-4 text-zinc-300 font-mono">{e.duration_ms} ms</td>
                        <td className="py-3 px-4 text-zinc-300 max-w-xs truncate" title={e.result_summary || ""}>
                          {e.result_summary || "-"}
                        </td>
                        <td className="py-3 px-4 text-zinc-400 text-[11px]">
                          {e.completed_at ? new Date(e.completed_at).toLocaleTimeString() : "-"}
                        </td>
                        <td className="py-3 px-4">
                          {e.status === "authorized" && (
                            <button
                              onClick={() => handleRunAuthorized(e.execution_id)}
                              className="px-2.5 py-1 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded font-medium transition-colors"
                            >
                              ⚡ Execute
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Tab 5: Interactive Tester */}
        {activeTab === "tester" && (
          <div className="space-y-4 max-w-2xl">
            <div>
              <h2 className="text-sm font-semibold text-zinc-200">Interactive Tool Request Console</h2>
              <p className="text-xs text-zinc-400">
                Submit live tool requests to verify policy evaluation and exact CAS approval flows.
              </p>
            </div>

            <div className="border border-zinc-800 rounded-xl bg-zinc-900/50 p-4 space-y-3">
              <div>
                <label className="text-xs text-zinc-400 block mb-1">Target Capability:</label>
                <select
                  value={testToolId}
                  onChange={(e) => {
                    setTestToolId(e.target.value);
                    const cap = capabilities.find((c) => c.tool_id === e.target.value);
                    if (cap && cap.supported_operations.length > 0) {
                      setTestOp(cap.supported_operations[0]);
                    }
                  }}
                  className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-xs text-zinc-200 focus:outline-none focus:border-indigo-500"
                >
                  {capabilities.map((c) => (
                    <option key={c.tool_id} value={c.tool_id}>
                      {c.tool_id} ({c.risk_class})
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-zinc-400 block mb-1">Operation:</label>
                  <input
                    type="text"
                    value={testOp}
                    onChange={(e) => setTestOp(e.target.value)}
                    className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-1.5 text-xs text-zinc-200 font-mono"
                  />
                </div>
                <div>
                  <label className="text-xs text-zinc-400 block mb-1">Resource Scope (optional):</label>
                  <input
                    type="text"
                    value={testScope}
                    onChange={(e) => setTestScope(e.target.value)}
                    placeholder="e.g. workspace or exports/doc.md"
                    className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-1.5 text-xs text-zinc-200 font-mono"
                  />
                </div>
              </div>

              <div>
                <label className="text-xs text-zinc-400 block mb-1">Arguments (JSON):</label>
                <textarea
                  value={testArgs}
                  onChange={(e) => setTestArgs(e.target.value)}
                  rows={4}
                  className="w-full bg-zinc-950 border border-zinc-700 rounded-lg p-2.5 text-xs text-indigo-300 font-mono focus:outline-none focus:border-indigo-500"
                />
              </div>

              <button
                onClick={handleTestSubmit}
                disabled={testSubmitting}
                className="w-full py-2 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs rounded-lg transition-colors disabled:opacity-50"
              >
                {testSubmitting ? "Submitting Request..." : "🚀 Submit Tool Request"}
              </button>

              {testResult && (
                <div className="mt-4 p-3 bg-zinc-950 border border-zinc-800 rounded-lg text-xs font-mono">
                  <div className="flex items-center justify-between mb-2 pb-2 border-b border-zinc-800">
                    <span className="text-zinc-400 font-semibold">Response Receipt:</span>
                    {getStatusBadge(testResult.execution.status)}
                  </div>
                  <pre className="text-emerald-300 overflow-x-auto whitespace-pre-wrap">
                    {JSON.stringify(testResult, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
export default ToolFabric;
