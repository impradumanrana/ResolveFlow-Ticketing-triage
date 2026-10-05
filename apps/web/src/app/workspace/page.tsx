import type { Metadata } from "next";
import Link from "next/link";

import { requirePageAccess } from "@/lib/auth/session";
import { can } from "@/lib/authz/authorize";
import { AutoRefresh } from "./auto-refresh";
import { workspaceCounts } from "@/lib/workspace/repository";
import { ticketQueryPath, parseTicketQuery } from "@/lib/workspace/filters";

export const metadata: Metadata = { title: "Overview" };
export const dynamic = "force-dynamic";

function viewPath(view: string): string {
  return ticketQueryPath(parseTicketQuery({ view }));
}

export default async function OverviewPage() {
  const context = await requirePageAccess("workspace.view");
  const counts = await workspaceCounts(context);

  const tiles = [
    {
      view: "new-to-me",
      label: "New to you",
      value: counts.newToMe,
      hint: "Activity you have not read. Counted for you alone, not shared with the team.",
    },
    {
      view: "needs-a-person",
      label: "Needs a person",
      value: counts.needsAPerson,
      hint: "Triage could not reach a safe answer.",
      urgent: counts.needsAPerson > 0,
    },
    {
      view: "awaiting-approval",
      label: "Awaiting approval",
      value: counts.awaitingApproval,
      hint: "A grounded draft is waiting for a decision. Nothing is sent without one.",
    },
    {
      view: "waiting",
      label: "Waiting on customer",
      value: counts.waitingOnCustomer,
      hint: "We asked a question and are waiting for a reply.",
    },
  ];

  return (
    <div className="ws-page">
      <div className="ws-pagehead">
        <div>
          <h1>Operations overview</h1>
          <p className="ws-lede">
            Everything across the mailboxes you can see. ResolveFlow drafts and
            routes; it never sends a message to a customer.
          </p>
        </div>
        <AutoRefresh />
      </div>

      <section aria-labelledby="queue-heading">
        <h2 id="queue-heading" className="ws-sectiontitle">
          Where attention is needed
        </h2>
        <ul className="ws-tiles">
          {tiles.map((tile) => (
            <li key={tile.view}>
              <Link
                className={`ws-tile${tile.urgent ? " ws-tile--urgent" : ""}`}
                href={viewPath(tile.view)}
              >
                <span className="ws-tilevalue">{tile.value}</span>
                <span className="ws-tilelabel">{tile.label}</span>
                <span className="ws-tilehint">{tile.hint}</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="sla-heading" className="ws-panel">
        <h2 id="sla-heading">Service levels</h2>
        {counts.slaAtRisk > 0 ? (
          <p className="ws-alert" role="status">
            <strong>{counts.slaAtRisk}</strong> open conversation
            {counts.slaAtRisk === 1 ? " is" : "s are"} within 30 minutes of a
            first-response target, or past it.{" "}
            <Link href={ticketQueryPath({ ...parseTicketQuery({ view: "all-open" }), sort: "sla" })}>
              Open them, soonest first
            </Link>
            .
          </p>
        ) : (
          <p className="ws-empty" role="status">
            {counts.open === 0
              ? "No open conversations."
              : "No conversation is close to a first-response target."}
          </p>
        )}
        <p className="ws-note">
          Targets come from the service-level policies configured for each queue.
          A conversation with no policy is shown without a target rather than
          assumed to be on time.
        </p>
      </section>

      <section aria-labelledby="state-heading" className="ws-panel">
        <h2 id="state-heading">What the states mean</h2>
        <dl className="ws-glossary">
          <div>
            <dt>New to you</dt>
            <dd>
              Has activity after the point you last read. This is per person:
              your colleague reading a conversation does not mark it read for
              you, and Gmail&rsquo;s own unread flag is shared by everyone with
              mailbox access, so it is not used here.
            </dd>
          </div>
          <div>
            <dt>Needs a person</dt>
            <dd>
              The AI provider failed, knowledge search was unavailable, or a
              draft could not be supported by its sources. The draft is withheld
              rather than shown.
            </dd>
          </div>
          <div>
            <dt>Awaiting approval</dt>
            <dd>A draft exists and is waiting for a human decision.</dd>
          </div>
        </dl>
      </section>

      {!can(context, "ticket.assign") && (
        <p className="ws-note" role="status">
          Your role can read conversations but not assign or act on them.
        </p>
      )}
    </div>
  );
}
