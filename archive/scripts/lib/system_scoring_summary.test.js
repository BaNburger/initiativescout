const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  parseGateBlockers,
  resolveRequiredFiles,
  assertRequiredFiles,
  buildSystemScoringSummary,
} = require("./system_scoring_summary");

test("parseGateBlockers extracts blocker keys from fail reasons", () => {
  const parsed = parseGateBlockers("Gate B failed: tech_dd_below_threshold, insufficient_source_diversity");
  assert.deepEqual(parsed, ["tech_dd_below_threshold", "insufficient_source_diversity"]);
  assert.deepEqual(parseGateBlockers("pass"), []);
});

test("assertRequiredFiles throws explicit error for missing files", () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "summary-required-"));
  const required = resolveRequiredFiles({ exportsDir: tempDir, configDir: tempDir });
  assert.throws(
    () => assertRequiredFiles(required),
    (error) => String(error.message).includes("Missing required files")
  );
});

test("buildSystemScoringSummary reports current metric integrity", () => {
  const repoRoot = path.resolve(__dirname, "..", "..");
  const summary = buildSystemScoringSummary({
    exportsDir: path.join(repoRoot, "data", "exports"),
    configDir: path.join(repoRoot, "config"),
    topN: 5,
  });

  assert.equal(summary.coverage.initiatives_total, 287);
  assert.equal(summary.coverage.legacy_scored_total, 287);
  assert.equal(summary.coverage.dd_scored_total, 287);
  assert.equal(summary.lens_outputs.outreach_top.length, 5);
  assert.equal(summary.lens_outputs.upside_top.length, 5);

  assert.equal(summary.dd_gates.pass_rates.A.pass_rate, 0);
  assert.equal(summary.dd_gates.pass_rates.B.pass_rate, 0);
  assert.equal(summary.dd_gates.pass_rates.C.pass_rate, 0);
  assert.equal(summary.dd_gates.pass_rates.D.pass_rate, 0);
});

test("buildSystemScoringSummary top rows match exported ranking heads", () => {
  const repoRoot = path.resolve(__dirname, "..", "..");
  const summary = buildSystemScoringSummary({
    exportsDir: path.join(repoRoot, "data", "exports"),
    configDir: path.join(repoRoot, "config"),
    topN: 5,
  });

  assert.equal(summary.lens_outputs.outreach_top[0].initiative_name, "PushQuantum");
  assert.equal(
    summary.lens_outputs.upside_top[0].initiative_name,
    "Electrochemical Society Student Chapter Munich"
  );
});
