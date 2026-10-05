import type { Metadata } from "next";

import {
  budgetUsage,
  describeFailure,
  describeFallbacks,
  statusLabel,
  statusTone,
  type AiSettingsView,
} from "@/lib/ai-settings/view";
import { can } from "@/lib/authz/authorize";
import { requirePageAccess } from "@/lib/auth/session";
import { callAiApi } from "@/lib/bff/ai-api";
import { ReplaceKeyForm, VerifyForm } from "./forms";

export const metadata: Metadata = { title: "AI provider" };

function when(value: string | null): string {
  if (!value) {
    return "never";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? "unknown"
    : parsed.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) +
        " UTC";
}

export default async function AiSettingsPage() {
  const context = await requirePageAccess("ai_settings.view");
  const manage = can(context, "ai_settings.manage");
  const response = await callAiApi<AiSettingsView>(context, { path: "/v1/ai/settings" });

  return (
    <section className="ws-page" aria-labelledby="ai-title">
      <div className="ws-pagehead">
        <h1 id="ai-title">AI provider</h1>
      </div>
      <p className="ws-lede">
        Your organization&apos;s own model account. Calls use only models you have approved, in your
        approved region, within your monthly budget. When anything fails, conversations go to a
        person.
      </p>

      {!response.ok || !response.data ? (
        <div className="ws-panel" role="alert">
          <p>The AI service could not be reached, so provider status is unavailable.</p>
        </div>
      ) : response.data.providers.length === 0 ? (
        <div className="ws-panel">
          <p>No AI provider is configured. Until one is, every conversation is routed to a person.</p>
        </div>
      ) : (
        <>
          {response.data.providers.map((provider) => {
            const budget = budgetUsage(provider);
            return (
              <article key={provider.provider} className="ws-panel" aria-labelledby={`p-${provider.provider}`}>
                <h2 id={`p-${provider.provider}`} className="ws-sectiontitle">
                  {provider.provider}{" "}
                  <span className={`ws-badge ws-badge--${statusTone(provider.status)}`}>
                    {statusLabel(provider.status)}
                  </span>
                </h2>

                {provider.last_failure_code ? (
                  <p className="ws-alert" role="status">
                    Last problem: {provider.last_failure_message ?? provider.last_failure_code}{" "}
                    <span className="ws-note">({when(provider.last_failure_at)})</span>
                  </p>
                ) : null}

                <dl className="ws-facts">
                  <dt>API key</dt>
                  <dd>{provider.credential_hint}</dd>
                  <dt>Last verified</dt>
                  <dd>{when(provider.last_verified_at)}</dd>
                  <dt>Region</dt>
                  <dd>{provider.region ?? "not set - calls are paused"}</dd>
                  <dt>Models</dt>
                  <dd>
                    <ul>
                      {Object.entries(provider.models).map(([use, model]) => (
                        <li key={use}>
                          {use}: {model}
                        </li>
                      ))}
                    </ul>
                  </dd>
                  <dt>Fallback</dt>
                  <dd>{provider.fallback.length ? provider.fallback.join("; ") : "none"}</dd>
                  <dt>Approved models</dt>
                  <dd>
                    {provider.approved_models.length ? (
                      <ul>
                        {provider.approved_models.map((entry) => (
                          <li key={entry}>{entry}</li>
                        ))}
                      </ul>
                    ) : (
                      "none - calls are paused"
                    )}
                  </dd>
                  <dt>Budget</dt>
                  <dd>
                    {budget.percent !== null ? (
                      <meter min={0} max={100} value={budget.percent} low={79} high={80} optimum={0}>
                        {budget.percent}%
                      </meter>
                    ) : null}{" "}
                    {budget.summary}
                  </dd>
                </dl>
                <p className="ws-note">{response.data?.cost_note}</p>

                {manage ? (
                  <div className="ws-actions">
                    <VerifyForm provider={provider.provider} />
                    <ReplaceKeyForm provider={provider.provider} />
                  </div>
                ) : (
                  <p className="ws-note">Only owners and administrators can check or replace the key.</p>
                )}
              </article>
            );
          })}

          <section className="ws-panel" aria-labelledby="failures-title">
            <h2 id="failures-title" className="ws-sectiontitle">
              Problems in the last 24 hours
            </h2>
            {response.data.recent_failures.length === 0 ? (
              <p>None.</p>
            ) : (
              <ul>
                {response.data.recent_failures.map((failure) => (
                  <li key={failure.code}>{describeFailure(failure)}</li>
                ))}
              </ul>
            )}
            {describeFallbacks(response.data.fallback_uses) ? (
              <p className="ws-note">{describeFallbacks(response.data.fallback_uses)}</p>
            ) : null}
          </section>
        </>
      )}
    </section>
  );
}
