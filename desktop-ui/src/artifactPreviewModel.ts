/**
 * Artifact preview planning.
 *
 * The artifact store keeps ONE canonical source per type (see
 * `src/amca/artifacts.py::CANONICAL_SOURCE_EXT`).  For binary-first types the
 * canonical source is not what a reader wants to look at — a spreadsheet's
 * source is CSV, and a document's is markdown — so showing the raw text is
 * technically faithful and practically useless.
 *
 * This module decides how to present a source, and — importantly — says so
 * honestly when no meaningful preview is possible instead of pretending.
 */

export type ArtifactPreviewKind =
  | "markdown"
  | "table"
  | "json"
  | "html"
  | "slides"
  | "image"
  | "source"
  | "unavailable";

export interface ArtifactPreviewPlan {
  kind: ArtifactPreviewKind;
  /** Human label for the preview pane heading. */
  label: string;
  /** Truthful note shown above the preview. Never a promise we cannot keep. */
  note?: string;
  /** Parsed rows for `kind === "table"`. */
  table?: { headers: string[]; rows: string[][] };
  /** Pretty-printed text for `kind === "json"`. */
  json?: string;
  /** One entry per slide for `kind === "slides"`. */
  slides?: { title: string; lines: string[] }[];
  /** Data URL for `kind === "image"`. */
  imageUrl?: string;
  /** Text to hand to the markdown renderer for `kind === "markdown"`/`"source"`. */
  source?: string;
}

/** Types whose canonical source is already the readable form. */
const MARKDOWN_TYPES = new Set(["markdown", "md", "text", "txt", "docx"]);
const TABLE_TYPES = new Set(["xlsx", "csv", "tsv"]);
const SOURCE_TYPES = new Set(["tex", "latex", "bib", "ini", "yaml", "yml", "xml", "sh", "bash"]);

/** Split one delimited line, honouring double-quoted fields. */
function splitDelimited(line: string, delimiter: string): string[] {
  const cells: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (quoted) {
      if (char === '"') {
        if (line[index + 1] === '"') {
          cell += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        cell += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === delimiter) {
      cells.push(cell);
      cell = "";
    } else {
      cell += char;
    }
  }
  cells.push(cell);
  return cells;
}

/** Parse delimited text into a header row plus body rows. */
export function parseDelimited(text: string, delimiter = ","): { headers: string[]; rows: string[][] } | null {
  const lines = text.replace(/\r\n?/g, "\n").split("\n").filter((line) => line.trim().length > 0);
  if (!lines.length) return null;
  const headers = splitDelimited(lines[0], delimiter);
  if (headers.length < 2) return null;
  const rows = lines.slice(1).map((line) => splitDelimited(line, delimiter));
  return { headers, rows };
}

/** Detect the delimiter a delimited source actually uses. */
function detectDelimiter(text: string, artifactType: string): string {
  if (artifactType === "tsv") return "\t";
  const firstLine = text.replace(/\r\n?/g, "\n").split("\n").find((line) => line.trim().length > 0) ?? "";
  if (firstLine.includes("\t") && !firstLine.includes(",")) return "\t";
  return ",";
}

function tryParseJson(text: string): unknown | undefined {
  const trimmed = text.trim();
  if (!trimmed || (!trimmed.startsWith("{") && !trimmed.startsWith("["))) return undefined;
  try {
    return JSON.parse(trimmed);
  } catch {
    return undefined;
  }
}

/** Turn a parsed presentation payload into per-slide summaries. */
function slidesFrom(value: unknown): { title: string; lines: string[] }[] | undefined {
  const raw = Array.isArray(value)
    ? value
    : value && typeof value === "object" && Array.isArray((value as { slides?: unknown }).slides)
      ? ((value as { slides: unknown[] }).slides)
      : undefined;
  if (!raw || !raw.length) return undefined;
  return raw.map((entry, index) => {
    if (typeof entry === "string") return { title: `Slide ${index + 1}`, lines: entry.split("\n") };
    const record = (entry ?? {}) as Record<string, unknown>;
    const title = String(record.title ?? record.heading ?? `Slide ${index + 1}`);
    const body = record.body ?? record.bullets ?? record.content ?? record.lines ?? "";
    const lines = Array.isArray(body) ? body.map((item) => String(item)) : String(body).split("\n");
    return { title, lines: lines.filter((line) => line.length > 0) };
  });
}

/**
 * Decide how to present an artifact's canonical source.
 *
 * Never claims a preview exists when it does not: `unavailable` carries a note
 * that names the real reason, so the UI can show it instead of raw text.
 */
export function planArtifactPreview(artifactType: string, content: string): ArtifactPreviewPlan {
  const type = String(artifactType || "").toLowerCase();
  const text = String(content ?? "");

  if (MARKDOWN_TYPES.has(type)) {
    return {
      kind: "markdown",
      label: type === "docx" ? "Document preview" : "Preview",
      note: type === "docx" ? "Shown as the document's markdown source; the DOCX export renders this." : undefined,
      source: text,
    };
  }

  if (TABLE_TYPES.has(type)) {
    const table = parseDelimited(text, detectDelimiter(text, type));
    if (table) {
      return {
        kind: "table",
        label: "Spreadsheet preview",
        note: `${table.rows.length} row${table.rows.length === 1 ? "" : "s"} · ${table.headers.length} columns`,
        table,
      };
    }
    return {
      kind: "source",
      label: "Spreadsheet source",
      note: "The stored source is not a delimited table, so it is shown as text.",
      source: text,
    };
  }

  if (type === "html") {
    return { kind: "html", label: "Rendered preview", note: "Scripts and remote loads are disabled in this preview.", source: text };
  }

  if (type === "image") {
    if (text.trim().startsWith("data:image/")) {
      return { kind: "image", label: "Image preview", imageUrl: text.trim() };
    }
    return { kind: "source", label: "Image source", note: "This image is stored as a reference, not embedded data.", source: text };
  }

  if (type === "pptx") {
    const slides = slidesFrom(tryParseJson(text));
    if (slides) {
      return { kind: "slides", label: "Slide preview", note: `${slides.length} slide${slides.length === 1 ? "" : "s"}`, slides };
    }
    return { kind: "source", label: "Presentation source", note: "No slide structure was recorded, so the source is shown as text.", source: text };
  }

  if (type === "pdf") {
    return {
      kind: "unavailable",
      label: "Preview unavailable",
      note: "No PDF renderer is installed, so a page preview cannot be produced. The stored source is shown below; the PDF export reports when no converter is available.",
      source: text,
    };
  }

  if (type === "zip") {
    return { kind: "unavailable", label: "Preview unavailable", note: "Archive contents cannot be previewed. Download the archive to inspect it." };
  }

  if (SOURCE_TYPES.has(type)) {
    return { kind: "source", label: "Source", source: text };
  }

  if (type === "json") {
    const parsed = tryParseJson(text);
    if (parsed !== undefined) {
      return { kind: "json", label: "JSON preview", note: "Formatted for reading. The stored source is unchanged.", json: JSON.stringify(parsed, null, 2) };
    }
    return { kind: "source", label: "Source", note: "This does not parse as JSON, so it is shown as text.", source: text };
  }

  return { kind: "source", label: "Source", source: text };
}

/** A short, honest caption for an artifact card. */
export function artifactTypeLabel(artifactType: string): string {
  const type = String(artifactType || "").toLowerCase();
  const labels: Record<string, string> = {
    markdown: "Markdown",
    docx: "Word document",
    xlsx: "Spreadsheet",
    csv: "Spreadsheet",
    pptx: "Presentation",
    pdf: "PDF",
    tex: "LaTeX",
    bib: "Bibliography",
    html: "HTML page",
    json: "JSON",
    image: "Image",
    zip: "Archive",
  };
  return labels[type] ?? (type || "File");
}

/**
 * The extension the canonical source is actually stored under, so "Save source"
 * writes a file the user can open rather than a misleading `.md`.
 */
export function canonicalSourceExtension(artifactType: string): string {
  const type = String(artifactType || "").toLowerCase();
  const extensions: Record<string, string> = {
    markdown: "md",
    docx: "md",
    xlsx: "csv",
    csv: "csv",
    tsv: "tsv",
    tex: "tex",
    bib: "bib",
    html: "html",
    json: "json",
    pptx: "json",
    pdf: "json",
  };
  return extensions[type] ?? "txt";
}
