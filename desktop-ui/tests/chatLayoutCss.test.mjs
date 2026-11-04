// Chat layout regression tests (source-level CSS assertions — no browser,
// no fetch, no backend). Run with: node --test tests/
//
// The legacy stylesheet caps the conversation column at max-width:980px and
// centres it with margin:0 auto. The studio shell override reset the other
// legacy properties (display, min-height, border, padding) but not the
// width cap, so the chat column sat in a 980px box with empty gutters while
// the rest of the window went unused. These assertions pin the override so
// the cap cannot silently come back.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const studioCss = readFileSync(new URL("../src/studio.css", import.meta.url), "utf8");
const legacyCss = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

test("the legacy chat shell really does carry a width cap", () => {
  // If this ever stops being true, the override below can be retired.
  assert.match(legacyCss, /\.chat-shell\s*\{[^}]*max-width:\s*980px/);
  assert.match(legacyCss, /\.chat-shell\s*\{[^}]*margin:\s*0\s+auto/);
});

test("the studio chat shell clears the legacy width cap and auto margins", () => {
  const rule = studioCss.match(/\.studio-shell \.chat-shell\s*\{[^}]*\}/);
  assert.ok(rule, "the studio chat shell rule must exist");
  assert.match(rule[0], /max-width:\s*none/);
  assert.match(rule[0], /margin:\s*0\s*;/);
});

test("the composer model row keeps its name, its checkbox and its action apart", () => {
  // The Verify action must not live inside the label, or clicking it would
  // toggle the checkbox.
  assert.match(appSource, /className="model-menu-text" htmlFor=\{inputId\}/);
  assert.match(appSource, /className="model-menu-verify"/);
  // The label must be associated with the input, so clicking the name
  // selects the model.
  assert.match(appSource, /<input id=\{inputId\} type="checkbox"/);
});

test("the composer row ids cannot collide", () => {
  // Folding a ref to "safe" characters maps "a/b" and "a_b" to the same
  // id, which would make the second row's label toggle the first row's
  // checkbox. The provider and model indexes are unique by construction.
  assert.doesNotMatch(
    appSource,
    /\.ref\.replace\(\/\[\^a-zA-Z0-9_-\]\/g/,
    "the row id must not be derived from a lossy character fold of the ref",
  );
  assert.match(
    appSource,
    /const inputId = `composer-model-\$\{groupIndex\}-\$\{entryIndex\}`/,
  );
});

// v1.1.14: the owner reported the composer's '+' option as "not well
// organized and not functional".  The cause was structural, not cosmetic:
// `.composer-plus-menu` had NO rule at all, so the menu was an in-flow
// static box, while the click-away overlay it renders (z-index 999) covered
// the whole window and was painted ABOVE it.  Every click on a menu entry
// therefore hit the overlay, which only closed the menu, so "Attachments"
// (and Commands, Context and Skills) could never fire and the file picker
// never opened.  These assertions pin the stacking relationship that
// decides whether the menu is usable at all.
test("the composer + menu is styled and painted above its click-away overlay", () => {
  const menuRule = studioCss.match(/\.composer-plus-menu\s*\{[^}]*\}/);
  assert.ok(
    menuRule,
    "the composer + menu must have a CSS rule; without one it is an " +
      "unstyled inline row of buttons",
  );
  assert.match(menuRule[0], /position:\s*fixed/, "the menu must be a popup");
  assert.match(menuRule[0], /z-index:\s*(\d+)/, "the menu must be stacked");

  const overlayRule = studioCss.match(/\.model-menu-overlay\s*\{[^}]*\}/);
  assert.ok(overlayRule, "the click-away overlay rule must exist");
  const overlayZ = Number(overlayRule[0].match(/z-index:\s*(\d+)/)[1]);
  const menuZ = Number(menuRule[0].match(/z-index:\s*(\d+)/)[1]);
  assert.ok(
    menuZ > overlayZ,
    `the + menu (z-index ${menuZ}) must paint above the click-away ` +
      `overlay (z-index ${overlayZ}), or every click closes the menu ` +
      "instead of running the entry",
  );
});

test("the composer + menu is anchored to its button and closes on Escape", () => {
  // A fixed-position menu needs the button's rectangle, and it must close
  // the way every other composer popup does.
  assert.match(
    appSource,
    /const updatePlusMenuPosition = useCallback/,
    "the + menu must compute its own position",
  );
  assert.match(
    appSource,
    /if \(event\.key === "Escape"\) \{ event\.stopPropagation\(\); setPlusMenuOpen\(false\); \}/,
    "Escape must close the + menu",
  );
  assert.match(
    appSource,
    /className="composer-plus-menu"[\s\S]{0,200}style=\{plusMenuPosition\}/,
    "the + menu must be positioned from the button's rectangle",
  );
});

test("the composer accepts a pasted file", () => {
  // The owner asked for a file to be pasteable straight into the composer.
  assert.match(
    appSource,
    /onPaste=\{\(event\) => \{ void pasteChatAttachments\(event\); \}\}/,
    "the composer textarea must handle a paste",
  );
  assert.match(
    appSource,
    /if \(!pasteCarriesFiles\(transfer\)\) return;/,
    "a TEXT paste must be left to the browser, so only a file paste is taken",
  );
});

test("the + menu's Attachments entry does not reuse the + glyph", () => {
  // The Attachments row used the same 'plus' icon as the button that
  // opens the menu, so the entry that adds a file looked like the control
  // that opened the list.
  const menuModel = readFileSync(
    new URL("../src/chatUxModel.ts", import.meta.url),
    "utf8",
  );
  assert.match(
    menuModel,
    /\{ id: "attach", icon: "files"/,
    "the Attachments entry must use the files glyph",
  );
});
