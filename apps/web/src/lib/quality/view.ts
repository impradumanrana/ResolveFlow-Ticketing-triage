/**
 * Presentation rules for the Quality Check page.
 *
 * Framework-free, so what an operator is told - passed or failed, by how much,
 * and whether the numbers still describe the current knowledge - is tested
 * directly.
 */

export interface QualityGate {
  readonly gate: string;
  readonly metric: string;
  readonly value: number | null;
  readonly threshold: number;
  readonly direction: "min" | "max";
  readonly passed: boolean;
  readonly detail: Readonly<Record<string, unknown>>;
}

export interface QualityRun {
  readonly present: boolean;
  readonly dataset_version: string | null;
  readonly threshold_version: string | null;
  readonly knowledge_fingerprint: string | null;
  readonly current_knowledge_fingerprint: string | null;
  readonly corpus_changed_since_run: boolean;
  readonly article_count: number | null;
  readonly status: string | null;
  readonly passed: boolean | null;
  readonly started_at: string | null;
  readonly completed_at: string | null;
  readonly gates: readonly QualityGate[];
}

/** Ratios are shown as percentages; counts and milliseconds as themselves. */
const RATIO_METRICS = new Set([
  "category_accuracy",
  "urgency_accuracy",
  "recall_at_1",
  "recall_at_3",
  "mrr",
  "validated_share",
  "citation_validity",
  "risky_never_auto_resolved",
  "guardrail_recall",
]);

export function formatMetric(metric: string, value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return "not measured";
  }
  if (RATIO_METRICS.has(metric)) {
    return `${(value * 100).toFixed(1).replace(/\.0$/, "")}%`;
  }
  if (metric.endsWith("_ms")) {
    return `${Math.round(value).toLocaleString("en-GB")} ms`;
  }
  if (metric.startsWith("mean_micro")) {
    // Micro-units of a minor unit: 1,000,000 is one cent.
    return `${(value / 1_000_000).toFixed(4)} minor units`;
  }
  return String(value);
}

export function describeThreshold(gate: QualityGate): string {
  const direction = gate.direction === "max" ? "at most" : "at least";
  return `${direction} ${formatMetric(gate.metric, gate.threshold)}`;
}

export function metricLabel(metric: string): string {
  return metric.replaceAll("_", " ");
}

export type RunVerdict = "PASSED" | "FAILED" | "RUNNING" | "NONE";

export function verdict(run: QualityRun | null): RunVerdict {
  if (!run || !run.present) {
    return "NONE";
  }
  if (run.status === "RUNNING") {
    return "RUNNING";
  }
  return run.passed ? "PASSED" : "FAILED";
}

export function verdictTone(value: RunVerdict): string {
  return { PASSED: "resolved", FAILED: "failed", RUNNING: "open", NONE: "awaiting-approval" }[value];
}

export function verdictLabel(value: RunVerdict): string {
  return { PASSED: "Passed", FAILED: "Failed", RUNNING: "Running", NONE: "Never run" }[value];
}

/**
 * What to tell an operator about how much the numbers can be trusted.
 * A corpus change does not fail a run; it means the run describes something
 * else, which is a different and quieter kind of wrong.
 */
export function freshness(run: QualityRun | null): string | null {
  if (!run || !run.present) {
    return "The Quality Check has never been run for this organization, so nothing has been measured.";
  }
  if (run.corpus_changed_since_run) {
    return "The knowledge has changed since this run, so these numbers describe an earlier corpus. Run the check again.";
  }
  return null;
}

export function failedGates(run: QualityRun | null): readonly QualityGate[] {
  return run?.gates.filter((gate) => !gate.passed) ?? [];
}

/** Gates in the order they matter, grouped by what they measure. */
export const GATE_ORDER = [
  "safety",
  "groundedness",
  "classification",
  "retrieval",
  "latency",
  "cost",
] as const;

export function groupGates(
  gates: readonly QualityGate[],
): readonly { gate: string; metrics: readonly QualityGate[] }[] {
  const seen = [...new Set(gates.map((gate) => gate.gate))];
  const ordered = [
    ...GATE_ORDER.filter((name) => seen.includes(name)),
    ...seen.filter((name) => !GATE_ORDER.includes(name as (typeof GATE_ORDER)[number])),
  ];
  return ordered.map((name) => ({
    gate: name,
    metrics: gates.filter((gate) => gate.gate === name),
  }));
}

export const GATE_EXPLANATIONS: Readonly<Record<string, string>> = {
  safety: "Whether a risky conversation was ever answered automatically.",
  groundedness: "Whether every automatic answer was supported by what was retrieved.",
  classification: "Whether the conversation was understood.",
  retrieval: "Whether the right article was found in your own knowledge.",
  latency: "How long a conversation took to triage.",
  cost: "What a conversation costs at your approved prices.",
};
