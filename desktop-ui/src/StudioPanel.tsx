// Round 25: ZCode-style tabbed right panel (owner screenshots, matched).
// Own chrome row (collapse chevron + tab strip + expand/hide controls),
// a single-column New-tab page with exactly the six ZCode tiles, a
// full-surface PowerShell terminal, ZCode's Files layout, and a browser
// toolbar. The Round 22 legacy facility tabs are removed (Settings keeps
// those facilities); Side conversation starts a standalone chat.
import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { amca } from "./api";
import type { ArtifactMeta, ExplorerEntry, ExplorerFile } from "./types";
import { Icon } from "./icons";
import { ArtifactPreview } from "./StudioComponents";
import {
  BROWSER_SCOPE_NOTE,
  FILES_SCOPE_NOTE,
  NEW_TAB_FACILITIES,
  SITES_LOCAL_NOTE,
  SIDE_CONVERSATION_TILE,
  TERMINAL_SCOPE_NOTE,
  panelFacilityById,
  type PanelFacilityId,
} from "./studioFacilitiesModel";

export interface PanelTab {
  /** Unique per open tab (two Terminal tabs = two sessions). */
  key: string;
  facility: PanelFacilityId;
}

/**
 * Live terminal sessions keyed by panel-tab key (gate F1): the PowerShell
 * session belongs to the TAB, not to the mounted view. Tab switches,
 * inspector hides, and view changes keep the process, cwd, and scrollback
 * alive; only closing the tab (or the 2h idle reaper) ends it.
 */
const terminalSessions = new Map<string, string>();

function closeTerminalSession(tabKey: string) {
  const sessionId = terminalSessions.get(tabKey);
  if (sessionId) {
    terminalSessions.delete(tabKey);
    void amca.terminalDelete(sessionId).catch(() => undefined);
  }
}

interface PanelProps {
  tabs: PanelTab[];
  activeKey: string | null;
  onSelect: (key: string) => void;
  onClose: (key: string) => void;
  onOpenFacility: (facility: PanelFacilityId) => void;
  onSideConversation: () => void;
  onCollapse: () => void;
  onWiden: () => void;
  projectId: string | null;
  /** Workspace name shown in the Files tree header and Terminal tab label. */
  workspaceLabel: string;
  /** App's ReviewCenter JSX (existing review surface). */
  reviewView: React.ReactNode;
}

export function StudioPanel({
  tabs,
  activeKey,
  onSelect,
  onClose,
  onOpenFacility,
  onSideConversation,
  onCollapse,
  onWiden,
  projectId,
  workspaceLabel,
  reviewView,
}: PanelProps) {
  const active = tabs.find((tab) => tab.key === activeKey) ?? null;
  const closeTab = (key: string) => {
    closeTerminalSession(key);
    onClose(key);
  };
  const tabLabel = (tab: PanelTab) =>
    tab.facility === "terminal" ? workspaceLabel : panelFacilityById(tab.facility)?.label ?? tab.facility;
  return (
    <div className="panel-root">
      <div className="panel-chrome">
        <button
          className="panel-chrome-icon"
          aria-label="Collapse panel"
          title="Collapse panel"
          onClick={onCollapse}
        >
          <Icon name="chevronDown" width={14} height={14} />
        </button>
        <div className="panel-tabbar" role="tablist" aria-label="Panel tabs">
          {tabs.map((tab) => {
            const isActive = tab.key === activeKey;
            return (
              <span key={tab.key} className={isActive ? "panel-tab active" : "panel-tab"}>
                <button role="tab" aria-selected={isActive} onClick={() => onSelect(tab.key)}>
                  <Icon name={facilityIcon(tab.facility)} width={13} height={13} />
                  <span>{tabLabel(tab)}</span>
                </button>
                <button
                  className="panel-tab-close"
                  aria-label={`Close ${tabLabel(tab)} tab`}
                  title="Close tab"
                  onClick={() => closeTab(tab.key)}
                >
                  <Icon name="close" width={10} height={10} />
                </button>
              </span>
            );
          })}
          <button
            className="panel-tab-new"
            aria-label="New tab"
            title="New tab"
            onClick={() => onSelect("__newtab__")}
          >
            <Icon name="plus" width={13} height={13} />
          </button>
        </div>
        <div className="panel-chrome-actions">
          <button className="panel-chrome-icon" aria-label="Expand panel" title="Expand panel" onClick={onWiden}>
            <Icon name="expand" width={13} height={13} />
          </button>
          <button
            className="panel-chrome-icon"
            aria-label="Hide inspector"
            title="Hide inspector"
            onClick={onCollapse}
          >
            <Icon name="layout" width={13} height={13} />
          </button>
        </div>
      </div>
      {active === null ? (
        <NewTabList onOpen={onOpenFacility} onSideConversation={onSideConversation} />
      ) : (
        <PanelTabBody
          tab={active}
          projectId={projectId}
          workspaceLabel={workspaceLabel}
          reviewView={reviewView}
        />
      )}
    </div>
  );
}

function facilityIcon(facility: PanelFacilityId) {
  switch (facility) {
    case "files":
      return "folder" as const;
    case "browser":
      return "globe" as const;
    case "terminal":
      return "terminal" as const;
    case "review":
      return "logs" as const;
    case "sites":
      return "layout" as const;
    default:
      return "box" as const;
  }
}

/** New tab page: exactly the six ZCode tiles, single column. */
function NewTabList({
  onOpen,
  onSideConversation,
}: {
  onOpen: (facility: PanelFacilityId) => void;
  onSideConversation: () => void;
}) {
  return (
    <div className="panel-newtab">
      <h1>New tab</h1>
      <div className="panel-tile-list">
        {NEW_TAB_FACILITIES.map((facility) => (
          <article key={facility.id} className="panel-tile">
            <span className="panel-tile-icon">
              <Icon name={facilityIcon(facility.id)} width={19} height={19} />
            </span>
            <div className="panel-tile-copy">
              <strong>{facility.title}</strong>
              <small>{facility.description}</small>
            </div>
            <button onClick={() => onOpen(facility.id)}>Open</button>
          </article>
        ))}
        <article className="panel-tile">
          <span className="panel-tile-icon">
            <Icon name="chat" width={19} height={19} />
          </span>
          <div className="panel-tile-copy">
            <strong>{SIDE_CONVERSATION_TILE.title}</strong>
            <small>{SIDE_CONVERSATION_TILE.description}</small>
          </div>
          <button onClick={onSideConversation}>Open</button>
        </article>
      </div>
    </div>
  );
}

function PanelTabBody({
  tab,
  projectId,
  workspaceLabel,
  reviewView,
}: {
  tab: PanelTab;
  projectId: string | null;
  workspaceLabel: string;
  reviewView: React.ReactNode;
}) {
  switch (tab.facility) {
    case "files":
      // key: re-reads the per-project folder override on project switch
      // (review R26 fix 1 - no stale root across projects).
      return <FileExplorer key={projectId ?? "none"} projectId={projectId} workspaceLabel={workspaceLabel} />;
    case "browser":
      return <BrowserPreview />;
    case "terminal":
      return <TerminalView key={tab.key} projectId={projectId} tabKey={tab.key} />;
    case "review":
      return <ReviewTab reviewView={reviewView} />;
    case "sites":
      return <SitesWorkspace projectId={projectId} />;
    default:
      return null;
  }
}

/** Review: AMCA's existing Review Center surfaced as a panel tab. */
function ReviewTab({ reviewView }: { reviewView: React.ReactNode }) {
  return <section className="panel-workspace panel-review">{reviewView}</section>;
}

/** Files: live tree of the project folder + any-type file viewer. */
function FileExplorer({
  projectId,
  workspaceLabel,
}: {
  projectId: string | null;
  workspaceLabel: string;
}) {
  // Round 26: the explorer operates on the project's REAL folder
  // (root_path) by default; the operator can pick any folder. Empty
  // override = the project folder.
  const filesRootKey = projectId ? `amca.panel.filesRoot.${projectId}` : "";
  const [rootOverride, setRootOverride] = useState(
    () => (projectId ? localStorage.getItem(`amca.panel.filesRoot.${projectId}`) ?? "" : ""),
  );
  const [tree, setTree] = useState<Map<string, ExplorerEntry[]>>(new Map());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [root, setRoot] = useState("");
  const [selected, setSelected] = useState<ExplorerFile | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const seqRef = useRef(0);
  const [rootTruncated, setRootTruncated] = useState(false);

  const loadDir = useCallback(
    async (path: string) => {
      if (!projectId) return;
      const seq = ++seqRef.current;
      setError("");
      try {
        const listing = await amca.explorerList(projectId, path, rootOverride || undefined);
        if (seq !== seqRef.current) return; // gate F9: stale response
        setRoot(listing.root);
        if (!path) setRootTruncated(listing.truncated);
        setTree((current) => {
          const next = new Map(current);
          next.set(listing.path, listing.entries);
          return next;
        });
      } catch (caught) {
        if (seq !== seqRef.current) return;
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    },
    [projectId, rootOverride],
  );

  useEffect(() => {
    setTree(new Map());
    setExpanded(new Set());
    setSelected(null);
    setQuery("");
    setRootTruncated(false);
    if (!projectId) return;
    setBusy(true);
    void loadDir("").finally(() => setBusy(false));
  }, [projectId, rootOverride, loadDir]);

  const pickFolder = async () => {
    try {
      const picked = await invoke<string | null>("pick_project_folder");
      if (picked) {
        if (projectId) localStorage.setItem(filesRootKey, picked);
        setRootOverride(picked);
      }
    } catch {
      // Browser-tab (no Tauri): fall back to a typed path.
      const typed = window.prompt("Folder to browse:", rootOverride || root || "");
      if (typed && typed.trim()) {
        if (projectId) localStorage.setItem(filesRootKey, typed.trim());
        setRootOverride(typed.trim());
      }
    }
  };

  const resetFolder = () => {
    if (projectId) localStorage.removeItem(filesRootKey);
    setRootOverride("");
  };

  const openDir = async (path: string) => {
    if (expanded.has(path)) {
      setExpanded((current) => {
        const next = new Set(current);
        next.delete(path);
        return next;
      });
      return;
    }
    setExpanded((current) => new Set(current).add(path));
    if (!tree.has(path)) await loadDir(path);
  };

  const openFile = async (path: string) => {
    if (!projectId) return;
    setError("");
    try {
      setSelected(await amca.explorerRead(projectId, path, rootOverride || undefined));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const downloadSelected = () => {
    if (!selected) return;
    try {
      const bytes =
        selected.encoding === "base64"
          ? Uint8Array.from(atob(selected.content), (ch) => ch.charCodeAt(0))
          : new TextEncoder().encode(selected.content);
      const blob = new Blob([bytes], { type: selected.media_type });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = selected.path.split("/").pop() || "file";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch {
      setError("Could not prepare the download.");
    }
  };

  if (!projectId) {
    return (
      <PanelShell title="Files" note={FILES_SCOPE_NOTE}>
        <div className="panel-empty">
          <Icon name="folder" width={30} height={30} />
          <p>Open a project to browse its files</p>
        </div>
      </PanelShell>
    );
  }

  const q = query.trim().toLowerCase();

  const renderLevel = (dirPath: string, depth: number): React.ReactNode[] => {
    const entries = tree.get(dirPath) ?? [];
    return entries
      .filter((entry) => !q || entry.name.toLowerCase().includes(q))
      .map((entry) => {
        const childPath = dirPath ? `${dirPath}/${entry.name}` : entry.name;
        if (entry.type === "dir") {
          const open = expanded.has(childPath);
          return (
            <div key={childPath}>
              <button
                className="panel-tree-row"
                style={{ paddingLeft: 10 + depth * 16 }}
                onClick={() => void openDir(childPath)}
              >
                <span className={open ? "panel-chevron open" : "panel-chevron"}>
                  <Icon name="chevron" width={12} height={12} />
                </span>
                <Icon name={open ? "folderOpen" : "folder"} width={14} height={14} />
                <span>{entry.name}</span>
              </button>
              {open && renderLevel(childPath, depth + 1)}
            </div>
          );
        }
        return (
          <button
            key={childPath}
            className={
              selected?.path === childPath ? "panel-tree-row active" : "panel-tree-row"
            }
            style={{ paddingLeft: 10 + depth * 16 + 16 }}
            onClick={() => void openFile(childPath)}
          >
            <Icon name="file" width={14} height={14} />
            <span>{entry.name}</span>
          </button>
        );
      });
  };

  return (
    <PanelShell
      title="Files"
      note={FILES_SCOPE_NOTE}
      error={error}
      onRetry={() => void loadDir("")}
    >
      <div className="panel-files-layout">
        <div className="panel-files-tree">
          <input
            className="panel-search"
            aria-label="Search files"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search files…"
          />
          <div className="panel-workspace-row">
            <strong title={root || rootOverride || workspaceLabel}>
              {root ? root.split(/[\\/]/).pop() : workspaceLabel || "folder"}
            </strong>
            <span className="panel-workspace-actions">
              <button aria-label="Change folder" title="Change folder" onClick={() => void pickFolder()}>
                <Icon name="folderOpen" width={13} height={13} />
              </button>
              <button aria-label="Refresh files" title="Refresh" onClick={() => void loadDir("")}>
                <Icon name="refresh" width={13} height={13} />
              </button>
            </span>
          </div>
          {rootOverride ? (
            <div className="panel-rootnote">
              <small title={rootOverride}>custom folder</small>
              <button onClick={resetFolder}>Use project folder</button>
            </div>
          ) : null}
          {busy && !tree.has("") ? (
            <p className="muted-copy">Loading…</p>
          ) : (
            <>
              {tree.get("")?.length ? (
                <div className="panel-tree">{renderLevel("", 0)}</div>
              ) : (
                <p className="muted-copy">
                  This folder is empty — use the folder button above to browse another folder.
                </p>
              )}
              {rootTruncated && (
                <p className="muted-copy">Folder listing capped at 500 entries — use search to narrow it.</p>
              )}
            </>
          )}
        </div>
        <div className="panel-files-view">
          {selected === null ? (
            <div className="panel-empty">
              <Icon name="folder" width={30} height={30} />
              <p>Select a file from the navigation</p>
            </div>
          ) : (
            <>
              <div className="panel-filehead">
                <strong>{selected.path}</strong>
                <small>
                  {selected.bytes} bytes · {selected.media_type}
                  {selected.truncated ? " · truncated" : ""}
                </small>
                <button onClick={downloadSelected}>Save a copy</button>
              </div>
              {selected.media_type.startsWith("image/") && selected.encoding === "base64" ? (
                <img
                  className="panel-image"
                  alt={selected.path}
                  src={`data:${selected.media_type};base64,${selected.content}`}
                />
              ) : selected.encoding === "text" ? (
                <pre className="panel-filebody">{selected.content}</pre>
              ) : (
                <p className="muted-copy">
                  Binary file — use “Save a copy” to open it outside AMCA.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </PanelShell>
  );
}

/** Browser: reader + direct preview of any address the operator enters. */
function BrowserPreview() {
  const [address, setAddress] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [cursor, setCursor] = useState(-1);
  const [frameUrl, setFrameUrl] = useState("");
  const [frameNonce, setFrameNonce] = useState(0);
  const [mode, setMode] = useState<"reader" | "direct">("reader");
  const [pageHtml, setPageHtml] = useState("");
  const [loading, setLoading] = useState(false);
  const [pageError, setPageError] = useState("");

  const navigate = (raw: string) => {
    const url = raw.trim();
    if (!url) return;
    const next = url.startsWith("http://") || url.startsWith("https://") ? url : `http://${url}`;
    setAddress(next);
    setHistory((current) => {
      const trimmed = current.slice(0, cursor + 1);
      return [...trimmed, next].slice(-50);
    });
    setCursor((value) => Math.min(value + 1, 49));
    setFrameUrl(next);
    setFrameNonce((value) => value + 1);
    if (mode === "reader") void loadReader(next);
  };

  const loadReader = async (url: string) => {
    setLoading(true);
    setPageError("");
    try {
      const page = await amca.panelFetchPage(url);
      setPageHtml(page.html);
    } catch (caught) {
      setPageHtml("");
      setPageError(
        `${caught instanceof Error ? caught.message : String(caught)} — try Direct mode or open it in your browser.`,
      );
    } finally {
      setLoading(false);
    }
  };

  const openExternal = async () => {
    if (!frameUrl) return;
    try {
      await invoke("open_external", { url: frameUrl });
    } catch {
      window.open(frameUrl, "_blank");
    }
  };

  const go = (target: string) => {
    setCursor((value) => value);
    setFrameUrl(target);
    setFrameNonce((value) => value + 1);
    if (mode === "reader") void loadReader(target);
  };

  return (
    <PanelShell title="Browser" note={BROWSER_SCOPE_NOTE} fill error={pageError} onRetry={() => { setPageError(""); if (frameUrl && mode === "reader") void loadReader(frameUrl); }}>
      <div className="panel-browser-bar">
        <button
          aria-label="Back"
          title="Back"
          disabled={cursor <= 0}
          onClick={() => {
            const next = cursor - 1;
            if (next < 0 || !history[next]) return;
            setCursor(next);
            setAddress(history[next]);
            go(history[next]);
          }}
        >
          ‹
        </button>
        <button
          aria-label="Forward"
          title="Forward"
          disabled={cursor < 0 || cursor >= history.length - 1}
          onClick={() => {
            const next = cursor + 1;
            if (!history[next]) return;
            setCursor(next);
            setAddress(history[next]);
            go(history[next]);
          }}
        >
          ›
        </button>
        <button
          aria-label="Refresh"
          title="Refresh"
          disabled={!frameUrl}
          onClick={() => {
            setFrameNonce((value) => value + 1);
            if (mode === "reader" && frameUrl) void loadReader(frameUrl);
          }}
        >
          <Icon name="refresh" width={13} height={13} />
        </button>
        <form
          className="panel-browser-url"
          onSubmit={(event) => {
            event.preventDefault();
            navigate(address);
          }}
        >
          <input
            aria-label="Address"
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            placeholder="Enter a URL"
          />
        </form>
        <button
          aria-label="Open in system browser"
          title="Open in system browser"
          disabled={!frameUrl}
          onClick={() => void openExternal()}
        >
          <Icon name="expand" width={13} height={13} />
        </button>
        <button
          aria-label="Copy address"
          title="Copy address"
          disabled={!frameUrl}
          onClick={() => void navigator.clipboard.writeText(frameUrl).catch(() => undefined)}
        >
          <Icon name="copy" width={13} height={13} />
        </button>
      </div>
      <div className="panel-browser-modes">
        <button
          className={mode === "reader" ? "active" : ""}
          onClick={() => {
            setMode("reader");
            if (frameUrl) void loadReader(frameUrl);
          }}
        >
          Reader
        </button>
        <button
          className={mode === "direct" ? "active" : ""}
          onClick={() => setMode("direct")}
        >
          Direct
        </button>
        <small>
          {mode === "reader"
            ? "Fetched and rendered by AMCA — works for most sites."
            : "Live frame — works for local apps and embedding-friendly sites."}
        </small>
      </div>
      {frameUrl ? (
        mode === "direct" ? (
          <iframe
            key={frameNonce}
            className="panel-browser-frame"
            title="Browser preview"
            src={frameUrl}
            sandbox="allow-scripts allow-forms"
          />
        ) : (
          <div className="panel-browser-reader">
            {loading ? (
              <p className="muted-copy">Fetching…</p>
            ) : pageHtml ? (
              <iframe
                key={frameNonce}
                className="panel-browser-frame"
                title="Browser preview (reader)"
                srcDoc={pageHtml}
                sandbox="allow-forms"
              />
            ) : null}
          </div>
        )
      ) : (
        <div className="panel-empty fill">
          <Icon name="globe" width={34} height={34} />
          <h2>Browser</h2>
          <p>Paste or type a URL to open a page.</p>
        </div>
      )}
    </PanelShell>
  );
}

/** Terminal: the operator's own full-surface PowerShell session. */
const PS_BANNER = ["Windows PowerShell", "Copyright (C) Microsoft Corporation. All rights reserved.", ""];

function TerminalView({ projectId, tabKey }: { projectId: string | null; tabKey: string }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [cwd, setCwd] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [ended, setEnded] = useState(false);
  const [error, setError] = useState("");
  const offsetRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [restartFolder, setRestartFolder] = useState("");

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    let stopped = false;
    // Gate F1: the session outlives this component. Re-attach to the tab's
    // existing session (polling from 0 rebuilds the scrollback from the
    // backend ring buffer) or create one; only tab close deletes it.
    setLines([]);
    setEnded(false);
    setError("");
    offsetRef.current = 0;
    void (async () => {
      try {
        let sessionId = tabKey ? terminalSessions.get(tabKey) ?? null : null;
        if (!sessionId) {
          const folder = projectId
            ? localStorage.getItem(`amca.panel.termCwd.${projectId}`) ?? undefined
            : undefined;
          const session = await amca.terminalCreate(projectId, folder || undefined);
          sessionId = session.session_id;
          if (tabKey) terminalSessions.set(tabKey, sessionId);
        }
        if (cancelled) return;
        setSessionId(sessionId);
        // Show the real prompt path immediately; the first poll confirms it.
        if (!offsetRef.current) {
          const first = await amca.terminalOutput(sessionId, 0);
          if (!cancelled && first) {
            offsetRef.current = first.next_offset;
            setCwd(first.cwd);
            if (first.text)
              setLines((current) => [...current, ...first.text.split("\n")]);
          }
        }
        while (!stopped) {
          const page = await amca.terminalOutput(sessionId, offsetRef.current);
          if (stopped || cancelled) return;
          if (page.text) {
            offsetRef.current = page.next_offset;
            setCwd(page.cwd);
            setLines((current) => [...current.slice(-800), ...page.text.split("\n")]);
          }
          if (!page.alive) {
            setEnded(true);
            return;
          }
          await new Promise((resolve) => setTimeout(resolve, 400));
        }
      } catch (caught) {
        if (cancelled) return;
        const message = caught instanceof Error ? caught.message : String(caught);
        // Gate F11: a deleted/unknown session means the tab is dead, not
        // retryable - show the ended state instead of a retry loop.
        if (/unknown terminal session/i.test(message)) {
          setEnded(true);
          if (tabKey) terminalSessions.delete(tabKey);
        } else {
          setError(message);
        }
      }
    })();
    return () => {
      stopped = true;
      cancelled = true;
    };
    // attempt: Retry re-runs attach/create after a recoverable failure.
  }, [projectId, tabKey, attempt]);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [lines, input]);

  const submit = async () => {
    const line = input;
    if (!sessionId || !line.trim()) return;
    setInput("");
    setLines((current) => [...current, `PS ${cwd}> ${line}`]);
    try {
      await amca.terminalInput(sessionId, line);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const breakSession = async () => {
    if (!sessionId) return;
    setLines((current) => [...current, "^C"]);
    try {
      await amca.terminalBreak(sessionId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  // Round 26: (re)start the session in a chosen folder - the project's real
  // folder is the backend default; this override re-spawns PowerShell there.
  const restartHere = async (folder: string) => {
    if (!projectId) return;
    const target = folder.trim();
    setError("");
    try {
      if (sessionId) {
        await amca.terminalDelete(sessionId).catch(() => undefined);
        if (tabKey) terminalSessions.delete(tabKey);
      }
      setSessionId(null);
      setLines([]);
      setEnded(false);
      offsetRef.current = 0;
      setAttempt((value) => value + 1);
      if (tabKey && target) {
        // The attach effect creates with the persisted folder.
        localStorage.setItem(`amca.panel.termCwd.${projectId}`, target);
      } else if (tabKey) {
        localStorage.removeItem(`amca.panel.termCwd.${projectId}`);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  if (!projectId) {
    return (
      <PanelShell title="Terminal" note={TERMINAL_SCOPE_NOTE}>
        <div className="panel-empty fill">
          <Icon name="terminal" width={30} height={30} />
          <p>Open a project to start a terminal in its folder</p>
        </div>
      </PanelShell>
    );
  }

  return (
    <PanelShell title="Terminal" note={TERMINAL_SCOPE_NOTE} error={error} onRetry={() => { setError(""); setAttempt((value) => value + 1); }} fill>
      <div
        className="panel-terminal"
        role="terminal"
        aria-label="Terminal"
        onClick={() => inputRef.current?.focus()}
      >
        <div className="panel-terminal-scroll" ref={scrollRef}>
          <pre className="panel-terminal-banner">{PS_BANNER.join("\n")}</pre>
          <pre className="panel-terminal-body">{lines.join("\n")}</pre>
          <div className="panel-terminal-line">
            <span className="panel-terminal-prompt">PS {cwd}&gt;&nbsp;</span>
            <input
              ref={inputRef}
              aria-label="Terminal command"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void submit();
                } else if (event.key === "c" && event.ctrlKey) {
                  event.preventDefault();
                  void breakSession();
                }
              }}
              disabled={!sessionId || ended}
              autoComplete="off"
              spellCheck={false}
            />
            {ended && <span className="panel-terminal-ended">[session ended]</span>}
          </div>
        </div>
        <div className="panel-terminal-status">
          <small title={cwd}>{cwd || "starting…"}</small>
          <span className="panel-terminal-actions">
            <button
              aria-label="Change start folder"
              title="Restart in a chosen folder"
              onClick={async () => {
                try {
                  const picked = await invoke<string | null>("pick_project_folder");
                  if (picked) {
                    setRestartFolder(picked);
                    await restartHere(picked);
                  }
                } catch {
                  const typed = window.prompt("Folder for the terminal:", restartFolder || cwd || "");
                  if (typed && typed.trim()) {
                    setRestartFolder(typed.trim());
                    await restartHere(typed.trim());
                  }
                }
              }}
            >
              Folder
            </button>
            <button onClick={() => void breakSession()} disabled={!sessionId || ended}>
              Stop (Ctrl+C)
            </button>
          </span>
        </div>
      </div>
    </PanelShell>
  );
}

/** Sites: local HTML/Markdown artifact previews (honest, no publishing). */
function SitesWorkspace({ projectId }: { projectId: string | null }) {
  const [sites, setSites] = useState<ArtifactMeta[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(Boolean(projectId));
  const [canRenderMarkdown, setCanRenderMarkdown] = useState(false);
  const [selected, setSelected] = useState<{ meta: ArtifactMeta; content: string; version: number; rendered: boolean } | null>(null);

  const loadSites = async (signal: { cancelled: boolean }) => {
    if (!projectId) {
      setSites([]);
      setBusy(false);
      return;
    }
    setBusy(true);
    setError("");
    const [arts, caps] = await Promise.allSettled([
      amca.artifactsList(projectId),
      amca.artifactRenderCapabilities(),
    ]);
    if (signal.cancelled) return;
    if (arts.status === "fulfilled") {
      setSites(arts.value.artifacts.filter((a) => a.artifact_type === "html" || a.artifact_type === "markdown"));
    } else {
      setSites([]);
      setError(arts.reason instanceof Error ? arts.reason.message : String(arts.reason));
    }
    if (caps.status === "fulfilled") {
      setCanRenderMarkdown(
        caps.value.capabilities.some((c) => c.artifact_type === "markdown" && c.target_format === "html" && c.available),
      );
    } else {
      setCanRenderMarkdown(false);
    }
    setBusy(false);
  };

  useEffect(() => {
    const signal = { cancelled: false };
    void loadSites(signal);
    return () => {
      signal.cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const openSite = async (meta: ArtifactMeta) => {
    if (!projectId) return;
    setError("");
    try {
      const source = await amca.artifactSource(projectId, meta.artifact_id);
      let content = source.source.content;
      let rendered = false;
      if (meta.artifact_type === "markdown" && canRenderMarkdown) {
        const out = await amca.artifactExport(projectId, meta.artifact_id, "html").catch(() => null);
        const encoded = out?.export.data_base64;
        if (typeof encoded === "string" && encoded) {
          content = new TextDecoder().decode(Uint8Array.from(atob(encoded), (ch) => ch.charCodeAt(0)));
          rendered = true;
        }
      }
      setSelected({
        meta,
        content,
        version: source.source.version,
        rendered: rendered || meta.artifact_type === "html",
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  if (!projectId) {
    return (
      <PanelShell title="Sites" note={SITES_LOCAL_NOTE}>
        <div className="panel-empty fill">
          <Icon name="globe" width={30} height={30} />
          <p>Open a project to preview its sites</p>
        </div>
      </PanelShell>
    );
  }

  return (
    <PanelShell
      title="Sites"
      note={SITES_LOCAL_NOTE}
      error={error}
      onRetry={() => void loadSites({ cancelled: false })}
    >
      {busy ? (
        <p className="muted-copy">Loading sites…</p>
      ) : sites.length ? (
        <div className="automation-list">
          {sites.map((meta) => (
            <article className="file-row" key={meta.artifact_id}>
              <div>
                <strong>{meta.title}</strong>
                <small>{meta.artifact_type} · version {meta.current_version}</small>
              </div>
              <button onClick={() => void openSite(meta)}>Preview</button>
            </article>
          ))}
        </div>
      ) : (
        <div className="panel-empty fill">
          <Icon name="globe" width={30} height={30} />
          <h2>Sites</h2>
          <p>No local sites yet</p>
          <small>Ask AMCA to author an HTML page — it will show up here for preview.</small>
        </div>
      )}
      {selected && (
        <article className="panel">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">Sandboxed preview · version {selected.version}</span>
              <h2>{selected.meta.title}</h2>
            </div>
          </div>
          <ArtifactPreview artifactType="html" content={selected.content} />
          <p className="settings-note">
            {selected.rendered
              ? "Scripts and remote loads are disabled in this preview."
              : "HTML rendition is unavailable for this artifact (converter missing) — showing stored source instead."}
          </p>
          <div className="form-actions">
            <button className="secondary-button" onClick={() => setSelected(null)}>Close preview</button>
          </div>
        </article>
      )}
    </PanelShell>
  );
}

function PanelShell({
  title,
  note,
  error,
  onRetry,
  fill,
  children,
}: {
  title: string;
  note: string;
  error?: string;
  onRetry?: () => void;
  fill?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className={fill ? "panel-workspace fill" : "panel-workspace"}>
      <div className="panel-subhead">
        <span className="panel-subhead-title">{title}</span>
      </div>
      {note ? <p className="settings-note panel-note">{note}</p> : null}
      {error ? (
        <div className="error-banner">
          <div>
            <strong>Panel request failed</strong>
            <span>{error}</span>
          </div>
          {onRetry && <button onClick={onRetry}>Retry</button>}
        </div>
      ) : null}
      {children}
    </section>
  );
}
