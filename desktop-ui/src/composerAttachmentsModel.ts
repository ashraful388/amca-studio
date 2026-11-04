// The composer's attachment rules, as PURE logic.
//
// The owner reported that the '+' option in the composer is "not well
// organized and not functional", and that a file should be pasteable
// straight into the composer.  The picker, the drag-and-drop path and the
// paste path must therefore obey IDENTICAL rules, or the same file behaves
// differently depending on how it arrived.
//
// This module holds that shared behaviour so it can be tested with
// `node --test` (the project's established pattern for composer logic: a
// pure .ts file with no browser and no fetch, imported straight from the
// .mjs test).  It also holds the reading of the browser's file-system
// entry API, because that is where the "never hang" property lives and it
// has to be testable to be trustworthy.

/** The per-file ceiling, in bytes. */
export const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;
/** The ceiling on the number of files attached to one message. */
export const MAX_CHAT_ATTACHMENTS = 64;

/**
 * The file types the composer accepts.
 *
 * This is the list the file PICKER was already filtered by.  A pasted or
 * dropped file bypasses the picker's own filter, so the same allow-list
 * has to be applied here as well - otherwise a .zip accepted by paste
 * would only fail later, at send time.
 */
export const CHAT_ATTACHMENT_ACCEPT: ReadonlyArray<string> = [
  ".txt", ".md", ".markdown", ".pdf", ".docx", ".xlsx", ".pptx", ".html", ".htm",
  ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff",
  ".wav", ".flac", ".mp3", ".ogg", ".oga", ".m4a",
  ".mp4", ".m4v", ".mov", ".avi", ".webm", ".mkv", ".ogv",
  ".csv", ".tsv", ".json", ".xml", ".rtf", ".tex", ".svg",
  ".py", ".js", ".jsx", ".ts", ".tsx",
  ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php",
  ".sql", ".yaml", ".yml", ".toml", ".ini", ".sh", ".ps1", ".bat",
];

/** The `accept` attribute for the composer's file input. */
export const CHAT_ATTACHMENT_ACCEPT_ATTRIBUTE = CHAT_ATTACHMENT_ACCEPT.join(",");

/**
 * The shape the composer needs from a file.
 *
 * The browser's `File` satisfies it, and so does any object with the same
 * fields, so this module can be tested without a DOM.
 */
export interface ComposerAttachment {
  name: string;
  size: number;
  type: string;
  lastModified: number;
}

/** The outcome of adding files to the pending set. */
export interface AddAttachmentsResult<T> {
  /** The new pending set, in the order the files were added. */
  files: T[];
  /** Files that were refused, with the reason. */
  rejected: Array<{ name: string; reason: string }>;
  /** A message to show the operator, or null when nothing went wrong. */
  error: string | null;
}

function isSameName(left: string, right: string): boolean {
  return left.toLocaleLowerCase() === right.toLocaleLowerCase();
}

/** The extension of a file name, lower-cased, or "" when it has none. */
export function fileExtension(name: string): string {
  const base = name.split(/[\\/]/).pop() ?? "";
  const dot = base.lastIndexOf(".");
  if (dot <= 0) return "";
  return base.slice(dot).toLocaleLowerCase();
}

/** Is this file type the composer accepts? */
export function isAcceptedAttachment(name: string): boolean {
  const extension = fileExtension(name);
  if (!extension) return false;
  return CHAT_ATTACHMENT_ACCEPT.includes(extension);
}

/**
 * Add files to the pending set, applying every limit.
 *
 * The rules are the ones the desktop already used for the picker, kept
 * exactly so that a pasted file is treated the same as a picked one:
 * the per-file ceiling, the ceiling on the count, the ceiling on the
 * combined size, a case-insensitive de-duplication by name, and the same
 * type allow-list the picker's `accept` attribute already enforced.
 *
 * The file type is carried through unchanged, so the real browser `File`
 * objects (which the request encoder needs for their bytes) survive.
 */
export function addPendingAttachments<T extends ComposerAttachment>(
  current: ReadonlyArray<T>,
  incoming: ReadonlyArray<T>,
): AddAttachmentsResult<T> {
  const files = [...current];
  const rejected: Array<{ name: string; reason: string }> = [];

  for (const file of incoming) {
    if (file.size > MAX_ATTACHMENT_BYTES) {
      rejected.push({ name: file.name, reason: "exceeds 25 MB" });
      continue;
    }
    if (!isAcceptedAttachment(file.name)) {
      rejected.push({ name: file.name, reason: "file type not supported" });
      continue;
    }
    if (files.some((item) => isSameName(item.name, file.name))) {
      rejected.push({ name: file.name, reason: "already attached" });
      continue;
    }
    files.push(file);
  }

  if (files.length > MAX_CHAT_ATTACHMENTS) {
    return {
      files: current.slice(),
      rejected,
      error: `Attach at most ${MAX_CHAT_ATTACHMENTS} files to one message.`,
    };
  }
  const total = files.reduce((sum, file) => sum + file.size, 0);
  if (total > MAX_ATTACHMENT_BYTES) {
    return {
      files: current.slice(),
      rejected,
      error: "Combined attachments must not exceed 25 MB.",
    };
  }
  if (rejected.length > 0) {
    return {
      files,
      rejected,
      error: `Some file(s) could not be attached: ${rejected
        .map((item) => `${item.name} (${item.reason})`)
        .join(", ")}`,
    };
  }
  return { files, rejected, error: null };
}

/** Remove one file from the pending set. */
export function removePendingAttachment<T extends ComposerAttachment>(
  current: ReadonlyArray<T>,
  target: T,
): T[] {
  return current.filter(
    (item) =>
      !(
        item.name === target.name &&
        item.size === target.size &&
        item.lastModified === target.lastModified
      ),
  );
}

/**
 * Does this paste carry FILES (as opposed to text)?
 *
 * A text paste must reach the composer's textarea untouched, so only a
 * paste that actually carries FILES is intercepted.
 */
export function pasteCarriesFiles(transfer: DataTransfer | null): boolean {
  if (!transfer) return false;
  if ((transfer.files?.length ?? 0) > 0) return true;
  return Array.from(transfer.items ?? []).some((item) => item.kind === "file");
}

/**
 * Does this paste also carry TEXT?
 *
 * A rich paste can carry both: copying a cell range out of a spreadsheet
 * also brings the sheet's logo image.  Such a paste must still put the
 * text in the composer, so the handler only suppresses the browser's own
 * paste when the transfer is FILES ONLY.
 */
export function pasteCarriesText(transfer: DataTransfer | null): boolean {
  if (!transfer) return false;
  return Array.from(transfer.items ?? []).some((item) => item.kind === "string");
}

/**
 * The shape of the browser's file-system entry API that this module uses.
 *
 * It is declared here rather than imported from the DOM so the model can
 * be tested with `node --test`, with no browser and no DOM.
 */
export interface ChromiumFileEntry {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file?: (
    success: (file: File) => void,
    failure?: (error: DOMException) => void,
  ) => void;
  createReader?: () => {
    readEntries: (
      success: (entries: ChromiumFileEntry[]) => void,
      failure?: (error: DOMException) => void,
    ) => void;
  };
}

/**
 * How long an entry callback is given to answer, in milliseconds.
 *
 * The entry API is a drag-and-drop API.  Its callbacks fire for a real
 * drop, but the SAME callbacks on an entry that came from the CLIPBOARD
 * never fire at all.  Every wait on this API is therefore bounded, so a
 * callback that never comes costs a missed file rather than a composer
 * that hangs for ever.
 */
export const ENTRY_READ_TIMEOUT_MS = 2000;

/**
 * The most batches a folder is read in.
 *
 * Chromium hands a folder's children over in batches and signals the end
 * with an empty one.  A reader that never returns an empty batch would
 * loop for ever, so the number of batches is bounded as well.  No real
 * folder comes close: a batch holds up to 100 entries.
 */
export const MAX_ENTRY_BATCHES = 1000;

/**
 * The name a dropped file gets, with the folder path folded in.
 *
 * A dropped folder's children keep their place in the name (joined with
 * `__`) so two files with the same name in different folders do not
 * collide, and the characters Windows forbids in a file name are
 * replaced.  The result can never contain a path separator.
 */
export function safeDroppedName(path: string, filename: string): string {
  const segments = [...path.split(/[\\/]+/), filename].filter(Boolean);
  return segments.join("__").replace(/[<>:"/\\|?*]/g, "_");
}

/**
 * Read one file out of an entry, giving up if the callback never comes.
 *
 * Returns null instead of hanging, so one unreadable entry cannot hold up
 * the whole attachment.  The promise settles exactly once, and the timer
 * is always cleared, so nothing is left running after the answer.
 */
export function readEntryFile(
  entry: ChromiumFileEntry,
  timeoutMs: number = ENTRY_READ_TIMEOUT_MS,
): Promise<File | null> {
  if (!entry.file) return Promise.resolve(null);
  return new Promise<File | null>((resolve) => {
    let settled = false;
    const finish = (file: File | null): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(file);
    };
    const timer = setTimeout(() => finish(null), timeoutMs);
    try {
      entry.file!(
        (file) => finish(file),
        () => finish(null),
      );
    } catch {
      finish(null);
    }
  });
}

/**
 * Read one batch of a folder's children, giving up if it never comes.
 *
 * The same bound as `readEntryFile`, for the same reason: a folder's
 * `readEntries` never fires for clipboard data.
 */
export function readEntryBatch(
  reader: ReturnType<NonNullable<ChromiumFileEntry["createReader"]>>,
  timeoutMs: number = ENTRY_READ_TIMEOUT_MS,
): Promise<ChromiumFileEntry[]> {
  return new Promise<ChromiumFileEntry[]>((resolve) => {
    let settled = false;
    const finish = (entries: ChromiumFileEntry[]): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(entries);
    };
    const timer = setTimeout(() => finish([]), timeoutMs);
    try {
      reader.readEntries(
        (entries) => finish(entries ?? []),
        () => finish([]),
      );
    } catch {
      finish([]);
    }
  });
}

/**
 * Read every file out of one entry, walking a folder when it is one.
 *
 * A file entry answers through its `file()` callback; a folder entry is
 * walked with `readEntries`, and each child is read the same way.  Every
 * wait is bounded, so this can never hang the composer.
 *
 * The name of a file that came out of a folder keeps the folder path,
 * sanitised by `safeDroppedName`.
 */
export async function filesFromEntry(
  entry: ChromiumFileEntry,
  path = "",
  timeoutMs: number = ENTRY_READ_TIMEOUT_MS,
): Promise<File[]> {
  if (entry.isFile && entry.file) {
    const file = await readEntryFile(entry, timeoutMs);
    if (!file) return [];
    const safeName = safeDroppedName(path, file.name);
    return [
      safeName === file.name
        ? file
        : new File([file], safeName, {
            type: file.type,
            lastModified: file.lastModified,
          }),
    ];
  }
  if (!entry.isDirectory || !entry.createReader) return [];
  const reader = entry.createReader();
  const children: ChromiumFileEntry[] = [];
  // A folder is read in batches, and an empty batch ends it.  The count is
  // bounded as well, because a reader that keeps handing back a non-empty
  // batch would otherwise loop for ever - and each of those batches is
  // fast, so the per-read timeout would never fire.
  for (let batchNumber = 0; batchNumber < MAX_ENTRY_BATCHES; batchNumber += 1) {
    const batch = await readEntryBatch(reader, timeoutMs);
    if (!batch.length) break;
    children.push(...batch);
  }
  return (
    await Promise.all(
      children.map((child) =>
        filesFromEntry(child, `${path}${entry.name}/`, timeoutMs),
      ),
    )
  ).flat();
}

/**
 * Read the files out of a DataTransfer (a drop or a paste).
 *
 * A clipboard paste and a drag-and-drop carry the same DataTransfer, and
 * a Chromium probe of a real Explorer file-paste showed all three of the
 * usual channels filled in: `transfer.files` had the file,
 * `items[i].getAsFile()` returned it, AND `items[i].webkitGetAsEntry()`
 * returned a non-null FILE entry.
 *
 * That last one is the trap.  The entry API is a DRAG-AND-DROP API: a
 * dropped file's `entry.file(callback)` fires, but for a PASTE the same
 * callback never fires at all.  Awaiting it hangs the handler forever,
 * so the attachment silently never appears.  The folder walker is
 * therefore used ONLY for entries that are directories - a folder is the
 * one thing the entry API is actually needed for, because a folder has
 * no `getAsFile()` result and never appears in `transfer.files`.
 *
 * `readEntries` is the caller's own folder walker, because a dropped
 * folder must keep its path in the file name (sanitised by the caller).
 *
 * The file handles are taken SYNCHRONOUSLY, before the first `await`: a
 * browser neuters a DataTransfer once the event handler yields, so they
 * have to be read while the event is still being dispatched.
 */
export async function filesFromDataTransfer(
  transfer: DataTransfer | null,
  readEntries?: (entry: FileSystemEntry) => Promise<File[]>,
): Promise<File[]> {
  if (!transfer) return [];
  const items = Array.from(transfer.items ?? []).filter(
    (item) => item.kind === "file",
  );

  const seen = new Set<string>();
  const files: File[] = [];
  const add = (file: File | null | undefined): void => {
    if (!file) return;
    const key = `${file.name}|${file.size}|${file.lastModified}`;
    if (seen.has(key)) return;
    seen.add(key);
    files.push(file);
  };

  // A plain file, from a paste or a drop: `getAsFile()` answers at once.
  for (const item of items) {
    let file: File | null = null;
    try {
      file = item.getAsFile ? item.getAsFile() : null;
    } catch {
      file = null;
    }
    add(file);
  }
  // Some builds fill only the flat list.
  for (const file of Array.from(transfer.files ?? [])) add(file);

  if (readEntries) {
    // Only a FOLDER needs the entry API.  Its `file()` callback is never
    // used - a directory is walked by the caller's reader instead.
    const folders = items
      .map((item) =>
        item.webkitGetAsEntry ? item.webkitGetAsEntry() : null,
      )
      .filter(
        (entry): entry is FileSystemEntry =>
          entry !== null && entry.isDirectory === true,
      );
    if (folders.length > 0) {
      const collected = await Promise.all(
        folders.map((entry) => readEntries(entry)),
      );
      for (const file of collected.flat()) add(file);
    }
  }

  return files;
}
