import React, { useCallback, useEffect, useState } from "react";
import { amca } from "./api";
import { Icon } from "./icons";
import type {
  ResearchSession,
  ResearchSource,
  ResearchSnapshot,
  ResearchExtraction,
  ResearchEvidence,
  ResearchCitation,
  ResearchQuestion,
  ResearchClaim,
  ResearchCoverage,
  ResearchSynthesisVersion,
  ResearchSearchEvent,
  ResearchWebAcquisition,
  ClaimType,
  ClaimRelation,
} from "./types";

interface Props {
  projectId?: string;
  onNavigateToApprovals?: () => void;
}

type TabType = "sources" | "search" | "web" | "evidence" | "claims" | "synthesis";

export const ResearchCenter: React.FC<Props> = ({
  projectId = "default",
  onNavigateToApprovals,
}) => {
  // Navigation & Tabs
  const [activeTab, setActiveTab] = useState<TabType>("sources");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Sessions State
  const [sessions, setSessions] = useState<ResearchSession[]>([]);
  const [activeSession, setActiveSession] = useState<ResearchSession | null>(null);
  const [showNewSessionModal, setShowNewSessionModal] = useState(false);
  const [newSessionTitle, setNewSessionTitle] = useState("");
  const [newSessionPurpose, setNewSessionPurpose] = useState("");

  // Sources & Snapshots State
  const [sources, setSources] = useState<ResearchSource[]>([]);
  
  const [snapshotsBySource, setSnapshotsBySource] = useState<Record<string, ResearchSnapshot[]>>({});
  const [selectedSnapshot, setSelectedSnapshot] = useState<ResearchSnapshot | null>(null);
  const [extractionsBySnapshot, setExtractionsBySnapshot] = useState<Record<string, ResearchExtraction[]>>({});
  const [snapshotContentModal, setSnapshotContentModal] = useState<{ id: string; url: string; title: string } | null>(null);

  // Ingestion Drafts
  const [ingestMode, setIngestMode] = useState<"local" | "text" | "artifact">("local");
  const [localPathDraft, setLocalPathDraft] = useState("");
  const [localTitleDraft, setLocalTitleDraft] = useState("");
  const [textContentDraft, setTextContentDraft] = useState("");
  const [textTitleDraft, setTextTitleDraft] = useState("");
  const [textTypeDraft, setTextTypeDraft] = useState("text/plain");
  const [artifactIdDraft, setArtifactIdDraft] = useState("");
  const [artifactVersionDraft, setArtifactVersionDraft] = useState(1);
  const [ingestBusy, setIngestBusy] = useState(false);

  // Lexical Search State
  const [lexicalQuery, setLexicalQuery] = useState("");
  const [lexicalLimit, setLexicalLimit] = useState(10);
  const [lexicalResults, setLexicalResults] = useState<Array<{ source_id: string; snapshot_id: string; locator: any; excerpt: string; score?: number }>>([]);
  const [searchBusy, setSearchBusy] = useState(false);

  // Web Research & Tool Fabric State
  const [webQuery, setWebQuery] = useState("");
  const [webMaxResults, setWebMaxResults] = useState(10);
  const [webSearchEvents, setWebSearchEvents] = useState<ResearchSearchEvent[]>([]);
  const [latestWebResults, setLatestWebResults] = useState<Array<{ title: string; url: string; snippet?: string }>>([]);
  const [webAcquisitions, setWebAcquisitions] = useState<ResearchWebAcquisition[]>([]);
  const [webSearchBusy, setWebSearchBusy] = useState(false);
  const [fetchBusyUrl, setFetchBusyUrl] = useState<string | null>(null);
  const [pendingApprovalPrompt, setPendingApprovalPrompt] = useState<{ type: "search" | "fetch"; payload: any; message: string } | null>(null);

  // Evidence & Citations State
  const [evidenceList, setEvidenceList] = useState<ResearchEvidence[]>([]);
  const [citationList, setCitationList] = useState<ResearchCitation[]>([]);
  const [showNewEvidenceModal, setShowNewEvidenceModal] = useState(false);
  const [newEvidenceSnapshotId, setNewEvidenceSnapshotId] = useState("");
  const [newEvidenceExcerpt, setNewEvidenceExcerpt] = useState("");
  const [newEvidenceLocator, setNewEvidenceLocator] = useState("");
  
  const [selectedCitationProvenance, setSelectedCitationProvenance] = useState<{ citation: ResearchCitation; evidence?: ResearchEvidence; snapshot?: ResearchSnapshot; source?: ResearchSource } | null>(null);

  // Questions & Claims State
  const [questions, setQuestions] = useState<ResearchQuestion[]>([]);
  const [claims, setClaims] = useState<ResearchClaim[]>([]);
  
  const [claimCoverageMap, setClaimCoverageMap] = useState<Record<string, ResearchCoverage>>({});
  const [showNewQuestionModal, setShowNewQuestionModal] = useState(false);
  const [newQuestionText, setNewQuestionText] = useState("");
  const [newQuestionParentId, setNewQuestionParentId] = useState<string>("");
  const [showNewClaimModal, setShowNewClaimModal] = useState(false);
  const [newClaimText, setNewClaimText] = useState("");
  const [newClaimType, setNewClaimType] = useState<ClaimType>("factual");
  const [newClaimQuestionId, setNewClaimQuestionId] = useState<string>("");
  const [newClaimScope, setNewClaimScope] = useState("");

  // Claim Evidence Link Modal
  const [linkModalClaim, setLinkModalClaim] = useState<ResearchClaim | null>(null);
  const [linkEvidenceId, setLinkEvidenceId] = useState("");
  const [linkRelation, setLinkRelation] = useState<ClaimRelation>("supports");
  const [linkRationale, setLinkRationale] = useState("");

  // Synthesis State
  const [synthesisVersions, setSynthesisVersions] = useState<ResearchSynthesisVersion[]>([]);
  const [selectedSynthesis, setSelectedSynthesis] = useState<ResearchSynthesisVersion | null>(null);
  const [synthesisTitleDraft, setSynthesisTitleDraft] = useState("Research Synthesis Report");
  const [synthesisQuestionIdDraft, setSynthesisQuestionIdDraft] = useState("");
  const [synthesisClaimIdsDraft, setSynthesisClaimIdsDraft] = useState<string[]>([]);
  const [synthesisBusy, setSynthesisBusy] = useState(false);

  // Load Sessions
  const loadSessions = useCallback(async () => {
    try {
      setLoading(true);
      const res = await amca.listResearchSessions(projectId);
      setSessions(res.sessions);
      if (res.sessions.length > 0 && !activeSession) {
        setActiveSession(res.sessions[0]);
      }
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      // H1.1-3: first use without a project is a guided empty state, not an
      // Action Failed condition.
      if (message.includes("project not found")) {
        setSessions([]);
        setError(null);
      } else {
        setError(message);
      }
    } finally {
      setLoading(false);
    }
  }, [projectId, activeSession]);

  // Load Session Data
  const loadSessionData = useCallback(async (sid: string) => {
    try {
      const [srcRes, evRes, citRes, qRes, clmRes, searchEvRes, acqRes, synthRes] = await Promise.all([
        amca.listResearchSources(projectId, sid),
        amca.listResearchEvidence(projectId, { session_id: sid }),
        amca.listResearchCitations(projectId, sid),
        amca.listResearchQuestions(projectId, sid),
        amca.listResearchClaims(projectId, sid),
        amca.listResearchSearchEvents(projectId, sid),
        amca.listResearchAcquisitions(projectId, sid),
        amca.listResearchSynthesisVersions(projectId, sid),
      ]);

      setSources(srcRes.sources);
      setEvidenceList(evRes.evidence);
      setCitationList(citRes.citations);
      setQuestions(qRes.questions);
      setClaims(clmRes.claims);
      setWebSearchEvents(searchEvRes.search_events);
      setWebAcquisitions(acqRes.acquisitions);
      setSynthesisVersions(synthRes.versions);
      if (synthRes.versions.length > 0 && !selectedSynthesis) {
        setSelectedSynthesis(synthRes.versions[0]);
      }

      // Load snapshots for each source
      const snapMap: Record<string, ResearchSnapshot[]> = {};
      const extMap: Record<string, ResearchExtraction[]> = {};
      for (const src of srcRes.sources) {
        try {
          const snapRes = await amca.listResearchSnapshots(projectId, src.source_id);
          snapMap[src.source_id] = snapRes.snapshots;
          for (const sn of snapRes.snapshots) {
            try {
              const extRes = await amca.listResearchExtractions(projectId, sn.snapshot_id);
              extMap[sn.snapshot_id] = extRes.extractions;
            } catch {}
          }
        } catch {}
      }
      setSnapshotsBySource(snapMap);
      setExtractionsBySnapshot(extMap);

      // Load coverage for each claim
      const covMap: Record<string, ResearchCoverage> = {};
      for (const clm of clmRes.claims) {
        try {
          const covRes = await amca.getResearchClaimCoverage(projectId, clm.claim_id);
          covMap[clm.claim_id] = covRes.coverage;
        } catch {}
      }
      setClaimCoverageMap(covMap);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [projectId, selectedSynthesis]);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    if (activeSession) {
      void loadSessionData(activeSession.session_id);
    }
  }, [activeSession, loadSessionData]);

  // Create Session
  const handleCreateSession = async () => {
    if (!newSessionTitle.trim()) return;
    try {
      setLoading(true);
      setError(null);
      const sess = await amca.createResearchSession(projectId, {
        title: newSessionTitle.trim(),
        purpose: newSessionPurpose.trim(),
      });
      setSessions((prev) => [sess, ...prev]);
      setActiveSession(sess);
      setShowNewSessionModal(false);
      setNewSessionTitle("");
      setNewSessionPurpose("");
      setNotice(`Created research session: ${sess.title}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  // Ingest Local File
  const handleIngestLocal = async () => {
    if (!activeSession || !localPathDraft.trim()) return;
    try {
      setIngestBusy(true);
      setError(null);
      const res = await amca.ingestResearchLocal(projectId, activeSession.session_id, {
        path: localPathDraft.trim(),
        title: localTitleDraft.trim() || undefined,
      });
      setNotice(`Ingested local file: ${res.source.title}`);
      setLocalPathDraft("");
      setLocalTitleDraft("");
      await loadSessionData(activeSession.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIngestBusy(false);
    }
  };

  // Ingest Direct Text
  const handleIngestText = async () => {
    if (!activeSession || !textContentDraft.trim()) return;
    try {
      setIngestBusy(true);
      setError(null);
      const res = await amca.ingestResearchText(projectId, activeSession.session_id, {
        title: textTitleDraft.trim() || "User Text Ingestion",
        content: textContentDraft,
        content_type: textTypeDraft,
      });
      setNotice(`Ingested text note: ${res.source.title}`);
      setTextContentDraft("");
      setTextTitleDraft("");
      await loadSessionData(activeSession.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIngestBusy(false);
    }
  };

  // Ingest Artifact
  const handleIngestArtifact = async () => {
    if (!activeSession || !artifactIdDraft.trim()) return;
    try {
      setIngestBusy(true);
      setError(null);
      const res = await amca.ingestResearchArtifact(projectId, activeSession.session_id, {
        artifact_id: artifactIdDraft.trim(),
        version: Number(artifactVersionDraft) || 1,
      });
      setNotice(`Ingested artifact snapshot: ${res.source.title}`);
      setArtifactIdDraft("");
      await loadSessionData(activeSession.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIngestBusy(false);
    }
  };

  // Extract Snapshot
  const handleExtractSnapshot = async (snapshotId: string) => {
    try {
      setLoading(true);
      setError(null);
      const ext = await amca.extractResearchSnapshot(projectId, snapshotId);
      setNotice(`Extracted ${ext.segment_count} segments (status: ${ext.status})`);
      if (activeSession) await loadSessionData(activeSession.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  // Lexical Search
  const handleLexicalSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!lexicalQuery.trim()) return;
    try {
      setSearchBusy(true);
      setError(null);
      const res = await amca.searchResearchLexical(projectId, lexicalQuery.trim(), lexicalLimit);
      setLexicalResults(res.results);
      setNotice(`Found ${res.results.length} lexical matches`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSearchBusy(false);
    }
  };

  // Web Search
  const handleWebSearch = async (approved: boolean = false) => {
    if (!activeSession || !webQuery.trim()) return;
    try {
      setWebSearchBusy(true);
      setError(null);
      const res = await amca.searchResearchWeb(projectId, activeSession.session_id, {
        query: webQuery.trim(),
        max_results: webMaxResults,
        approved,
      });
      setLatestWebResults(res.results);
      setNotice(`Web search completed: ${res.results.length} previews received (Gated: not yet evidence).`);
      setPendingApprovalPrompt(null);
      await loadSessionData(activeSession.session_id);
    } catch (err: any) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("approval") || msg.includes("ApprovalRequired") || msg.includes("REQUIRE_APPROVAL") || msg.includes("denied")) {
        setPendingApprovalPrompt({
          type: "search",
          payload: { query: webQuery.trim(), max_results: webMaxResults },
          message: `Tool Fabric Policy Gate: Web Search requires explicit review approval for query "${webQuery.trim()}".`,
        });
      } else {
        setError(msg);
      }
    } finally {
      setWebSearchBusy(false);
    }
  };

  // Web Fetch
  const handleWebFetch = async (url: string, approved: boolean = false) => {
    if (!activeSession || !url.trim()) return;
    try {
      setFetchBusyUrl(url);
      setError(null);
      const res = await amca.fetchResearchAcquisition(projectId, activeSession.session_id, {
        url: url.trim(),
        approved,
      });
      setNotice(`Acquisition successful: HTTP ${res.acquisition.http_status} (${res.acquisition.byte_count} bytes). Snapshot created.`);
      setPendingApprovalPrompt(null);
      await loadSessionData(activeSession.session_id);
    } catch (err: any) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("approval") || msg.includes("ApprovalRequired") || msg.includes("REQUIRE_APPROVAL") || msg.includes("denied")) {
        setPendingApprovalPrompt({
          type: "fetch",
          payload: { url: url.trim() },
          message: `Tool Fabric Policy Gate: Web Fetch requires explicit resource approval for URL: ${url.trim()}`,
        });
      } else {
        setError(msg);
      }
    } finally {
      setFetchBusyUrl(null);
    }
  };

  // Create Evidence from Excerpt
  const handleCreateEvidence = async () => {
    if (!newEvidenceSnapshotId || !newEvidenceExcerpt.trim()) return;
    try {
      setLoading(true);
      setError(null);
      let loc: any = { type: "manual" };
      if (newEvidenceLocator.trim()) {
        try {
          loc = JSON.parse(newEvidenceLocator);
        } catch {
          loc = { type: "custom", label: newEvidenceLocator.trim() };
        }
      }
      const ev = await amca.createResearchEvidence(projectId, {
        snapshot_id: newEvidenceSnapshotId,
        excerpt: newEvidenceExcerpt.trim(),
        locator: loc,
      });
      setNotice(`Created evidence chunk: ${ev.evidence_id}`);
      setShowNewEvidenceModal(false);
      setNewEvidenceExcerpt("");
      setNewEvidenceLocator("");
      if (activeSession) await loadSessionData(activeSession.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  // Create Citation for Evidence
  const handleCreateCitation = async (evidenceId: string, customLabel?: string) => {
    try {
      setLoading(true);
      setError(null);
      const label = customLabel || `[${citationList.length + 1}]`;
      const cit = await amca.createResearchCitation(projectId, {
        evidence_id: evidenceId,
        display_label: label,
      });
      setNotice(`Created citation: ${cit.display_label}`);
      if (activeSession) await loadSessionData(activeSession.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  // Create Question
  const handleCreateQuestion = async () => {
    if (!activeSession || !newQuestionText.trim()) return;
    try {
      setLoading(true);
      setError(null);
      const q = await amca.createResearchQuestion(projectId, activeSession.session_id, {
        text: newQuestionText.trim(),
        parent_question_id: newQuestionParentId || undefined,
      });
      setNotice(`Created research question: ${q.question}`);
      setShowNewQuestionModal(false);
      setNewQuestionText("");
      setNewQuestionParentId("");
      await loadSessionData(activeSession.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  // Create Claim
  const handleCreateClaim = async () => {
    if (!activeSession || !newClaimText.trim()) return;
    try {
      setLoading(true);
      setError(null);
      const clm = await amca.createResearchClaim(projectId, activeSession.session_id, {
        claim_type: newClaimType,
        text: newClaimText.trim(),
        question_id: newClaimQuestionId || undefined,
        scope_qualifier: newClaimScope.trim() || undefined,
      });
      setNotice(`Created claim: ${clm.claim_text.slice(0, 40)}...`);
      setShowNewClaimModal(false);
      setNewClaimText("");
      setNewClaimScope("");
      await loadSessionData(activeSession.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  // Link Claim Evidence
  const handleLinkClaimEvidence = async () => {
    if (!linkModalClaim || !linkEvidenceId) return;
    try {
      setLoading(true);
      setError(null);
      await amca.linkResearchClaimEvidence(projectId, linkModalClaim.claim_id, {
        evidence_id: linkEvidenceId,
        relation: linkRelation,
        assessor: "user",
        notes: linkRationale.trim() || undefined,
      });
      setNotice(`Linked evidence as ${linkRelation.toUpperCase()} for claim.`);
      setLinkModalClaim(null);
      setLinkEvidenceId("");
      setLinkRationale("");
      if (activeSession) await loadSessionData(activeSession.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  // Generate Synthesis
  const handleGenerateSynthesis = async () => {
    if (!activeSession) return;
    try {
      setSynthesisBusy(true);
      setError(null);
      const res = await amca.generateResearchSynthesis(projectId, activeSession.session_id, {
        title: synthesisTitleDraft.trim() || "Grounded Synthesis Report",
        question_id: synthesisQuestionIdDraft || undefined,
        claim_ids: synthesisClaimIdsDraft.length > 0 ? synthesisClaimIdsDraft : undefined,
      });
      setSelectedSynthesis(res);
      setNotice(`Synthesis generated (Grounding: ${res.grounding_status.toUpperCase()})`);
      await loadSessionData(activeSession.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSynthesisBusy(false);
    }
  };

  // View Citation Provenance Modal
  const openCitationProvenance = (citationId: string) => {
    const cit = citationList.find((c) => c.citation_id === citationId || c.display_label === citationId);
    if (!cit) return;
    const ev = evidenceList.find((e) => e.evidence_id === cit.evidence_id);
    let snap: ResearchSnapshot | undefined;
    let src: ResearchSource | undefined;
    if (ev) {
      for (const sId in snapshotsBySource) {
        const found = snapshotsBySource[sId].find((s) => s.snapshot_id === ev.snapshot_id);
        if (found) {
          snap = found;
          src = sources.find((s) => s.source_id === found.source_id);
          break;
        }
      }
    }
    setSelectedCitationProvenance({ citation: cit, evidence: ev, snapshot: snap, source: src });
  };

  return (
    <div className="research-center-shell">
      {/* Top Header & Session Bar */}
      <header className="research-header">
        <div className="research-header-left">
          <div className="research-kicker">
            <Icon name="room" />
            <span>Research Center · Grounded Knowledge Fabric</span>
          </div>
          <h1>{activeSession?.title || "Research Workspace"}</h1>
          {activeSession?.purpose && <p className="research-purpose">{activeSession.purpose}</p>}
        </div>

        <div className="research-header-actions">
          <div className="session-selector-wrap">
            <label htmlFor="research-session-select">Session:</label>
            <select
              id="research-session-select"
              value={activeSession?.session_id || ""}
              onChange={(e) => {
                const found = sessions.find((s) => s.session_id === e.target.value);
                if (found) setActiveSession(found);
              }}
            >
              {sessions.map((s) => (
                <option key={s.session_id} value={s.session_id}>
                  {s.title} ({s.session_id.slice(0, 8)})
                </option>
              ))}
              {sessions.length === 0 && <option value="">No sessions</option>}
            </select>
          </div>

          <button
            className="secondary-button"
            onClick={() => setShowNewSessionModal(true)}
            title="Create new research session"
          >
            <Icon name="room" /> + New Session
          </button>

          <button
            className="icon-button"
            onClick={() => activeSession && void loadSessionData(activeSession.session_id)}
            title="Refresh session data"
          >
            <Icon name="refresh" className={loading ? "spinning" : ""} />
          </button>
        </div>
      </header>

      {/* H1.1-3: guided empty state for first use without a project */}
      {!activeSession && !loading && sessions.length === 0 && !error && (
        <div className="research-banner notice" role="status">
          <div>
            <strong>Research Center</strong>
            <span>
              {projectId
                ? "No research sessions yet for this project. Create your first session with “+ New Session” to ingest sources, ask grounded questions, and build cited synthesis."
                : "Open or create a Project first, then create a research session inside it to use the Research Center."}
            </span>
          </div>
          <button onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {/* Status Banners */}
      {error && (
        <div className="research-banner error">
          <div>
            <strong>Action Failed:</strong>
            <span>{error}</span>
          </div>
          <button onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {notice && (
        <div className="research-banner notice">
          <div>
            <strong>Status:</strong>
            <span>{notice}</span>
          </div>
          <button onClick={() => setNotice(null)}>✕</button>
        </div>
      )}

      {pendingApprovalPrompt && (
        <div className="research-banner approval-prompt">
          <div>
            <strong>Tool Fabric Approval Required</strong>
            <p>{pendingApprovalPrompt.message}</p>
          </div>
          <div className="approval-banner-actions">
            <button
              className="primary-button"
              onClick={() => {
                if (pendingApprovalPrompt.type === "search") {
                  void handleWebSearch(true);
                } else {
                  void handleWebFetch(pendingApprovalPrompt.payload.url, true);
                }
              }}
            >
              Approve & Execute via Tool Fabric
            </button>
            {onNavigateToApprovals && (
              <button className="secondary-button" onClick={onNavigateToApprovals}>
                Review in Approval Center
              </button>
            )}
            <button className="secondary-button" onClick={() => setPendingApprovalPrompt(null)}>
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Sub-Navigation Tabs */}
      <nav className="research-tabs" aria-label="Research Workspace Navigation">
        <button
          className={activeTab === "sources" ? "active" : ""}
          onClick={() => setActiveTab("sources")}
        >
          <Icon name="files" /> Sources & Ingestion
          <span className="badge">{sources.length}</span>
        </button>
        <button
          className={activeTab === "search" ? "active" : ""}
          onClick={() => setActiveTab("search")}
        >
          <Icon name="brain" /> Lexical Search
        </button>
        <button
          className={activeTab === "web" ? "active" : ""}
          onClick={() => setActiveTab("web")}
        >
          <Icon name="shield" /> Web Acquisition
          <span className="badge">{webAcquisitions.length}</span>
        </button>
        <button
          className={activeTab === "evidence" ? "active" : ""}
          onClick={() => setActiveTab("evidence")}
        >
          <Icon name="files" /> Evidence & Citations
          <span className="badge">{evidenceList.length}</span>
        </button>
        <button
          className={activeTab === "claims" ? "active" : ""}
          onClick={() => setActiveTab("claims")}
        >
          <Icon name="whiteboard" /> Questions & Claims
          <span className="badge">{claims.length}</span>
        </button>
        <button
          className={activeTab === "synthesis" ? "active" : ""}
          onClick={() => setActiveTab("synthesis")}
        >
          <Icon name="brain" /> Grounded Synthesis
          <span className="badge">{synthesisVersions.length}</span>
        </button>
      </nav>

      {/* Tab 1: Sources & Ingestion */}
      {activeTab === "sources" && (
        <section className="research-tab-content sources-tab">
          {/* Ingestion Panel */}
          <div className="panel ingestion-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Material Ingestion</span>
                <h2>Ingest Research Content</h2>
              </div>
              <div className="segmented-control">
                <button
                  className={ingestMode === "local" ? "active" : ""}
                  onClick={() => setIngestMode("local")}
                >
                  Local File
                </button>
                <button
                  className={ingestMode === "text" ? "active" : ""}
                  onClick={() => setIngestMode("text")}
                >
                  User Text
                </button>
                <button
                  className={ingestMode === "artifact" ? "active" : ""}
                  onClick={() => setIngestMode("artifact")}
                >
                  Artifact Snapshot
                </button>
              </div>
            </div>

            {ingestMode === "local" && (
              <div className="ingest-form">
                <p className="hint">
                  Ingest files from contained project scope (TXT, MD, PDF, DOCX, XLSX, PPTX). Traversal and sensitive patterns are rejected.
                </p>
                <div className="form-row">
                  <label>
                    <span>Relative File Path</span>
                    <input
                      type="text"
                      placeholder="e.g. notes/research_paper.md"
                      value={localPathDraft}
                      onChange={(e) => setLocalPathDraft(e.target.value)}
                    />
                  </label>
                  <label>
                    <span>Display Title (Optional)</span>
                    <input
                      type="text"
                      placeholder="e.g. Superconducting Quantum Circuits"
                      value={localTitleDraft}
                      onChange={(e) => setLocalTitleDraft(e.target.value)}
                    />
                  </label>
                  <button
                    className="primary-button"
                    disabled={ingestBusy || !localPathDraft.trim() || !activeSession}
                    onClick={() => void handleIngestLocal()}
                  >
                    {ingestBusy ? "Ingesting..." : "Ingest File"}
                  </button>
                </div>
              </div>
            )}

            {ingestMode === "text" && (
              <div className="ingest-form">
                <p className="hint">Ingest direct text notes or markdown passages into the research session.</p>
                <div className="form-col">
                  <div className="form-row">
                    <label>
                      <span>Title</span>
                      <input
                        type="text"
                        placeholder="e.g. Lab Observations"
                        value={textTitleDraft}
                        onChange={(e) => setTextTitleDraft(e.target.value)}
                      />
                    </label>
                    <label>
                      <span>Content Type</span>
                      <select value={textTypeDraft} onChange={(e) => setTextTypeDraft(e.target.value)}>
                        <option value="text/plain">text/plain</option>
                        <option value="text/markdown">text/markdown</option>
                        <option value="application/json">application/json</option>
                      </select>
                    </label>
                  </div>
                  <label>
                    <span>Content</span>
                    <textarea
                      rows={4}
                      placeholder="Enter raw text content..."
                      value={textContentDraft}
                      onChange={(e) => setTextContentDraft(e.target.value)}
                    />
                  </label>
                  <button
                    className="primary-button"
                    disabled={ingestBusy || !textContentDraft.trim() || !activeSession}
                    onClick={() => void handleIngestText()}
                  >
                    {ingestBusy ? "Ingesting..." : "Ingest Text"}
                  </button>
                </div>
              </div>
            )}

            {ingestMode === "artifact" && (
              <div className="ingest-form">
                <p className="hint">Ingest an existing Studio Artifact version as a snapshot in this research session.</p>
                <div className="form-row">
                  <label>
                    <span>Artifact ID</span>
                    <input
                      type="text"
                      placeholder="e.g. art-123456"
                      value={artifactIdDraft}
                      onChange={(e) => setArtifactIdDraft(e.target.value)}
                    />
                  </label>
                  <label>
                    <span>Version</span>
                    <input
                      type="number"
                      min={1}
                      value={artifactVersionDraft}
                      onChange={(e) => setArtifactVersionDraft(parseInt(e.target.value, 10) || 1)}
                    />
                  </label>
                  <button
                    className="primary-button"
                    disabled={ingestBusy || !artifactIdDraft.trim() || !activeSession}
                    onClick={() => void handleIngestArtifact()}
                  >
                    {ingestBusy ? "Ingesting..." : "Ingest Artifact"}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Sources and Snapshots List */}
          <div className="panel sources-list-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Session Materials</span>
                <h2>Ingested Sources & Snapshots ({sources.length})</h2>
              </div>
            </div>

            {sources.length === 0 ? (
              <div className="empty-state">
                <Icon name="files" />
                <p>No sources ingested in this session yet.</p>
                <small>Use the form above or the Web Acquisition tab to bring research materials into this session.</small>
              </div>
            ) : (
              <div className="sources-table-wrap">
                <table className="sources-table">
                  <thead>
                    <tr>
                      <th>Title / Location</th>
                      <th>Type</th>
                      <th>Snapshots</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sources.map((src) => {
                      const snaps = snapshotsBySource[src.source_id] || [];
                      const latestSnap = snaps[0];
                      const extractions = latestSnap ? extractionsBySnapshot[latestSnap.snapshot_id] || [] : [];
                      const latestExt = extractions[0];

                      return (
                        <tr key={src.source_id}>
                          <td>
                            <strong>{src.title}</strong>
                            <code className="source-location">{src.location}</code>
                          </td>
                          <td>
                            <span className={`pill type-${src.source_type}`}>{src.source_type}</span>
                          </td>
                          <td>
                            <div className="snapshot-chips">
                              {snaps.map((sn, idx) => (
                                <button
                                  key={sn.snapshot_id}
                                  className={`snapshot-chip ${selectedSnapshot?.snapshot_id === sn.snapshot_id ? "active" : ""}`}
                                  onClick={() => setSelectedSnapshot(sn)}
                                  title={`SHA256: ${sn.sha256} | ${sn.byte_count} bytes`}
                                >
                                  v{snaps.length - idx} ({sn.byte_count} B)
                                </button>
                              ))}
                            </div>
                          </td>
                          <td>
                            <span className={`status-pill status-${src.status}`}>{src.status}</span>
                            {latestExt && (
                              <small className="extraction-status">
                                Ext: {latestExt.status} ({latestExt.segment_count} seg)
                              </small>
                            )}
                          </td>
                          <td>
                            <div className="table-actions">
                              {latestSnap && (
                                <>
                                  <button
                                    className="small-button"
                                    onClick={() =>
                                      setSnapshotContentModal({
                                        id: latestSnap.snapshot_id,
                                        url: amca.getResearchSnapshotContentUrl(projectId, latestSnap.snapshot_id),
                                        title: src.title,
                                      })
                                    }
                                  >
                                    View Raw
                                  </button>
                                  <button
                                    className="small-button"
                                    onClick={() => void handleExtractSnapshot(latestSnap.snapshot_id)}
                                  >
                                    Extract
                                  </button>
                                  <button
                                    className="small-button primary"
                                    onClick={() => {
                                      setNewEvidenceSnapshotId(latestSnap.snapshot_id);
                                      setShowNewEvidenceModal(true);
                                    }}
                                  >
                                    + Evidence
                                  </button>
                                </>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Tab 2: Lexical Search */}
      {activeTab === "search" && (
        <section className="research-tab-content search-tab">
          <div className="panel search-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">FTS5 Local Index</span>
                <h2>Lexical Full-Text Search</h2>
              </div>
            </div>

            <form className="search-form" onSubmit={(e) => void handleLexicalSearch(e)}>
              <input
                type="text"
                placeholder="Search extracted segments, keywords, and passages..."
                value={lexicalQuery}
                onChange={(e) => setLexicalQuery(e.target.value)}
              />
              <select
                value={lexicalLimit}
                onChange={(e) => setLexicalLimit(parseInt(e.target.value, 10) || 10)}
              >
                <option value={5}>Top 5</option>
                <option value={10}>Top 10</option>
                <option value={25}>Top 25</option>
                <option value={50}>Top 50</option>
              </select>
              <button className="primary-button" type="submit" disabled={searchBusy || !lexicalQuery.trim()}>
                {searchBusy ? "Searching..." : "Search Index"}
              </button>
            </form>

            <div className="search-results-list">
              {lexicalResults.length === 0 ? (
                <div className="empty-state">
                  <p>No search results. Enter keywords to query the extracted research index.</p>
                </div>
              ) : (
                lexicalResults.map((r, i) => (
                  <article className="search-result-card" key={i}>
                    <div className="search-result-header">
                      <span className="result-rank">#{i + 1}</span>
                      <code>Snapshot: {r.snapshot_id}</code>
                      {r.locator && <small>Locator: {JSON.stringify(r.locator)}</small>}
                    </div>
                    <blockquote className="search-excerpt">{r.excerpt}</blockquote>
                    <div className="search-result-footer">
                      <button
                        className="small-button primary"
                        onClick={() => {
                          setNewEvidenceSnapshotId(r.snapshot_id);
                          setNewEvidenceExcerpt(r.excerpt);
                          setNewEvidenceLocator(JSON.stringify(r.locator));
                          setShowNewEvidenceModal(true);
                        }}
                      >
                        Capture as Evidence
                      </button>
                    </div>
                  </article>
                ))
              )}
            </div>
          </div>
        </section>
      )}

      {/* Tab 3: Web Research & Tool Fabric */}
      {activeTab === "web" && (
        <section className="research-tab-content web-tab">
          <div className="panel web-search-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Permissioned Tool Fabric</span>
                <h2>Web Research & Acquisition</h2>
              </div>
              <span className="status-pill status-gated">Gated Network Fabric (DEFAULT = DENY)</span>
            </div>

            <div className="web-search-box">
              <p className="hint">
                Web search queries provider endpoints without persistence of credentials. Search snippets are previews and <strong>never directly evidence</strong>.
              </p>
              <div className="form-row">
                <input
                  type="text"
                  placeholder="Enter research search query (e.g. superconducting qubit coherence times)..."
                  value={webQuery}
                  onChange={(e) => setWebQuery(e.target.value)}
                />
                <select
                  value={webMaxResults}
                  onChange={(e) => setWebMaxResults(parseInt(e.target.value, 10) || 10)}
                >
                  <option value={5}>5 results</option>
                  <option value={10}>10 results</option>
                  <option value={20}>20 results</option>
                </select>
                <button
                  className="primary-button"
                  disabled={webSearchBusy || !webQuery.trim() || !activeSession}
                  onClick={() => void handleWebSearch(false)}
                >
                  {webSearchBusy ? "Executing..." : "Request Web Search"}
                </button>
              </div>
            </div>

            {/* Search Events History */}
            {webSearchEvents.length > 0 && (
              <div className="search-events-history">
                <span className="section-kicker">Recent Search Events</span>
                <div className="event-pills">
                  {webSearchEvents.slice(0, 5).map((ev) => (
                    <span className="event-pill" key={ev.event_id} title={`Provider: ${ev.provider} | Results: ${ev.result_count}`}>
                      "{ev.query}" ({ev.result_count} results)
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Search Previews vs Evidence Notice */}
            {latestWebResults.length > 0 && (
              <div className="search-previews-container">
                <div className="preview-distinction-banner">
                  <Icon name="shield" />
                  <div>
                    <strong>SEARCH RESULT PREVIEW ≠ VERIFIED SOURCE / EVIDENCE</strong>
                    <p>To use content as evidence, you must independently fetch and snapshot the target URL.</p>
                  </div>
                </div>

                <div className="web-results-grid">
                  {latestWebResults.map((item, idx) => {
                    const existingAcq = webAcquisitions.find((a) => a.requested_url === item.url || a.final_url === item.url);
                    const isFetched = existingAcq && existingAcq.status === "succeeded";

                    return (
                      <article className="web-result-card" key={idx}>
                        <div className="web-result-header">
                          <h4>{item.title}</h4>
                          <span className={`pill ${isFetched ? "pill-success" : "pill-muted"}`}>
                            {isFetched ? "Fetched" : "Not fetched"}
                          </span>
                        </div>
                        <code className="web-result-url">{item.url}</code>
                        {item.snippet && <p className="web-result-snippet">{item.snippet}</p>}
                        <div className="web-result-actions">
                          <button
                            className="primary-button small"
                            disabled={fetchBusyUrl === item.url || isFetched}
                            onClick={() => void handleWebFetch(item.url, false)}
                          >
                            {fetchBusyUrl === item.url ? "Fetching..." : isFetched ? "Acquired" : "Fetch Source"}
                          </button>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Acquisition History Table */}
          <div className="panel acquisitions-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">SSRF Hardened</span>
                <h2>Acquisition History ({webAcquisitions.length})</h2>
              </div>
            </div>

            {webAcquisitions.length === 0 ? (
              <div className="empty-state">
                <p>No web acquisitions recorded in this session.</p>
              </div>
            ) : (
              <table className="sources-table">
                <thead>
                  <tr>
                    <th>URL</th>
                    <th>Status</th>
                    <th>HTTP</th>
                    <th>Size</th>
                    <th>Snapshot ID</th>
                  </tr>
                </thead>
                <tbody>
                  {webAcquisitions.map((a) => (
                    <tr key={a.acquisition_id}>
                      <td>
                        <strong>{a.requested_url}</strong>
                        {a.final_url !== a.requested_url && <small>↳ {a.final_url}</small>}
                      </td>
                      <td>
                        <span className={`status-pill status-${a.status}`}>{a.status}</span>
                      </td>
                      <td>{a.http_status}</td>
                      <td>{a.byte_count} B</td>
                      <td>
                        {a.snapshot_id ? (
                          <code>{a.snapshot_id}</code>
                        ) : (
                          <span className="muted-text">None</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      )}

      {/* Tab 4: Evidence & Citations */}
      {activeTab === "evidence" && (
        <section className="research-tab-content evidence-tab">
          <div className="panel evidence-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Unbroken Provenance</span>
                <h2>Captured Evidence & Citations ({evidenceList.length})</h2>
              </div>
              <button
                className="primary-button"
                onClick={() => {
                  if (sources.length > 0 && snapshotsBySource[sources[0].source_id]?.length > 0) {
                    setNewEvidenceSnapshotId(snapshotsBySource[sources[0].source_id][0].snapshot_id);
                  }
                  setShowNewEvidenceModal(true);
                }}
              >
                + Capture Evidence
              </button>
            </div>

            {evidenceList.length === 0 ? (
              <div className="empty-state">
                <Icon name="files" />
                <p>No evidence captured in this session yet.</p>
                <small>Capture excerpts from extracted snapshots or search results.</small>
              </div>
            ) : (
              <div className="evidence-grid">
                {evidenceList.map((ev) => {
                  const citation = citationList.find((c) => c.evidence_id === ev.evidence_id);

                  return (
                    <article className="evidence-card" key={ev.evidence_id}>
                      <div className="evidence-card-header">
                        <div className="evidence-identity">
                          <strong>{ev.evidence_id}</strong>
                          <code>Snapshot: {ev.snapshot_id}</code>
                        </div>
                        {citation ? (
                          <span
                            className="citation-badge"
                            onClick={() => openCitationProvenance(citation.citation_id)}
                            title="Click to view full provenance"
                          >
                            {citation.display_label}
                          </span>
                        ) : (
                          <button
                            className="small-button secondary"
                            onClick={() => void handleCreateCitation(ev.evidence_id)}
                          >
                            + Add Citation
                          </button>
                        )}
                      </div>

                      <blockquote className="evidence-quote">{ev.excerpt}</blockquote>

                      <div className="evidence-card-footer">
                        <small>Locator: {JSON.stringify(ev.locator)}</small>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </section>
      )}

      {/* Tab 5: Questions & Claims */}
      {activeTab === "claims" && (
        <section className="research-tab-content claims-tab">
          {/* Questions Panel */}
          <div className="panel questions-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Inquiry Structure</span>
                <h2>Research Questions ({questions.length})</h2>
              </div>
              <button className="secondary-button" onClick={() => setShowNewQuestionModal(true)}>
                + New Question
              </button>
            </div>

            {questions.length === 0 ? (
              <div className="empty-state">
                <p>No questions created in this session.</p>
              </div>
            ) : (
              <ul className="questions-list">
                {questions.map((q) => (
                  <li key={q.question_id} className="question-item">
                    <span className="question-icon">?</span>
                    <div className="question-content">
                      <strong>{q.question}</strong>
                      <small>ID: {q.question_id}</small>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Claims & Contradictions Panel */}
          <div className="panel claims-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Knowledge Graph</span>
                <h2>Claims & Contradiction Analysis ({claims.length})</h2>
              </div>
              <button className="primary-button" onClick={() => setShowNewClaimModal(true)}>
                + New Claim
              </button>
            </div>

            {claims.length === 0 ? (
              <div className="empty-state">
                <p>No claims recorded in this session.</p>
              </div>
            ) : (
              <div className="claims-list">
                {claims.map((clm) => {
                  const coverage = claimCoverageMap[clm.claim_id];
                  const state = coverage?.coverage || "no_evidence";

                  return (
                    <article className={`claim-card coverage-${state}`} key={clm.claim_id}>
                      <div className="claim-header">
                        <div className="claim-meta">
                          <span className={`pill claim-type-${clm.claim_type}`}>{clm.claim_type}</span>
                          <strong>{clm.claim_id}</strong>
                          {clm.scope_qualifier && <span className="scope-tag">{clm.scope_qualifier}</span>}
                        </div>
                        <span className={`coverage-pill coverage-${state}`}>
                          {state.toUpperCase().replace("_", " ")}
                        </span>
                      </div>

                      <p className="claim-text">{clm.claim_text}</p>

                      {/* Coverage Metrics & Contradiction Details */}
                      {coverage && (
                        <div className="claim-coverage-section">
                          <div className="coverage-metrics-bar">
                            <span className="metric-chip">
                              <strong>Supports:</strong> {coverage.supporting_count}
                            </span>
                            <span className="metric-chip">
                              <strong>Contradicts:</strong> {coverage.contradicting_count}
                            </span>
                            <span className="metric-chip">
                              <strong>Distinct Sources:</strong> {coverage.distinct_source_count}
                            </span>
                            <span className="metric-chip">
                              <strong>Distinct Chunks:</strong> {coverage.distinct_chunk_count}
                            </span>
                          </div>

                          {state === "mixed" && (
                            <div className="contradiction-split-view">
                              <div className="split-column support-side">
                                <h5>Supporting Evidence ({coverage.supporting_count})</h5>
                                <p className="side-note">Evidence supporting this proposition.</p>
                              </div>
                              <div className="split-column contradict-side">
                                <h5>Contradicting Evidence ({coverage.contradicting_count})</h5>
                                <p className="side-note">Evidence actively contradicting this proposition.</p>
                              </div>
                            </div>
                          )}
                        </div>
                      )}

                      <div className="claim-footer">
                        <button
                          className="small-button"
                          onClick={() => {
                            setLinkModalClaim(clm);
                            if (evidenceList.length > 0) setLinkEvidenceId(evidenceList[0].evidence_id);
                          }}
                        >
                          + Link Evidence
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </section>
      )}

      {/* Tab 6: Grounded Synthesis */}
      {activeTab === "synthesis" && (
        <section className="research-tab-content synthesis-tab">
          {/* Synthesis Generation Panel */}
          <div className="panel synthesis-generation-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Deterministic Synthesizer</span>
                <h2>Generate Grounded Synthesis</h2>
              </div>
            </div>

            <div className="synthesis-controls">
              <p className="hint">
                Synthesizes structured reports strictly from acquired evidence with zero external network activity.
              </p>
              <div className="form-col">
                <div className="form-row">
                  <label>
                    <span>Report Title</span>
                    <input
                      type="text"
                      placeholder="Report Title"
                      value={synthesisTitleDraft}
                      onChange={(e) => setSynthesisTitleDraft(e.target.value)}
                    />
                  </label>
                  <label>
                    <span>Scope to Question (Optional)</span>
                    <select
                      value={synthesisQuestionIdDraft}
                      onChange={(e) => setSynthesisQuestionIdDraft(e.target.value)}
                    >
                      <option value="">All Session Questions</option>
                      {questions.map((q) => (
                        <option key={q.question_id} value={q.question_id}>
                          {q.question}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                {claims.length > 0 && (
                  <div className="claim-checkboxes-scope">
                    <span className="hint">Include specific claims (leave unchecked for all):</span>
                    <div className="claim-check-grid">
                      {claims.map((clm) => (
                        <label key={clm.claim_id} className="checkbox-label">
                          <input
                            type="checkbox"
                            checked={synthesisClaimIdsDraft.includes(clm.claim_id)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setSynthesisClaimIdsDraft((prev) => [...prev, clm.claim_id]);
                              } else {
                                setSynthesisClaimIdsDraft((prev) => prev.filter((id) => id !== clm.claim_id));
                              }
                            }}
                          />
                          <span>{clm.claim_text.slice(0, 60)}...</span>
                        </label>
                      ))}
                    </div>
                  </div>
                )}
                <button
                  className="primary-button"
                  disabled={synthesisBusy || !activeSession}
                  onClick={() => void handleGenerateSynthesis()}
                >
                  {synthesisBusy ? "Synthesizing..." : "Generate Synthesis"}
                </button>
              </div>
            </div>
          </div>

          {/* Grounded Report View */}
          {selectedSynthesis ? (
            <div className="panel synthesis-view-panel">
              <div className="panel-heading">
                <div>
                  <span className="section-kicker">Synthesis Version {selectedSynthesis.version_id}</span>
                  <h2>{selectedSynthesis.title}</h2>
                </div>
                <div className="grounding-badge-wrap">
                  <span className={`grounding-pill grounding-${selectedSynthesis.grounding_status}`}>
                    {selectedSynthesis.grounding_status.toUpperCase()}
                  </span>
                </div>
              </div>

              {/* Explicit Grounding Disclaimer */}
              <div className="grounding-disclaimer-banner">
                <Icon name="shield" />
                <div>
                  <strong>GROUNDED STATUS DEFINITION</strong>
                  <p>
                    <strong>GROUNDED</strong> indicates strict traceability to acquired evidence chunks. It does <strong>not</strong> guarantee absolute factual truth.
                  </p>
                </div>
              </div>

              {/* Research Gaps & Warnings */}
              {selectedSynthesis.grounding_status !== "grounded" && (
                <div className="research-gaps-panel">
                  <h4>Research Gaps Detected</h4>
                  <ul>
                    {selectedSynthesis.grounding_status === "incomplete" && (
                      <li>Grounding is incomplete: one or more factual statements lack linked citations.</li>
                    )}
                    {selectedSynthesis.grounding_status === "invalid_citation" && (
                      <li>Invalid citation ID detected: references evidence outside the session or nonexistent chunks.</li>
                    )}
                    {selectedSynthesis.grounding_status === "unsupported_claims" && (
                      <li>Claims included without adequate supporting evidence.</li>
                    )}
                    {selectedSynthesis.grounding_status === "contested" && (
                      <li>Contradictions detected between sources without consensus.</li>
                    )}
                  </ul>
                </div>
              )}

              {/* Summary Text */}
              <div className="synthesis-summary-box">
                <h3>Executive Summary</h3>
                <p>{selectedSynthesis.summary_text}</p>
              </div>

              {/* Structured Sections */}
              <div className="synthesis-sections">
                {selectedSynthesis.structured_sections?.map((sec, sIdx) => (
                  <section key={sIdx} className="synthesis-section">
                    <h4>{sec.heading}</h4>
                    <p className="synthesis-body">
                      {sec.body}
                      {sec.citation_ids?.map((cid) => (
                        <button
                          key={cid}
                          className="inline-citation-badge"
                          onClick={() => openCitationProvenance(cid)}
                          title="View exact provenance"
                        >
                          {selectedSynthesis.citation_map?.[cid] || `[${cid}]`}
                        </button>
                      ))}
                    </p>
                  </section>
                ))}
              </div>
            </div>
          ) : (
            <div className="panel empty-state">
              <Icon name="brain" />
              <p>No synthesis version generated yet for this session.</p>
              <small>Click "Generate Synthesis" above to assemble grounded insights.</small>
            </div>
          )}
        </section>
      )}

      {/* MODALS */}

      {/* Modal 1: New Session Modal */}
      {showNewSessionModal && (
        <div className="research-modal-backdrop">
          <div className="research-modal">
            <div className="modal-header">
              <h3>Create Research Session</h3>
              <button onClick={() => setShowNewSessionModal(false)}>✕</button>
            </div>
            <div className="modal-body">
              <label>
                <span>Session Title</span>
                <input
                  type="text"
                  placeholder="e.g. Qubit Coherence Investigation"
                  value={newSessionTitle}
                  onChange={(e) => setNewSessionTitle(e.target.value)}
                />
              </label>
              <label>
                <span>Research Purpose / Scope</span>
                <textarea
                  rows={3}
                  placeholder="Describe inquiry goals..."
                  value={newSessionPurpose}
                  onChange={(e) => setNewSessionPurpose(e.target.value)}
                />
              </label>
            </div>
            <div className="modal-footer">
              <button className="secondary-button" onClick={() => setShowNewSessionModal(false)}>
                Cancel
              </button>
              <button
                className="primary-button"
                disabled={!newSessionTitle.trim() || loading}
                onClick={() => void handleCreateSession()}
              >
                Create Session
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal 2: Create Evidence Modal */}
      {showNewEvidenceModal && (
        <div className="research-modal-backdrop">
          <div className="research-modal">
            <div className="modal-header">
              <h3>Capture Evidence Chunk</h3>
              <button onClick={() => setShowNewEvidenceModal(false)}>✕</button>
            </div>
            <div className="modal-body">
              <label>
                <span>Target Snapshot ID</span>
                <input
                  type="text"
                  value={newEvidenceSnapshotId}
                  onChange={(e) => setNewEvidenceSnapshotId(e.target.value)}
                />
              </label>
              <label>
                <span>Excerpt Text</span>
                <textarea
                  rows={4}
                  placeholder="Exact quote or excerpt..."
                  value={newEvidenceExcerpt}
                  onChange={(e) => setNewEvidenceExcerpt(e.target.value)}
                />
              </label>
              <label>
                <span>Locator Info (JSON or label)</span>
                <input
                  type="text"
                  placeholder='e.g. {"page": 1, "segment": 3}'
                  value={newEvidenceLocator}
                  onChange={(e) => setNewEvidenceLocator(e.target.value)}
                />
              </label>
            </div>
            <div className="modal-footer">
              <button className="secondary-button" onClick={() => setShowNewEvidenceModal(false)}>
                Cancel
              </button>
              <button
                className="primary-button"
                disabled={!newEvidenceSnapshotId || !newEvidenceExcerpt.trim() || loading}
                onClick={() => void handleCreateEvidence()}
              >
                Save Evidence
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal 3: New Question Modal */}
      {showNewQuestionModal && (
        <div className="research-modal-backdrop">
          <div className="research-modal">
            <div className="modal-header">
              <h3>Create Research Question</h3>
              <button onClick={() => setShowNewQuestionModal(false)}>✕</button>
            </div>
            <div className="modal-body">
              <label>
                <span>Question</span>
                <input
                  type="text"
                  placeholder="e.g. What materials achieve high Q factors at millikelvin temperatures?"
                  value={newQuestionText}
                  onChange={(e) => setNewQuestionText(e.target.value)}
                />
              </label>
              <label>
                <span>Parent Question (Optional)</span>
                <select
                  value={newQuestionParentId}
                  onChange={(e) => setNewQuestionParentId(e.target.value)}
                >
                  <option value="">None (Root Question)</option>
                  {questions.map((q) => (
                    <option key={q.question_id} value={q.question_id}>
                      {q.question}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="modal-footer">
              <button className="secondary-button" onClick={() => setShowNewQuestionModal(false)}>
                Cancel
              </button>
              <button
                className="primary-button"
                disabled={!newQuestionText.trim() || loading}
                onClick={() => void handleCreateQuestion()}
              >
                Save Question
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal 4: New Claim Modal */}
      {showNewClaimModal && (
        <div className="research-modal-backdrop">
          <div className="research-modal">
            <div className="modal-header">
              <h3>Create Knowledge Claim</h3>
              <button onClick={() => setShowNewClaimModal(false)}>✕</button>
            </div>
            <div className="modal-body">
              <label>
                <span>Claim Type</span>
                <select
                  value={newClaimType}
                  onChange={(e) => setNewClaimType(e.target.value as ClaimType)}
                >
                  <option value="factual">Factual (direct empirical or literature fact)</option>
                  <option value="inferential">Inferential (logical deduction or synthesis)</option>
                  <option value="procedural">Procedural (methodological instruction)</option>
                </select>
              </label>
              <label>
                <span>Claim Statement</span>
                <textarea
                  rows={3}
                  placeholder="State the proposition clearly..."
                  value={newClaimText}
                  onChange={(e) => setNewClaimText(e.target.value)}
                />
              </label>
              <label>
                <span>Linked Question (Optional)</span>
                <select
                  value={newClaimQuestionId}
                  onChange={(e) => setNewClaimQuestionId(e.target.value)}
                >
                  <option value="">None</option>
                  {questions.map((q) => (
                    <option key={q.question_id} value={q.question_id}>
                      {q.question}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Scope Qualifier (Optional)</span>
                <input
                  type="text"
                  placeholder="e.g. Under cryogenic conditions below 20mK"
                  value={newClaimScope}
                  onChange={(e) => setNewClaimScope(e.target.value)}
                />
              </label>
            </div>
            <div className="modal-footer">
              <button className="secondary-button" onClick={() => setShowNewClaimModal(false)}>
                Cancel
              </button>
              <button
                className="primary-button"
                disabled={!newClaimText.trim() || loading}
                onClick={() => void handleCreateClaim()}
              >
                Save Claim
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal 5: Link Claim Evidence Modal */}
      {linkModalClaim && (
        <div className="research-modal-backdrop">
          <div className="research-modal">
            <div className="modal-header">
              <h3>Link Evidence to Claim</h3>
              <button onClick={() => setLinkModalClaim(null)}>✕</button>
            </div>
            <div className="modal-body">
              <p className="claim-quote">
                <strong>Claim:</strong> {linkModalClaim.claim_text}
              </p>
              <label>
                <span>Select Evidence Chunk</span>
                <select
                  value={linkEvidenceId}
                  onChange={(e) => setLinkEvidenceId(e.target.value)}
                >
                  {evidenceList.map((ev) => (
                    <option key={ev.evidence_id} value={ev.evidence_id}>
                      {ev.evidence_id} - "{ev.excerpt.slice(0, 50)}..."
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Relation</span>
                <select
                  value={linkRelation}
                  onChange={(e) => setLinkRelation(e.target.value as ClaimRelation)}
                >
                  <option value="supports">SUPPORTS (Directly provides evidence for)</option>
                  <option value="contradicts">CONTRADICTS (Directly refutes or conflicts with)</option>
                  <option value="context">CONTEXT (Provides background context)</option>
                  <option value="qualifies">QUALIFIES (Limits or conditions the scope)</option>
                </select>
              </label>
              <label>
                <span>Rationale / Assessor Note (Optional)</span>
                <input
                  type="text"
                  placeholder="Rationale for this relationship..."
                  value={linkRationale}
                  onChange={(e) => setLinkRationale(e.target.value)}
                />
              </label>
            </div>
            <div className="modal-footer">
              <button className="secondary-button" onClick={() => setLinkModalClaim(null)}>
                Cancel
              </button>
              <button
                className="primary-button"
                disabled={!linkEvidenceId || loading}
                onClick={() => void handleLinkClaimEvidence()}
              >
                Save Link
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal 6: Citation Provenance Viewer */}
      {selectedCitationProvenance && (
        <div className="research-modal-backdrop">
          <div className="research-modal provenance-modal">
            <div className="modal-header">
              <h3>Citation Provenance: {selectedCitationProvenance.citation.display_label}</h3>
              <button onClick={() => setSelectedCitationProvenance(null)}>✕</button>
            </div>
            <div className="modal-body">
              <div className="provenance-chain">
                <div className="chain-step">
                  <span className="step-tag">1. Source</span>
                  <strong>{selectedCitationProvenance.source?.title || "Unknown Source"}</strong>
                  <code>{selectedCitationProvenance.source?.location}</code>
                </div>
                <div className="chain-arrow">↳</div>
                <div className="chain-step">
                  <span className="step-tag">2. Snapshot</span>
                  <code>{selectedCitationProvenance.snapshot?.snapshot_id}</code>
                  <small>
                    SHA256: {selectedCitationProvenance.snapshot?.sha256} (
                    {selectedCitationProvenance.snapshot?.byte_count} bytes)
                  </small>
                </div>
                <div className="chain-arrow">↳</div>
                <div className="chain-step">
                  <span className="step-tag">3. Locator & Evidence</span>
                  <code>Locator: {JSON.stringify(selectedCitationProvenance.evidence?.locator)}</code>
                  <blockquote className="provenance-excerpt">
                    {selectedCitationProvenance.evidence?.excerpt}
                  </blockquote>
                </div>
              </div>
            </div>
            <div className="modal-footer">
              <button className="primary-button" onClick={() => setSelectedCitationProvenance(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal 7: Snapshot Content Viewer */}
      {snapshotContentModal && (
        <div className="research-modal-backdrop">
          <div className="research-modal large-modal">
            <div className="modal-header">
              <h3>Raw Content: {snapshotContentModal.title}</h3>
              <button onClick={() => setSnapshotContentModal(null)}>✕</button>
            </div>
            <div className="modal-body">
              <iframe
                src={snapshotContentModal.url}
                title="Snapshot Content"
                className="snapshot-iframe"
              />
            </div>
            <div className="modal-footer">
              <button className="primary-button" onClick={() => setSnapshotContentModal(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
