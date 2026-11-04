import React, { useEffect, useState } from "react";
import { amca } from "./api";
import type {
  EngineeringWorkspace,
  RepositoryStatus,
  FileTreeResponse,
  FileTreeEntry,
  FileInspectResponse,
  WorkspaceSearchResponse,
  EngineeringDiff,
  ApplyRecord,
  RunnerProfile,
  ExecutionPlan,
  CommandResult,
  TestRun,
  BuildResult,
  GitWorkspaceState,
  GitBranch,
  GitWorktree,
  CommitPreparation,
  GitCommitResult,
  EngineeringWorkflowPipeline,
} from "./types";

interface Props {
  projectId?: string;
}

export const EngineeringWorkspaceView: React.FC<Props> = ({ projectId = "default" }) => {
  const [workspaces, setWorkspaces] = useState<EngineeringWorkspace[]>([]);
  const [selectedWorkspace, setSelectedWorkspace] = useState<EngineeringWorkspace | null>(null);
  const [repoStatus, setRepoStatus] = useState<RepositoryStatus | null>(null);
  const [fileTree, setFileTree] = useState<FileTreeResponse | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<FileInspectResponse | null>(null);
  const [workingTreeDiff, setWorkingTreeDiff] = useState<EngineeringDiff | null>(null);
  const [proposedDiffs, setProposedDiffs] = useState<Array<{ diff_id: string; state: string; total_additions: number; total_deletions: number; created_at: string }>>([]);
  const [selectedDiff, setSelectedDiff] = useState<EngineeringDiff | null>(null);
  const [applies, setApplies] = useState<ApplyRecord[]>([]);
  const [interruptedApplies, setInterruptedApplies] = useState<ApplyRecord[]>([]);

  // E1.3: Runners and Execution state
  const [runners, setRunners] = useState<RunnerProfile[]>([]);
  const [executionPlans, setExecutionPlans] = useState<ExecutionPlan[]>([]);
  const [commandResults, setCommandResults] = useState<CommandResult[]>([]);
  const [testRuns, setTestRuns] = useState<TestRun[]>([]);
  const [buildResults, setBuildResults] = useState<BuildResult[]>([]);
  const [activePlanModal, setActivePlanModal] = useState<ExecutionPlan | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [activeRunningPlanId, setActiveRunningPlanId] = useState<string | null>(null);
  const [selectedResult, setSelectedResult] = useState<CommandResult | null>(null);
  const [runningElapsed, setRunningElapsed] = useState(0);

  // E1.4: Git, Branch, Worktree & Safe Commit State
  const [gitState, setGitState] = useState<GitWorkspaceState | null>(null);
  const [branches, setBranches] = useState<GitBranch[]>([]);
  const [worktrees, setWorktrees] = useState<GitWorktree[]>([]);
  const [stagedDiff, setStagedDiff] = useState<EngineeringDiff | null>(null);
  const [stagedDigest, setStagedDigest] = useState<string>("");
  const [commitMessage, setCommitMessage] = useState<string>("");
  const [commitHistory, setCommitHistory] = useState<GitCommitResult[]>([]);
  const [activePrepModal, setActivePrepModal] = useState<CommitPreparation | null>(null);
  const [newBranchModalOpen, setNewBranchModalOpen] = useState(false);
  const [newBranchName, setNewBranchName] = useState("");
  const [newWorktreeModalOpen, setNewWorktreeModalOpen] = useState(false);
  const [newWorktreeBranch, setNewWorktreeBranch] = useState("");

  // E1.5: Integrated Workflow Pipeline State
  const [pipelines, setPipelines] = useState<EngineeringWorkflowPipeline[]>([]);
  const [activePipeline, setActivePipeline] = useState<EngineeringWorkflowPipeline | null>(null);
  const [newPipelineTitle, setNewPipelineTitle] = useState("");
  const [newPipelineModalOpen, setNewPipelineModalOpen] = useState(false);



  // Modals & confirmation
  const [applyModalDiff, setApplyModalDiff] = useState<EngineeringDiff | null>(null);
  const [rollbackModalApply, setRollbackModalApply] = useState<ApplyRecord | null>(null);
  const [showOpenModal, setShowOpenModal] = useState(false);
  const [newRootPath, setNewRootPath] = useState("");
  const [newName, setNewName] = useState("");

  // Search
  const [searchQuery, setSearchQuery] = useState("");
  const [searchRegex, setSearchRegex] = useState(false);
  const [searchResults, setSearchResults] = useState<WorkspaceSearchResponse | null>(null);

  const [activeTab, setActiveTab] = useState<"viewer" | "diff" | "proposed" | "history" | "runner" | "git" | "pipeline" | "search">("viewer");
  const [loading, setLoading] = useState(false);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({});


  useEffect(() => {
    let interval: any = null;
    if (isRunning) {
      interval = setInterval(() => {
        setRunningElapsed((prev) => +(prev + 0.1).toFixed(1));
      }, 100);
    } else {
      setRunningElapsed(0);
    }
    return () => clearInterval(interval);
  }, [isRunning]);

  useEffect(() => {
    loadWorkspaces();
  }, [projectId]);

  useEffect(() => {
    if (selectedWorkspace) {
      loadWorkspaceDetails(selectedWorkspace.workspace_id);
    }
  }, [selectedWorkspace?.workspace_id]);

  const loadWorkspaces = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await amca.listEngineeringWorkspaces(projectId);
      setWorkspaces(res.workspaces);
      if (res.workspaces.length > 0 && !selectedWorkspace) {
        setSelectedWorkspace(res.workspaces[0]);
      }
    } catch (err: any) {
      setError(err?.message || "Failed to load engineering workspaces");
    } finally {
      setLoading(false);
    }
  };

  const loadWorkspaceDetails = async (wsId: string) => {
    try {
      setLoading(true);
      setError(null);
      const [repoRes, treeRes, diffRes, proposedRes, appliesRes, interruptedRes, runnersRes, plansRes, execsRes, testRunsRes, buildsRes, gitStateRes, branchesRes, worktreesRes, stagedDiffRes, commitsRes, pipelinesRes] = await Promise.all([
        amca.getEngineeringRepositoryStatus(projectId, wsId).catch(() => ({ repository: null })),
        amca.getEngineeringFileTree(projectId, wsId, "", 4, 500),
        amca.getEngineeringWorkingTreeDiff(projectId, wsId).catch(() => ({ diff: null })),
        amca.listEngineeringDiffs(projectId, wsId).catch(() => ({ diffs: [] })),
        amca.listEngineeringApplies(projectId, wsId).catch(() => ({ applies: [] })),
        amca.detectInterruptedEngineeringApplies(projectId, wsId).catch(() => ({ interrupted: [] })),
        amca.discoverEngineeringRunners(projectId, wsId).catch(() => ({ runners: [] })),
        amca.listEngineeringExecutionPlans(projectId, wsId).catch(() => ({ plans: [] })),
        amca.listEngineeringCommandResults(projectId, wsId, 50).catch(() => ({ executions: [] })),
        amca.listEngineeringTestRuns(projectId, wsId).catch(() => ({ test_runs: [] })),
        amca.listEngineeringBuildResults(projectId, wsId).catch(() => ({ builds: [] })),
        amca.getEngineeringGitState(projectId, wsId).catch(() => ({ git_state: null })),
        amca.listEngineeringBranches(projectId, wsId).catch(() => ({ branches: [] })),
        amca.listEngineeringWorktrees(projectId, wsId).catch(() => ({ worktrees: [] })),
        amca.getEngineeringStagedDiff(projectId, wsId).catch(() => ({ diff: null, staged_diff_digest: "" })),
        amca.listEngineeringCommitResults(projectId, wsId, 50).catch(() => ({ commits: [] })),
        amca.listEngineeringPipelines(projectId, wsId, 50).catch(() => ({ pipelines: [] })),
      ]);

      if (repoRes && repoRes.repository) {
        setRepoStatus(repoRes.repository);
      }
      setFileTree(treeRes.tree);
      if (diffRes && diffRes.diff) {
        setWorkingTreeDiff(diffRes.diff);
      }
      setProposedDiffs(proposedRes.diffs || []);
      setApplies(appliesRes.applies || []);
      setInterruptedApplies(interruptedRes.interrupted || []);
      setRunners(runnersRes.runners || []);
      setExecutionPlans(plansRes.plans || []);
      setCommandResults(execsRes.executions || []);
      setTestRuns(testRunsRes.test_runs || []);
      setBuildResults(buildsRes.builds || []);
      setGitState(gitStateRes.git_state);
      setBranches(branchesRes.branches || []);
      setWorktrees(worktreesRes.worktrees || []);
      setStagedDiff(stagedDiffRes.diff);
      setStagedDigest(stagedDiffRes.staged_diff_digest || "");
      setCommitHistory(commitsRes.commits || []);
      const loadedPipes = pipelinesRes.pipelines || [];
      setPipelines(loadedPipes);
      if (loadedPipes.length > 0 && !activePipeline) {
        setActivePipeline(loadedPipes[0]);
      }
    } catch (err: any) {
      setError(err?.message || "Failed to load workspace details");
    } finally {
      setLoading(false);
    }
  };

  const handleOpenFile = async (path: string) => {
    if (!selectedWorkspace) return;
    try {
      setError(null);
      setSelectedFile(path);
      setActiveTab("viewer");
      const res = await amca.readEngineeringFile(projectId, selectedWorkspace.workspace_id, path, 1, 1000);
      setFileContent(res.file);
    } catch (err: any) {
      setError(err?.message || `Failed to read file ${path}`);
      setFileContent(null);
    }
  };

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedWorkspace || !searchQuery.trim()) return;
    try {
      setLoading(true);
      setError(null);
      const res = await amca.searchEngineeringWorkspace(projectId, selectedWorkspace.workspace_id, searchQuery, searchRegex);
      setSearchResults(res.search);
      setActiveTab("search");
    } catch (err: any) {
      setError(err?.message || "Search failed");
    } finally {
      setLoading(false);
    }
  };

  const handleOpenWorkspace = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setLoading(true);
      setError(null);
      const res = await amca.createOrOpenEngineeringWorkspace(projectId, {
        root_path: newRootPath.trim() || undefined,
        name: newName.trim() || undefined,
      });
      setShowOpenModal(false);
      setNewRootPath("");
      setNewName("");
      await loadWorkspaces();
      setSelectedWorkspace(res.workspace);
    } catch (err: any) {
      setError(err?.message || "Failed to create/open workspace");
    } finally {
      setLoading(false);
    }
  };

  const handleViewProposedDiff = async (diffId: string) => {
    if (!selectedWorkspace) return;
    try {
      setLoading(true);
      const res = await amca.getEngineeringDiff(projectId, selectedWorkspace.workspace_id, diffId);
      setSelectedDiff(res.diff);
    } catch (err: any) {
      setError(err?.message || "Failed to load diff");
    } finally {
      setLoading(false);
    }
  };


  const handlePreparePlanFromRunner = async (profile: RunnerProfile) => {
    if (!selectedWorkspace) return;
    try {
      setLoading(true);
      setError(null);
      const res = await amca.createEngineeringExecutionPlan(projectId, selectedWorkspace.workspace_id, {
        kind: profile.kind,
        runner_profile: profile.runner_id,
        executable: profile.executable,
        arguments: profile.default_args,
        working_directory: ".",
        timeout_seconds: profile.kind === "TEST" ? 120 : profile.kind === "BUILD" ? 300 : 60,
      });
      setActivePlanModal(res.plan);
      const plansRes = await amca.listEngineeringExecutionPlans(projectId, selectedWorkspace.workspace_id);
      setExecutionPlans(plansRes.plans || []);
    } catch (err: any) {
      setError(err?.message || "Failed to create execution plan");
    } finally {
      setLoading(false);
    }
  };

  const handleExecutePlan = async (plan: ExecutionPlan) => {
    if (!selectedWorkspace) return;
    try {
      setIsRunning(true);
      setActiveRunningPlanId(plan.execution_plan_id);
      setError(null);
      setActionSuccess(null);
      const res = await amca.runEngineeringExecutionPlan(projectId, selectedWorkspace.workspace_id, plan.execution_plan_id);
      setActivePlanModal(null);
      setSelectedResult(res.execution);
      setActionSuccess(`Command execution completed with status: ${res.execution.status}`);
      // Refresh lists
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Execution failed");
    } finally {
      setIsRunning(false);
      setActiveRunningPlanId(null);
    }
  };


  const handleCreateBranch = async () => {
    if (!selectedWorkspace || !newBranchName.trim()) return;
    try {
      setLoading(true);
      setError(null);
      await amca.createEngineeringBranch(projectId, selectedWorkspace.workspace_id, {
        branch_name: newBranchName.trim(),
      });
      setActionSuccess(`Branch '${newBranchName.trim()}' created successfully.`);
      setNewBranchName("");
      setNewBranchModalOpen(false);
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Failed to create branch");
    } finally {
      setLoading(false);
    }
  };

  const handleSwitchBranch = async (branchName: string) => {
    if (!selectedWorkspace) return;
    try {
      setLoading(true);
      setError(null);
      await amca.switchEngineeringBranch(projectId, selectedWorkspace.workspace_id, {
        branch_name: branchName,
      });
      setActionSuccess(`Switched to branch '${branchName}'.`);
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Failed to switch branch");
    } finally {
      setLoading(false);
    }
  };

  const handleCreateWorktree = async () => {
    if (!selectedWorkspace || !newWorktreeBranch.trim()) return;
    try {
      setLoading(true);
      setError(null);
      const res = await amca.createEngineeringWorktree(projectId, selectedWorkspace.workspace_id, {
        branch_name: newWorktreeBranch.trim(),
      });
      setActionSuccess(`Isolated AMCA worktree created at: ${res.worktree.path}`);
      setNewWorktreeBranch("");
      setNewWorktreeModalOpen(false);
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Failed to create worktree");
    } finally {
      setLoading(false);
    }
  };

  const handleRemoveWorktree = async (wtId: string) => {
    if (!selectedWorkspace) return;
    try {
      setLoading(true);
      setError(null);
      await amca.removeEngineeringWorktree(projectId, selectedWorkspace.workspace_id, wtId);
      setActionSuccess("Worktree removed successfully.");
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Failed to remove worktree");
    } finally {
      setLoading(false);
    }
  };

  const handleStageFiles = async (paths: string[]) => {
    if (!selectedWorkspace || paths.length === 0) return;
    try {
      setLoading(true);
      setError(null);
      await amca.stageEngineeringPaths(projectId, selectedWorkspace.workspace_id, paths);
      setActionSuccess(`Staged ${paths.length} file(s).`);
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Failed to stage files");
    } finally {
      setLoading(false);
    }
  };

  const handleUnstageFiles = async (paths: string[]) => {
    if (!selectedWorkspace || paths.length === 0) return;
    try {
      setLoading(true);
      setError(null);
      await amca.unstageEngineeringPaths(projectId, selectedWorkspace.workspace_id, paths);
      setActionSuccess(`Unstaged ${paths.length} file(s).`);
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Failed to unstage files");
    } finally {
      setLoading(false);
    }
  };

  const handlePrepareCommit = async () => {
    if (!selectedWorkspace || !commitMessage.trim()) return;
    try {
      setLoading(true);
      setError(null);
      const testIds = testRuns.filter((t) => t.status === 'passed' || (t.status as any) === 'PASSED').map((t) => t.test_run_id);
      const res = await amca.prepareEngineeringCommit(projectId, selectedWorkspace.workspace_id, {
        message: commitMessage.trim(),
        test_run_ids: testIds,
      });
      setActivePrepModal(res.preparation);
    } catch (err: any) {
      setError(err?.message || "Failed to prepare commit");
    } finally {
      setLoading(false);
    }
  };


  const handleStartPipeline = async () => {
    if (!selectedWorkspace) return;
    try {
      setLoading(true);
      setError(null);
      const res = await amca.createEngineeringPipeline(projectId, selectedWorkspace.workspace_id, {
        title: newPipelineTitle.trim() || undefined,
        diff_id: selectedDiff?.diff_id || undefined,
      });
      setActivePipeline(res.pipeline);
      setNewPipelineTitle("");
      setNewPipelineModalOpen(false);
      setActionSuccess(`Started engineering workflow pipeline '${res.pipeline.title}'.`);
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Failed to start pipeline");
    } finally {
      setLoading(false);
    }
  };

  const handleApplyPipelineDiff = async () => {
    if (!selectedWorkspace || !activePipeline) return;
    try {
      setLoading(true);
      setError(null);
      const res = await amca.applyEngineeringPipelineDiff(projectId, selectedWorkspace.workspace_id, activePipeline.pipeline_id);
      setActivePipeline(res.pipeline);
      setActionSuccess("Pipeline diff applied successfully.");
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Failed to apply pipeline diff");
    } finally {
      setLoading(false);
    }
  };

  const handleStagePipelineChanges = async () => {
    if (!selectedWorkspace || !activePipeline) return;
    try {
      setLoading(true);
      setError(null);
      const res = await amca.stageEngineeringPipelineChanges(projectId, selectedWorkspace.workspace_id, activePipeline.pipeline_id, {});
      setActivePipeline(res.pipeline);
      setActionSuccess(`Staged ${res.pipeline.staged_paths.length} file(s) in pipeline.`);
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Failed to stage changes in pipeline");
    } finally {
      setLoading(false);
    }
  };

  const handleCommitPipeline = async () => {
    if (!selectedWorkspace || !activePipeline || !commitMessage.trim()) return;
    try {
      setLoading(true);
      setError(null);
      const res = await amca.commitEngineeringPipeline(projectId, selectedWorkspace.workspace_id, activePipeline.pipeline_id, {
        message: commitMessage.trim(),
      });
      setActivePipeline(res.pipeline);
      setCommitMessage("");
      setActionSuccess(`Pipeline committed successfully at SHA: ${res.pipeline.commit_sha?.slice(0, 8)}!`);
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Failed to commit pipeline");
    } finally {
      setLoading(false);
    }
  };

  const handleRollbackPipeline = async () => {
    if (!selectedWorkspace || !activePipeline) return;
    try {
      setLoading(true);
      setError(null);
      const res = await amca.rollbackEngineeringPipeline(projectId, selectedWorkspace.workspace_id, activePipeline.pipeline_id);
      setActivePipeline(res.pipeline);
      setActionSuccess("Pipeline changes rolled back cleanly.");
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Failed to rollback pipeline");
    } finally {
      setLoading(false);
    }
  };

  const handleExecuteCommit = async (prepId: string) => {
    if (!selectedWorkspace) return;
    try {
      setLoading(true);
      setError(null);
      const res = await amca.executeEngineeringCommit(projectId, selectedWorkspace.workspace_id, prepId);
      setActivePrepModal(null);
      setCommitMessage("");
      setActionSuccess(`Commit ${res.commit.new_commit_sha.slice(0, 8)} executed successfully!`);
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Failed to execute commit");
    } finally {
      setLoading(false);
    }
  };

  const handleCancelExecution = async () => {
    if (!selectedWorkspace || !activeRunningPlanId) return;
    try {
      await amca.cancelEngineeringExecution(projectId, selectedWorkspace.workspace_id, activeRunningPlanId);
      setActionSuccess("Execution cancellation requested.");
    } catch (err: any) {
      setError(err?.message || "Failed to cancel execution");
    }
  };

  const handleApplyDiff = async () => {
    if (!selectedWorkspace || !applyModalDiff) return;
    try {
      setLoading(true);
      setError(null);
      const res = await amca.applyEngineeringDiff(projectId, selectedWorkspace.workspace_id, applyModalDiff.diff_id, {
        confirm_apply: true,
        provenance: { actor: "user_ui" },
      });
      setApplyModalDiff(null);
      setActionSuccess(`Diff ${applyModalDiff.diff_id} successfully applied to working tree! (Apply ID: ${res.apply.apply_id})`);
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
      setActiveTab("history");
    } catch (err: any) {
      setError(err?.message || "Failed to apply diff");
    } finally {
      setLoading(false);
    }
  };

  const handleRejectDiff = async (diffId: string) => {
    if (!selectedWorkspace) return;
    try {
      setLoading(true);
      setError(null);
      await amca.rejectEngineeringDiff(projectId, selectedWorkspace.workspace_id, diffId);
      setActionSuccess(`Diff ${diffId} marked as REJECTED.`);
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Failed to reject diff");
    } finally {
      setLoading(false);
    }
  };

  const handleRollbackApply = async () => {
    if (!selectedWorkspace || !rollbackModalApply) return;
    try {
      setLoading(true);
      setError(null);
      await amca.rollbackEngineeringApply(projectId, selectedWorkspace.workspace_id, rollbackModalApply.apply_id, {
        provenance: { actor: "user_ui" },
      });
      setRollbackModalApply(null);
      setActionSuccess(`Apply ${rollbackModalApply.apply_id} successfully rolled back to pre-patch state!`);
      await loadWorkspaceDetails(selectedWorkspace.workspace_id);
    } catch (err: any) {
      setError(err?.message || "Rollback failed");
    } finally {
      setLoading(false);
    }
  };

  const toggleFolder = (path: string) => {
    setExpandedFolders((prev) => ({ ...prev, [path]: !prev[path] }));
  };

  const renderTreeEntries = (entries: FileTreeEntry[], level = 0) => {
    return (
      <div style={{ paddingLeft: level > 0 ? 14 : 0 }}>
        {entries.map((entry) => {
          if (entry.is_dir) {
            const isExpanded = expandedFolders[entry.path] ?? (level < 1);
            return (
              <div key={entry.path}>
                <div
                  onClick={() => toggleFolder(entry.path)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    padding: "4px 6px",
                    cursor: "pointer",
                    borderRadius: 4,
                    fontSize: 13,
                    fontWeight: 500,
                    color: "#cbd5e1",
                    userSelect: "none",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "#1e293b")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  <span style={{ marginRight: 6, fontSize: 11 }}>{isExpanded ? "▼" : "▶"}</span>
                  <span style={{ marginRight: 6 }}>📁</span>
                  <span>{entry.name}</span>
                </div>
                {isExpanded && entry.children && renderTreeEntries(entry.children, level + 1)}
              </div>
            );
          } else {
            const isSelected = selectedFile === entry.path;
            return (
              <div
                key={entry.path}
                onClick={() => handleOpenFile(entry.path)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "4px 6px",
                  cursor: "pointer",
                  borderRadius: 4,
                  fontSize: 13,
                  color: isSelected ? "#38bdf8" : "#94a3b8",
                  background: isSelected ? "#0f172a" : "transparent",
                  borderLeft: isSelected ? "2px solid #38bdf8" : "2px solid transparent",
                }}
                onMouseEnter={(e) => {
                  if (!isSelected) e.currentTarget.style.background = "#1e293b";
                }}
                onMouseLeave={(e) => {
                  if (!isSelected) e.currentTarget.style.background = "transparent";
                }}
              >
                <div style={{ display: "flex", alignItems: "center", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  <span style={{ marginRight: 6 }}>📄</span>
                  <span>{entry.name}</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  {entry.sensitive && (
                    <span
                      style={{
                        fontSize: 10,
                        padding: "1px 4px",
                        borderRadius: 3,
                        background: "#7f1d1d",
                        color: "#fca5a5",
                        fontWeight: 600,
                      }}
                    >
                      RESTRICTED
                    </span>
                  )}
                  <span style={{ fontSize: 11, color: "#64748b" }}>
                    {entry.size > 1024 ? `${(entry.size / 1024).toFixed(1)}k` : `${entry.size}b`}
                  </span>
                </div>
              </div>
            );
          }
        })}
      </div>
    );
  };

  const renderDiffViewer = (diff: EngineeringDiff | null, _isWorkingTree = true) => {
    if (!diff || !diff.files || diff.files.length === 0) {
      return (
        <div style={{ padding: 32, textAlign: "center", color: "#64748b" }}>
          <div style={{ fontSize: 32, marginBottom: 8 }}>✨</div>
          <div>No changes detected in working tree (clean state).</div>
        </div>
      );
    }

    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            background: "#0f172a",
            padding: "10px 14px",
            borderRadius: 6,
            border: "1px solid #1e293b",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span
              style={{
                fontSize: 12,
                fontWeight: 700,
                padding: "2px 8px",
                borderRadius: 4,
                background:
                  diff.state === "applied"
                    ? "#1e3a8a"
                    : diff.state === "proposed"
                    ? "#065f46"
                    : "#7f1d1d",
                color:
                  diff.state === "applied"
                    ? "#93c5fd"
                    : diff.state === "proposed"
                    ? "#6ee7b7"
                    : "#fca5a5",
                textTransform: "uppercase",
              }}
            >
              {diff.state}
            </span>
            <span style={{ fontSize: 13, color: "#94a3b8" }}>
              Base: <code style={{ color: "#cbd5e1" }}>{diff.base_revision}</code>
            </span>
            <span style={{ fontSize: 13, color: "#94a3b8" }}>
              Files changed: <strong style={{ color: "#f8fafc" }}>{diff.files.length}</strong>
            </span>
          </div>
          <div style={{ display: "flex", gap: 12, fontSize: 13 }}>
            <span style={{ color: "#4ade80", fontWeight: 600 }}>+{diff.total_additions}</span>
            <span style={{ color: "#f87171", fontWeight: 600 }}>-{diff.total_deletions}</span>
          </div>
        </div>

        {diff.files.map((file, fileIdx) => (
          <div
            key={fileIdx}
            style={{
              background: "#0f172a",
              border: "1px solid #1e293b",
              borderRadius: 6,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "8px 12px",
                background: "#1e293b",
                borderBottom: "1px solid #334155",
                fontSize: 13,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    padding: "2px 6px",
                    borderRadius: 3,
                    background:
                      file.change_kind === "added"
                        ? "#065f46"
                        : file.change_kind === "deleted"
                        ? "#7f1d1d"
                        : "#854d0e",
                    color:
                      file.change_kind === "added"
                        ? "#6ee7b7"
                        : file.change_kind === "deleted"
                        ? "#fca5a5"
                        : "#fde047",
                    textTransform: "uppercase",
                  }}
                >
                  {file.change_kind}
                </span>
                <span style={{ fontWeight: 600, color: "#f8fafc" }}>{file.path}</span>
                {file.old_path && (
                  <span style={{ color: "#64748b", fontSize: 12 }}>
                    (renamed from {file.old_path})
                  </span>
                )}
              </div>
              <div style={{ display: "flex", gap: 8, fontSize: 12 }}>
                <span style={{ color: "#4ade80" }}>+{file.additions}</span>
                <span style={{ color: "#f87171" }}>-{file.deletions}</span>
              </div>
            </div>

            {file.binary ? (
              <div style={{ padding: 16, color: "#94a3b8", fontStyle: "italic", fontSize: 13 }}>
                Binary file difference not shown.
              </div>
            ) : file.hunks.length === 0 ? (
              <div style={{ padding: 16, color: "#64748b", fontSize: 13 }}>
                Empty diff hunks.
              </div>
            ) : (
              <div style={{ fontFamily: "monospace", fontSize: 12, overflowX: "auto" }}>
                {file.hunks.map((hunk, hunkIdx) => (
                  <div key={hunkIdx}>
                    <div
                      style={{
                        background: "#091e3a",
                        color: "#38bdf8",
                        padding: "3px 12px",
                        borderTop: hunkIdx > 0 ? "1px solid #1e293b" : "none",
                        borderBottom: "1px solid #1e293b",
                        fontSize: 11,
                      }}
                    >
                      @@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines} @@ {hunk.header}
                    </div>
                    {hunk.lines.map((line, lineIdx) => {
                      const isAdd = line.startsWith("+");
                      const isDel = line.startsWith("-");
                      return (
                        <div
                          key={lineIdx}
                          style={{
                            padding: "1px 12px",
                            background: isAdd
                              ? "rgba(34, 197, 94, 0.12)"
                              : isDel
                              ? "rgba(239, 68, 68, 0.12)"
                              : "transparent",
                            color: isAdd ? "#4ade80" : isDel ? "#f87171" : "#cbd5e1",
                            whiteSpace: "pre",
                          }}
                        >
                          {line}
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "#090d16", color: "#f8fafc" }}>
      {/* Header bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 20px",
          background: "#0f172a",
          borderBottom: "1px solid #1e293b",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span style={{ fontSize: 20 }}>🛠️</span>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 16, fontWeight: 700 }}>Engineering Workspace</span>
              {selectedWorkspace && (
                <span
                  style={{
                    fontSize: 11,
                    padding: "2px 8px",
                    borderRadius: 4,
                    background: selectedWorkspace.repository_kind === "git" ? "#1e3a8a" : "#334155",
                    color: selectedWorkspace.repository_kind === "git" ? "#93c5fd" : "#cbd5e1",
                    fontWeight: 600,
                    textTransform: "uppercase",
                  }}
                >
                  {selectedWorkspace.repository_kind === "git" ? "Git Repository" : "Filesystem Workspace"}
                </span>
              )}
            </div>
            {selectedWorkspace && (
              <div style={{ fontSize: 12, color: "#64748b", marginTop: 2 }}>
                {selectedWorkspace.root_path}
              </div>
            )}
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {workspaces.length > 1 && (
            <select
              value={selectedWorkspace?.workspace_id || ""}
              onChange={(e) => {
                const ws = workspaces.find((w) => w.workspace_id === e.target.value);
                if (ws) setSelectedWorkspace(ws);
              }}
              style={{
                background: "#1e293b",
                color: "#f8fafc",
                border: "1px solid #334155",
                borderRadius: 6,
                padding: "6px 10px",
                fontSize: 13,
              }}
            >
              {workspaces.map((w) => (
                <option key={w.workspace_id} value={w.workspace_id}>
                  {w.name} ({w.workspace_id.slice(-6)})
                </option>
              ))}
            </select>
          )}

          <button
            onClick={() => setShowOpenModal(true)}
            style={{
              background: "#2563eb",
              color: "#fff",
              border: "none",
              borderRadius: 6,
              padding: "6px 12px",
              fontSize: 13,
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            + Open Workspace
          </button>

          <button
            onClick={() => selectedWorkspace && loadWorkspaceDetails(selectedWorkspace.workspace_id)}
            style={{
              background: "#1e293b",
              color: "#94a3b8",
              border: "1px solid #334155",
              borderRadius: 6,
              padding: "6px 12px",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Safety & Stage Banner */}
      <div
        style={{
          background: "linear-gradient(90deg, #1e1b4b 0%, #172554 100%)",
          borderBottom: "1px solid #312e81",
          padding: "8px 20px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ background: "#4338ca", color: "#e0e7ff", padding: "1px 6px", borderRadius: 3, fontWeight: 700 }}>
            E1.2 REVIEW-FIRST MUTATION ACTIVE
          </span>
          <span style={{ color: "#c7d2fe" }}>
            Staged patch validation, conflict protection, and crash-safe rollback active. Applied changes modify working tree (NOT Git committed).
          </span>
        </div>
        {repoStatus && repoStatus.repository_kind === "git" && (
          <div style={{ display: "flex", gap: 12, color: "#93c5fd" }}>
            <span>
              Branch: <strong>{repoStatus.current_branch || "HEAD"}</strong>
            </span>
            {repoStatus.head_commit && (
              <span>
                HEAD: <code>{repoStatus.head_commit.slice(0, 7)}</code>
              </span>
            )}
            <span>
              Status: <strong>{repoStatus.is_clean ? "Clean" : "Modified"}</strong>
            </span>
          </div>
        )}
      </div>

      {interruptedApplies.length > 0 && (
        <div style={{ background: "#854d0e", color: "#fef08a", padding: "10px 20px", fontSize: 13, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span>⚠️ {interruptedApplies.length} interrupted patch application(s) detected across restart. Recovery required.</span>
          <button
            onClick={() => setActiveTab("history")}
            style={{ background: "#ca8a04", color: "#000", border: "none", borderRadius: 4, padding: "4px 10px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}
          >
            Inspect Interrupted
          </button>
        </div>
      )}

      {actionSuccess && (
        <div style={{ background: "#065f46", color: "#6ee7b7", padding: "8px 20px", fontSize: 13, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span>{actionSuccess}</span>
          <button onClick={() => setActionSuccess(null)} style={{ background: "transparent", border: "none", color: "#6ee7b7", cursor: "pointer" }}>✕</button>
        </div>
      )}

      {error && (
        <div style={{ background: "#7f1d1d", color: "#fecaca", padding: "8px 20px", fontSize: 13, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span>{error}</span>
          <button onClick={() => setError(null)} style={{ background: "transparent", border: "none", color: "#fecaca", cursor: "pointer" }}>✕</button>
        </div>
      )}

      {/* Main 3-panel layout */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* Left Panel: File Tree */}
        <div
          style={{
            width: 280,
            borderRight: "1px solid #1e293b",
            background: "#0b1120",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <div
            style={{
              padding: "10px 14px",
              borderBottom: "1px solid #1e293b",
              fontSize: 12,
              fontWeight: 600,
              color: "#94a3b8",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <span>WORKSPACE FILES</span>
            {fileTree && <span style={{ color: "#64748b" }}>{fileTree.entries_count} items</span>}
          </div>

          <div style={{ flex: 1, overflowY: "auto", padding: "8px 6px" }}>
            {fileTree && fileTree.tree.length > 0 ? (
              renderTreeEntries(fileTree.tree)
            ) : (
              <div style={{ padding: 16, color: "#64748b", fontSize: 13, textAlign: "center" }}>
                {loading ? "Loading tree..." : "No files found"}
              </div>
            )}
          </div>
        </div>

        {/* Center Panel: Main Viewers */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", background: "#090d16" }}>
          {/* Tabs bar */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              borderBottom: "1px solid #1e293b",
              background: "#0f172a",
              padding: "0 16px",
            }}
          >
            <div style={{ display: "flex", gap: 4 }}>
              <button
                onClick={() => setActiveTab("viewer")}
                style={{
                  background: activeTab === "viewer" ? "#090d16" : "transparent",
                  color: activeTab === "viewer" ? "#38bdf8" : "#94a3b8",
                  border: "none",
                  borderTop: activeTab === "viewer" ? "2px solid #38bdf8" : "2px solid transparent",
                  padding: "10px 16px",
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                📄 File Viewer {selectedFile && `(${selectedFile.split("/").pop()})`}
              </button>
              <button
                onClick={() => setActiveTab("diff")}
                style={{
                  background: activeTab === "diff" ? "#090d16" : "transparent",
                  color: activeTab === "diff" ? "#38bdf8" : "#94a3b8",
                  border: "none",
                  borderTop: activeTab === "diff" ? "2px solid #38bdf8" : "2px solid transparent",
                  padding: "10px 16px",
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <span>Working Tree Diff</span>
                {workingTreeDiff && workingTreeDiff.files.length > 0 && (
                  <span
                    style={{
                      background: "#ef4444",
                      color: "#fff",
                      borderRadius: 10,
                      padding: "1px 6px",
                      fontSize: 11,
                    }}
                  >
                    {workingTreeDiff.files.length}
                  </span>
                )}
              </button>
              <button
                onClick={() => setActiveTab("proposed")}
                style={{
                  background: activeTab === "proposed" ? "#090d16" : "transparent",
                  color: activeTab === "proposed" ? "#38bdf8" : "#94a3b8",
                  border: "none",
                  borderTop: activeTab === "proposed" ? "2px solid #38bdf8" : "2px solid transparent",
                  padding: "10px 16px",
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                💡 Proposed Patches ({proposedDiffs.length})
              </button>
              <button
                onClick={() => setActiveTab("history")}
                style={{
                  background: activeTab === "history" ? "#090d16" : "transparent",
                  color: activeTab === "history" ? "#38bdf8" : "#94a3b8",
                  border: "none",
                  borderTop: activeTab === "history" ? "2px solid #38bdf8" : "2px solid transparent",
                  padding: "10px 16px",
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                📜 Applies & Rollbacks ({applies.length})
              </button>
              <button
                onClick={() => setActiveTab("search")}
                style={{
                  background: activeTab === "search" ? "#090d16" : "transparent",
                  color: activeTab === "search" ? "#38bdf8" : "#94a3b8",
                  border: "none",
                  borderTop: activeTab === "search" ? "2px solid #38bdf8" : "2px solid transparent",
                  padding: "10px 16px",
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                🔍 Search
              </button>
            </div>

            {/* Quick search input */}
            <form onSubmit={handleSearch} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search workspace text..."
                style={{
                  background: "#1e293b",
                  border: "1px solid #334155",
                  borderRadius: 4,
                  padding: "4px 8px",
                  fontSize: 12,
                  color: "#f8fafc",
                  width: 180,
                }}
              />
              <button
                type="submit"
                style={{
                  background: "#334155",
                  color: "#f8fafc",
                  border: "none",
                  borderRadius: 4,
                  padding: "4px 8px",
                  fontSize: 12,
                  cursor: "pointer",
                }}
              >
                Find
              </button>
            </form>
          </div>

          {/* View content area */}
          <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
            {activeTab === "viewer" && (
              <div>
                {fileContent ? (
                  <div
                    style={{
                      background: "#0f172a",
                      border: "1px solid #1e293b",
                      borderRadius: 6,
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        padding: "8px 14px",
                        background: "#1e293b",
                        borderBottom: "1px solid #334155",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        fontSize: 12,
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span style={{ fontWeight: 600, color: "#f8fafc" }}>{fileContent.path}</span>
                        {fileContent.sensitive && (
                          <span
                            style={{
                              fontSize: 10,
                              padding: "1px 6px",
                              borderRadius: 3,
                              background: "#7f1d1d",
                              color: "#fca5a5",
                              fontWeight: 700,
                            }}
                          >
                            RESTRICTED / SENSITIVE
                          </span>
                        )}
                      </div>
                      <div style={{ color: "#94a3b8" }}>
                        Lines {fileContent.start_line}–{fileContent.end_line} of {fileContent.total_lines} ({fileContent.size_bytes} bytes)
                      </div>
                    </div>

                    <div style={{ fontFamily: "monospace", fontSize: 13, overflowX: "auto" }}>
                      {fileContent.lines.map((line, idx) => {
                        const lineNo = fileContent.start_line + idx;
                        return (
                          <div
                            key={idx}
                            style={{
                              display: "flex",
                              padding: "1px 0",
                              lineHeight: "20px",
                            }}
                            onMouseEnter={(e) => (e.currentTarget.style.background = "#1e293b")}
                            onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                          >
                            <span
                              style={{
                                width: 50,
                                textAlign: "right",
                                paddingRight: 14,
                                color: "#475569",
                                userSelect: "none",
                                flexShrink: 0,
                              }}
                            >
                              {lineNo}
                            </span>
                            <span style={{ color: "#e2e8f0", whiteSpace: "pre" }}>{line}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : (
                  <div style={{ padding: 48, textAlign: "center", color: "#64748b" }}>
                    <div style={{ fontSize: 36, marginBottom: 12 }}>📄</div>
                    <div>Select a file from the workspace file tree on the left to inspect its contents.</div>
                  </div>
                )}
              </div>
            )}

            {activeTab === "diff" && renderDiffViewer(workingTreeDiff, true)}

            {activeTab === "proposed" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                {proposedDiffs.length === 0 ? (
                  <div style={{ padding: 48, textAlign: "center", color: "#64748b" }}>
                    <div style={{ fontSize: 36, marginBottom: 12 }}>💡</div>
                    <div>No proposed diffs recorded yet. AI-proposed code patches will appear here for review.</div>
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, color: "#cbd5e1" }}>
                      Recorded Proposed Patches
                    </div>
                    {proposedDiffs.map((d) => (
                      <div
                        key={d.diff_id}
                        onClick={() => handleViewProposedDiff(d.diff_id)}
                        style={{
                          background: "#0f172a",
                          border: "1px solid #1e293b",
                          padding: "10px 14px",
                          borderRadius: 6,
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                        }}
                      >
                        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                          <span
                            style={{
                              fontSize: 11,
                              fontWeight: 700,
                              padding: "2px 6px",
                              borderRadius: 3,
                              background:
                                d.state === "applied"
                                  ? "#1e3a8a"
                                  : d.state === "proposed"
                                  ? "#065f46"
                                  : "#7f1d1d",
                              color:
                                d.state === "applied"
                                  ? "#93c5fd"
                                  : d.state === "proposed"
                                  ? "#6ee7b7"
                                  : "#fca5a5",
                            }}
                          >
                            {d.state.toUpperCase()}
                          </span>
                          <span style={{ fontWeight: 600 }}>{d.diff_id}</span>
                          <span style={{ fontSize: 12, color: "#64748b" }}>{d.created_at}</span>
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                          <div style={{ display: "flex", gap: 10, fontSize: 13 }}>
                            <span style={{ color: "#4ade80" }}>+{d.total_additions}</span>
                            <span style={{ color: "#f87171" }}>-{d.total_deletions}</span>
                          </div>
                          {d.state === "proposed" && (
                            <div style={{ display: "flex", gap: 6 }}>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleViewProposedDiff(d.diff_id).then(() => {
                                    setApplyModalDiff(selectedDiff || null);
                                  });
                                }}
                                style={{
                                  background: "#16a34a",
                                  color: "#fff",
                                  border: "none",
                                  borderRadius: 4,
                                  padding: "4px 8px",
                                  fontSize: 12,
                                  fontWeight: 600,
                                  cursor: "pointer",
                                }}
                              >
                                Apply
                              </button>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleRejectDiff(d.diff_id);
                                }}
                                style={{
                                  background: "#dc2626",
                                  color: "#fff",
                                  border: "none",
                                  borderRadius: 4,
                                  padding: "4px 8px",
                                  fontSize: 12,
                                  fontWeight: 600,
                                  cursor: "pointer",
                                }}
                              >
                                Reject
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    ))}

                    {selectedDiff && (
                      <div style={{ marginTop: 16 }}>
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                          <div style={{ fontSize: 14, fontWeight: 600 }}>
                            Inspecting Patch: {selectedDiff.diff_id} ({selectedDiff.state.toUpperCase()})
                          </div>
                          {selectedDiff.state === "proposed" && (
                            <div style={{ display: "flex", gap: 8 }}>
                              <button
                                onClick={() => setApplyModalDiff(selectedDiff)}
                                style={{
                                  background: "#16a34a",
                                  color: "#fff",
                                  border: "none",
                                  borderRadius: 6,
                                  padding: "6px 14px",
                                  fontSize: 13,
                                  fontWeight: 600,
                                  cursor: "pointer",
                                }}
                              >
                                ✓ Review & Apply Changes
                              </button>
                              <button
                                onClick={() => handleRejectDiff(selectedDiff.diff_id)}
                                style={{
                                  background: "#dc2626",
                                  color: "#fff",
                                  border: "none",
                                  borderRadius: 6,
                                  padding: "6px 14px",
                                  fontSize: 13,
                                  fontWeight: 600,
                                  cursor: "pointer",
                                }}
                              >
                                ✕ Reject
                              </button>
                            </div>
                          )}
                        </div>
                        {renderDiffViewer(selectedDiff, false)}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {activeTab === "history" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: "#cbd5e1" }}>
                  Mutation Journal & Rollback History
                </div>

                {applies.length === 0 ? (
                  <div style={{ padding: 48, textAlign: "center", color: "#64748b" }}>
                    <div style={{ fontSize: 36, marginBottom: 12 }}>📜</div>
                    <div>No patch applications recorded yet.</div>
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                    {applies.map((app) => (
                      <div
                        key={app.apply_id}
                        style={{
                          background: "#0f172a",
                          border: "1px solid #1e293b",
                          borderRadius: 6,
                          padding: 14,
                          display: "flex",
                          flexDirection: "column",
                          gap: 10,
                        }}
                      >
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                            <span
                              style={{
                                fontSize: 11,
                                fontWeight: 700,
                                padding: "2px 6px",
                                borderRadius: 3,
                                background:
                                  app.status === "applied"
                                    ? "#065f46"
                                    : app.status === "rolled_back"
                                    ? "#334155"
                                    : "#7f1d1d",
                                color:
                                  app.status === "applied"
                                    ? "#6ee7b7"
                                    : app.status === "rolled_back"
                                    ? "#cbd5e1"
                                    : "#fca5a5",
                              }}
                            >
                              {app.status.toUpperCase()}
                            </span>
                            <span style={{ fontWeight: 600 }}>{app.apply_id}</span>
                            <span style={{ fontSize: 12, color: "#64748b" }}>Diff: {app.diff_id}</span>
                          </div>
                          <div>
                            {app.status === "applied" && (
                              <button
                                onClick={() => setRollbackModalApply(app)}
                                style={{
                                  background: "#475569",
                                  color: "#f8fafc",
                                  border: "none",
                                  borderRadius: 4,
                                  padding: "4px 10px",
                                  fontSize: 12,
                                  fontWeight: 600,
                                  cursor: "pointer",
                                }}
                              >
                                ↩ Rollback
                              </button>
                            )}
                          </div>
                        </div>

                        <div style={{ fontSize: 12, color: "#94a3b8" }}>
                          Applied: <span style={{ color: "#f8fafc" }}>{app.applied_at || app.created_at}</span>
                          {app.rolled_back_at && (
                            <span> | Rolled Back: <span style={{ color: "#fca5a5" }}>{app.rolled_back_at}</span></span>
                          )}
                        </div>

                        {app.files_affected.length > 0 && (
                          <div style={{ background: "#090d16", borderRadius: 4, padding: 8, fontSize: 12 }}>
                            <div style={{ color: "#64748b", marginBottom: 4, fontWeight: 600 }}>AFFECTED FILES ({app.files_affected.length}):</div>
                            {app.files_affected.map((f, fIdx) => (
                              <div key={fIdx} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "2px 0" }}>
                                <span style={{ color: "#cbd5e1" }}>{f.path} ({f.change_kind})</span>
                                <span style={{ color: "#475569", fontFamily: "monospace", fontSize: 11 }}>{f.applied_sha256 ? f.applied_sha256.slice(0, 8) : "deleted"}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

                  {/* Tab: Runners & Permissioned Execution (AG-E1.3) */}
            {/* Tab: Git, Branch, Worktree & Safe Commit (AG-E1.4) */}
            {/* Tab: Guided Engineering Workflow (AG-E1.5) */}
      {activeTab === "pipeline" && (
        <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: "24px" }}>
          {/* Header & Controls */}
          <div
            style={{
              background: "#090f1f",
              border: "1px solid #1e293b",
              borderRadius: "8px",
              padding: "16px 20px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              flexWrap: "wrap",
              gap: "16px",
            }}
          >
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "4px" }}>
                <span style={{ fontSize: "16px", fontWeight: 700, color: "#f8fafc" }}>
                  ⚡ {activePipeline ? activePipeline.title : "Integrated Engineering Pipeline"}
                </span>
                {activePipeline && (
                  <span
                    style={{
                      fontSize: "11px",
                      fontWeight: 600,
                      padding: "2px 8px",
                      borderRadius: "10px",
                      background: activePipeline.status === "completed" ? "#064e3b" : "#1e3a8a",
                      color: activePipeline.status === "completed" ? "#34d399" : "#60a5fa",
                    }}
                  >
                    {activePipeline.status.toUpperCase()}
                  </span>
                )}
              </div>
              <div style={{ fontSize: "12px", color: "#94a3b8", marginBottom: "6px" }}>
                End-to-End Guided Engineering: Inspect ➔ Diff ➔ Review ➔ Apply ➔ Test ➔ Evidence ➔ Stage ➔ Digest ➔ Commit
              </div>
            {pipelines.length > 1 && (
              <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                <span style={{ fontSize: "11px", color: "#94a3b8" }}>Select Pipeline:</span>
                <select
                  value={activePipeline?.pipeline_id || ""}
                  onChange={(e) => {
                    const found = pipelines.find((p) => p.pipeline_id === e.target.value);
                    if (found) setActivePipeline(found);
                  }}
                  style={{ background: "#050b14", color: "#f8fafc", border: "1px solid #1e293b", borderRadius: "4px", padding: "4px 8px", fontSize: "12px" }}
                >
                  {pipelines.map((p) => (
                    <option key={p.pipeline_id} value={p.pipeline_id}>{p.title} ({p.current_stage})</option>
                  ))}
                </select>
              </div>
            )}
            </div>
            <div style={{ display: "flex", gap: "10px" }}>
              <button
                onClick={() => setNewPipelineModalOpen(true)}
                style={{
                  background: "#0284c7",
                  color: "#ffffff",
                  border: "none",
                  borderRadius: "6px",
                  padding: "8px 16px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                + New Pipeline
              </button>
              {activePipeline && activePipeline.status === "in_progress" && (
                <button
                  onClick={handleRollbackPipeline}
                  style={{
                    background: "#7f1d1d",
                    color: "#fca5a5",
                    border: "none",
                    borderRadius: "6px",
                    padding: "8px 14px",
                    fontSize: "12px",
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                >
                  Rollback Pipeline
                </button>
              )}
            </div>
          </div>

          {/* Stepper Bar */}
          {activePipeline && (
            <div
              style={{
                background: "#090f1f",
                border: "1px solid #1e293b",
                borderRadius: "8px",
                padding: "16px 20px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                overflowX: "auto",
                gap: "12px",
              }}
            >
              {[
                { stage: "inspect", label: "1. Inspect", icon: "🔍" },
                { stage: "propose", label: "2. Propose", icon: "📝" },
                { stage: "review", label: "3. Review", icon: "👁️" },
                { stage: "applied", label: "4. Apply", icon: "⚡" },
                { stage: "testing", label: "5. Test", icon: "🧪" },
                { stage: "evidence", label: "6. Evidence", icon: "📊" },
                { stage: "staged", label: "7. Stage", icon: "📦" },
                { stage: "review_staged", label: "8. Digest", icon: "🔐" },
                { stage: "committed", label: "9. Commit", icon: "✅" },
              ].map((step, idx) => {
                const isCurrent = activePipeline.current_stage === step.stage;
                const isPassed = activePipeline.status === "completed" || idx < [
                  "inspect", "propose", "review", "applied", "testing", "evidence", "staged", "review_staged", "committed"
                ].indexOf(activePipeline.current_stage);

                return (
                  <div
                    key={step.stage}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                      opacity: isCurrent || isPassed ? 1 : 0.4,
                    }}
                  >
                    <div
                      style={{
                        padding: "6px 12px",
                        borderRadius: "20px",
                        background: isCurrent ? "#0284c7" : isPassed ? "#064e3b" : "#1e293b",
                        color: isCurrent ? "#ffffff" : isPassed ? "#34d399" : "#94a3b8",
                        fontSize: "12px",
                        fontWeight: 600,
                        whiteSpace: "nowrap",
                      }}
                    >
                      {step.icon} {step.label}
                    </div>
                    {idx < 8 && <span style={{ color: "#475569", fontSize: "14px" }}>➔</span>}
                  </div>
                );
              })}
            </div>
          )}

          {/* Active Stage Action Board */}
          {activePipeline && (
            <div style={{ display: "grid", gridTemplateColumns: "1.2fr 0.8fr", gap: "16px" }}>
              {/* Left Column: Stage Actions & Inputs */}
              <div style={{ background: "#090f1f", border: "1px solid #1e293b", borderRadius: "8px", padding: "20px" }}>
                <h3 style={{ margin: "0 0 16px 0", fontSize: "15px", color: "#f8fafc" }}>
                  Current Stage: <span style={{ color: "#38bdf8" }}>{activePipeline.current_stage.toUpperCase()}</span>
                </h3>

                {/* Stage 1 / 2: Inspect / Propose */}
                {(activePipeline.current_stage === "inspect" || activePipeline.current_stage === "propose") && (
                  <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                    <div style={{ fontSize: "13px", color: "#cbd5e1" }}>
                      Select a proposed diff from the Proposed Diffs tab or attach a diff to advance to Review.
                    </div>
                    {proposedDiffs.length > 0 ? (
                      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                        {proposedDiffs.map((d: any) => (
                          <div
                            key={d.diff_id}
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "center",
                              background: "#050b14",
                              padding: "10px 14px",
                              borderRadius: "4px",
                              border: "1px solid #1e293b",
                            }}
                          >
                            <span style={{ fontFamily: "monospace", fontSize: "12px", color: "#e2e8f0" }}>
                              {d.diff_id} (+{d.total_additions}/-{d.total_deletions})
                            </span>
                            <button
                              onClick={async () => {
                                await amca.attachEngineeringPipelineDiff(projectId, selectedWorkspace!.workspace_id, activePipeline.pipeline_id, { diff_id: d.diff_id });
                                await loadWorkspaceDetails(selectedWorkspace!.workspace_id);
                              }}
                              style={{ background: "#0284c7", color: "#ffffff", border: "none", borderRadius: "4px", padding: "4px 10px", fontSize: "11px", cursor: "pointer", fontWeight: 600 }}
                            >
                              Attach & Review
                            </button>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div style={{ color: "#64748b", fontSize: "12px" }}>No proposed diffs available. Generate one in the Viewer tab.</div>
                    )}
                  </div>
                )}

                {/* Stage 3: Review */}
                {activePipeline.current_stage === "review" && (
                  <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                    <div style={{ fontSize: "13px", color: "#cbd5e1" }}>
                      Diff attached (<code>{activePipeline.diff_id}</code>). Review the exact hunk modifications, then apply transactionally.
                    </div>
                    <button
                      onClick={handleApplyPipelineDiff}
                      style={{ background: "#0284c7", color: "#ffffff", border: "none", borderRadius: "6px", padding: "10px 20px", fontSize: "13px", fontWeight: 600, cursor: "pointer" }}
                    >
                      Apply Patch Transactionally
                    </button>
                  </div>
                )}

                {/* Stage 4 / 5: Applied / Testing */}
                {(activePipeline.current_stage === "applied" || activePipeline.current_stage === "testing") && (
                  <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                    <div style={{ fontSize: "13px", color: "#cbd5e1" }}>
                      Patch applied successfully! Run test suites to gather verifiable evidence before staging.
                    </div>
                    <div style={{ display: "flex", gap: "10px" }}>
                      {runners.map((p: RunnerProfile) => (
                        <button
                          key={p.runner_id}
                          onClick={async () => {
                            const plan = await amca.createEngineeringExecutionPlan(projectId, selectedWorkspace!.workspace_id, {
                              kind: p.kind,
                              runner_profile: p.name,
                              executable: p.executable,
                              arguments: p.default_args,
                            });
                            await amca.runEngineeringExecutionPlan(projectId, selectedWorkspace!.workspace_id, plan.plan.execution_plan_id);
                            await loadWorkspaceDetails(selectedWorkspace!.workspace_id);
                          }}
                          style={{ background: "#312e81", color: "#e0e7ff", border: "1px solid #4338ca", borderRadius: "6px", padding: "8px 14px", fontSize: "12px", cursor: "pointer", fontWeight: 600 }}
                        >
                          Run {p.name}
                        </button>
                      ))}
                      <button
                        onClick={handleStagePipelineChanges}
                        style={{ background: "#0284c7", color: "#ffffff", border: "none", borderRadius: "6px", padding: "8px 16px", fontSize: "12px", fontWeight: 600, cursor: "pointer" }}
                      >
                        Skip to Stage ➔
                      </button>
                    </div>
                  </div>
                )}

                {/* Stage 6: Evidence */}
                {activePipeline.current_stage === "evidence" && (
                  <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                    <div style={{ fontSize: "13px", color: "#34d399", fontWeight: 600 }}>
                      ✓ Test Evidence Verified ({activePipeline.test_run_ids.length} test runs recorded).
                    </div>
                    <button
                      onClick={handleStagePipelineChanges}
                      style={{ background: "#0284c7", color: "#ffffff", border: "none", borderRadius: "6px", padding: "10px 20px", fontSize: "13px", fontWeight: 600, cursor: "pointer" }}
                    >
                      Stage Verified Changes ➔
                    </button>
                  </div>
                )}

                {/* Stage 7 / 8: Staged / Review Staged */}
                {(activePipeline.current_stage === "staged" || activePipeline.current_stage === "review_staged") && (
                  <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                    <div style={{ fontSize: "13px", color: "#cbd5e1" }}>
                      Staged {activePipeline.staged_paths.length} file(s). Digest: <code>{activePipeline.staged_diff_digest?.slice(0, 16)}...</code>
                    </div>
                    <div>
                      <label style={{ fontSize: "12px", color: "#94a3b8", display: "block", marginBottom: "4px" }}>Commit Message:</label>
                      <textarea
                        value={commitMessage}
                        onChange={(e) => setCommitMessage(e.target.value)}
                        placeholder="feat(core): verified integrated patch"
                        rows={2}
                        style={{ width: "100%", boxSizing: "border-box", background: "#050b14", border: "1px solid #1e293b", borderRadius: "6px", padding: "8px", color: "#f8fafc", fontFamily: "monospace", fontSize: "12px" }}
                      />
                    </div>
                    <button
                      onClick={handleCommitPipeline}
                      disabled={!commitMessage.trim()}
                      style={{ background: commitMessage.trim() ? "#16a34a" : "#334155", color: "#ffffff", border: "none", borderRadius: "6px", padding: "10px 20px", fontSize: "13px", fontWeight: 600, cursor: commitMessage.trim() ? "pointer" : "not-allowed" }}
                    >
                      Execute Safe CAS Commit
                    </button>
                  </div>
                )}

                {/* Stage 9: Committed */}
                {activePipeline.current_stage === "committed" && (
                  <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                    <div style={{ fontSize: "14px", color: "#34d399", fontWeight: 700 }}>
                      🎉 Pipeline Successfully Completed & Committed!
                    </div>
                    <div style={{ fontSize: "12px", color: "#cbd5e1" }}>
                      Commit SHA: <code style={{ color: "#38bdf8", fontWeight: 700 }}>{activePipeline.commit_sha}</code>
                    </div>
                  </div>
                )}
              </div>

              {/* Right Column: Full Provenance Chain */}
              <div style={{ background: "#090f1f", border: "1px solid #1e293b", borderRadius: "8px", padding: "20px" }}>
                <h4 style={{ margin: "0 0 12px 0", fontSize: "14px", color: "#f8fafc" }}>
                  Verifiable Provenance Chain
                </h4>
                <div style={{ display: "flex", flexDirection: "column", gap: "10px", fontSize: "12px" }}>
                  <div>
                    <span style={{ color: "#64748b" }}>Workspace: </span>
                    <span style={{ color: "#cbd5e1" }}>{selectedWorkspace?.name}</span>
                  </div>
                  <div>
                    <span style={{ color: "#64748b" }}>Diff ID: </span>
                    <code style={{ color: activePipeline.diff_id ? "#38bdf8" : "#64748b" }}>{activePipeline.diff_id || "none"}</code>
                  </div>
                  <div>
                    <span style={{ color: "#64748b" }}>Apply ID: </span>
                    <code style={{ color: activePipeline.apply_id ? "#34d399" : "#64748b" }}>{activePipeline.apply_id || "none"}</code>
                  </div>
                  <div>
                    <span style={{ color: "#64748b" }}>Test Runs: </span>
                    <span style={{ color: "#e2e8f0" }}>{activePipeline.test_run_ids.length} recorded ({activePipeline.test_passed ? "PASSED" : "pending"})</span>
                  </div>
                  <div>
                    <span style={{ color: "#64748b" }}>Staged Digest: </span>
                    <code style={{ color: "#38bdf8" }}>{activePipeline.staged_diff_digest ? activePipeline.staged_diff_digest.slice(0, 16) + "..." : "none"}</code>
                  </div>
                  <div>
                    <span style={{ color: "#64748b" }}>Commit SHA: </span>
                    <code style={{ color: "#34d399", fontWeight: 700 }}>{activePipeline.commit_sha ? activePipeline.commit_sha.slice(0, 8) : "uncommitted"}</code>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Modal: New Pipeline */}
          {newPipelineModalOpen && (
            <div
              style={{
                position: "fixed",
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
                background: "rgba(0,0,0,0.75)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                zIndex: 1000,
              }}
            >
              <div
                style={{
                  background: "#0f172a",
                  border: "1px solid #1e293b",
                  borderRadius: "8px",
                  padding: "24px",
                  maxWidth: "450px",
                  width: "100%",
                }}
              >
                <h3 style={{ margin: "0 0 14px 0", fontSize: "16px", color: "#f8fafc" }}>Start Guided Workflow Pipeline</h3>
                <div style={{ marginBottom: "16px" }}>
                  <label style={{ fontSize: "12px", color: "#94a3b8", display: "block", marginBottom: "4px" }}>Pipeline Title:</label>
                  <input
                    type="text"
                    value={newPipelineTitle}
                    onChange={(e) => setNewPipelineTitle(e.target.value)}
                    placeholder="e.g. Implement authentication feature"
                    style={{ width: "100%", boxSizing: "border-box", background: "#050b14", border: "1px solid #1e293b", borderRadius: "6px", padding: "8px 12px", color: "#f8fafc", fontSize: "13px" }}
                  />
                </div>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                  <button
                    onClick={() => setNewPipelineModalOpen(false)}
                    style={{ background: "#1e293b", color: "#cbd5e1", border: "none", borderRadius: "6px", padding: "8px 14px", cursor: "pointer", fontSize: "12px" }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleStartPipeline}
                    style={{ background: "#0284c7", color: "#ffffff", border: "none", borderRadius: "6px", padding: "8px 16px", fontWeight: 600, fontSize: "12px", cursor: "pointer" }}
                  >
                    Start Pipeline
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === "git" && (
        <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: "24px" }}>
          {/* Section 1: Git Status Header */}
          <div
            style={{
              background: "#090f1f",
              border: "1px solid #1e293b",
              borderRadius: "8px",
              padding: "16px 20px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              flexWrap: "wrap",
              gap: "16px",
            }}
          >
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "4px" }}>
                <span style={{ fontSize: "16px", fontWeight: 700, color: "#f8fafc" }}>
                  🌿 {gitState?.current_branch || "(detached / non-git)"}
                </span>
                <span style={{ fontFamily: "monospace", fontSize: "12px", color: "#38bdf8" }}>
                  {gitState?.head_commit ? gitState.head_commit.slice(0, 8) : "no HEAD"}
                </span>
                <span
                  style={{
                    fontSize: "11px",
                    fontWeight: 600,
                    padding: "2px 8px",
                    borderRadius: "10px",
                    background: gitState?.dirty ? "#78350f" : "#064e3b",
                    color: gitState?.dirty ? "#fbbf24" : "#34d399",
                  }}
                >
                  {gitState?.dirty ? "DIRTY" : "CLEAN"}
                </span>
              </div>
              <div style={{ fontSize: "12px", color: "#94a3b8" }}>
                Staged: {gitState?.staged_count ?? 0} • Unstaged: {gitState?.unstaged_count ?? 0} • Untracked: {gitState?.untracked_count ?? 0}
                {gitState?.upstream && ` • Upstream: ${gitState.upstream} (↑${gitState.ahead || 0} ↓${gitState.behind || 0})`}
              </div>
            </div>
            <div style={{ display: "flex", gap: "10px" }}>
              <button
                onClick={() => setNewBranchModalOpen(true)}
                style={{
                  background: "#0284c7",
                  color: "#ffffff",
                  border: "none",
                  borderRadius: "6px",
                  padding: "8px 14px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                + New Branch
              </button>
              <button
                onClick={() => setNewWorktreeModalOpen(true)}
                style={{
                  background: "#312e81",
                  color: "#e0e7ff",
                  border: "1px solid #4338ca",
                  borderRadius: "6px",
                  padding: "8px 14px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                + Isolated Worktree
              </button>
            </div>
          </div>

          {/* Section 2: Local Branches & Isolated Worktrees */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
            {/* Branches Card */}
            <div style={{ background: "#090f1f", border: "1px solid #1e293b", borderRadius: "8px", padding: "16px" }}>
              <h4 style={{ margin: "0 0 12px 0", fontSize: "14px", color: "#f8fafc" }}>
                Local Branches ({branches.length})
              </h4>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px", maxHeight: "200px", overflowY: "auto" }}>
                {branches.map((b) => (
                  <div
                    key={b.name}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "8px 12px",
                      background: b.is_current ? "#0f172a" : "#050b14",
                      borderRadius: "4px",
                      border: b.is_current ? "1px solid #0284c7" : "1px solid #1e293b",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <span style={{ fontSize: "13px", fontWeight: b.is_current ? 700 : 500, color: b.is_current ? "#38bdf8" : "#cbd5e1" }}>
                        {b.is_current ? "● " : ""}{b.name}
                      </span>
                      <span style={{ fontFamily: "monospace", fontSize: "11px", color: "#64748b" }}>
                        {b.head_commit.slice(0, 7)}
                      </span>
                    </div>
                    {!b.is_current && (
                      <button
                        onClick={() => handleSwitchBranch(b.name)}
                        disabled={gitState?.dirty}
                        title={gitState?.dirty ? "Branch switch blocked while primary workspace is dirty" : ""}
                        style={{
                          background: gitState?.dirty ? "#334155" : "#1e293b",
                          color: gitState?.dirty ? "#64748b" : "#cbd5e1",
                          border: "none",
                          borderRadius: "4px",
                          padding: "4px 8px",
                          fontSize: "11px",
                          cursor: gitState?.dirty ? "not-allowed" : "pointer",
                        }}
                      >
                        Switch
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* Isolated Worktrees Card */}
            <div style={{ background: "#090f1f", border: "1px solid #1e293b", borderRadius: "8px", padding: "16px" }}>
              <h4 style={{ margin: "0 0 12px 0", fontSize: "14px", color: "#f8fafc" }}>
                AMCA Isolated Worktrees ({worktrees.length})
              </h4>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px", maxHeight: "200px", overflowY: "auto" }}>
                {worktrees.length === 0 ? (
                  <div style={{ color: "#64748b", fontSize: "12px", textAlign: "center", padding: "20px" }}>
                    No isolated worktrees created yet.
                  </div>
                ) : (
                  worktrees.map((wt) => (
                    <div
                      key={wt.worktree_id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "8px 12px",
                        background: "#050b14",
                        borderRadius: "4px",
                        border: "1px solid #1e293b",
                      }}
                    >
                      <div>
                        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                          <span style={{ fontSize: "12px", fontWeight: 600, color: "#f1f5f9" }}>
                            {wt.branch}
                          </span>
                          <span
                            style={{
                              fontSize: "9px",
                              padding: "1px 5px",
                              borderRadius: "4px",
                              background: wt.amca_managed ? "#064e3b" : "#334155",
                              color: wt.amca_managed ? "#34d399" : "#94a3b8",
                            }}
                          >
                            {wt.amca_managed ? "AMCA" : "USER"}
                          </span>
                        </div>
                        <div style={{ fontSize: "10px", color: "#64748b", wordBreak: "break-all" }}>
                          {wt.path}
                        </div>
                      </div>
                      {wt.amca_managed && (
                        <button
                          onClick={() => handleRemoveWorktree(wt.worktree_id)}
                          style={{
                            background: "#7f1d1d",
                            color: "#fca5a5",
                            border: "none",
                            borderRadius: "4px",
                            padding: "4px 8px",
                            fontSize: "10px",
                            cursor: "pointer",
                          }}
                        >
                          Remove
                        </button>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Section 3: Staging & Staged Diff */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
            {/* Unstaged Files */}
            <div style={{ background: "#090f1f", border: "1px solid #1e293b", borderRadius: "8px", padding: "16px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
                <h4 style={{ margin: 0, fontSize: "14px", color: "#f8fafc" }}>
                  Unstaged Changes
                </h4>
                {workingTreeDiff && workingTreeDiff.files.length > 0 && (
                  <button
                    onClick={() => handleStageFiles(workingTreeDiff.files.map((f) => f.path || f.old_path || ''))}
                    style={{
                      background: "#0284c7",
                      color: "#ffffff",
                      border: "none",
                      borderRadius: "4px",
                      padding: "4px 10px",
                      fontSize: "11px",
                      cursor: "pointer",
                      fontWeight: 600,
                    }}
                  >
                    Stage All ({workingTreeDiff.files.length})
                  </button>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px", maxHeight: "180px", overflowY: "auto" }}>
                {!workingTreeDiff || workingTreeDiff.files.length === 0 ? (
                  <div style={{ color: "#64748b", fontSize: "12px", padding: "12px", textAlign: "center" }}>
                    No unstaged changes in working tree.
                  </div>
                ) : (
                  workingTreeDiff.files.map((f) => {
                    const p = f.path || f.old_path || '';
                    return (
                      <div
                        key={p}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          padding: "6px 10px",
                          background: "#050b14",
                          borderRadius: "4px",
                          fontSize: "12px",
                        }}
                      >
                        <span style={{ color: "#e2e8f0", fontFamily: "monospace" }}>{p}</span>
                        <button
                          onClick={() => handleStageFiles([p])}
                          style={{
                            background: "#1e293b",
                            color: "#38bdf8",
                            border: "none",
                            borderRadius: "4px",
                            padding: "2px 8px",
                            fontSize: "11px",
                            cursor: "pointer",
                          }}
                        >
                          Stage +
                        </button>
                      </div>
                    );
                  })
                )}
              </div>
            </div>

            {/* Staged Files */}
            <div style={{ background: "#090f1f", border: "1px solid #1e293b", borderRadius: "8px", padding: "16px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
                <h4 style={{ margin: 0, fontSize: "14px", color: "#f8fafc" }}>
                  Staged for Commit ({stagedDiff?.files.length ?? 0})
                </h4>
                {stagedDiff && stagedDiff.files.length > 0 && (
                  <button
                    onClick={() => handleUnstageFiles(stagedDiff.files.map((f) => f.path || f.old_path || ''))}
                    style={{
                      background: "#334155",
                      color: "#cbd5e1",
                      border: "none",
                      borderRadius: "4px",
                      padding: "4px 10px",
                      fontSize: "11px",
                      cursor: "pointer",
                      fontWeight: 600,
                    }}
                  >
                    Unstage All
                  </button>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px", maxHeight: "180px", overflowY: "auto" }}>
                {!stagedDiff || stagedDiff.files.length === 0 ? (
                  <div style={{ color: "#64748b", fontSize: "12px", padding: "12px", textAlign: "center" }}>
                    No files staged for commit yet.
                  </div>
                ) : (
                  stagedDiff.files.map((f) => {
                    const p = f.path || f.old_path || '';
                    return (
                      <div
                        key={p}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          padding: "6px 10px",
                          background: "#050b14",
                          borderRadius: "4px",
                          fontSize: "12px",
                        }}
                      >
                        <span style={{ color: "#34d399", fontFamily: "monospace" }}>{p}</span>
                        <button
                          onClick={() => handleUnstageFiles([p])}
                          style={{
                            background: "#1e293b",
                            color: "#f87171",
                            border: "none",
                            borderRadius: "4px",
                            padding: "2px 8px",
                            fontSize: "11px",
                            cursor: "pointer",
                          }}
                        >
                          Unstage -
                        </button>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </div>

          {/* Section 4: Safe Commit Approval Box */}
          <div
            style={{
              background: "#090f1f",
              border: "1px solid #1e293b",
              borderRadius: "8px",
              padding: "20px",
            }}
          >
            <h4 style={{ margin: "0 0 12px 0", fontSize: "15px", color: "#f8fafc" }}>
              Prepare & Review Safe Commit (CAS Protected)
            </h4>
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <div>
                <label style={{ fontSize: "12px", color: "#94a3b8", display: "block", marginBottom: "4px" }}>
                  Commit Message:
                </label>
                <textarea
                  value={commitMessage}
                  onChange={(e) => setCommitMessage(e.target.value)}
                  placeholder="e.g. feat(auth): add password policy validation"
                  rows={3}
                  style={{
                    width: "100%",
                    boxSizing: "border-box",
                    background: "#050b14",
                    border: "1px solid #1e293b",
                    borderRadius: "6px",
                    padding: "10px",
                    color: "#f8fafc",
                    fontSize: "13px",
                    fontFamily: "monospace",
                    resize: "vertical",
                  }}
                />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontSize: "12px", color: "#64748b" }}>
                  Digest: <code style={{ color: "#38bdf8" }}>{stagedDigest ? stagedDigest.slice(0, 16) + "..." : "none"}</code>
                </div>
                <button
                  onClick={handlePrepareCommit}
                  disabled={!stagedDiff || stagedDiff.files.length === 0 || !commitMessage.trim()}
                  style={{
                    background: stagedDiff && stagedDiff.files.length > 0 && commitMessage.trim() ? "#16a34a" : "#334155",
                    color: "#ffffff",
                    border: "none",
                    borderRadius: "6px",
                    padding: "8px 20px",
                    fontWeight: 600,
                    fontSize: "13px",
                    cursor: stagedDiff && stagedDiff.files.length > 0 && commitMessage.trim() ? "pointer" : "not-allowed",
                  }}
                >
                  Review & Authorize Commit
                </button>
              </div>
            </div>
          </div>

          {/* Section 5: Commit History */}
          {commitHistory.length > 0 && (
            <div>
              <h4 style={{ margin: "0 0 12px 0", fontSize: "15px", color: "#f8fafc" }}>
                Verified Commits ({commitHistory.length})
              </h4>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {commitHistory.map((c) => (
                  <div
                    key={c.commit_id}
                    style={{
                      background: "#090f1f",
                      border: "1px solid #1e293b",
                      borderRadius: "6px",
                      padding: "12px 16px",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                        <span style={{ fontFamily: "monospace", fontWeight: 700, color: "#38bdf8", fontSize: "13px" }}>
                          {c.new_commit_sha.slice(0, 8)}
                        </span>
                        <span style={{ fontSize: "12px", fontWeight: 600, color: "#f1f5f9" }}>
                          {c.branch}
                        </span>
                        <span style={{ fontSize: "11px", color: "#64748b" }}>
                          {new Date(c.created_at).toLocaleString()}
                        </span>
                      </div>
                      <div style={{ fontSize: "12px", color: "#cbd5e1" }}>
                        {c.message}
                      </div>
                    </div>
                    <span style={{ fontSize: "11px", color: "#94a3b8" }}>
                      {c.files_committed.length} file(s)
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Modal: Commit CAS Review */}
          {activePrepModal && (
            <div
              style={{
                position: "fixed",
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
                background: "rgba(0,0,0,0.75)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                zIndex: 1000,
              }}
            >
              <div
                style={{
                  background: "#0f172a",
                  border: "1px solid #1e293b",
                  borderRadius: "8px",
                  padding: "24px",
                  maxWidth: "600px",
                  width: "100%",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
                  <h3 style={{ margin: 0, fontSize: "16px", color: "#f8fafc" }}>
                    Explicit Commit Authorization (CAS Verified)
                  </h3>
                  <button
                    onClick={() => setActivePrepModal(null)}
                    style={{ background: "none", border: "none", color: "#94a3b8", cursor: "pointer", fontSize: "18px" }}
                  >
                    ✕
                  </button>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginBottom: "20px", fontSize: "13px" }}>
                  <div>
                    <span style={{ color: "#64748b" }}>Branch: </span>
                    <strong style={{ color: "#38bdf8" }}>{activePrepModal.branch}</strong>
                  </div>
                  <div>
                    <span style={{ color: "#64748b" }}>Parent HEAD: </span>
                    <code style={{ color: "#f1f5f9" }}>{activePrepModal.prepared_head.slice(0, 12)}</code>
                  </div>
                  <div>
                    <span style={{ color: "#64748b" }}>Staged Diff Digest: </span>
                    <code style={{ color: "#34d399" }}>{activePrepModal.staged_diff_digest.slice(0, 16)}...</code>
                  </div>
                  <div>
                    <span style={{ color: "#64748b" }}>Files to Commit: </span>
                    <div style={{ maxHeight: "100px", overflowY: "auto", background: "#050b14", padding: "6px 10px", borderRadius: "4px", marginTop: "4px" }}>
                      {activePrepModal.staged_files.map((f) => (
                        <div key={f.path} style={{ fontFamily: "monospace", fontSize: "11px", color: "#e2e8f0" }}>
                          ● {f.path} (+{f.additions}/-{f.deletions})
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <span style={{ color: "#64748b" }}>Commit Message: </span>
                    <div style={{ background: "#050b14", padding: "8px", borderRadius: "4px", fontFamily: "monospace", fontSize: "12px", color: "#f8fafc", marginTop: "4px" }}>
                      {activePrepModal.message}
                    </div>
                  </div>
                </div>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "12px" }}>
                  <button
                    onClick={() => setActivePrepModal(null)}
                    style={{ background: "#1e293b", color: "#cbd5e1", border: "none", borderRadius: "6px", padding: "8px 16px", cursor: "pointer", fontSize: "12px" }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => handleExecuteCommit(activePrepModal.preparation_id)}
                    style={{ background: "#16a34a", color: "#ffffff", border: "none", borderRadius: "6px", padding: "8px 20px", fontWeight: 600, fontSize: "12px", cursor: "pointer" }}
                  >
                    Authorize & Commit
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Modal: New Branch */}
          {newBranchModalOpen && (
            <div
              style={{
                position: "fixed",
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
                background: "rgba(0,0,0,0.75)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                zIndex: 1000,
              }}
            >
              <div
                style={{
                  background: "#0f172a",
                  border: "1px solid #1e293b",
                  borderRadius: "8px",
                  padding: "24px",
                  maxWidth: "450px",
                  width: "100%",
                }}
              >
                <h3 style={{ margin: "0 0 14px 0", fontSize: "16px", color: "#f8fafc" }}>Create New Branch</h3>
                <div style={{ marginBottom: "16px" }}>
                  <label style={{ fontSize: "12px", color: "#94a3b8", display: "block", marginBottom: "4px" }}>Branch Name:</label>
                  <input
                    type="text"
                    value={newBranchName}
                    onChange={(e) => setNewBranchName(e.target.value)}
                    placeholder="feature/xyz"
                    style={{
                      width: "100%",
                      boxSizing: "border-box",
                      background: "#050b14",
                      border: "1px solid #1e293b",
                      borderRadius: "6px",
                      padding: "8px 12px",
                      color: "#f8fafc",
                      fontSize: "13px",
                    }}
                  />
                </div>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                  <button
                    onClick={() => setNewBranchModalOpen(false)}
                    style={{ background: "#1e293b", color: "#cbd5e1", border: "none", borderRadius: "6px", padding: "8px 14px", cursor: "pointer", fontSize: "12px" }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleCreateBranch}
                    disabled={!newBranchName.trim()}
                    style={{ background: "#0284c7", color: "#ffffff", border: "none", borderRadius: "6px", padding: "8px 16px", fontWeight: 600, fontSize: "12px", cursor: "pointer" }}
                  >
                    Create Branch
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Modal: New Isolated Worktree */}
          {newWorktreeModalOpen && (
            <div
              style={{
                position: "fixed",
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
                background: "rgba(0,0,0,0.75)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                zIndex: 1000,
              }}
            >
              <div
                style={{
                  background: "#0f172a",
                  border: "1px solid #1e293b",
                  borderRadius: "8px",
                  padding: "24px",
                  maxWidth: "450px",
                  width: "100%",
                }}
              >
                <h3 style={{ margin: "0 0 14px 0", fontSize: "16px", color: "#f8fafc" }}>Create Isolated Worktree</h3>
                <div style={{ marginBottom: "16px" }}>
                  <label style={{ fontSize: "12px", color: "#94a3b8", display: "block", marginBottom: "4px" }}>Branch Name:</label>
                  <input
                    type="text"
                    value={newWorktreeBranch}
                    onChange={(e) => setNewWorktreeBranch(e.target.value)}
                    placeholder="agent-work/xyz"
                    style={{
                      width: "100%",
                      boxSizing: "border-box",
                      background: "#050b14",
                      border: "1px solid #1e293b",
                      borderRadius: "6px",
                      padding: "8px 12px",
                      color: "#f8fafc",
                      fontSize: "13px",
                    }}
                  />
                  <div style={{ fontSize: "11px", color: "#64748b", marginTop: "6px" }}>
                    Worktree will be created in isolated AMCA storage outside your primary checkout.
                  </div>
                </div>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                  <button
                    onClick={() => setNewWorktreeModalOpen(false)}
                    style={{ background: "#1e293b", color: "#cbd5e1", border: "none", borderRadius: "6px", padding: "8px 14px", cursor: "pointer", fontSize: "12px" }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleCreateWorktree}
                    disabled={!newWorktreeBranch.trim()}
                    style={{ background: "#312e81", color: "#ffffff", border: "none", borderRadius: "6px", padding: "8px 16px", fontWeight: 600, fontSize: "12px", cursor: "pointer" }}
                  >
                    Create Worktree
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === "runner" && (
        <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: "24px" }}>
          {/* Running Status Banner */}
          {isRunning && (
            <div
              style={{
                background: "linear-gradient(90deg, rgba(2,132,199,0.2) 0%, rgba(14,165,233,0.1) 100%)",
                border: "1px solid #0284c7",
                borderRadius: "8px",
                padding: "16px 20px",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                <span style={{ animation: "pulse 1.5s infinite", fontSize: "20px" }}>⏳</span>
                <div>
                  <div style={{ fontWeight: 600, color: "#38bdf8", fontSize: "14px" }}>
                    Executing Permissioned Plan: {activeRunningPlanId}
                  </div>
                  <div style={{ fontSize: "12px", color: "#94a3b8" }}>
                    Elapsed time: {runningElapsed}s • stdin: closed • process-tree monitored
                  </div>
                </div>
              </div>
              <button
                onClick={handleCancelExecution}
                style={{
                  background: "#ef4444",
                  color: "#ffffff",
                  border: "none",
                  borderRadius: "6px",
                  padding: "8px 16px",
                  fontWeight: 600,
                  fontSize: "12px",
                  cursor: "pointer",
                }}
              >
                Cancel Execution
              </button>
            </div>
          )}

          {/* Section 1: Detected Runners */}
          <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px" }}>
              <h3 style={{ margin: 0, fontSize: "15px", color: "#f8fafc", fontWeight: 600 }}>
                Truthful Detected Runners ({runners.length})
              </h3>
              <span style={{ fontSize: "12px", color: "#64748b" }}>
                Discovery reflects actual workspace contents without shell fallback
              </span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: "12px" }}>
              {runners.map((r) => (
                <div
                  key={r.runner_id}
                  style={{
                    background: "#0b1329",
                    border: r.available ? "1px solid #1e293b" : "1px solid #1e293b88",
                    borderRadius: "8px",
                    padding: "14px",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "space-between",
                    gap: "10px",
                    opacity: r.available ? 1 : 0.65,
                  }}
                >
                  <div>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" }}>
                      <span style={{ fontWeight: 600, color: "#f1f5f9", fontSize: "13px" }}>{r.name}</span>
                      <span
                        style={{
                          fontSize: "10px",
                          fontWeight: 600,
                          padding: "2px 8px",
                          borderRadius: "10px",
                          background: r.available ? "#064e3b" : "#334155",
                          color: r.available ? "#34d399" : "#94a3b8",
                        }}
                      >
                        {r.available ? "AVAILABLE" : "UNAVAILABLE"}
                      </span>
                    </div>
                    <div style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "8px" }}>
                      {r.description}
                    </div>
                    <div
                      style={{
                        background: "#050b14",
                        padding: "6px 8px",
                        borderRadius: "4px",
                        fontFamily: "monospace",
                        fontSize: "11px",
                        color: "#38bdf8",
                        wordBreak: "break-all",
                      }}
                    >
                      {r.executable} {r.default_args.join(" ")}
                    </div>
                    {r.version && (
                      <div style={{ fontSize: "10px", color: "#64748b", marginTop: "4px" }}>
                        Version: {r.version}
                      </div>
                    )}
                    {r.unavailability_reason && (
                      <div style={{ fontSize: "10px", color: "#f87171", marginTop: "4px" }}>
                        Reason: {r.unavailability_reason}
                      </div>
                    )}
                  </div>
                  <div style={{ display: "flex", justifyContent: "flex-end" }}>
                    <button
                      onClick={() => handlePreparePlanFromRunner(r)}
                      disabled={!r.available || isRunning}
                      style={{
                        background: r.available ? "#0284c7" : "#334155",
                        color: "#f8fafc",
                        border: "none",
                        borderRadius: "6px",
                        padding: "6px 12px",
                        fontSize: "11px",
                        fontWeight: 600,
                        cursor: r.available && !isRunning ? "pointer" : "not-allowed",
                      }}
                    >
                      Prepare & Review Plan
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Section 2: Prepared Execution Plans */}
          {executionPlans.length > 0 && (
            <div>
              <h3 style={{ margin: "0 0 12px 0", fontSize: "15px", color: "#f8fafc", fontWeight: 600 }}>
                Prepared Execution Plans ({executionPlans.length})
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {executionPlans.slice(0, 5).map((p) => (
                  <div
                    key={p.execution_plan_id}
                    style={{
                      background: "#090f1f",
                      border: "1px solid #1e293b",
                      borderRadius: "6px",
                      padding: "12px 16px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                    }}
                  >
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                        <span
                          style={{
                            fontSize: "10px",
                            fontWeight: 700,
                            padding: "2px 6px",
                            borderRadius: "4px",
                            background: p.kind === "TEST" ? "#1e3a8a" : "#312e81",
                            color: "#93c5fd",
                          }}
                        >
                          {p.kind}
                        </span>
                        <span style={{ fontSize: "13px", fontWeight: 600, color: "#f1f5f9" }}>
                          {p.execution_plan_id}
                        </span>
                        <span
                          style={{
                            fontSize: "11px",
                            color:
                              p.status === "PREPARED"
                                ? "#38bdf8"
                                : p.status === "SUCCEEDED"
                                ? "#34d399"
                                : p.status === "FAILED"
                                ? "#f87171"
                                : "#94a3b8",
                          }}
                        >
                          ● {p.status}
                        </span>
                      </div>
                      <div style={{ fontFamily: "monospace", fontSize: "11px", color: "#cbd5e1" }}>
                        {p.executable} {p.arguments.join(" ")}
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: "8px" }}>
                      <button
                        onClick={() => setActivePlanModal(p)}
                        style={{
                          background: "#1e293b",
                          color: "#cbd5e1",
                          border: "none",
                          borderRadius: "4px",
                          padding: "6px 12px",
                          fontSize: "11px",
                          cursor: "pointer",
                        }}
                      >
                        Inspect
                      </button>
                      <button
                        onClick={() => handleExecutePlan(p)}
                        disabled={isRunning}
                        style={{
                          background: isRunning ? "#334155" : "#16a34a",
                          color: "#ffffff",
                          border: "none",
                          borderRadius: "4px",
                          padding: "6px 14px",
                          fontSize: "11px",
                          fontWeight: 600,
                          cursor: isRunning ? "not-allowed" : "pointer",
                        }}
                      >
                        {p.kind === "TEST" ? "Run Tests" : p.kind === "BUILD" ? "Run Build" : "Run Plan"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Section 3: Execution History & Structured Results */}
          <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px" }}>
              <h3 style={{ margin: 0, fontSize: "15px", color: "#f8fafc", fontWeight: 600 }}>
                Execution Provenance & Results ({commandResults.length} runs • {buildResults.length} builds)
              </h3>
              <span style={{ fontSize: "12px", color: "#64748b" }}>
                Preserves exact process exit codes, durations, and output streams
              </span>
            </div>
            {commandResults.length === 0 ? (
              <div
                style={{
                  background: "#090f1f",
                  border: "1px dashed #1e293b",
                  borderRadius: "8px",
                  padding: "32px",
                  textAlign: "center",
                  color: "#64748b",
                  fontSize: "13px",
                }}
              >
                No execution runs recorded yet. Select a runner above and click "Prepare & Review Plan".
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                {commandResults.map((cmd) => {
                  const associatedTest = testRuns.find((t) => t.command_id === cmd.command_id);
                  const isSelected = selectedResult?.command_id === cmd.command_id;
                  return (
                    <div
                      key={cmd.command_id}
                      style={{
                        background: isSelected ? "#0e172e" : "#090f1f",
                        border: isSelected ? "1px solid #0284c7" : "1px solid #1e293b",
                        borderRadius: "8px",
                        padding: "14px 18px",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          cursor: "pointer",
                        }}
                        onClick={() => setSelectedResult(isSelected ? null : cmd)}
                      >
                        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                          <span
                            style={{
                              fontSize: "11px",
                              fontWeight: 700,
                              padding: "3px 8px",
                              borderRadius: "4px",
                              background:
                                cmd.status === "SUCCEEDED"
                                  ? "#064e3b"
                                  : cmd.status === "FAILED"
                                  ? "#7f1d1d"
                                  : cmd.status === "TIMED_OUT"
                                  ? "#78350f"
                                  : cmd.status === "CANCELLED"
                                  ? "#334155"
                                  : "#4c1d95",
                              color:
                                cmd.status === "SUCCEEDED"
                                  ? "#34d399"
                                  : cmd.status === "FAILED"
                                  ? "#f87171"
                                  : cmd.status === "TIMED_OUT"
                                  ? "#fbbf24"
                                  : cmd.status === "CANCELLED"
                                  ? "#94a3b8"
                                  : "#c084fc",
                            }}
                          >
                            {cmd.status}
                          </span>
                          <span style={{ fontWeight: 600, color: "#f1f5f9", fontSize: "13px" }}>
                            {cmd.executable} {cmd.arguments.join(" ")}
                          </span>
                          <span style={{ fontSize: "12px", color: "#64748b" }}>
                            (exit: {cmd.exit_code ?? "N/A"} • {(cmd.duration_ms / 1000).toFixed(2)}s)
                          </span>
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                          {associatedTest && associatedTest.total !== null && (
                            <span style={{ fontSize: "12px", color: "#94a3b8" }}>
                              {associatedTest.passed ?? 0} passed / {associatedTest.total} total
                            </span>
                          )}
                          <span style={{ fontSize: "12px", color: "#38bdf8" }}>
                            {isSelected ? "▲ Collapse" : "▼ Expand Output"}
                          </span>
                        </div>
                      </div>

                      {/* Expanded details */}
                      {isSelected && (
                        <div style={{ marginTop: "14px", borderTop: "1px solid #1e293b", paddingTop: "14px" }}>
                          {/* Test breakdown if present */}
                          {associatedTest && (
                            <div
                              style={{
                                background: "#050b14",
                                borderRadius: "6px",
                                padding: "10px 14px",
                                marginBottom: "12px",
                                display: "flex",
                                gap: "18px",
                                fontSize: "12px",
                              }}
                            >
                              <span style={{ color: "#34d399" }}>Passed: {associatedTest.passed ?? "N/A"}</span>
                              <span style={{ color: "#f87171" }}>Failed: {associatedTest.failed ?? "N/A"}</span>
                              <span style={{ color: "#fbbf24" }}>Errors: {associatedTest.errors ?? "N/A"}</span>
                              <span style={{ color: "#94a3b8" }}>Skipped: {associatedTest.skipped ?? "N/A"}</span>
                              <span style={{ color: "#f1f5f9" }}>Total: {associatedTest.total ?? "N/A"}</span>
                            </div>
                          )}

                          {/* Stdout view */}
                          <div style={{ marginBottom: "10px" }}>
                            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "4px" }}>
                              <span style={{ fontSize: "11px", fontWeight: 600, color: "#94a3b8" }}>
                                STDOUT {cmd.stdout_truncated ? "(Truncated Preview)" : ""}
                              </span>
                              <button
                                onClick={() => navigator.clipboard.writeText(cmd.stdout)}
                                style={{ background: "none", border: "none", color: "#38bdf8", cursor: "pointer", fontSize: "11px" }}
                              >
                                Copy stdout
                              </button>
                            </div>
                            <pre
                              style={{
                                background: "#050b14",
                                border: "1px solid #1e293b",
                                borderRadius: "4px",
                                padding: "10px",
                                fontSize: "11px",
                                color: "#e2e8f0",
                                maxHeight: "200px",
                                overflowY: "auto",
                                whiteSpace: "pre-wrap",
                                margin: 0,
                              }}
                            >
                              {cmd.stdout || "(empty stdout)"}
                            </pre>
                          </div>

                          {/* Stderr view */}
                          {cmd.stderr && (
                            <div>
                              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "4px" }}>
                                <span style={{ fontSize: "11px", fontWeight: 600, color: "#f87171" }}>
                                  STDERR {cmd.stderr_truncated ? "(Truncated Preview)" : ""}
                                </span>
                                <button
                                  onClick={() => navigator.clipboard.writeText(cmd.stderr)}
                                  style={{ background: "none", border: "none", color: "#38bdf8", cursor: "pointer", fontSize: "11px" }}
                                >
                                  Copy stderr
                                </button>
                              </div>
                              <pre
                                style={{
                                  background: "#180909",
                                  border: "1px solid #7f1d1d",
                                  borderRadius: "4px",
                                  padding: "10px",
                                  fontSize: "11px",
                                  color: "#fca5a5",
                                  maxHeight: "150px",
                                  overflowY: "auto",
                                  whiteSpace: "pre-wrap",
                                  margin: 0,
                                }}
                              >
                                {cmd.stderr}
                              </pre>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Modal: Explicit Plan Review & Approval */}
          {activePlanModal && (
            <div
              style={{
                position: "fixed",
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
                background: "rgba(0,0,0,0.75)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                zIndex: 1000,
              }}
            >
              <div
                style={{
                  background: "#0f172a",
                  border: "1px solid #1e293b",
                  borderRadius: "8px",
                  padding: "24px",
                  maxWidth: "600px",
                  width: "100%",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
                  <h3 style={{ margin: 0, fontSize: "16px", color: "#f8fafc" }}>
                    Explicit Plan Review: {activePlanModal.kind}
                  </h3>
                  <button
                    onClick={() => setActivePlanModal(null)}
                    style={{ background: "none", border: "none", color: "#94a3b8", cursor: "pointer", fontSize: "18px" }}
                  >
                    ✕
                  </button>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "12px", marginBottom: "20px", fontSize: "13px" }}>
                  <div>
                    <span style={{ color: "#64748b" }}>Executable: </span>
                    <code style={{ color: "#38bdf8" }}>{activePlanModal.executable}</code>
                  </div>
                  <div>
                    <span style={{ color: "#64748b" }}>Arguments: </span>
                    <code style={{ color: "#38bdf8" }}>{JSON.stringify(activePlanModal.arguments)}</code>
                  </div>
                  <div>
                    <span style={{ color: "#64748b" }}>Working Directory: </span>
                    <span style={{ color: "#f1f5f9" }}>{activePlanModal.working_directory}</span>
                  </div>
                  <div>
                    <span style={{ color: "#64748b" }}>Timeout: </span>
                    <span style={{ color: "#f1f5f9" }}>{activePlanModal.timeout_seconds}s</span>
                  </div>
                  <div>
                    <span style={{ color: "#64748b" }}>Environment Policy: </span>
                    <span style={{ color: "#f1f5f9" }}>{activePlanModal.environment_policy}</span>
                  </div>
                </div>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "12px" }}>
                  <button
                    onClick={() => setActivePlanModal(null)}
                    style={{
                      background: "#1e293b",
                      color: "#cbd5e1",
                      border: "none",
                      borderRadius: "6px",
                      padding: "8px 16px",
                      cursor: "pointer",
                      fontSize: "12px",
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => handleExecutePlan(activePlanModal)}
                    disabled={isRunning}
                    style={{
                      background: "#0284c7",
                      color: "#ffffff",
                      border: "none",
                      borderRadius: "6px",
                      padding: "8px 18px",
                      cursor: isRunning ? "not-allowed" : "pointer",
                      fontWeight: 600,
                      fontSize: "12px",
                    }}
                  >
                    {isRunning ? "Running..." : "Authorize & Execute"}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === "search" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search workspace text..."
                    style={{
                      flex: 1,
                      background: "#0f172a",
                      border: "1px solid #334155",
                      borderRadius: 6,
                      padding: "8px 12px",
                      fontSize: 14,
                      color: "#f8fafc",
                    }}
                  />
                  <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "#94a3b8", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={searchRegex}
                      onChange={(e) => setSearchRegex(e.target.checked)}
                    />
                    Regex
                  </label>
                  <button
                    onClick={handleSearch}
                    style={{
                      background: "#2563eb",
                      color: "#fff",
                      border: "none",
                      borderRadius: 6,
                      padding: "8px 16px",
                      fontSize: 13,
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    Search
                  </button>
                </div>

                {searchResults && (
                  <div>
                    <div style={{ fontSize: 13, color: "#94a3b8", marginBottom: 12 }}>
                      Found <strong style={{ color: "#f8fafc" }}>{searchResults.total_matches}</strong> matches across{" "}
                      <strong>{searchResults.files_scanned}</strong> files.
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {searchResults.matches.map((m, idx) => (
                        <div
                          key={idx}
                          onClick={() => handleOpenFile(m.path)}
                          style={{
                            background: "#0f172a",
                            border: "1px solid #1e293b",
                            borderRadius: 6,
                            padding: "8px 12px",
                            cursor: "pointer",
                          }}
                          onMouseEnter={(e) => (e.currentTarget.style.borderColor = "#38bdf8")}
                          onMouseLeave={(e) => (e.currentTarget.style.borderColor = "#1e293b")}
                        >
                          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, marginBottom: 4 }}>
                            <span style={{ color: "#38bdf8", fontWeight: 600 }}>{m.path}</span>
                            <span style={{ color: "#64748b" }}>Line {m.line_number}</span>
                          </div>
                          <div style={{ fontFamily: "monospace", fontSize: 12, color: "#cbd5e1", whiteSpace: "pre-wrap" }}>
                            {m.snippet}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Right Panel: Repository & Provenance Inspector */}
        <div
          style={{
            width: 280,
            borderLeft: "1px solid #1e293b",
            background: "#0b1120",
            padding: 16,
            display: "flex",
            flexDirection: "column",
            gap: 16,
            overflowY: "auto",
          }}
        >
          <div style={{ fontSize: 12, fontWeight: 700, color: "#94a3b8", textTransform: "uppercase" }}>
            Repository Status
          </div>

          {repoStatus ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 13 }}>
              <div style={{ background: "#0f172a", padding: 10, borderRadius: 6, border: "1px solid #1e293b" }}>
                <div style={{ color: "#64748b", fontSize: 11 }}>KIND</div>
                <div style={{ fontWeight: 600, color: "#f8fafc", textTransform: "uppercase" }}>
                  {repoStatus.repository_kind}
                </div>
              </div>

              {repoStatus.repository_kind === "git" ? (
                <>
                  <div style={{ background: "#0f172a", padding: 10, borderRadius: 6, border: "1px solid #1e293b" }}>
                    <div style={{ color: "#64748b", fontSize: 11 }}>BRANCH</div>
                    <div style={{ fontWeight: 600, color: "#38bdf8" }}>
                      {repoStatus.current_branch || "HEAD (detached)"}
                    </div>
                  </div>

                  <div style={{ background: "#0f172a", padding: 10, borderRadius: 6, border: "1px solid #1e293b" }}>
                    <div style={{ color: "#64748b", fontSize: 11 }}>HEAD COMMIT</div>
                    <div style={{ fontFamily: "monospace", fontSize: 12, color: "#cbd5e1" }}>
                      {repoStatus.head_commit || "None"}
                    </div>
                  </div>

                  <div style={{ background: "#0f172a", padding: 10, borderRadius: 6, border: "1px solid #1e293b" }}>
                    <div style={{ color: "#64748b", fontSize: 11, marginBottom: 6 }}>FILE COUNTS</div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, fontSize: 12 }}>
                      <div>Tracked: <strong>{repoStatus.tracked_count}</strong></div>
                      <div>Untracked: <strong>{repoStatus.untracked_count}</strong></div>
                      <div>Modified: <strong style={{ color: "#f59e0b" }}>{repoStatus.modified_count}</strong></div>
                      <div>Staged: <strong style={{ color: "#10b981" }}>{repoStatus.staged_count}</strong></div>
                    </div>
                  </div>
                </>
              ) : (
                <div style={{ background: "#0f172a", padding: 10, borderRadius: 6, border: "1px solid #1e293b" }}>
                  <div style={{ color: "#64748b", fontSize: 11 }}>FILES</div>
                  <div style={{ fontWeight: 600 }}>{repoStatus.tracked_count} total scanned</div>
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: "#64748b", fontSize: 12 }}>No repository data</div>
          )}

          <div style={{ borderTop: "1px solid #1e293b", paddingTop: 14 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: "#94a3b8", textTransform: "uppercase", marginBottom: 8 }}>
              Workspace Provenance
            </div>
            {selectedWorkspace && (
              <div style={{ fontSize: 12, color: "#94a3b8", display: "flex", flexDirection: "column", gap: 6 }}>
                <div>ID: <code style={{ color: "#cbd5e1" }}>{selectedWorkspace.workspace_id}</code></div>
                <div>Created: <span style={{ color: "#cbd5e1" }}>{new Date(selectedWorkspace.created_at).toLocaleDateString()}</span></div>
                <div>Status: <span style={{ color: "#4ade80", fontWeight: 600 }}>{selectedWorkspace.status}</span></div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Apply Confirmation Modal */}
      {applyModalDiff && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}>
          <div style={{ background: "#0f172a", border: "1px solid #334155", borderRadius: 8, padding: 24, width: 500, display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ fontSize: 16, fontWeight: 700, color: "#f8fafc" }}>Confirm File Mutation & Patch Apply</div>
            <div style={{ fontSize: 13, color: "#94a3b8", lineHeight: "20px" }}>
              You are about to apply proposed patch <code style={{ color: "#38bdf8" }}>{applyModalDiff.diff_id}</code> to your working tree filesystem.
            </div>

            <div style={{ background: "#1e293b", borderRadius: 6, padding: 12, fontSize: 13 }}>
              <div style={{ fontWeight: 600, color: "#cbd5e1", marginBottom: 6 }}>Changes to be applied:</div>
              <div style={{ display: "flex", gap: 14, color: "#94a3b8" }}>
                <span>Files: <strong style={{ color: "#fff" }}>{applyModalDiff.files.length}</strong></span>
                <span>Additions: <strong style={{ color: "#4ade80" }}>+{applyModalDiff.total_additions}</strong></span>
                <span>Deletions: <strong style={{ color: "#f87171" }}>-{applyModalDiff.total_deletions}</strong></span>
              </div>
            </div>

            <div style={{ background: "#451a03", border: "1px solid #78350f", borderRadius: 6, padding: 10, fontSize: 12, color: "#fed7aa" }}>
              ℹ️ Precondition validation will verify all file base hashes before modifying disk. An automatic rollback will trigger if any write fails.
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 6 }}>
              <button
                onClick={() => setApplyModalDiff(null)}
                style={{ background: "#1e293b", color: "#94a3b8", border: "1px solid #334155", borderRadius: 6, padding: "8px 16px", fontSize: 13, cursor: "pointer" }}
              >
                Cancel
              </button>
              <button
                onClick={handleApplyDiff}
                style={{ background: "#16a34a", color: "#fff", border: "none", borderRadius: 6, padding: "8px 18px", fontSize: 13, fontWeight: 600, cursor: "pointer" }}
              >
                Confirm Apply
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Rollback Confirmation Modal */}
      {rollbackModalApply && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}>
          <div style={{ background: "#0f172a", border: "1px solid #334155", borderRadius: 8, padding: 24, width: 500, display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ fontSize: 16, fontWeight: 700, color: "#f8fafc" }}>Confirm Rollback of Applied Patch</div>
            <div style={{ fontSize: 13, color: "#94a3b8", lineHeight: "20px" }}>
              Rollback will restore files changed by apply <code style={{ color: "#38bdf8" }}>{rollbackModalApply.apply_id}</code> back to their original state.
            </div>

            <div style={{ background: "#7f1d1d", border: "1px solid #991b1b", borderRadius: 6, padding: 10, fontSize: 12, color: "#fecaca" }}>
              ⚠️ Rollback conflict protection is active: if any file has been modified since the apply, rollback will safely abort to prevent overwriting your work.
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 6 }}>
              <button
                onClick={() => setRollbackModalApply(null)}
                style={{ background: "#1e293b", color: "#94a3b8", border: "1px solid #334155", borderRadius: 6, padding: "8px 16px", fontSize: 13, cursor: "pointer" }}
              >
                Cancel
              </button>
              <button
                onClick={handleRollbackApply}
                style={{ background: "#dc2626", color: "#fff", border: "none", borderRadius: 6, padding: "8px 18px", fontSize: 13, fontWeight: 600, cursor: "pointer" }}
              >
                Confirm Rollback
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Open workspace modal */}
      {showOpenModal && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}>
          <div style={{ background: "#0f172a", border: "1px solid #334155", borderRadius: 8, padding: 24, width: 460, display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ fontSize: 16, fontWeight: 700 }}>Open Engineering Workspace</div>
            <form onSubmit={handleOpenWorkspace} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div>
                <label style={{ display: "block", fontSize: 12, color: "#94a3b8", marginBottom: 4 }}>
                  Directory Root Path (leave empty for project default)
                </label>
                <input
                  type="text"
                  value={newRootPath}
                  onChange={(e) => setNewRootPath(e.target.value)}
                  placeholder="e.g. C:/path/to/repo"
                  style={{ width: "100%", background: "#1e293b", border: "1px solid #334155", borderRadius: 6, padding: "8px 12px", color: "#f8fafc", fontSize: 13 }}
                />
              </div>

              <div>
                <label style={{ display: "block", fontSize: 12, color: "#94a3b8", marginBottom: 4 }}>
                  Workspace Display Name
                </label>
                <input
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="e.g. Main Backend Repo"
                  style={{ width: "100%", background: "#1e293b", border: "1px solid #334155", borderRadius: 6, padding: "8px 12px", color: "#f8fafc", fontSize: 13 }}
                />
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 8 }}>
                <button
                  type="button"
                  onClick={() => setShowOpenModal(false)}
                  style={{ background: "#1e293b", color: "#94a3b8", border: "1px solid #334155", borderRadius: 6, padding: "8px 16px", fontSize: 13, cursor: "pointer" }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  style={{ background: "#2563eb", color: "#fff", border: "none", borderRadius: 6, padding: "8px 16px", fontSize: 13, fontWeight: 600, cursor: "pointer" }}
                >
                  Open
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
