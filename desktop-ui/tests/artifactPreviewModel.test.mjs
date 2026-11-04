import test from "node:test";
import assert from "node:assert/strict";
import {
  artifactTypeLabel,
  canonicalSourceExtension,
  parseDelimited,
  planArtifactPreview,
} from "../src/artifactPreviewModel.ts";

test("a spreadsheet source is previewed as a table, not raw text", () => {
  const plan = planArtifactPreview("xlsx", "name,qty\nWidget,3\nGadget,7");
  assert.equal(plan.kind, "table");
  assert.deepEqual(plan.table.headers, ["name", "qty"]);
  assert.deepEqual(plan.table.rows, [["Widget", "3"], ["Gadget", "7"]]);
});

test("quoted fields keep their delimiter and escaped quotes", () => {
  const plan = planArtifactPreview("csv", 'label,note\n"a,b","say ""hi"""');
  assert.equal(plan.kind, "table");
  assert.deepEqual(plan.table.rows, [["a,b", 'say "hi"']]);
});

test("a tab-separated source is detected without being told", () => {
  const plan = planArtifactPreview("xlsx", "a\tb\n1\t2");
  assert.equal(plan.kind, "table");
  assert.deepEqual(plan.table.headers, ["a", "b"]);
});

test("a single-column spreadsheet is not presented as a table", () => {
  const plan = planArtifactPreview("xlsx", "onlyone\nvalue");
  assert.equal(plan.kind, "source");
  assert.match(plan.note, /not a delimited table/);
});

test("a docx artifact previews as its markdown source, and says so", () => {
  const plan = planArtifactPreview("docx", "# Title\n\nBody.");
  assert.equal(plan.kind, "markdown");
  assert.match(plan.note, /markdown source/);
});

test("a plain markdown artifact carries no docx note", () => {
  const plan = planArtifactPreview("markdown", "# Title");
  assert.equal(plan.kind, "markdown");
  assert.equal(plan.note, undefined);
});

test("json is pretty-printed for reading", () => {
  const plan = planArtifactPreview("json", '{"a":1,"b":[2,3]}');
  assert.equal(plan.kind, "json");
  assert.equal(plan.json, '{\n  "a": 1,\n  "b": [\n    2,\n    3\n  ]\n}');
});

test("invalid json falls back to source with an honest note", () => {
  const plan = planArtifactPreview("json", "{not json");
  assert.equal(plan.kind, "source");
  assert.match(plan.note, /does not parse as JSON/);
});

test("a presentation with slide structure previews as slides", () => {
  const plan = planArtifactPreview("pptx", JSON.stringify({ slides: [{ title: "Intro", bullets: ["a", "b"] }] }));
  assert.equal(plan.kind, "slides");
  assert.equal(plan.slides[0].title, "Intro");
  assert.deepEqual(plan.slides[0].lines, ["a", "b"]);
});

test("a presentation without slide structure does not claim a preview", () => {
  const plan = planArtifactPreview("pptx", "just some prose");
  assert.equal(plan.kind, "source");
  assert.match(plan.note, /No slide structure/);
});

test("pdf admits that no preview is possible rather than showing raw bytes", () => {
  const plan = planArtifactPreview("pdf", '{"page":1}');
  assert.equal(plan.kind, "unavailable");
  assert.match(plan.note, /No PDF renderer/);
  assert.equal(plan.source, '{"page":1}');
});

test("an archive admits that it cannot be previewed", () => {
  const plan = planArtifactPreview("zip", "PK");
  assert.equal(plan.kind, "unavailable");
  assert.match(plan.note, /cannot be previewed/);
});

test("html previews rendered with scripts disabled", () => {
  const plan = planArtifactPreview("html", "<h1>Hi</h1>");
  assert.equal(plan.kind, "html");
  assert.match(plan.note, /disabled/);
});

test("an embedded image is previewed; a referenced one is not faked", () => {
  assert.equal(planArtifactPreview("image", "data:image/png;base64,AAA").kind, "image");
  const referenced = planArtifactPreview("image", "C:/tmp/photo.png");
  assert.equal(referenced.kind, "source");
  assert.match(referenced.note, /not embedded data/);
});

test("tex and bib are their own meaningful source", () => {
  assert.equal(planArtifactPreview("tex", "\\documentclass{article}").kind, "source");
  assert.equal(planArtifactPreview("bib", "@article{k,").kind, "source");
});

test("an unknown type is shown as source rather than guessed at", () => {
  const plan = planArtifactPreview("somethingnew", "content");
  assert.equal(plan.kind, "source");
  assert.equal(plan.source, "content");
});

test("delimited parsing rejects blank input and single-column input", () => {
  assert.equal(parseDelimited(""), null);
  assert.equal(parseDelimited("   \n  "), null);
  assert.equal(parseDelimited("solo"), null);
});

test("canonical extensions match what the backend actually stores", () => {
  assert.equal(canonicalSourceExtension("xlsx"), "csv");
  assert.equal(canonicalSourceExtension("docx"), "md");
  assert.equal(canonicalSourceExtension("pptx"), "json");
  assert.equal(canonicalSourceExtension("pdf"), "json");
  assert.equal(canonicalSourceExtension("tex"), "tex");
  assert.equal(canonicalSourceExtension("unknown"), "txt");
});

test("type labels are readable and never fall back to an internal id silently", () => {
  assert.equal(artifactTypeLabel("docx"), "Word document");
  assert.equal(artifactTypeLabel("xlsx"), "Spreadsheet");
  assert.equal(artifactTypeLabel("pdf"), "PDF");
  assert.equal(artifactTypeLabel(""), "File");
});
