import React, { useEffect, useState } from "react";
import { amca } from "./api";
import type {
  ArtifactExportCapabilities,
  ArtifactExportRecord,
  ArtifactExportResult,
  ArtifactItem,
  ArtifactPreview,
  ArtifactVersionItem,
} from "./types";
import { KnowledgeSearchPanel } from "./KnowledgeSearchPanel";
import { artifactWorkflowTrace } from "./crossModuleWorkflowContinuityModel";
import {
  fromArtifactItem,
  knowledgeAssets,
  knowledgeCollections,
  knowledgeEmptyState,
  knowledgeRelations,
  provenanceChain,
  KNOWLEDGE_BACKEND_NOTE,
} from "./knowledgeUxModel";
import {
  EXPORT_EMPTY_STATE,
  ARTIFACT_CENTER_FILTERS,
  artifactCenterFilterForType,
  type ArtifactCenterFilter,
  localStorageFormatStore,
} from "./feUxBatchModel";

type ArtifactFilter = ArtifactCenterFilter;

interface ArtifactCenterProps {
  projectId: string;
  /** W1 deep link: recorded artifact type to preselect in the existing filter tabs. */
  initialTypeFilter?: string | null;
  /** W1 deep link marker from the Dashboard's Recent outputs panel. */
  focusSource?: "dashboard_recent_outputs" | null;
  /** W3: offered when the export surface has nothing to export yet. */
  onOpenProjects?: () => void;
}

export const ArtifactCenter: React.FC<ArtifactCenterProps> = ({ projectId, initialTypeFilter, onOpenProjects }) => {
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  const [filter, setFilter] = useState<ArtifactFilter>(
    initialTypeFilter ? artifactCenterFilterForType(initialTypeFilter) : "all",
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Inspector state
  const [activeTab, setActiveTab] = useState<"preview" | "versions" | "export" | "knowledge">("preview");
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const [bibSource, setBibSource] = useState<string | null>(null);
  const [versions, setVersions] = useState<ArtifactVersionItem[]>([]);
  const [capabilities, setCapabilities] = useState<ArtifactExportCapabilities | null>(null);
  const [exportHistory, setExportHistory] = useState<ArtifactExportRecord[]>([]);

  // Export action state
  const [exportingFormat, setExportingFormat] = useState<string | null>(null);
  const [customFilename, setCustomFilename] = useState("");
  const [lastExportResult, setLastExportResult] = useState<ArtifactExportResult | null>(null);
  const [restoreInProgress, setRestoreInProgress] = useState<number | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  // W4: local per-project format preference (frontend-only, no backend).
  const formatStore = localStorageFormatStore();
  const [lastFormat, setLastFormat] = useState<string | null>(() => formatStore.read(projectId));
  useEffect(() => {
    setLastFormat(formatStore.read(projectId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const fetchArtifacts = async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await amca.listArtifacts(projectId);
      setArtifacts(res.artifacts || []);
      if (res.artifacts?.length && !selectedArtifactId) {
        setSelectedArtifactId(res.artifacts[0].artifact_id);
      }
    } catch (err: any) {
      setError(err?.message || "Failed to load project artifacts from backend.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchArtifacts();
  }, [projectId]);

  const selectedArtifact = artifacts.find((a) => a.artifact_id === selectedArtifactId);

  // Load inspector details when selected artifact changes
  useEffect(() => {
    if (!projectId || !selectedArtifactId) {
      setPreview(null);
      setVersions([]);
      setCapabilities(null);
      setExportHistory([]);
      return;
    }

    let cancelled = false;
    setBibSource(null);
    const loadDetails = async () => {
      try {
        if (selectedArtifact?.artifact_type === "bib") {
          // Native bibliography uses the existing source endpoint, not the
          // richer document-preview routes unavailable in this backend.
          const res = await amca.artifactSource(projectId, selectedArtifactId);
          if (!cancelled) {
            setBibSource(res.source.content);
            setPreview(null);
            setVersions([]);
            setCapabilities(null);
            setExportHistory([]);
          }
          return;
        }
        const [prevRes, versRes, capsRes, histRes] = await Promise.all([
          amca.getArtifactPreview(projectId, selectedArtifactId),
          amca.listArtifactVersions(projectId, selectedArtifactId),
          amca.getArtifactExportCapabilities(projectId, selectedArtifactId),
          amca.listArtifactExports(projectId, selectedArtifactId),
        ]);
        setPreview(prevRes.preview);
        setVersions(versRes.versions || []);
        setCapabilities(capsRes.capabilities);
        setExportHistory(histRes.exports || []);
      } catch (err: any) {
        console.error("Failed to load artifact details", err);
      }
    };

    loadDetails();
    return () => {
      cancelled = true;
    };
  }, [projectId, selectedArtifactId]);

  const handleRestore = async (targetVersionNumber: number) => {
    if (!selectedArtifact) return;
    const currentVersion = selectedArtifact.current_version_number;
    if (!window.confirm(`Restore Version ${targetVersionNumber} as new Version ${currentVersion + 1}? Current version and full history will remain preserved.`)) {
      return;
    }

    setRestoreInProgress(targetVersionNumber);
    setActionNotice(null);
    try {
      const res = await amca.restoreArtifactVersion(
        projectId,
        selectedArtifact.artifact_id,
        targetVersionNumber,
        currentVersion
      );
      setActionNotice(`Successfully restored v${targetVersionNumber} as new version v${res.restored_version.version_number}!`);
      await fetchArtifacts();
      // Reload details
      const [prevRes, versRes, histRes] = await Promise.all([
        amca.getArtifactPreview(projectId, selectedArtifact.artifact_id),
        amca.listArtifactVersions(projectId, selectedArtifact.artifact_id),
        amca.listArtifactExports(projectId, selectedArtifact.artifact_id),
      ]);
      setPreview(prevRes.preview);
      setVersions(versRes.versions || []);
      setExportHistory(histRes.exports || []);
    } catch (err: any) {
      if (err?.message?.includes("conflict") || err?.status === 409) {
        alert("Version Conflict: Artifact was updated by another process. Refreshing latest state.");
        await fetchArtifacts();
      } else {
        alert(`Failed to restore version: ${err?.message || "Unknown error"}`);
      }
    } finally {
      setRestoreInProgress(null);
    }
  };

  const downloadBib = () => {
    if (bibSource === null || !selectedArtifact) return;
    const url = URL.createObjectURL(new Blob([bibSource], { type: "text/x-bibtex" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${(customFilename.trim() || selectedArtifact.title || "Bibliography").replace(/[<>:"/\\|?*]/g, "_").replace(/\.bib$/i, "")}.bib`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const handleExport = async (format: string) => {
    if (!selectedArtifact) return;
    setExportingFormat(format);
    setLastExportResult(null);
    try {
      const res = await amca.exportArtifactVersion(
        projectId,
        selectedArtifact.artifact_id,
        selectedArtifact.current_version_number,
        format,
        customFilename.trim() || undefined
      );
      setLastExportResult(res.export);
      formatStore.write(projectId, format);
      setLastFormat(format);
      const histRes = await amca.listArtifactExports(projectId, selectedArtifact.artifact_id);
      setExportHistory(histRes.exports || []);
    } catch (err: any) {
      alert(`Export failed: ${err?.message || "Unknown error"}`);
    } finally {
      setExportingFormat(null);
    }
  };

  const filteredArtifacts = artifacts.filter((a) => {
    const matchesFilter = filter === "all" || a.artifact_type.toLowerCase() === filter;
    const matchesSearch =
      !searchQuery.trim() ||
      a.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      a.artifact_id.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesFilter && matchesSearch;
  });

  return (
    <div className="artifact-center-container" style={{ display: "flex", height: "100%", width: "100%", overflow: "hidden" }}>
      {/* Left Sidebar: Artifact List */}
      <div
        className="artifact-sidebar"
        style={{
          width: "340px",
          minWidth: "300px",
          borderRight: "1px solid var(--border-color, #2d3748)",
          display: "flex",
          flexDirection: "column",
          background: "var(--bg-secondary, #1a202c)",
        }}
      >
        <div style={{ padding: "12px", borderBottom: "1px solid var(--border-color, #2d3748)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
            <h3 style={{ margin: 0, fontSize: "16px", fontWeight: "600" }}>Artifact Center</h3>
            <button
              onClick={fetchArtifacts}
              disabled={loading}
              style={{
                padding: "4px 8px",
                fontSize: "12px",
                borderRadius: "4px",
                cursor: "pointer",
                background: "var(--btn-bg, #2b6cb0)",
                color: "#fff",
                border: "none",
              }}
            >
              {loading ? "Refreshing..." : "Refresh"}
            </button>
          </div>

          {/* Search input */}
          <input
            type="text"
            placeholder="Search artifacts..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              width: "100%",
              padding: "6px 8px",
              borderRadius: "4px",
              border: "1px solid var(--border-color, #4a5568)",
              background: "var(--input-bg, #2d3748)",
              color: "#fff",
              fontSize: "13px",
              boxSizing: "border-box",
              marginBottom: "8px",
            }}
          />

          {/* Filter tabs */}
          <div style={{ display: "flex", gap: "4px", flexWrap: "wrap" }}>
            {(ARTIFACT_CENTER_FILTERS as readonly ArtifactFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                style={{
                  padding: "3px 8px",
                  fontSize: "11px",
                  borderRadius: "12px",
                  border: "none",
                  cursor: "pointer",
                  background: filter === f ? "var(--btn-bg, #3182ce)" : "var(--filter-bg, #4a5568)",
                  color: "#fff",
                  textTransform: "capitalize",
                }}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        {/* Artifact List */}
        <div style={{ flex: 1, overflowY: "auto", padding: "8px" }}>
          {loading && artifacts.length === 0 && (
            <div style={{ padding: "20px", textAlign: "center", color: "#a0aec0" }}>Loading artifacts...</div>
          )}

          {error && (
            <div style={{ padding: "12px", background: "rgba(229,62,62,0.2)", color: "#fc8181", borderRadius: "4px", margin: "8px" }}>
              <strong>Error:</strong> {error}
            </div>
          )}

          {!loading && !error && filteredArtifacts.length === 0 && (
            <div style={{ padding: "20px", textAlign: "center", color: "#a0aec0" }}>
              {artifacts.length === 0 ? "No artifacts created in this project yet." : "No artifacts match filter."}
            </div>
          )}

          {filteredArtifacts.map((art) => {
            const isSelected = art.artifact_id === selectedArtifactId;
            return (
              <div
                key={art.artifact_id}
                onClick={() => setSelectedArtifactId(art.artifact_id)}
                style={{
                  padding: "10px",
                  marginBottom: "6px",
                  borderRadius: "6px",
                  cursor: "pointer",
                  background: isSelected ? "var(--card-selected, #2b6cb0)" : "var(--card-bg, #2d3748)",
                  border: isSelected ? "1px solid #63b3ed" : "1px solid transparent",
                  transition: "background 0.15s",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontWeight: "600", fontSize: "14px", color: "#fff" }}>{art.title || "Untitled"}</span>
                  <span
                    style={{
                      fontSize: "10px",
                      padding: "2px 6px",
                      borderRadius: "4px",
                      background: "rgba(0,0,0,0.3)",
                      color: "#e2e8f0",
                      textTransform: "uppercase",
                    }}
                  >
                    {art.artifact_type}
                  </span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: "4px", fontSize: "11px", color: "#cbd5e0" }}>
                  <span>Version: v{art.current_version_number}</span>
                  <span>{new Date(art.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Right Content: Artifact Inspector */}
      {(() => {
        // v0.8.0 continuity: artifact → originating workflow trace from
        // recorded linkage only; honest degradation when absent.
        const trace = artifactWorkflowTrace(selectedArtifact?.artifact_id ?? "", [], projectId, null);
        return selectedArtifact ? (
          <div className="cwf-trace">
            <small>
              {trace.originatingWorkflowId
                ? `Originating workflow: ${trace.originatingWorkflowId.slice(0, 18)}… · project ${trace.projectId?.slice(0, 12)}${trace.recordedCreator ? ` · created by ${trace.recordedCreator}` : ""}`
                : trace.unavailableReason}
            </small>
          </div>
        ) : null;
      })()}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", background: "var(--bg-main, #171923)", overflowY: "auto" }}>
        {!selectedArtifact ? (
          artifacts.length === 0 && !loading && !error ? (
            // W3: the recorded dead-end — no artifacts meant no next step.
            <div style={{ padding: "40px", textAlign: "center", color: "#a0aec0" }}>
              <p style={{ margin: "0 0 6px" }}><strong style={{ color: "#fff" }}>{EXPORT_EMPTY_STATE.title}</strong></p>
              <p style={{ margin: "0 0 14px" }}>{EXPORT_EMPTY_STATE.detail}</p>
              {onOpenProjects && (
                <button
                  className="secondary-button"
                  onClick={onOpenProjects}
                  style={{ padding: "6px 14px", fontSize: "13px", borderRadius: "4px", border: "1px solid #4a5568", background: "#2d3748", color: "#fff", cursor: "pointer" }}
                >
                  {EXPORT_EMPTY_STATE.actionLabel}
                </button>
              )}
            </div>
          ) : (
            <div style={{ padding: "40px", textAlign: "center", color: "#a0aec0" }}>
              Select an artifact on the left to inspect versions and export.
            </div>
          )
        ) : (
          <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: "16px" }}>
            {/* Header / Meta Card */}
            <div
              style={{
                background: "var(--card-bg, #2d3748)",
                borderRadius: "8px",
                padding: "16px",
                border: "1px solid var(--border-color, #4a5568)",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div>
                  <h2 style={{ margin: "0 0 4px 0", fontSize: "20px", color: "#fff" }}>{selectedArtifact.title}</h2>
                  <div style={{ display: "flex", gap: "8px", alignItems: "center", fontSize: "12px", color: "#cbd5e0" }}>
                    <span style={{ padding: "2px 6px", borderRadius: "4px", background: "#3182ce", color: "#fff", textTransform: "uppercase" }}>
                      {selectedArtifact.artifact_type}
                    </span>
                    <span>ID: {selectedArtifact.artifact_id}</span>
                    <span>Current Version: <strong>v{selectedArtifact.current_version_number}</strong></span>
                  </div>
                </div>
                <div style={{ textAlign: "right", fontSize: "12px", color: "#a0aec0" }}>
                  <div>Updated: {new Date(selectedArtifact.updated_at).toLocaleString()}</div>
                  <div>Created: {new Date(selectedArtifact.created_at).toLocaleString()}</div>
                </div>
              </div>

              {actionNotice && (
                <div style={{ marginTop: "12px", padding: "8px 12px", borderRadius: "4px", background: "rgba(72,187,120,0.2)", color: "#9ae6b4", fontSize: "13px" }}>
                  {actionNotice}
                </div>
              )}
            </div>

            {/* Navigation Tabs */}
            <div style={{ display: "flex", gap: "8px", borderBottom: "1px solid var(--border-color, #4a5568)", paddingBottom: "8px" }}>
              <button
                onClick={() => setActiveTab("preview")}
                style={{
                  padding: "6px 14px",
                  fontSize: "13px",
                  borderRadius: "4px",
                  border: "none",
                  cursor: "pointer",
                  background: activeTab === "preview" ? "#3182ce" : "transparent",
                  color: "#fff",
                  fontWeight: activeTab === "preview" ? "600" : "400",
                }}
              >
                Bounded Preview
              </button>
              <button
                onClick={() => setActiveTab("versions")}
                style={{
                  padding: "6px 14px",
                  fontSize: "13px",
                  borderRadius: "4px",
                  border: "none",
                  cursor: "pointer",
                  background: activeTab === "versions" ? "#3182ce" : "transparent",
                  color: "#fff",
                  fontWeight: activeTab === "versions" ? "600" : "400",
                }}
              >
                Version History ({versions.length})
              </button>
              <button
                onClick={() => setActiveTab("export")}
                style={{
                  padding: "6px 14px",
                  fontSize: "13px",
                  borderRadius: "4px",
                  border: "none",
                  cursor: "pointer",
                  background: activeTab === "export" ? "#3182ce" : "transparent",
                  color: "#fff",
                  fontWeight: activeTab === "export" ? "600" : "400",
                }}
              >
                Export Center
              </button>
              <button
                onClick={() => setActiveTab("knowledge")}
                style={{
                  padding: "6px 14px",
                  fontSize: "13px",
                  borderRadius: "4px",
                  border: "none",
                  cursor: "pointer",
                  background: activeTab === "knowledge" ? "#3182ce" : "transparent",
                  color: "#fff",
                  fontWeight: activeTab === "knowledge" ? "600" : "400",
                }}
              >
                Knowledge
              </button>
            </div>

            {/* Tab 4: Knowledge (BETA-B12) — browser, provenance, relations,
                safety, from existing artifact records only. */}
            {activeTab === "knowledge" && (() => {
              const inputs = artifacts.map(fromArtifactItem);
              const assets = knowledgeAssets(inputs);
              const selected = assets.find((asset) => asset.artifactId === selectedArtifactId) ?? assets[0] ?? null;
              const collections = knowledgeCollections(assets);
              const relations = selected ? knowledgeRelations(selected, assets) : [];
              const chain = selected ? provenanceChain(selected) : [];
              return (
                <div style={{ background: "var(--card-bg, #2d3748)", borderRadius: "8px", padding: "16px", border: "1px solid #4a5568" }}>
                  <KnowledgeSearchPanel workspaceId={projectId} />
                  <h4 style={{ margin: "18px 0 6px 0", color: "#fff" }}>Knowledge assets</h4>
                  <p style={{ margin: "0 0 10px 0", color: "#a0aec0", fontSize: "12px" }}>{KNOWLEDGE_BACKEND_NOTE}</p>
                  {assets.length === 0 ? (
                    <div style={{ border: "1px dashed #4a5568", borderRadius: "8px", padding: "18px", textAlign: "center" }}>
                      <strong style={{ display: "block", color: "#fff" }}>{knowledgeEmptyState("no_assets").title}</strong>
                      <span style={{ color: "#a0aec0", fontSize: "12px" }}>{knowledgeEmptyState("no_assets").detail}</span>
                    </div>
                  ) : (
                    <>
                      {collections.length > 0 && (
                        <p style={{ margin: "0 0 10px 0", color: "#a0aec0", fontSize: "12px" }}>
                          Collections: {collections.map((collection) => `${collection.label} (${collection.count})`).join(" · ")}
                        </p>
                      )}
                      <div style={{ display: "grid", gap: "8px", marginBottom: "14px" }}>
                        {assets.map((asset) => (
                          <button
                            key={asset.artifactId}
                            onClick={() => setSelectedArtifactId(asset.artifactId)}
                            style={{
                              textAlign: "left", padding: "8px 12px", borderRadius: "8px", cursor: "pointer",
                              border: `1px solid ${selected?.artifactId === asset.artifactId ? "#3182ce" : "#4a5568"}`,
                              background: selected?.artifactId === asset.artifactId ? "rgba(49,130,206,0.12)" : "transparent",
                              color: "#fff",
                            }}
                          >
                            <strong style={{ display: "block" }}>{asset.title}</strong>
                            <span style={{ fontSize: "11.5px", color: "#a0aec0" }}>
                              {asset.typeLabel} · v{asset.currentVersion} · {asset.createdByKind} ·{" "}
                              <b style={{ color: asset.shareability === "shareable" ? "#68d391" : "#f6ad55" }}>{asset.shareability}</b>
                            </span>
                          </button>
                        ))}
                      </div>
                      {selected && (
                        <>
                          <h4 style={{ margin: "0 0 6px 0", color: "#fff" }}>Provenance — {selected.title}</h4>
                          <div style={{ display: "grid", gap: "4px", marginBottom: "12px" }}>
                            {chain.map((step) => (
                              <div key={step.stage} style={{ display: "grid", gridTemplateColumns: "130px 1fr", gap: "8px", fontSize: "12px", color: step.recorded ? "#fff" : "#718096" }}>
                                <span style={{ fontWeight: 600 }}>{step.stage}</span>
                                <span>{step.recorded ? step.value : "not recorded — nothing inferred"}</span>
                              </div>
                            ))}
                          </div>
                          <div style={{ border: "1px solid #4a5568", borderRadius: "8px", padding: "10px 12px", marginBottom: "12px" }}>
                            <strong style={{ color: "#fff", fontSize: "12.5px" }}>
                              Sharing: {selected.shareability}
                            </strong>
                            <p style={{ margin: "4px 0 0", color: "#a0aec0", fontSize: "11.5px" }}>{selected.shareabilityReason}</p>
                            <p style={{ margin: "6px 0 0", color: "#a0aec0", fontSize: "11px" }}>
                              Allowed to share: templates and approved assets. Never shared: credentials, private memories, sensitive data.
                            </p>
                          </div>
                          <h4 style={{ margin: "0 0 6px 0", color: "#fff" }}>Related records</h4>
                          {relations.length ? (
                            <ul style={{ margin: 0, paddingLeft: "18px", color: "#e2e8f0", fontSize: "12px" }}>
                              {relations.slice(0, 8).map((relation, index) => (
                                <li key={`${relation.kind}-${relation.relatedId}-${index}`}>
                                  {relation.label} · <code style={{ fontSize: "10.5px" }}>{relation.relatedId.slice(0, 14)}…</code>
                                </li>
                              ))}
                            </ul>
                          ) : (
                            <span style={{ color: "#a0aec0", fontSize: "12px" }}>{knowledgeEmptyState("no_provenance").title} — no related records exist.</span>
                          )}
                        </>
                      )}
                    </>
                  )}
                </div>
              );
            })()}

            {/* Tab 1: Bounded Preview */}
            {activeTab === "preview" && (
              <div style={{ background: "var(--card-bg, #2d3748)", borderRadius: "8px", padding: "16px", border: "1px solid #4a5568" }}>
                {selectedArtifact.artifact_type === "bib" ? (
                  <div>
                    <h4>Bibliography</h4>
                    <button onClick={downloadBib} disabled={bibSource === null}>Download .bib</button>
                    <pre style={{ whiteSpace: "pre-wrap" }}>{bibSource === null ? "Loading bibliography..." : bibSource.slice(0, 20000)}</pre>
                    {bibSource !== null && bibSource.length > 20000 && <p>Preview truncated; download contains the full source.</p>}
                  </div>
                ) : !preview ? (
                  <div style={{ color: "#a0aec0" }}>Loading bounded preview...</div>
                ) : (
                  <div>
                    <h4 style={{ margin: "0 0 12px 0", color: "#fff" }}>Content Summary (v{preview.version_number})</h4>

                    {/* Document Preview */}
                    {preview.artifact_type === "document" && preview.blocks_summary && (
                      <div>
                        <div style={{ marginBottom: "8px", fontSize: "12px", color: "#a0aec0" }}>
                          Total blocks: {preview.total_blocks} (Showing first {preview.blocks_summary.length})
                        </div>
                        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                          {preview.blocks_summary.map((b, idx) => (
                            <div key={idx} style={{ padding: "8px", background: "rgba(0,0,0,0.2)", borderRadius: "4px" }}>
                              <span style={{ fontSize: "11px", color: "#63b3ed", textTransform: "uppercase", marginRight: "8px" }}>
                                {b.type} {b.level ? `(H${b.level})` : ""}
                              </span>
                              <span style={{ fontSize: "13px", color: "#e2e8f0" }}>{b.text}</span>
                              {b.item_count !== null && b.item_count !== undefined && (
                                <span style={{ fontSize: "11px", color: "#a0aec0", marginLeft: "8px" }}>({b.item_count} items)</span>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Spreadsheet Preview */}
                    {preview.artifact_type === "spreadsheet" && preview.sheets && (
                      <div>
                        <div style={{ marginBottom: "8px", fontSize: "12px", color: "#a0aec0" }}>
                          Total sheets: {preview.sheet_count}
                        </div>
                        {preview.sheets.map((s) => (
                          <div key={s.sheet_id} style={{ marginBottom: "12px", padding: "10px", background: "rgba(0,0,0,0.2)", borderRadius: "4px" }}>
                            <div style={{ fontWeight: "600", fontSize: "13px", color: "#90cdf4", marginBottom: "6px" }}>
                              Sheet: {s.name} ({s.row_count} rows)
                            </div>
                            <div style={{ fontSize: "12px", color: "#cbd5e0", marginBottom: "4px" }}>
                              Headers: {s.headers.length ? s.headers.join(" | ") : "(none)"}
                            </div>
                            {s.sample_rows.length > 0 && (
                              <div style={{ fontSize: "11px", color: "#a0aec0" }}>
                                Sample row 1: {s.sample_rows[0].join(" | ")}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Presentation Preview */}
                    {preview.artifact_type === "presentation" && preview.slides && (
                      <div>
                        <div style={{ marginBottom: "8px", fontSize: "12px", color: "#a0aec0" }}>
                          Total slides: {preview.total_slides} (Showing first {preview.slides.length})
                        </div>
                        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                          {preview.slides.map((s, idx) => (
                            <div key={s.slide_id} style={{ padding: "8px", background: "rgba(0,0,0,0.2)", borderRadius: "4px" }}>
                              <div style={{ display: "flex", justifyContent: "space-between" }}>
                                <span style={{ fontWeight: "600", color: "#fff", fontSize: "13px" }}>
                                  Slide {idx + 1}: {s.title || "(Untitled Slide)"}
                                </span>
                                <span style={{ fontSize: "11px", color: "#a0aec0" }}>Layout: {s.layout}</span>
                              </div>
                              <div style={{ fontSize: "11px", color: "#cbd5e0", marginTop: "4px" }}>
                                Elements ({s.element_count}): {s.element_types.join(", ") || "none"}
                                {s.has_notes && <span style={{ marginLeft: "8px", color: "#68d391" }}>• Has Speaker Notes</span>}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Tab 2: Version History */}
            {activeTab === "versions" && (
              <div style={{ background: "var(--card-bg, #2d3748)", borderRadius: "8px", padding: "16px", border: "1px solid #4a5568" }}>
                <h4 style={{ margin: "0 0 12px 0", color: "#fff" }}>Immutable Version History</h4>
                <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                  {[...versions].reverse().map((v) => {
                    const isCurrent = v.version_number === selectedArtifact.current_version_number;
                    const opSummary = (v.provenance?.operation_summary as string[]) || [];
                    return (
                      <div
                        key={v.version_id}
                        style={{
                          padding: "12px",
                          borderRadius: "6px",
                          background: isCurrent ? "rgba(49,130,206,0.15)" : "rgba(0,0,0,0.2)",
                          border: isCurrent ? "1px solid #3182ce" : "1px solid #4a5568",
                        }}
                      >
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                          <div>
                            <span style={{ fontWeight: "700", fontSize: "14px", color: isCurrent ? "#63b3ed" : "#fff" }}>
                              Version {v.version_number}
                            </span>
                            {isCurrent && (
                              <span style={{ marginLeft: "8px", padding: "2px 6px", borderRadius: "4px", background: "#3182ce", fontSize: "10px", color: "#fff" }}>
                                CURRENT
                              </span>
                            )}
                          </div>
                          {!isCurrent && (
                            <button
                              onClick={() => handleRestore(v.version_number)}
                              disabled={restoreInProgress !== null}
                              style={{
                                padding: "4px 10px",
                                fontSize: "12px",
                                borderRadius: "4px",
                                background: "#4a5568",
                                color: "#fff",
                                border: "none",
                                cursor: "pointer",
                              }}
                            >
                              {restoreInProgress === v.version_number ? "Restoring..." : `Restore as v${selectedArtifact.current_version_number + 1}`}
                            </button>
                          )}
                        </div>
                        <div style={{ marginTop: "6px", fontSize: "12px", color: "#cbd5e0" }}>
                          <div>Created: {new Date(v.created_at).toLocaleString()} by <strong>{v.created_by}</strong></div>
                          {opSummary.length > 0 && (
                            <div style={{ marginTop: "2px", color: "#a0aec0" }}>
                              Operations: {opSummary.slice(0, 3).join(", ")}{opSummary.length > 3 ? "..." : ""}
                            </div>
                          )}
                          <div style={{ fontSize: "11px", color: "#718096", marginTop: "2px" }}>
                            Digest: {v.content_digest.slice(0, 16)}...
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Tab 3: Export Center */}
            {activeTab === "export" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                {/* Available Formats Card */}
                <div style={{ background: "var(--card-bg, #2d3748)", borderRadius: "8px", padding: "16px", border: "1px solid #4a5568" }}>
                  <h4 style={{ margin: "0 0 12px 0", color: "#fff" }}>Export Capabilities & Formats</h4>
                  {selectedArtifact.artifact_type === "bib" && (
                    <button onClick={downloadBib} disabled={bibSource === null}>Download .bib (text/x-bibtex)</button>
                  )}
                  <div style={{ marginBottom: "12px" }}>
                    <label style={{ fontSize: "12px", color: "#cbd5e0", display: "block", marginBottom: "4px" }}>
                      Custom Filename (Optional):
                    </label>
                    <input
                      type="text"
                      placeholder={`${selectedArtifact.artifact_id}_v${selectedArtifact.current_version_number}`}
                      value={customFilename}
                      onChange={(e) => setCustomFilename(e.target.value)}
                      style={{
                        padding: "6px 8px",
                        borderRadius: "4px",
                        border: "1px solid #4a5568",
                        background: "#1a202c",
                        color: "#fff",
                        fontSize: "13px",
                        width: "300px",
                      }}
                    />
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "10px" }}>
                    {capabilities &&
                      Object.entries(capabilities.formats).map(([fmt, cap]) => {
                        const isSupported = cap.status === "supported" || cap.status === "available_with_warning";
                        return (
                          <div
                            key={fmt}
                            style={{
                              padding: "10px",
                              borderRadius: "6px",
                              background: "rgba(0,0,0,0.2)",
                              border: isSupported ? "1px solid #4a5568" : "1px solid #742a2a",
                              display: "flex",
                              flexDirection: "column",
                              justifyContent: "space-between",
                            }}
                          >
                            <div>
                              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                                <span style={{ fontWeight: "700", textTransform: "uppercase", color: "#fff" }}>{fmt}</span>
                                <span
                                  style={{
                                    fontSize: "10px",
                                    padding: "2px 6px",
                                    borderRadius: "4px",
                                    background:
                                      cap.status === "supported"
                                        ? "#22543d"
                                        : cap.status === "available_with_warning"
                                        ? "#744210"
                                        : "#742a2a",
                                    color: "#fff",
                                  }}
                                >
                                  {cap.status.replace(/_/g, " ")}
                                </span>
                              </div>
                              {lastFormat === fmt && (
                                <div style={{ fontSize: "10px", color: "#90cdf4", marginTop: "4px" }}>
                                  Last used in this project
                                </div>
                              )}
                              <div style={{ fontSize: "11px", color: "#a0aec0", marginTop: "4px" }}>
                                Converter: {cap.converter}
                              </div>
                              {cap.warnings.length > 0 && (
                                <div style={{ fontSize: "11px", color: "#ecc94b", marginTop: "4px" }}>
                                  {cap.warnings[0]}
                                </div>
                              )}
                            </div>

                            <button
                              onClick={() => handleExport(fmt)}
                              disabled={!isSupported || exportingFormat !== null}
                              style={{
                                marginTop: "8px",
                                padding: "6px",
                                fontSize: "12px",
                                borderRadius: "4px",
                                background: isSupported ? "#2b6cb0" : "#4a5568",
                                color: "#fff",
                                border: "none",
                                cursor: isSupported ? "pointer" : "not-allowed",
                              }}
                            >
                              {exportingFormat === fmt ? "Exporting..." : `Export ${fmt.toUpperCase()}`}
                            </button>
                          </div>
                        );
                      })}
                  </div>

                  {lastExportResult && (
                    <div
                      style={{
                        marginTop: "16px",
                        padding: "12px",
                        borderRadius: "6px",
                        background: lastExportResult.success ? "rgba(72,187,120,0.15)" : "rgba(229,62,62,0.15)",
                        border: lastExportResult.success ? "1px solid #48bb78" : "1px solid #f56565",
                      }}
                    >
                      <div style={{ fontWeight: "600", color: lastExportResult.success ? "#9ae6b4" : "#feb2b2" }}>
                        {lastExportResult.success ? "Export Successful" : "Export Failed"}
                      </div>
                      <div style={{ fontSize: "12px", color: "#e2e8f0", marginTop: "4px" }}>
                        Path: {lastExportResult.output_path} ({lastExportResult.byte_count} bytes)
                      </div>
                      {Boolean(lastExportResult.warnings && lastExportResult.warnings.length > 0) && (
                        <div style={{ fontSize: "11px", color: "#ecc94b", marginTop: "4px" }}>
                          Warnings: {lastExportResult.warnings?.join("; ")}
                        </div>
                      )}

                      {lastExportResult.error && (
                        <div style={{ fontSize: "11px", color: "#feb2b2", marginTop: "4px" }}>
                          Error: {lastExportResult.error}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Past Exports History Card */}
                <div style={{ background: "var(--card-bg, #2d3748)", borderRadius: "8px", padding: "16px", border: "1px solid #4a5568" }}>
                  <h4 style={{ margin: "0 0 12px 0", color: "#fff" }}>Export History</h4>
                  {exportHistory.length === 0 ? (
                    <div style={{ color: "#a0aec0", fontSize: "12px" }}>No past exports recorded for this artifact.</div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                      {exportHistory.map((h) => (
                        <div key={h.export_id} style={{ padding: "8px", background: "rgba(0,0,0,0.2)", borderRadius: "4px", fontSize: "12px" }}>
                          <div style={{ display: "flex", justifyContent: "space-between" }}>
                            <span style={{ fontWeight: "600", color: "#fff" }}>
                              {h.format.toUpperCase()} (v{h.version_number})
                            </span>
                            <span style={{ color: "#a0aec0" }}>{new Date(h.created_at).toLocaleString()}</span>
                          </div>
                          <div style={{ color: "#cbd5e0", marginTop: "2px" }}>
                            Output: {h.output_path} ({h.byte_count} bytes) • Status: {h.status}
                          </div>
                          {h.warnings?.length > 0 && (
                            <div style={{ color: "#ecc94b", fontSize: "11px", marginTop: "2px" }}>
                              Warnings: {h.warnings.join(", ")}
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
        )}
      </div>
    </div>
  );
};
