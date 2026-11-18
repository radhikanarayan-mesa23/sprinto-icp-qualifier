import assert from "node:assert/strict";
import { test } from "node:test";
import schema from "../lib/generated/extraction-schema.js";
import { buildReasons } from "../lib/run/evidence.js";

test("right_stage cites the field that satisfied it (enterprise tier), not the first filled field (open roles = 0)", () => {
  const fields = Object.fromEntries(Object.keys(schema.properties).map((f) => [f, { value: null, source_url: null, evidence_quote: null }]));
  fields.open_roles_count = { value: 0, source_url: "https://x.test/careers", evidence_quote: "0 open roles" };
  fields.enterprise_signal = { value: "Enterprise", source_url: "https://x.test/pricing", evidence_quote: "Enterprise" };
  const rows = { already_certified: { status: "missing" }, b2b_saas: { status: "missing" }, compliance_pressure: { status: "missing" }, right_stage: { status: "resolved", met_by: ["enterprise_tier"] } };
  const out = buildReasons(fields, rows, { already_certified: "x", b2b_saas: "x", right_stage: "resolved", compliance_pressure: "x" });
  const stage = out.find((r) => r.row === "right_stage");
  assert.equal(stage.field, "enterprise_signal"); assert.ok(stage.source_url.endsWith("/pricing")); assert.equal(stage.evidence_quote, "Enterprise");
});
