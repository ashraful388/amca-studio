import type { SVGProps } from "react";

export type IconName =
  | "home"
  | "room"
  | "whiteboard"
  | "files"
  | "cost"
  | "benchmark"
  | "logs"
  | "settings"
  | "refresh"
  | "play"
  | "spark"
  | "shield"
  | "brain"
  | "agents"
  | "automation"
  | "plugins"
  | "models"
  | "folder"
  | "folderOpen"
  | "chat"
  | "box"
  | "pencil"
  | "trash"
  | "plug"
  | "eye"
  | "eyeOff"
  | "kebab"
  | "plus"
  | "slash"
  | "at"
  | "copy"
  | "thumbUp"
  | "thumbDown"
  // Round 24 ZCode-style panel workspaces
  | "terminal"
  | "globe"
  | "layout"
  | "close"
  | "chevron"
  | "chevronDown"
  | "expand"
  | "file";

const paths: Record<IconName, React.ReactNode> = {
  home: <><path d="m3 10 9-7 9 7"/><path d="M5 9v11h14V9"/><path d="M9 20v-6h6v6"/></>,
  room: <><circle cx="8" cy="8" r="3"/><circle cx="17" cy="7" r="2.5"/><path d="M2.5 20c.4-4.3 2.4-6.5 5.5-6.5s5.1 2.2 5.5 6.5"/><path d="M14 13c4.1-.7 6.6 1.5 7 5"/></>,
  whiteboard: <><rect x="3" y="4" width="18" height="13" rx="2"/><path d="m8 21 4-4 4 4M8 9h8M8 13h5"/></>,
  files: <><path d="M4 4h6l2 3h8v13H4z"/><path d="M4 9h16"/></>,
  cost: <><circle cx="12" cy="12" r="9"/><path d="M15.5 8.5c-.7-1-1.8-1.5-3.4-1.5-1.8 0-3.1.9-3.1 2.3 0 3.7 6.5 1.5 6.5 5.2 0 1.5-1.3 2.5-3.4 2.5-1.6 0-2.9-.6-3.7-1.7M12 5v14"/></>,
  benchmark: <><path d="M5 20V9M12 20V4M19 20v-7"/><path d="M3 20h18"/></>,
  logs: <><path d="M6 3h12v18H6z"/><path d="M9 8h6M9 12h6M9 16h4"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21h-4v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3.1 14H3v-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.5V3h4v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.1v4h-.1a1.7 1.7 0 0 0-1.5 1Z"/></>,
  refresh: <><path d="M20 6v5h-5"/><path d="M18.2 16.5A8 8 0 1 1 19.5 9"/></>,
  play: <path d="m9 7 8 5-8 5z"/>,
  spark: <><path d="m12 2 1.5 5.2L19 9l-5.5 1.8L12 16l-1.5-5.2L5 9l5.5-1.8z"/><path d="m19 15 .7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7z"/></>,
  shield: <><path d="M12 3 20 6v5c0 5-3.4 8.3-8 10-4.6-1.7-8-5-8-10V6z"/><path d="m8.5 12 2.3 2.3 4.7-5"/></>,
  brain: <><path d="M9.5 4.5A3 3 0 0 0 4 6.2a3 3 0 0 0 .5 5.8A3 3 0 0 0 8 16.8V20"/><path d="M14.5 4.5A3 3 0 0 1 20 6.2a3 3 0 0 1-.5 5.8 3 3 0 0 1-3.5 4.8V20M12 4v16M8 9h4M12 14h4"/></>,
  agents: <><circle cx="8" cy="8" r="3"/><circle cx="17" cy="7" r="2.5"/><path d="M2.5 20c.4-4.3 2.4-6.5 5.5-6.5s5.1 2.2 5.5 6.5M14 13c4.1-.7 6.6 1.5 7 5"/><path d="M12 4v3M10.5 5.5h3"/></>,
  automation: <><path d="M7 4h10l3 5-3 5H7L4 9z"/><path d="m9 14-2 6h10l-2-6M9 9h6"/></>,
  plugins: <><path d="M8 3v4H5a2 2 0 0 0-2 2v3h4a3 3 0 1 1 0 6H3v3h18v-4h-3a3 3 0 1 1 0-6h3V7h-4V3z"/></>,
  models: <><path d="m12 3 8 4.5-8 4.5-8-4.5zM4 12l8 4.5 8-4.5M4 16.5l8 4.5 8-4.5"/></>,
  folder: <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.2l1.8 2.5h9A1.5 1.5 0 0 1 21 9v9.5A1.5 1.5 0 0 1 19.5 20h-15A1.5 1.5 0 0 1 3 18.5z"/>,
  folderOpen: <><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.2l1.8 2.5h9A1.5 1.5 0 0 1 21 9v1H6.4a1.5 1.5 0 0 0-1.45 1.1L3 18.5z"/><path d="m3 18.5 1.95-7.4A1.5 1.5 0 0 1 6.4 10H21l-1.95 7.4A1.5 1.5 0 0 1 17.6 18.5H4.5A1.5 1.5 0 0 1 3 18.5z"/></>,
  chat: <path d="M20 12a7.5 7.5 0 0 1-7.5 7.5H9l-4.5 3v-4.2A7.5 7.5 0 0 1 12 4.5h.5A7.5 7.5 0 0 1 20 12z"/>,
  // Model settings surface: provider rows, per-model actions and the
  // write-only API key field. These carry the operator's actions that used to
  // be text buttons, so each needs an accessible label at the call site.
  box: <><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/></>,
  pencil: <><path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/><path d="m15 5 4 4"/></>,
  trash: <><path d="M4 7h16M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13"/><path d="M10 11v5M14 11v5"/></>,
  plug: <><path d="M9 3v6M15 3v6"/><path d="M6.5 9h11v3a5.5 5.5 0 0 1-11 0z"/><path d="M12 17.5V21"/></>,
  eye: <><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/></>,
  eyeOff: <><path d="M4 4l16 16"/><path d="M9.9 5.9A9.9 9.9 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a17.6 17.6 0 0 1-3 3.9M6.1 8.3A16.9 16.9 0 0 0 2.5 12S6 18.5 12 18.5a9.4 9.4 0 0 0 4-.9"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></>,
  kebab: <><circle cx="12" cy="5.5" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="12" cy="18.5" r="1.3"/></>,
  plus: <><path d="M12 5v14M5 12h14"/></>,
  slash: <><path d="m7 20 10-16"/><path d="M5 12h.01M19 12h.01"/></>,
  at: <><circle cx="12" cy="12" r="4"/><path d="M16 12v1.5a2.5 2.5 0 0 0 5 0V12a9 9 0 1 0-3.5 7.1"/></>,
  // Response actions: the four everyday actions are icons, the rest live
  // behind the "more options" menu.
  copy: <><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4.5A1.5 1.5 0 0 1 3 13.5v-9A1.5 1.5 0 0 1 4.5 3h9A1.5 1.5 0 0 1 15 4.5V5"/></>,
  thumbUp: <><path d="M7 21V10l4-7a2 2 0 0 1 2 2v4h5.2a2 2 0 0 1 2 2.4l-1.4 7A2 2 0 0 1 16.8 20H7z"/><path d="M7 10H4.5A1.5 1.5 0 0 0 3 11.5v8A1.5 1.5 0 0 0 4.5 21H7"/></>,
  thumbDown: <><path d="M7 3v11l4 7a2 2 0 0 0 2-2v-4h5.2a2 2 0 0 0 2-2.4l-1.4-7A2 2 0 0 0 16.8 4H7z"/><path d="M7 14H4.5A1.5 1.5 0 0 1 3 12.5v-8A1.5 1.5 0 0 1 4.5 3H7"/></>,
  // Round 24 ZCode-style panel workspaces: Files/Browser/Terminal/Sites.
  terminal: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M12.5 15H17"/></>,
  globe: <><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14.5 14.5 0 0 1 0 18M12 3a14.5 14.5 0 0 0 0 18"/></>,
  layout: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11"/></>,
  close: <path d="M6 6l12 12M18 6 6 18"/>,
  chevron: <path d="m9 6 6 6-6 6"/>,
  chevronDown: <path d="m6 9 6 6 6-6"/>,
  expand: <><path d="M15 4h5v5"/><path d="M9 20H4v-5"/><path d="M20 15v5h-5M4 9V4h5"/></>,
  file: <><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/></>,
};

export function Icon({ name, ...props }: { name: IconName } & SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      {paths[name]}
    </svg>
  );
}
