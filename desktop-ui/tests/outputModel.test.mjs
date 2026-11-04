// v1.1.8 Phase 7: output helpers — the filename a user actually receives.
// Run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import { artifactFileName, codeFileExtension } from "../src/outputModel.ts";

test("an export is named after its artifact, not a fixed placeholder", () => {
  assert.equal(artifactFileName("Quarterly Report", "docx", "docx"), "Quarterly Report.docx");
  assert.notEqual(artifactFileName("Quarterly Report", "docx", "docx"), "AMCA-artifact.docx");
});

test("a title that already carries the extension is not doubled", () => {
  assert.equal(artifactFileName("notes.v2", "md", "md"), "notes.md");
  assert.equal(artifactFileName("report.docx", "docx", "docx"), "report.docx");
});

test("filesystem-hostile characters are replaced", () => {
  const name = artifactFileName('a/b\\c:d*e?f"g<h>i|j', "txt", "txt");
  assert.equal(name, "a_b_c_d_e_f_g_h_i_j.txt");
});

test("an empty title falls back to the artifact type, then to a literal", () => {
  assert.equal(artifactFileName("", "tex", "tex"), "tex.tex");
  assert.equal(artifactFileName("", "", "md"), "artifact.md");
});

test("a code block saves with its language's real extension", () => {
  assert.equal(codeFileExtension("python"), "py");
  assert.equal(codeFileExtension("javascript"), "js");
  assert.equal(codeFileExtension("typescript"), "ts");
  assert.equal(codeFileExtension("bash"), "sh");
  assert.equal(codeFileExtension("latex"), "tex");
});

test("an unknown or missing language still yields a usable extension", () => {
  assert.equal(codeFileExtension("brainfuck"), "brainfuck");
  assert.equal(codeFileExtension(""), "txt");
  assert.equal(codeFileExtension("   "), "txt");
});
