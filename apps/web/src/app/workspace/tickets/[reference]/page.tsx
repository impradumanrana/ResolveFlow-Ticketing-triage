import { randomUUID } from "node:crypto";

import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { requirePageAccess } from "@/lib/auth/session";
import { can } from "@/lib/authz/authorize";
import { callAiApi } from "@/lib/bff/ai-api";
import { DECISIONS, type DecisionName, type DraftPreviewView } from "@/lib/review/view";
import { filterOptions, markTicketSeen, ticketDetail } from "@/lib/workspace/repository";
import { ReviewPanel } from "./review-panel";
import {
  explainRuleCode,
  relativeTime,
  slaLabel,
  slaState,
  stateLabel,
  ticketState,
} from "@/lib/workspace/state";

export const metadata: Metadata = { title: "Conversation" };
export const dynamic = "force-dynamic";

type Params = Promise<{ reference: string }>;

export default async function TicketPage({ params }: { params: Params }) {
  const context = await requirePageAccess("ticket.view");
  const { reference } = await params;

  const parsed = Number.parseInt(reference, 10);
  if (!Number.isFinite(parsed) || parsed < 1) {
    notFound();
  }

  const detail = await ticketDetail(context, parsed);
  if (!detail) {
    // Deliberately identical to "does not exist": whether a conversation
    // exists in a mailbox you cannot see is itself information.
    notFound();
  }

  // Opening a conversation is reading it. Recorded for this person alone,
  // through the repository rather than a server action: revalidating the cache
  // during render is not allowed, and the page is already rendering fresh data.
  await markTicketSeen(context, detail.ticket.id);

  // What this person may decide, and what approving would create. The preview
  // is read-only, and the key below is minted with the page: one rendered form
  // is one decision, so a double submit replays rather than deciding twice.
  const allowed = DECISIONS.filter((spec) => can(context, spec.permission)).map(
    (spec) => spec.decision as DecisionName,
  );
  const [options, previewResult] = await Promise.all([
    filterOptions(context),
    allowed.includes("APPROVE")
      ? callAiApi<DraftPreviewView>(context, {
          path: `/v1/tickets/${detail.ticket.id}/draft-preview`,
          method: "POST",
          body: {},
        })
      : Promise.resolve(null),
  ]);
  const preview = previewResult?.ok ? previewResult.data : null;
  const nonce = randomUUID();

  const now = detail.now;
  const state = ticketState(detail.ticket);
  const sla = slaState(detail.ticket, now);

  return (
    <div className="ws-page ws-detail">
      <p className="ws-breadcrumb">
        <Link href="/workspace/inbox">Inbox</Link> <span aria-hidden="true">/</span> #{detail.ticket.reference}
      </p>

      <div className="ws-pagehead">
        <div>
          <h1>{detail.ticket.subject ?? "(no subject)"}</h1>
          <p className="ws-lede">
            <span className={`ws-badge ws-badge--${state.toLowerCase().replaceAll("_", "-")}`}>
              {stateLabel(state)}
            </span>{" "}
            <span className={`ws-sla ws-sla--${sla.toLowerCase()}`}>{slaLabel(sla)}</span>
          </p>
        </div>
      </div>

      <div className="ws-detailgrid">
        <section aria-labelledby="conversation-heading" className="ws-panel">
          <h2 id="conversation-heading">Conversation</h2>
          {detail.messages.length === 0 ? (
            <p className="ws-empty">No messages have been ingested for this conversation yet.</p>
          ) : (
            <ol className="ws-messages">
              {detail.messages.map((message) => (
                <li key={message.id} className={`ws-message ws-message--${message.direction.toLowerCase()}`}>
                  <div className="ws-messagehead">
                    <span className="ws-messagefrom">{message.fromAddress ?? "unknown sender"}</span>
                    <time dateTime={message.sentAt}>{relativeTime(message.sentAt, now)}</time>
                  </div>
                  <p className="ws-messagebody">{message.bodyText ?? "(no readable text)"}</p>
                  {message.attachments.length > 0 && (
                    <ul className="ws-attachments">
                      {message.attachments.map((attachment) => (
                        <li key={attachment.filename}>
                          <span>{attachment.filename}</span>{" "}
                          <span className={`ws-scan ws-scan--${attachment.scanState.toLowerCase()}`}>
                            {attachment.scanState === "SKIPPED"
                              ? "not downloaded (outside the attachment policy)"
                              : attachment.scanState.toLowerCase()}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ol>
          )}
        </section>

        <div className="ws-sidebar">
          <section aria-labelledby="context-heading" className="ws-panel">
            <h2 id="context-heading">Context</h2>
            <dl className="ws-facts">
              <div>
                <dt>Customer</dt>
                <dd>{detail.ticket.customerAddress ?? "unknown"}</dd>
              </div>
              <div>
                <dt>Mailbox</dt>
                <dd>
                  {detail.ticket.mailboxAddress}
                  {detail.mailboxStatus !== "CONNECTED" && (
                    <span className="ws-warn"> ({detail.mailboxStatus.toLowerCase()})</span>
                  )}
                </dd>
              </div>
              <div>
                <dt>Queue</dt>
                <dd>{detail.ticket.queueName ?? "Unrouted"}</dd>
              </div>
              <div>
                <dt>Assignee</dt>
                <dd>{detail.ticket.assigneeName ?? "Unassigned"}</dd>
              </div>
              <div>
                <dt>Urgency</dt>
                <dd>{detail.ticket.urgency ?? "not classified"}</dd>
              </div>
            </dl>
          </section>

          <section aria-labelledby="decision-heading" className="ws-panel">
            <h2 id="decision-heading">Triage decision</h2>
            {detail.ticket.route === null ? (
              <p className="ws-empty">
                This conversation has not been triaged yet. Classification,
                retrieval, and drafting arrive with the triage pipeline.
              </p>
            ) : (
              <>
                <p className="ws-route">
                  Route: <strong>{detail.ticket.route}</strong>
                </p>
                {detail.decisionSummary && <p>{detail.decisionSummary}</p>}
                {detail.ticket.ruleCodes.length > 0 && (
                  <ul className="ws-rules">
                    {detail.ticket.ruleCodes.map((code) => (
                      <li key={code}>
                        <span className="ws-rulecode">{code}</span>
                        <span className="ws-ruletext">{explainRuleCode(code)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </section>

          <section aria-labelledby="draft-heading" className="ws-panel">
            <h2 id="draft-heading">Draft</h2>
            {detail.draft === null ? (
              <p className="ws-empty">
                No draft. A draft is only written when the evidence supports one,
                and it is withheld if it cannot be verified against its sources.
              </p>
            ) : (
              <>
                <p className={detail.groundingValidated ? "ws-grounded" : "ws-ungrounded"} role="status">
                  {detail.groundingValidated
                    ? "Every claim was checked against the cited sources."
                    : "This draft is not verified and must not be sent."}
                </p>
                <blockquote className="ws-draft">{detail.draft}</blockquote>
                {detail.citations.length > 0 && (
                  <ul className="ws-citations">
                    {detail.citations.map((citation) => (
                      <li key={citation}>{citation}</li>
                    ))}
                  </ul>
                )}
                <p className="ws-note">
                  Approving creates a draft in the mailbox for a person to review and send.
                  ResolveFlow never sends a message itself.
                </p>
              </>
            )}
          </section>

          {allowed.length > 0 ? (
            <ReviewPanel
              ticketId={detail.ticket.id}
              reference={detail.ticket.reference}
              version={detail.ticket.version}
              nonce={nonce}
              allowed={allowed}
              // Only a verified customer answer pre-fills the reply. An
              // escalation note is internal - "Triggered controls:
              // HIGH_URGENCY" is not something to send anyone, and a box
              // pre-filled with it invites exactly that.
              draftBody={detail.groundingValidated ? detail.draft : null}
              preview={preview}
              assignees={options.assignees}
              queues={options.queues}
              departments={options.departments}
            />
          ) : (
            <section className="ws-panel">
              <p className="ws-note">
                Your role can read this conversation but not decide on it.
              </p>
            </section>
          )}

          <section aria-labelledby="history-heading" className="ws-panel">
            <h2 id="history-heading">Action history</h2>
            {detail.actions.length === 0 ? (
              <p className="ws-empty">Nothing has been done to this conversation yet.</p>
            ) : (
              <ol className="ws-history">
                {detail.actions.map((action, index) => (
                  <li key={`${action.actionType}-${index}`}>
                    <span>{action.actionType.replaceAll("_", " ").toLowerCase()}</span>
                    <span className="ws-dim">
                      {action.actorName ?? "system"} &middot; {relativeTime(action.occurredAt, now)}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </section>

          {detail.trace.length > 0 && (
            <section aria-labelledby="trace-heading" className="ws-panel">
              <h2 id="trace-heading">Trace</h2>
              <details>
                <summary>What the pipeline did, step by step</summary>
                <ol className="ws-trace">
                  {detail.trace.map((event, index) => (
                    <li key={`${event.node}-${index}`}>
                      <span className="ws-tracenode">{event.node}</span>
                      <span className={`ws-tracestatus ws-tracestatus--${event.status}`}>{event.status}</span>
                      <span>{event.message}</span>
                      <span className="ws-dim">{event.durationMs} ms</span>
                    </li>
                  ))}
                </ol>
              </details>
              {detail.correlationId && (
                <p className="ws-note">Correlation ID {detail.correlationId}</p>
              )}
            </section>
          )}

          {!can(context, "ticket.approve_draft") && (
            <p className="ws-note" role="status">
              Your role can read this conversation but not act on it.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
