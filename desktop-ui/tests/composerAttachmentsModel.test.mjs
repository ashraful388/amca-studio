// Composer attachment rules (pure model, no browser, no backend).
// Run with: node --test tests/composerAttachmentsModel.test.mjs
//
// The owner reported that the composer's '+' option is "not well organized
// and not functional", and that a file should be pasteable straight into
// the composer.  A pasted file, a dropped file and a picked file must
// therefore obey the SAME rules, or the same file behaves differently
// depending on how it arrived.  These assertions pin that shared rule set.
import test from "node:test";
import assert from "node:assert/strict";
import {
  addPendingAttachments,
  ENTRY_READ_TIMEOUT_MS,
  filesFromDataTransfer,
  filesFromEntry,
  isAcceptedAttachment,
  MAX_ATTACHMENT_BYTES,
  MAX_CHAT_ATTACHMENTS,
  pasteCarriesFiles,
  pasteCarriesText,
  readEntryBatch,
  readEntryFile,
  removePendingAttachment,
  safeDroppedName,
} from "../src/composerAttachmentsModel.ts";

/** A stand-in for a browser File: the model only reads these fields. */
function file(name, size = 10, type = "text/plain", lastModified = 1000) {
  return { name, size, type, lastModified };
}

test("a file added to an empty set is kept", () => {
  const outcome = addPendingAttachments([], [file("notes.txt")]);
  assert.equal(outcome.files.length, 1);
  assert.equal(outcome.files[0].name, "notes.txt");
  assert.equal(outcome.error, null);
});

test("the same file name is refused, case-insensitively", () => {
  const outcome = addPendingAttachments(
    [file("Notes.TXT")],
    [file("notes.txt")],
  );
  assert.equal(outcome.files.length, 1, "a duplicate name must not be added");
  assert.equal(outcome.rejected.length, 1);
  assert.match(outcome.rejected[0].reason, /already attached/);
  assert.match(outcome.error, /could not be attached/);
});

test("a file over the per-file ceiling is refused with a reason", () => {
  const outcome = addPendingAttachments(
    [],
    [file("huge.bin", MAX_ATTACHMENT_BYTES + 1)],
  );
  assert.equal(outcome.files.length, 0);
  assert.equal(outcome.rejected.length, 1);
  assert.match(outcome.rejected[0].reason, /exceeds 25 MB/);
});

test("too many files are refused, and the existing set is left alone", () => {
  const many = Array.from({ length: MAX_CHAT_ATTACHMENTS + 1 }, (_, index) =>
    file(`file-${index}.txt`),
  );
  const outcome = addPendingAttachments([], many);
  assert.equal(outcome.files.length, 0);
  assert.match(outcome.error, /at most 64 files/);
});

test("the combined size ceiling is enforced", () => {
  // Both files must be of an ACCEPTED type, or the type rule would
  // refuse them first and this ceiling would never be reached.
  const half = Math.floor(MAX_ATTACHMENT_BYTES / 2) + 1;
  const outcome = addPendingAttachments(
    [],
    [file("a.txt", half), file("b.txt", half)],
  );
  assert.equal(outcome.files.length, 0, "the set must not be replaced");
  assert.match(outcome.error, /must not exceed 25 MB/);
});

test("an unsupported file type is refused in the composer", () => {
  // The picker's `accept` attribute already refused a .zip; a pasted or
  // dropped file bypasses that attribute, so the same allow-list has to be
  // applied here or the file would only fail later, at send time.
  const outcome = addPendingAttachments([], [file("archive.zip")]);
  assert.equal(outcome.files.length, 0);
  assert.match(outcome.rejected[0].reason, /file type not supported/);
});

test("the accepted types are the ones the picker offers", () => {
  assert.equal(isAcceptedAttachment("notes.txt"), true);
  assert.equal(isAcceptedAttachment("REPORT.PDF"), true, "the check is case-insensitive");
  assert.equal(isAcceptedAttachment("archive.zip"), false);
  assert.equal(isAcceptedAttachment("no-extension"), false);
});

test("removing one file leaves the others alone", () => {
  const first = file("a.txt", 10, "text/plain", 1000);
  const second = file("b.txt", 20, "text/plain", 2000);
  const remaining = removePendingAttachment([first, second], first);
  assert.equal(remaining.length, 1);
  assert.equal(remaining[0].name, "b.txt");
});

test("removing a file that is not in the set changes nothing", () => {
  const present = file("a.txt");
  const absent = file("a.txt", 99, "text/plain", 999);
  const remaining = removePendingAttachment([present], absent);
  assert.equal(remaining.length, 1, "a different file must not be removed");
});

test("a paste that carries text is NOT treated as a file paste", () => {
  // A plain text paste must reach the composer textarea untouched; only a
  // paste that actually carries FILES is intercepted.
  assert.equal(
    pasteCarriesFiles({
      files: [],
      items: [{ kind: "string", type: "text/plain" }],
    }),
    false,
  );
  assert.equal(pasteCarriesFiles(null), false);
});

test("a paste that carries a file IS treated as a file paste", () => {
  assert.equal(
    pasteCarriesFiles({
      files: [file("a.txt")],
      items: [{ kind: "file" }],
    }),
    true,
  );
  assert.equal(
    pasteCarriesFiles({
      files: [],
      items: [{ kind: "file" }],
    }),
    true,
  );
});

test("a MIXED paste carries both files and text", () => {
  // Copying a spreadsheet range also brings its logo image.  Such a paste
  // must still put the text in the composer, so the handler only
  // suppresses the browser's own paste when the transfer is FILES ONLY.
  const mixed = {
    files: [file("logo.png")],
    items: [
      { kind: "string", type: "text/plain" },
      { kind: "file" },
    ],
  };
  assert.equal(pasteCarriesFiles(mixed), true, "the file must be attached");
  assert.equal(pasteCarriesText(mixed), true, "the text must not be lost");
});

test("a files-only paste carries no text", () => {
  const onlyFiles = {
    files: [file("logo.png")],
    items: [{ kind: "file" }],
  };
  assert.equal(pasteCarriesFiles(onlyFiles), true);
  assert.equal(pasteCarriesText(onlyFiles), false);
});

test("filesFromDataTransfer returns the transfer's own files", async () => {
  // The request encoder calls file.arrayBuffer() on what it is given, so
  // the REAL File objects must survive - not copies of their fields.
  const real = [file("a.txt"), file("b.pdf")];
  const files = await filesFromDataTransfer({ files: real, items: [] });
  assert.equal(files.length, 2);
  assert.equal(files[0], real[0], "the caller's own File must be returned");
  assert.equal(files[1], real[1]);
});

test("filesFromDataTransfer walks a FOLDER entry with the caller's reader", async () => {
  // A dropped folder must be walked by the caller's reader, so a pasted
  // folder keeps its path-bearing names.  A folder is the one thing the
  // entry API is needed for.
  const walked = [file("folder/inner.txt")];
  let sawEntry = false;
  const files = await filesFromDataTransfer(
    {
      files: [],
      items: [
        {
          kind: "file",
          webkitGetAsEntry: () => {
            sawEntry = true;
            return { isFile: false, isDirectory: true };
          },
        },
      ],
    },
    async () => walked,
  );
  assert.equal(sawEntry, true, "the entry reader must be used");
  assert.equal(files.length, 1);
  assert.equal(files[0], walked[0]);
});

test("a FILE entry is NOT walked: its file() callback never fires on a paste", async () => {
  // This is the bug the physical test caught.  On a real Explorer
  // file-paste, Chromium returns a non-null FILE entry from
  // webkitGetAsEntry(), and the entry API is a drag-and-drop API: the
  // `file()` callback of a pasted entry NEVER fires.  Awaiting it hung
  // the paste handler for ever, so the attachment never appeared.
  //
  // The file must therefore come from getAsFile() - which answers at
  // once - and the folder reader must not be asked to walk a file entry.
  const pasted = file("pasted-proof.txt", 22, "text/plain");
  let readerCalled = false;
  const files = await filesFromDataTransfer(
    {
      files: [],
      items: [
        {
          kind: "file",
          type: "text/plain",
          getAsFile: () => pasted,
          webkitGetAsEntry: () => ({ isFile: true, isDirectory: false }),
        },
      ],
    },
    async () => {
      readerCalled = true;
      throw new Error("a file entry must not be walked");
    },
  );
  assert.equal(readerCalled, false, "the folder reader must not see a file");
  assert.equal(files.length, 1, "the pasted file must be attached");
  assert.equal(files[0], pasted);
});

test("filesFromDataTransfer falls back to the flat file list", async () => {
  const real = [file("plain.txt")];
  const files = await filesFromDataTransfer(
    { files: real, items: [] },
    async () => {
      throw new Error("the reader must not be used when there are no entries");
    },
  );
  assert.equal(files.length, 1);
  assert.equal(files[0], real[0]);
});

test("a PASTE is read from items[].getAsFile(), because files is empty", async () => {
  // This is the shape a real Chromium paste has.  Copying a file in
  // Windows Explorer and pasting it into a page leaves `dataTransfer.files`
  // EMPTY and puts the file on the item instead, so reading only `files`
  // found nothing and the paste attached nothing.  `webkitGetAsEntry()` is
  // a drag-and-drop API and returns null for clipboard data.
  const pasted = file("pasted-proof.txt", 26, "text/plain");
  const files = await filesFromDataTransfer({
    files: [],
    items: [
      { kind: "file", type: "text/plain", getAsFile: () => pasted },
    ],
  });
  assert.equal(files.length, 1, "the pasted file must be found");
  assert.equal(files[0], pasted, "the real File must be returned");
});

test("a paste that also has a flat file list returns the file only once", async () => {
  // Some browsers populate BOTH for a paste.  The file must not be
  // attached twice, or the same name would be rejected as a duplicate.
  const pasted = file("same.txt", 10, "text/plain");
  const files = await filesFromDataTransfer({
    files: [pasted],
    items: [{ kind: "file", type: "text/plain", getAsFile: () => pasted }],
  });
  assert.equal(files.length, 1);
  assert.equal(files[0], pasted);
});

test("a paste of two different files returns both", async () => {
  const one = file("one.txt", 10, "text/plain");
  const two = file("two.pdf", 20, "application/pdf");
  const files = await filesFromDataTransfer({
    files: [],
    items: [
      { kind: "file", type: "text/plain", getAsFile: () => one },
      { kind: "file", type: "application/pdf", getAsFile: () => two },
    ],
  });
  assert.equal(files.length, 2);
  assert.equal(files[0], one);
  assert.equal(files[1], two);
});

test("a paste item whose getAsFile throws does not break the read", async () => {
  const good = file("good.txt", 10, "text/plain");
  const files = await filesFromDataTransfer({
    files: [],
    items: [
      {
        kind: "file",
        getAsFile: () => {
          throw new Error("no file for this item");
        },
      },
      { kind: "file", getAsFile: () => good },
    ],
  });
  assert.equal(files.length, 1);
  assert.equal(files[0], good);
});

// ---------------------------------------------------------------------------
// The entry API must never be able to hang the composer.
//
// The bug this whole change is about WAS a hang: the paste handler awaited
// an entry callback that never fires for clipboard data.  These assertions
// pin the property that fixes it - every wait on the entry API is bounded.
// ---------------------------------------------------------------------------

test("readEntryFile resolves null when the callback never fires", async () => {
  // This is the clipboard case: the callback is never invoked at all.
  const entry = { isFile: true, isDirectory: false, name: "x.txt", file: () => {} };
  const started = Date.now();
  const result = await readEntryFile(entry, 40);
  assert.equal(result, null, "the read must give up rather than hang");
  assert.ok(Date.now() - started < 2000, "it must not wait for ever");
});

test("readEntryFile resolves the file when the callback fires", async () => {
  const real = file("ok.txt");
  const entry = {
    isFile: true,
    isDirectory: false,
    name: "ok.txt",
    file: (success) => success(real),
  };
  assert.equal(await readEntryFile(entry, 1000), real);
});

test("readEntryFile resolves null when the callback reports an error", async () => {
  const entry = {
    isFile: true,
    isDirectory: false,
    name: "bad.txt",
    file: (_success, failure) => failure(new Error("denied")),
  };
  assert.equal(await readEntryFile(entry, 1000), null);
});

test("readEntryFile resolves null when the entry throws", async () => {
  const entry = {
    isFile: true,
    isDirectory: false,
    name: "boom.txt",
    file: () => { throw new Error("boom"); },
  };
  assert.equal(await readEntryFile(entry, 1000), null);
});

test("readEntryFile settles exactly once, even if the callback fires late", async () => {
  let calls = 0;
  const entry = {
    isFile: true,
    isDirectory: false,
    name: "late.txt",
    file: (success) => {
      calls += 1;
      setTimeout(() => success(file("late.txt")), 60);
    },
  };
  // The timeout fires first; the late callback must not change the answer
  // or resolve the promise a second time.
  const result = await readEntryFile(entry, 20);
  assert.equal(result, null);
  await new Promise((resolve) => setTimeout(resolve, 120));
  assert.equal(calls, 1);
});

test("readEntryBatch resolves empty when the folder read never answers", async () => {
  const reader = { readEntries: () => {} };
  assert.deepEqual(await readEntryBatch(reader, 40), []);
});

test("readEntryBatch resolves the children when the folder read answers", async () => {
  const children = [{ isFile: true, isDirectory: false, name: "a.txt" }];
  const reader = { readEntries: (success) => success(children) };
  assert.deepEqual(await readEntryBatch(reader, 1000), children);
});

test("filesFromEntry walks a folder and keeps the path in the name", async () => {
  const inner = file("inner.txt");
  // A real reader hands the children over and then returns an EMPTY batch
  // to signal the end of the folder.
  let served = false;
  const entry = {
    isFile: false,
    isDirectory: true,
    name: "folder",
    createReader: () => ({
      readEntries: (success) => {
        if (served) { success([]); return; }
        served = true;
        success([
          {
            isFile: true,
            isDirectory: false,
            name: "inner.txt",
            file: (ok) => ok(inner),
          },
        ]);
      },
    }),
  };
  const files = await filesFromEntry(entry, "", 1000);
  assert.equal(files.length, 1);
  assert.equal(files[0].name, "folder__inner.txt");
});

test("filesFromEntry stops on a reader that never returns an empty batch", async () => {
  // A reader that keeps handing back the same children would loop for
  // ever.  Each of those batches is fast, so the per-read timeout would
  // never fire - only a bound on the number of batches stops it.
  const entry = {
    isFile: false,
    isDirectory: true,
    name: "endless",
    createReader: () => ({
      readEntries: (success) =>
        success([
          {
            isFile: true,
            isDirectory: false,
            name: "same.txt",
            file: (ok) => ok(file("same.txt")),
          },
        ]),
    }),
  };
  const started = Date.now();
  const files = await filesFromEntry(entry, "", 1000);
  assert.ok(Date.now() - started < 10000, "an endless folder must not hang");
  assert.ok(files.length >= 1, "the children it did serve are still returned");
});

test("filesFromEntry does NOT hang on a folder whose read never answers", async () => {
  const entry = {
    isFile: false,
    isDirectory: true,
    name: "stuck",
    createReader: () => ({ readEntries: () => {} }),
  };
  const started = Date.now();
  const files = await filesFromEntry(entry, "", 40);
  assert.equal(files.length, 0);
  assert.ok(Date.now() - started < 2000, "a stuck folder must not hang");
});

test("safeDroppedName never leaves a path separator in the name", () => {
  const name = safeDroppedName("dir\\sub", "a/b.txt");
  assert.equal(name.includes("/"), false);
  assert.equal(name.includes("\\"), false);
  assert.equal(name, "dir__sub__a_b.txt");
});

test("the entry read timeout is a real, finite bound", () => {
  assert.equal(typeof ENTRY_READ_TIMEOUT_MS, "number");
  assert.ok(ENTRY_READ_TIMEOUT_MS > 0);
  assert.ok(ENTRY_READ_TIMEOUT_MS <= 10000, "the bound must be short");
});
