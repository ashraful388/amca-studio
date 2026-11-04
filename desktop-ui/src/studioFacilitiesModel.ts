// ZCode-style tabbed panel model for the AMCA right panel
// (pure logic - no browser, no fetch, no backend). Run with:
// node --test tests/studioFacilitiesModel.test.mjs
//
// Round 25 (owner screenshots, verbatim): the New tab page shows exactly the
// six ZCode tiles - Files, Browser, Terminal, Review, Sites, Side
// conversation - in that order, as a single-column list. Files/Browser/
// Terminal/Sites/Review open tabs; Side conversation starts a standalone
// chat (AMCA's conversation surface) and is an action, not a tab. The
// Round 22 legacy facility tabs are removed from the panel: those
// facilities stay in Settings.

export type PanelFacilityId = "files" | "browser" | "terminal" | "sites" | "review";

export interface PanelFacility {
  id: PanelFacilityId;
  /** Tab label (Terminal is named after the workspace, like ZCode). */
  label: string;
  /** New-tab tile title. */
  title: string;
  /** New-tab tile description - the ZCode screenshot wording. */
  description: string;
}

/** The six ZCode-screenshot facilities, in the screenshot's order. */
export const NEW_TAB_FACILITIES: PanelFacility[] = [
  { id: "files", label: "Files", title: "Files", description: "Select a file from the navigation" },
  { id: "browser", label: "Browser", title: "Browser", description: "Browse websites and preview apps." },
  { id: "terminal", label: "Terminal", title: "Terminal", description: "Run commands and view output." },
  { id: "review", label: "Review", title: "Review", description: "Review code changes and diffs." },
  { id: "sites", label: "Sites", title: "Sites", description: "View sites, deployments, and usage." },
];

/** Side conversation is the sixth tile: an action, not a tab. */
export const SIDE_CONVERSATION_TILE = {
  title: "Side conversation",
  description: "Continue a conversation in the side pane.",
} as const;

export function panelFacilityById(id: string): PanelFacility | null {
  return NEW_TAB_FACILITIES.find((facility) => facility.id === id) ?? null;
}

/** Fixed hook event enum (mirrors run_hooks.HOOK_EVENTS on the backend). */
export const HOOK_EVENTS = [
  "run_started",
  "run_completed",
  "run_failed",
  "approval_pending",
  "artifact_created",
] as const;

export type HookEvent = (typeof HOOK_EVENTS)[number];

/** Fixed hook action enum (mirrors run_hooks.HOOK_ACTIONS on the backend). */
export const HOOK_ACTIONS = ["notify", "append-memory", "run-command"] as const;

export type HookAction = (typeof HOOK_ACTIONS)[number];

export function isHookEvent(value: string): value is HookEvent {
  return (HOOK_EVENTS as readonly string[]).includes(value);
}

export function isHookAction(value: string): value is HookAction {
  return (HOOK_ACTIONS as readonly string[]).includes(value);
}

/** Honest states the workspaces show instead of fake affordances. */
export const FILES_SCOPE_NOTE =
  "Live view of this project's workspace folder. Folders and files are read straight from disk; files open read-only in the panel.";

export const BROWSER_SCOPE_NOTE =
  "Embedded preview of any address you enter. Some sites refuse embedding and stay blank; AMCA never shares your sessions with them.";

export const TERMINAL_SCOPE_NOTE =
  "Your own PowerShell session in this project's folder. This terminal is yours - the agent still cannot run commands without Tool Approval.";

export const SITES_LOCAL_NOTE =
  "Local sites only: preview HTML artifacts in a sandbox and save them to disk. There is no publish step and no share links in this release.";
