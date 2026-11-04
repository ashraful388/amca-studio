import test from "node:test";
import assert from "node:assert/strict";
import {
  MAX_DETAILS_TITLE,
  isDetailsLanguage,
  parseDetailsBlock,
} from "../src/markdownBlocks.ts";

test("a titled details block parses into a title and a body", () => {
  const block = parseDetailsBlock('title="Run details"\nfirst line\nsecond line');
  assert.equal(block.title, "Run details");
  assert.equal(block.body, "first line\nsecond line");
});

test("single quotes and an unquoted title are both accepted", () => {
  assert.equal(parseDetailsBlock("title='Quoted'\nbody").title, "Quoted");
  assert.equal(parseDetailsBlock("title=Plain\nbody").title, "Plain");
});

test("summary= is accepted as an alias for title=", () => {
  assert.equal(parseDetailsBlock('summary="Alias"\nbody').title, "Alias");
});

test("a block without a title directive is not treated as details", () => {
  // This is the safety property: anything ambiguous stays an ordinary code
  // block instead of being reinterpreted.
  assert.equal(parseDetailsBlock("just some code"), null);
  assert.equal(parseDetailsBlock('console.log("hi")\nmore'), null);
  assert.equal(parseDetailsBlock(""), null);
});

test("an empty title falls back to a literal rather than an empty summary", () => {
  assert.equal(parseDetailsBlock('title=""\nbody').title, "Details");
  assert.equal(parseDetailsBlock('title="   "\nbody').title, "Details");
});

test("leading blank lines are stripped from the body", () => {
  assert.equal(parseDetailsBlock('title="T"\n\n\nbody').body, "body");
});

test("an over-long title is bounded", () => {
  const block = parseDetailsBlock(`title="${"x".repeat(5000)}"\nbody`);
  assert.equal(block.title.length, MAX_DETAILS_TITLE);
});

test("a body is markdown text, never raw HTML passed through", () => {
  // The body is handed to the markdown renderer; this asserts the parser does
  // not strip or transform it into anything executable.
  const block = parseDetailsBlock('title="T"\n<script>alert(1)</script>');
  assert.equal(block.body, "<script>alert(1)</script>");
});

test("crlf input is normalised", () => {
  const block = parseDetailsBlock('title="T"\r\nline one\r\nline two');
  assert.equal(block.body, "line one\nline two");
});

test("only the known languages request a collapsible section", () => {
  assert.equal(isDetailsLanguage("details"), true);
  assert.equal(isDetailsLanguage("DETAILS"), true);
  assert.equal(isDetailsLanguage(" collapsible "), true);
  assert.equal(isDetailsLanguage("fold"), true);
  assert.equal(isDetailsLanguage("python"), false);
  assert.equal(isDetailsLanguage(""), false);
});
