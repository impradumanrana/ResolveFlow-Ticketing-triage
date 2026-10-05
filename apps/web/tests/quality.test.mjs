/**
 * The Quality page: what an operator is told about the numbers.
 *
 * A failed gate and a stale corpus are different problems - one says the
 * pipeline is wrong, the other says the measurement is out of date - and the
 * page has to say which.
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  GATE_EXPLANATIONS,
  GATE_ORDER,
  describeThreshold,
  failedGates,
  formatMetric,
  freshness,
  groupGates,
  metricLabel,
  verdict,
  verdictLabel,
  verdictTone,
} from "../.test-build/lib/quality/view.js";

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), "utf8");

const gate = (overrides = {}) => ({
  gate: "retrieval",
  metric: "recall_at_1",
  value: 0.9,
  threshold: 0.8,
  direction: "min",
  passed: true,
  detail: {},
  ...overrides,
});

const run = (overrides = {}) => ({
  present: true,
  dataset_version: "v1",
  threshold_version: "v1",
  knowledge_fingerprint: "abc123",
  current_knowledge_fingerprint: "abc123",
  corpus_changed_since_run: false,
  article_count: 12,
  status: "COMPLETED",
  passed: true,
  started_at: "2026-09-17T09:00:00Z",
  completed_at: "2026-09-17T09:02:00Z",
  gates: [gate()],
  ...overrides,
});

test("ratios read as percentages and other measures keep their units", () => {
  assert.equal(formatMetric("recall_at_1", 0.875), "87.5%");
  assert.equal(formatMetric("guardrail_recall", 1), "100%");
  assert.equal(formatMetric("p95_ms", 1234.6), "1,235 ms");
  assert.equal(formatMetric("mean_micro_units_per_run", 83732), "0.0837 minor units");
});

test("a metric that could not be measured says so rather than showing zero", () => {
  assert.equal(formatMetric("recall_at_1", null), "not measured");
  assert.equal(formatMetric("recall_at_1", Number.NaN), "not measured");
});

test("a threshold reads as the direction it is", () => {
  assert.equal(describeThreshold(gate()), "at least 80%");
  assert.equal(
    describeThreshold(gate({ metric: "p95_ms", threshold: 20000, direction: "max" })),
    "at most 20,000 ms",
  );
});

test("the verdict distinguishes never run, running, passed and failed", () => {
  assert.equal(verdict(null), "NONE");
  assert.equal(verdict(run({ present: false })), "NONE");
  assert.equal(verdict(run({ status: "RUNNING", passed: null })), "RUNNING");
  assert.equal(verdict(run({ passed: false })), "FAILED");
  assert.equal(verdict(run()), "PASSED");
  for (const value of ["NONE", "RUNNING", "FAILED", "PASSED"]) {
    assert.ok(verdictLabel(value));
    assert.ok(verdictTone(value));
  }
});

test("each verdict tone is an existing contrast-checked badge", async () => {
  const css = await source("app/globals.css");
  for (const value of ["NONE", "RUNNING", "FAILED", "PASSED"]) {
    assert.match(css, new RegExp(`\\.ws-badge--${verdictTone(value)}[ ,]`), value);
  }
});

test("a changed corpus is reported as stale numbers, not as a failure", () => {
  const stale = run({ corpus_changed_since_run: true, current_knowledge_fingerprint: "def456" });
  assert.match(freshness(stale), /knowledge has changed/);
  assert.equal(verdict(stale), "PASSED", "a corpus change does not fail the run");
  assert.equal(freshness(run()), null);
  assert.match(freshness(null), /never been run/);
});

test("failed gates are listed, and a passing run lists none", () => {
  assert.deepEqual(failedGates(run()), []);
  const failing = run({ gates: [gate(), gate({ metric: "mrr", passed: false, value: 0.4 })] });
  assert.deepEqual(
    failedGates(failing).map((g) => g.metric),
    ["mrr"],
  );
  assert.deepEqual(failedGates(null), []);
});

test("safety and groundedness are shown before speed and cost", () => {
  const groups = groupGates([
    gate({ gate: "cost", metric: "mean_micro_units_per_run" }),
    gate({ gate: "safety", metric: "guardrail_recall" }),
    gate({ gate: "retrieval" }),
    gate({ gate: "groundedness", metric: "validated_share" }),
  ]);
  assert.deepEqual(
    groups.map((group) => group.gate),
    ["safety", "groundedness", "retrieval", "cost"],
  );
  assert.equal(groups[0].metrics.length, 1);
});

test("an unknown gate is still shown rather than dropped", () => {
  const groups = groupGates([gate({ gate: "invented" }), gate({ gate: "safety" })]);
  assert.deepEqual(
    groups.map((group) => group.gate),
    ["safety", "invented"],
  );
});

test("every gate the pipeline measures is explained on the page", async () => {
  const thresholds = JSON.parse(await readFile(new URL("../../../app/fixtures/quality/thresholds_v1.json", import.meta.url), "utf8"));
  const measured = new Set(thresholds.gates.map((entry) => entry.gate));
  for (const name of measured) {
    assert.ok(GATE_EXPLANATIONS[name], `${name} has no explanation`);
    assert.ok(GATE_ORDER.includes(name), `${name} has no place in the order`);
  }
  assert.equal(metricLabel("recall_at_1"), "recall at 1");
});

test("the page checks permission on the server and never starts a run", async () => {
  const page = await source("app/workspace/quality/page.tsx");
  assert.match(page, /requirePageAccess\("quality\.view"\)/);
  assert.match(page, /path: "\/v1\/quality\/latest"/);
  assert.doesNotMatch(page, /method: "POST"|useActionState|"use client"/);
});

test("the navigation offers quality only to people who may see it", async () => {
  const layout = await source("app/workspace/layout.tsx");
  assert.match(layout, /can\(context, "quality\.view"\) \? <Link href="\/workspace\/quality">/);
});
