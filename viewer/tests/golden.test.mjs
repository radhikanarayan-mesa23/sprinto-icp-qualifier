// The live run's JavaScript cleaner and evidence check must behave like the Python pipeline's. Golden outputs come from
// the Python code (scripts/make_viewer_golden.py) over every fixture page and a set of evidence scenarios.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import config from "../lib/generated/config.js";
import { cleanHtml } from "../lib/run/clean.js";
import { checkEvidence } from "../lib/run/evidence.js";
import { FIXTURES, golden } from "./helpers.mjs";

for (const [file, want] of Object.entries(golden.clean)) {
  test(`clean parity: ${file}`, () => {
    const got = cleanHtml(readFileSync(join(FIXTURES, file), "utf8"), config);
    assert.equal(got.text, want.text, "cleaned text");
    assert.equal(got.footer_signals, want.footer_signals, "footer signals");
    assert.equal(got.words, want.words, "word count");
    assert.equal(got.tokens_after, want.tokens_after, "tokens after");
    assert.deepEqual(got.injection_flags, want.injection_flags, "agent-directed text found");
  });
}

for (const g of golden.evidence) {
  test(`evidence parity: ${g.name}`, () => {
    const page = { url: "https://x.test/", cleaned: { text: g.text, footer_signals: g.footer } };
    const got = checkEvidence(g.fields, config, page);
    assert.deepEqual(Object.fromEntries(Object.entries(got.rows).map(([r, v]) => [r, v.status])), g.rows);
    assert.deepEqual(got.notes, g.notes);
    for (const [r, extra] of Object.entries(g.extra)) for (const [k, v] of Object.entries(extra)) assert.deepEqual(got.rows[r][k], v, `${r}.${k}`);
  });
}

test("the clean step strips noise and keeps real content (spot check on the good-fit page)", () => {
  const c = cleanHtml(readFileSync(join(FIXTURES, "goodfit/index.html"), "utf8"), config);
  assert.match(c.text, /revenue reconciliation/);
  for (const noise of ["cookies", "Log in", "var x=1"]) assert.ok(!c.text.includes(noise), noise);
  assert.ok(c.tokens_after <= config.clean.max_tokens + config.clean.footer_max_tokens);
});
