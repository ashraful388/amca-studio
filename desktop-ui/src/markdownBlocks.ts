/**
 * Model-authored expandable sections.
 *
 * A model can ask for a collapsible section with a fenced block:
 *
 *   ```details title="Run details"
 *   ...markdown...
 *   ```
 *
 * The body is rendered through the normal markdown renderer, so a model can
 * never inject executable HTML or script — it can only supply markdown.  A
 * fenced block that does not open with a title directive is treated as an
 * ordinary code block, so nothing is reinterpreted by accident.
 */

export interface DetailsBlock {
  title: string;
  body: string;
}

/** Longest title accepted, so a pathological line cannot dominate the UI. */
export const MAX_DETAILS_TITLE = 200;

/** Languages that request a collapsible section. */
const DETAILS_LANGUAGES = new Set(["details", "collapsible", "fold"]);

export function isDetailsLanguage(language: string): boolean {
  return DETAILS_LANGUAGES.has(String(language || "").trim().toLowerCase());
}

/**
 * Parse a details fence body into a title and a markdown body.
 *
 * Returns `null` when the block does not declare a title, so the caller can
 * fall back to rendering it as a code block rather than guessing.
 */
export function parseDetailsBlock(text: string): DetailsBlock | null {
  const raw = String(text ?? "").replace(/\r\n?/g, "\n");
  const lines = raw.split("\n");
  if (!lines.length) return null;

  const header = lines[0].trim();
  const match = /^(?:title|summary)\s*=\s*(?:"([^"]*)"|'([^']*)'|(.+?))\s*$/.exec(header);
  if (!match) return null;

  const title = (match[1] ?? match[2] ?? match[3] ?? "").trim().slice(0, MAX_DETAILS_TITLE);
  const body = lines.slice(1).join("\n").replace(/^\n+/, "").replace(/\s+$/, "");
  return { title: title || "Details", body };
}
