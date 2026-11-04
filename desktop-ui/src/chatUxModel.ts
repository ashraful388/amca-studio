// Gate-2 follow-up (manager decisions 2b/3b/7): the composer Plus menu, the
// effort control, and the PDF-preview classification are part of the
// reviewed UX contract. These constants are the single source of truth so
// deterministic tests can assert the exact entries without automating
// native popups (which UIA cannot see and screen capture cannot reach).

// R6 operator decision: /, @, $ and Attachments all live INSIDE the +
// menu — one entry point on the toolbar, nothing duplicated beside it.
// Each entry runs its own working handler; the menu does not just insert
// tokens.
//
// v1.1.15: the Attachments row used the SAME 'plus' glyph as the button
// that opens this menu, so the entry that adds a file looked like the
// control that opened the list.  It now uses the 'files' glyph every other
// attachment surface in the app already uses.
export const COMPOSER_MENU_ACTIONS: ReadonlyArray<{
  id: string;
  icon: "files" | "slash" | "at" | "brain";
  label: string;
  description: string;
}> = [
  { id: "attach", icon: "files", label: "Attachments", description: "Add files to this message" },
  { id: "commands", icon: "slash", label: "Commands (/)", description: "Search chats, projects and views" },
  { id: "context", icon: "at", label: "Context (@)", description: "Attach project knowledge" },
  { id: "skills", icon: "brain", label: "Skills ($)", description: "Open saved skills" },
];

// R6: the Effort selector is real — it scales this run's bounded
// frontier-call ceiling (low = the v1.1.10 baseline, high x2, max x3). It
// changes how long a run may keep working, never what it may touch.
export const EFFORT_LEVELS: ReadonlyArray<"low" | "high" | "max"> = ["low", "high", "max"];
export const DEFAULT_EFFORT: "low" | "high" | "max" = "low";
export const EFFORT_LABELS: Record<"low" | "high" | "max", string> = {
  low: "Low effort",
  high: "High effort",
  max: "Max effort",
};
export function effortLabel(effort: string): string {
  return EFFORT_LABELS[effort as "low" | "high" | "max"] ?? EFFORT_LABELS.low;
}

// Manager decision (PDF preview): classified unavailable for v1.1.8. The
// viewer states this explicitly and keeps Open/Download intact; it never
// renders an incoherent binary dump.
export const PDF_PREVIEW_UNAVAILABLE =
  "Inline PDF preview is unavailable in this build. Download the PDF to view it — the file content is unchanged.";

// Manager decision 5c: relative timestamps ("4 seconds ago" -> "1 minute
// ago") must keep ticking. The App re-renders on this interval; 30 s is
// fine-grained enough to cross a displayed boundary within one tick while
// costing nothing.
export const CLOCK_TICK_MS = 30000;
