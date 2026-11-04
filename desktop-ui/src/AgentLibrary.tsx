import { useEffect, useMemo, useState } from "react";
import { amca } from "./api";
import { Icon } from "./icons";
import { NanoAgentsPanel } from "./NanoAgents";
import { AgentMonitor } from "./AgentMonitor";
import type { AgentFabricSettings, AgentFabricState, NewCustomAgent } from "./types";
import {
  agentCatalogStats,
  agentDetailSections,
  filterAgents,
  type AgentTemplateLike,
} from "./agentControlModel";

export type AgentLibraryTab = "monitor" | "overview" | "library" | "custom" | "controls" | "nano";

const EMPTY_CUSTOM: NewCustomAgent = {
  name: "",
  description: "",
  instructions: "",
  category: "Custom",
  preferred_model_policy: "auto_intelligence",
  allowed_tools: [],
  permissions: [],
  can_spawn: false,
  max_children: 0,
  enabled: true,
};

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function AgentLibrary({ initialTab = "monitor" }: { initialTab?: AgentLibraryTab }) {
  const [state, setState] = useState<AgentFabricState | null>(null);
  const [tab, setTab] = useState<AgentLibraryTab>(initialTab);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [draft, setDraft] = useState<NewCustomAgent>(EMPTY_CUSTOM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Z5: selected agent for the commercial detail view.
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);

  const refresh = async () => {
    setError(null);
    try { setState(await amca.agentFabric()); }
    catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
  };

  useEffect(() => { void refresh(); }, []);

  const agents = useMemo(
    () => filterAgents(state?.agents ?? [], query, category),
    [state, query, category],
  );

  // Z5: catalog statistics + the manager's five UX states, from real docs.
  const catalogStats = useMemo(() => agentCatalogStats(state?.agents ?? []), [state]);
  const selectedAgent = useMemo(
    () => (state?.agents ?? []).find((agent) => agent.id === selectedAgentId) ?? null,
    [state, selectedAgentId],
  );

  const saveCustom = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(null);
    try {
      await amca.saveCustomAgent(draft);
      setDraft(EMPTY_CUSTOM);
      await refresh();
      setTab("library");
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setBusy(false); }
  };

  const deleteCustom = async (id: string, name: string) => {
    if (!window.confirm(`Delete custom agent “${name}”?`)) return;
    setBusy(true); setError(null);
    try { await amca.deleteCustomAgent(id); await refresh(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setBusy(false); }
  };

  const updateSettings = async (changes: Partial<AgentFabricSettings>) => {
    if (!state) return;
    setBusy(true); setError(null);
    try {
      const result = await amca.updateAgentSettings(changes);
      setState({ ...state, settings: result.settings });
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setBusy(false); }
  };

  if (!state) return <section className="workspace-view"><div className="panel agent-loading"><Icon name="brain" /><h2>Loading Agent Fabric…</h2>{error && <p>{error}</p>}</div></section>;

  return (
    <section className="workspace-view agent-library-view">
      <div className="view-heading agent-hero">
        <div><span className="section-kicker">Dynamic Agent Fabric</span><h1>Agents</h1><p>AMCA Studio builds the smallest effective temporary AI organization for each task.</p></div>
        <div className="agent-ready"><i /><span><strong>{state.status === "ready" ? "Fabric ready" : humanize(state.status)}</strong><small>Auto Agents {state.settings.auto_agents ? "on" : "off"}</small></span></div>
      </div>

      {error && <div className="error-banner"><div><strong>Agent Fabric reported a problem</strong><span>{error}</span></div><button onClick={() => void refresh()}>Retry</button></div>}

      <nav className="agent-tabs" aria-label="Agent sections">
        {(["monitor", "overview", "library", "custom", "controls", "nano"] as AgentLibraryTab[]).map((item) => <button key={item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>{humanize(item)}</button>)}
      </nav>

      {tab === "monitor" && <AgentMonitor />}

      {tab === "overview" && <>
        <div className="metrics-grid agent-metrics">
          <article className="metric-card tone-green"><span>Permanent core</span><strong>{state.counts.core}</strong><small>Governance, routing, verification</small></article>
          <article className="metric-card"><span>Built-in specialists</span><strong>{state.counts.built_in_specialists}</strong><small>Reusable roles across global domains</small></article>
          <article className="metric-card"><span>Custom agents</span><strong>{state.counts.custom}</strong><small>User-created, locally persisted</small></article>
          <article className="metric-card"><span>Currently active</span><strong>{state.counts.active}</strong><small>Temporary organizations retire after work</small></article>
        </div>
        <article className="panel settings-wide agent-principle">
          <div className="panel-heading"><div><span className="section-kicker">Execution hierarchy</span><h2>Tools first, agents only when useful</h2></div><span className="status-pill status-ready"><i />Economy protected</span></div>
          <div className="agent-hierarchy">{state.hierarchy.map((item, index) => <div key={item}><span>{index}</span><strong>{humanize(item)}</strong>{index < state.hierarchy.length - 1 && <b>→</b>}</div>)}</div>
          <p className="settings-note">Deterministic tools are Level 0. Nano and Micro workers receive minimal context. Managers activate only when coordination benefits exceed their overhead. The deterministic Governor enforces permissions, concurrency, recursion, runtime, and cost limits.</p>
        </article>
        <article className="panel settings-wide">
          <div className="panel-heading"><div><span className="section-kicker">Categories</span><h2>General-purpose specialist coverage</h2></div><button className="secondary-button" onClick={() => setTab("library")}>Browse library</button></div>
          <div className="category-cloud">{Object.entries(state.categories).map(([name, count]) => <button key={name} onClick={() => { setCategory(name); setTab("library"); }}><strong>{name}</strong><span>{count}</span></button>)}</div>
        </article>
      </>}

      {tab === "library" && <article className="panel settings-wide">
        <div className="panel-heading"><div><span className="section-kicker">Agent library</span><h2>{agents.length} available role{agents.length === 1 ? "" : "s"}</h2></div><button className="primary-button" onClick={() => setTab("custom")}>+ Create agent</button></div>
        <div className="agent-filters"><input aria-label="Search agents" placeholder="Search agents and capabilities" value={query} onChange={(event) => setQuery(event.target.value)} /><select aria-label="Agent category" value={category} onChange={(event) => setCategory(event.target.value)}><option value="all">All categories</option>{Object.keys(state.categories).map((item) => <option key={item}>{item}</option>)}</select></div>
        {selectedAgent && <AgentDetailCard agent={selectedAgent} onClose={() => setSelectedAgentId(null)} />}
        {agents.length ? <div className="agent-card-grid">
          {agents.map((agent) => {
            const detail = agentDetailSections(agent);
            const identity = detail[0].rows;
            const model = detail[2].rows;
            return (
              <article className={`agent-card selectable ${selectedAgentId === agent.id ? "selected" : ""}`} key={agent.id} onClick={() => setSelectedAgentId(selectedAgentId === agent.id ? null : agent.id)}>
                <div><span className={`agent-origin ${agent.origin}`}>{humanize(agent.origin)}</span><span>{humanize(agent.agent_class)}</span>{!agent.enabled && <span className="agent-disabled-pill">Disabled</span>}</div>
                <h3>{agent.name}</h3>
                <p>{agent.description}</p>
                <ul className="agent-card-facts">
                  <li title={model.find((row) => row.label === "Assignment policy")?.value}>Model: {humanize(agent.preferred_model_policy)}</li>
                  <li>Tools: {agent.allowed_tools.length || "none"}</li>
                  <li>Permissions: {agent.permissions.length || "default"}</li>
                  <li>{agent.can_spawn ? `Can spawn · max ${agent.max_children}` : "No spawning"}</li>
                </ul>
                <footer><small>{agent.category} · {identity.find((row) => row.label === "Status")?.value}</small><span className="agent-card-open">{selectedAgentId === agent.id ? "Hide details" : "View details"}</span>{agent.origin === "custom" && <button className="danger-link" disabled={busy} onClick={(event) => { event.stopPropagation(); void deleteCustom(agent.id, agent.name); }}>Delete</button>}</footer>
              </article>
            );
          })}
        </div> : (
          <div className="empty-state">
            <div className="empty-glyph"><Icon name="agents" /></div>
            <h3>{catalogStats.total === 0 ? "No agents available" : "No agents match the filters"}</h3>
            <p>{catalogStats.total === 0
              ? "The Agent Fabric catalog is empty. Create a custom specialist or check the service."
              : "Clear the search box or pick another category to see the full catalog."}</p>
            {catalogStats.total === 0 && <button className="empty-next" onClick={() => setTab("custom")}>Create agent</button>}
          </div>
        )}
        <p className="settings-note">Catalog: {catalogStats.total} roles — {catalogStats.builtIn} built-in · {catalogStats.custom} custom · {catalogStats.installed} installed · {catalogStats.spawnCapable} can spawn · {catalogStats.withTools} with explicit tools. Details show only what the fabric document records; per-agent task history is not tracked.</p>
      </article>}

      {tab === "custom" && <form className="panel settings-wide agent-form" onSubmit={(event) => void saveCustom(event)}>
        <div className="panel-heading"><div><span className="section-kicker">Custom agent</span><h2>Create a reusable specialist</h2></div><span className="status-pill"><i />Local metadata</span></div>
        <p className="settings-note">Custom agents define task behavior and boundaries. Creating one does not run a model or grant new permissions.</p>
        <div className="agent-form-grid">
          <label><span>Name</span><input required maxLength={120} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="Contract Evidence Reviewer" /></label>
          <label><span>Category</span><input required maxLength={80} value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })} /></label>
          <label className="wide"><span>Description</span><input required maxLength={500} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="What this agent is for" /></label>
          <label className="wide"><span>Instructions</span><textarea required maxLength={8000} value={draft.instructions} onChange={(event) => setDraft({ ...draft, instructions: event.target.value })} placeholder="Objective, boundaries, method, and expected behavior" /></label>
          <label><span>Preferred model policy</span><select value={draft.preferred_model_policy} onChange={(event) => setDraft({ ...draft, preferred_model_policy: event.target.value })}><option value="auto_intelligence">Auto Intelligence</option><option value="manual_only">Manual only</option><option value="local_preferred">Local preferred</option><option value="local_only">Local only</option></select></label>
          <label><span>Output schema</span><input value={draft.output_schema ?? ""} onChange={(event) => setDraft({ ...draft, output_schema: event.target.value })} placeholder="structured_result" /></label>
          <label className="wide"><span>Success criteria</span><input value={draft.success_criteria ?? ""} onChange={(event) => setDraft({ ...draft, success_criteria: event.target.value })} placeholder="Observable completion criteria" /></label>
        </div>
        <div className="form-actions"><button type="button" className="secondary-button" onClick={() => setDraft(EMPTY_CUSTOM)}>Clear</button><button className="primary-button" disabled={busy}>{busy ? "Saving…" : "Save custom agent"}</button></div>
      </form>}

      {tab === "controls" && <article className="panel settings-wide agent-controls">
        <div className="panel-heading"><div><span className="section-kicker">Governor controls</span><h2>Safe, cost-aware defaults</h2></div><span className="status-pill status-ready"><i />Deterministic</span></div>
        <div className="control-grid">
          {([
            ["auto_agents", "Auto Agents", "Choose specialists only when useful"],
            ["dynamic_agents", "Dynamic temporary agents", "Allow task-specific logical roles"],
            ["nano_agents", "Nano Agents", "Allow atomic minimal-context workers"],
            ["recursive_spawning", "Recursive spawning", "Allow bounded child proposals"],
            ["speculative_execution", "Speculative execution", "Trade bounded cost for latency"],
          ] as const).map(([key, label, detail]) => <label className="control-toggle" key={key}><span><strong>{label}</strong><small>{detail}</small></span><input type="checkbox" disabled={busy} checked={state.settings[key]} onChange={(event) => void updateSettings({ [key]: event.target.checked })} /></label>)}
          <label className="control-select"><span><strong>Scaling policy</strong><small>Controls proposed organization size</small></span><select disabled={busy} value={state.settings.scaling} onChange={(event) => void updateSettings({ scaling: event.target.value as AgentFabricSettings["scaling"] })}><option value="conservative">Conservative</option><option value="balanced">Balanced</option><option value="aggressive">Aggressive</option></select></label>
          <label className="control-select"><span><strong>Activity display</strong><small>Internal messages remain collapsed</small></span><select disabled={busy} value={state.settings.activity_detail} onChange={(event) => void updateSettings({ activity_detail: event.target.value as AgentFabricSettings["activity_detail"] })}><option value="compact">Compact</option><option value="detailed">Detailed</option></select></label>
        </div>
        <div className="envelope-grid">{Object.entries(state.settings.envelope).map(([key, value]) => <div key={key}><span>{humanize(key)}</span><strong>{key.includes("cost") ? `$${Number(value).toFixed(2)}` : value}</strong></div>)}</div>
      </article>}

      {tab === "nano" && <NanoAgentsPanel />}
    </section>
  );
}

// Z5: the commercial Agent Detail view (order scope 2). Sections follow the
// manager's sequence; where the runtime tracks nothing per-template
// (recent tasks / performance / cost) the section renders its honest
// note instead of invented metrics. "Recent tasks" links to the live
// surfaces that DO carry agent work.
function AgentDetailCard({ agent, onClose }: { agent: AgentTemplateLike; onClose: () => void }) {
  const sections = agentDetailSections(agent);
  return (
    <section className="agent-detail" aria-label={`Details for ${agent.name}`}>
      <div className="agent-detail-head">
        <div>
          <span className={`agent-origin ${agent.origin}`}>{humanize(agent.origin)}</span>
          <h3>{agent.name}</h3>
          <small>{agent.category} · {humanize(agent.agent_class)}</small>
        </div>
        <button className="secondary-button" onClick={onClose}>Close details</button>
      </div>
      <div className="agent-detail-grid">
        {sections.map((section) => (
          <article key={section.id} className={`agent-detail-section ${section.rows.length ? "" : "note-only"}`}>
            <h4>{section.title}</h4>
            {section.rows.length > 0 ? (
              <dl>
                {section.rows.map((row) => (
                  <div key={row.label}><dt>{row.label}</dt><dd>{row.value}</dd></div>
                ))}
              </dl>
            ) : (
              <p>{section.note}</p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
