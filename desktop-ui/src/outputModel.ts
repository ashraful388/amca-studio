// v1.1.8 Phase 7: pure output helpers, kept out of the JSX module so they can
// be unit-tested.  These decide the filename a user actually receives, which
// the plan requires to be correct ("correct filename, no silent failure").

export interface SaveOutcome {
  ok: boolean;
  path: string;
  /** True when the native shell wrote the file; false for a browser download. */
  native: boolean;
  reason?: string;
}

/**
 * Save a file, and say truthfully whether it was saved.
 *
 * The desktop host cancels a Blob download, so a failed native save inside the
 * shell is a real failure - it must be reported, not silently retried through
 * a path that cannot work.  The browser fallback is used only when the native
 * shell is genuinely absent (the import itself fails).
 */
/**
 * Save a file, and say truthfully whether it was saved.
 *
 * The desktop host cancels a Blob download, so a failed native save inside the
 * shell is a real failure - it must be reported, not silently retried through a
 * path that cannot work.  The browser fallback is used only when the native
 * shell is genuinely absent (the import itself fails).
 */
export async function saveFile(name: string, content: Blob | string): Promise<SaveOutcome> {
  const text = typeof content === "string" ? content : await content.text();
  type InvokeFn = (command: string, args: Record<string, unknown>) => Promise<unknown>;
  let invoke: InvokeFn | null = null;
  try {
    const api = await import("@tauri-apps/api/core");
    invoke = api.invoke as unknown as InvokeFn;
  } catch {
    invoke = null;
  }
  if (invoke) {
    try {
      const written = await invoke("save_export", { filename: name, contents: text });
      const path = typeof written === "string" && written ? written : name;
      return { ok: true, path, native: true };
    } catch (caught) {
      return {
        ok: false,
        path: name,
        native: true,
        reason: caught instanceof Error ? caught.message : String(caught),
      };
    }
  }
  try {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return { ok: true, path: name, native: false };
  } catch (caught) {
    return {
      ok: false,
      path: name,
      native: false,
      reason: caught instanceof Error ? caught.message : String(caught),
    };
  }
}

/** A filesystem-safe filename for an artifact export. */
export function artifactFileName(title: string, artifactType: string, format: string): string {
  const base = String(title || artifactType || "artifact")
    // The class must contain a literal backslash as well as the Windows-hostile
    // characters, or a path separator survives into the filename.
    .replace(/[<>:"/\\|?*]/g, "_")
    .replace(/\.[A-Za-z0-9]{1,5}$/, "")
    .trim();
  return `${base || "artifact"}.${format}`;
}

export function codeFileExtension(language: string): string {
  const key = String(language || "").trim().toLowerCase();
  const known: Record<string, string> = {
    python: "py", py: "py",
    javascript: "js", js: "js", jsx: "jsx",
    typescript: "ts", ts: "ts", tsx: "tsx",
    markdown: "md", md: "md",
    shell: "sh", bash: "sh", sh: "sh", zsh: "sh",
    powershell: "ps1", ps1: "ps1",
    yaml: "yml", yml: "yml",
    latex: "tex", tex: "tex",
    "c++": "cpp", cpp: "cpp", csharp: "cs", "c#": "cs",
    ruby: "rb", rust: "rs", golang: "go", go: "go",
    json: "json", csv: "csv", html: "html", css: "css", sql: "sql",
  };
  return known[key] ?? (key || "txt");
}
