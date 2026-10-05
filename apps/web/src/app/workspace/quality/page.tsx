import type { Metadata } from "next";

import { requirePageAccess } from "@/lib/auth/session";
import { callAiApi } from "@/lib/bff/ai-api";
import {
  GATE_EXPLANATIONS,
  describeThreshold,
  failedGates,
  formatMetric,
  freshness,
  groupGates,
  metricLabel,
  verdict,
  verdictLabel,
  verdictTone,
  type QualityRun,
} from "@/lib/quality/view";

export const metadata: Metadata = { title: "Quality" };

function when(value: string | null): string {
  if (!value) {
    return "never";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? "unknown"
    : `${parsed.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC`;
}

export default async function QualityPage() {
  const context = await requirePageAccess("quality.view");
  const response = await callAiApi<QualityRun>(context, { path: "/v1/quality/latest" });
  const run = response.data;
  const outcome = verdict(run);
  const note = freshness(run);
  const failures = failedGates(run);

  return (
    <section className="ws-page" aria-labelledby="quality-title">
      <div className="ws-pagehead">
        <h1 id="quality-title">Quality</h1>
        {run?.present ? (
          <span className={`ws-badge ws-badge--${verdictTone(outcome)}`}>{verdictLabel(outcome)}</span>
        ) : null}
      </div>
      <p className="ws-lede">
        What the triage pipeline actually did on a labelled set of conversations, measured against
        the thresholds agreed with you. A failed gate means conversations of that kind go to a
        person until it passes again.
      </p>

      {!response.ok || !run ? (
        <div className="ws-panel" role="alert">
          <p>Quality results could not be loaded.</p>
        </div>
      ) : (
        <>
          {note ? (
            <div className="ws-panel" role="status">
              <p>{note}</p>
            </div>
          ) : null}

          {run.present ? (
            <>
              <dl className="ws-facts">
                <dt>Verdict</dt>
                <dd>{verdictLabel(outcome)}</dd>
                <dt>Measured</dt>
                <dd>{when(run.completed_at ?? run.started_at)}</dd>
                <dt>Dataset</dt>
                <dd>{run.dataset_version}</dd>
                <dt>Thresholds</dt>
                <dd>{run.threshold_version}</dd>
                <dt>Knowledge</dt>
                <dd>
                  {run.article_count} articles{" "}
                  <span className="ws-note">({run.knowledge_fingerprint})</span>
                </dd>
              </dl>

              {failures.length > 0 ? (
                <div className="ws-panel" role="alert">
                  <h2 className="ws-sectiontitle">What failed</h2>
                  <ul>
                    {failures.map((gate) => (
                      <li key={`${gate.gate}.${gate.metric}`}>
                        <strong>
                          {gate.gate} · {metricLabel(gate.metric)}
                        </strong>
                        : {formatMetric(gate.metric, gate.value)}, needs {describeThreshold(gate)}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {groupGates(run.gates).map((group) => (
                <section key={group.gate} className="ws-panel" aria-labelledby={`gate-${group.gate}`}>
                  <h2 id={`gate-${group.gate}`} className="ws-sectiontitle">
                    {group.gate}
                  </h2>
                  <p className="ws-note">{GATE_EXPLANATIONS[group.gate] ?? ""}</p>
                  <table className="ws-table">
                    <thead>
                      <tr>
                        <th scope="col">Measure</th>
                        <th scope="col">Result</th>
                        <th scope="col">Needs</th>
                        <th scope="col">Verdict</th>
                      </tr>
                    </thead>
                    <tbody>
                      {group.metrics.map((gate) => (
                        <tr key={gate.metric}>
                          <td>{metricLabel(gate.metric)}</td>
                          <td>{formatMetric(gate.metric, gate.value)}</td>
                          <td>{describeThreshold(gate)}</td>
                          <td>
                            <span
                              className={`ws-badge ws-badge--${gate.passed ? "resolved" : "failed"}`}
                            >
                              {gate.passed ? "pass" : "fail"}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              ))}
            </>
          ) : null}

          <p className="ws-note">
            A check measures your own knowledge with your own model account, so it is started
            deliberately rather than by opening this page.
          </p>
        </>
      )}
    </section>
  );
}
