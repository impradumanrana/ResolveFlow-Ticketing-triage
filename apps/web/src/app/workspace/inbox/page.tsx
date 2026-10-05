import type { Metadata } from "next";
import Link from "next/link";

import { requirePageAccess } from "@/lib/auth/session";
import { can } from "@/lib/authz/authorize";
import { isOrganizationWideRole } from "@/lib/authz/roles";
import {
  DEFAULT_PAGE_SIZE,
  SAVED_VIEWS,
  findSavedView,
  hasActiveFilters,
  parseTicketQuery,
  ticketQueryPath,
  withFilter,
} from "@/lib/workspace/filters";
import { filterOptions, listTickets } from "@/lib/workspace/repository";
import { isNewToMe, relativeTime, slaLabel, slaState, stateLabel, ticketState } from "@/lib/workspace/state";
import { AutoRefresh } from "../auto-refresh";
import { ListKeyboard } from "../list-keyboard";
import { BulkAssign } from "./bulk-assign";

export const metadata: Metadata = { title: "Inbox" };
export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function InboxPage({ searchParams }: { searchParams: SearchParams }) {
  const context = await requirePageAccess("ticket.view");
  const params = await searchParams;
  const query = parseTicketQuery(params);
  const view = findSavedView(query.view);

  const [page, options] = await Promise.all([
    listTickets(context, query),
    filterOptions(context),
  ]);

  const mayAssign = can(context, "ticket.assign");
  const now = page.now;

  return (
    <div className="ws-page">
      <div className="ws-pagehead">
        <div>
          <h1>Inbox</h1>
          <p className="ws-lede">{view.description}</p>
        </div>
        <AutoRefresh />
      </div>

      <nav className="ws-views" aria-label="Saved views">
        {SAVED_VIEWS.map((saved) => (
          <Link
            key={saved.id}
            href={ticketQueryPath(withFilter(query, { view: saved.id, sort: saved.defaultSort }))}
            aria-current={saved.id === query.view ? "page" : undefined}
            className="ws-viewlink"
          >
            {saved.label}
          </Link>
        ))}
      </nav>

      {/* A plain GET form: filters land in the URL, so a filtered queue can be
          linked and shared, and the page works before any JavaScript runs. */}
      <form className="ws-filters" method="get" role="search" aria-label="Filter conversations">
        <input type="hidden" name="view" value={query.view} />

        <div className="ws-field">
          <label htmlFor="q">Search subject or customer</label>
          <input id="q" name="q" type="search" defaultValue={query.search} maxLength={120} />
        </div>

        <div className="ws-field">
          <label htmlFor="mailbox">Mailbox</label>
          <select id="mailbox" name="mailbox" defaultValue={query.mailboxId ?? ""}>
            <option value="">All mailboxes</option>
            {options.mailboxes.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div className="ws-field">
          <label htmlFor="queue">Queue</label>
          <select id="queue" name="queue" defaultValue={query.queueId ?? ""}>
            <option value="">All queues</option>
            {options.queues.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div className="ws-field">
          <label htmlFor="assignee">Assignee</label>
          <select
            id="assignee"
            name="assignee"
            defaultValue={typeof query.assignee === "string" ? query.assignee : query.assignee.membershipId}
          >
            <option value="any">Anyone</option>
            <option value="me">Me</option>
            <option value="unassigned">Unassigned</option>
            {options.assignees.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div className="ws-field">
          <label htmlFor="sort">Sort</label>
          <select id="sort" name="sort" defaultValue={query.sort}>
            <option value="sla">Soonest target first</option>
            <option value="newest">Newest activity</option>
            <option value="oldest">Oldest activity</option>
          </select>
        </div>

        <div className="ws-filteractions">
          <button className="ws-button" type="submit">
            Apply filters
          </button>
          {hasActiveFilters(query) && (
            <Link className="ws-clear" href={ticketQueryPath(parseTicketQuery({ view: query.view }))}>
              Clear
            </Link>
          )}
        </div>
      </form>

      <p className="ws-resultcount" role="status" aria-live="polite">
        {page.total === 0
          ? "No conversations match."
          : `${page.total} conversation${page.total === 1 ? "" : "s"}, showing page ${page.page} of ${page.pageCount}.`}
      </p>

      {page.tickets.length === 0 ? (
        <div className="ws-emptystate">
          <h2>Nothing here</h2>
          <p>
            {hasActiveFilters(query)
              ? "No conversation matches these filters. Try clearing them."
              : "This view is empty. That is good news, not an error."}
          </p>
          {/* An empty queue caused by missing access looks exactly like an
              empty queue caused by no work. Saying which saves a support call. */}
          {!isOrganizationWideRole(context.role) && context.departmentIds.length === 0 && (
            <p className="ws-note">
              Your account is not assigned to a department, so conversations
              routed to one are not shown to you. An administrator can add you
              to a department.
            </p>
          )}
          {!isOrganizationWideRole(context.role) && options.mailboxes.length === 0 && (
            <p className="ws-note">
              You do not have access to any mailbox yet. An administrator grants
              mailbox access per person.
            </p>
          )}
        </div>
      ) : (
        <>
          <ListKeyboard selector=".ws-row-link" />

          <table className="ws-table">
            <caption className="ws-visually-hidden">
              Conversations in the {view.label} view. Use arrow keys or j and k to move between rows.
            </caption>
            <thead>
              <tr>
                {mayAssign && <th scope="col" className="ws-selectcol">Select</th>}
                <th scope="col">Conversation</th>
                <th scope="col">State</th>
                <th scope="col">Mailbox</th>
                <th scope="col">Assignee</th>
                <th scope="col">Target</th>
                <th scope="col">Last activity</th>
              </tr>
            </thead>
            <tbody>
              {page.tickets.map((ticket) => {
                const state = ticketState(ticket);
                const sla = slaState(ticket, now);
                const unseen = isNewToMe(ticket);
                return (
                  <tr key={ticket.id} className={unseen ? "ws-row ws-row--unseen" : "ws-row"}>
                    {mayAssign && (
                      <td className="ws-selectcol">
                        <input
                          type="checkbox"
                          name="ticketId"
                          value={ticket.id}
                          form="bulk-assign"
                          aria-label={`Select conversation ${ticket.reference}`}
                        />
                      </td>
                    )}
                    <td>
                      <Link className="ws-row-link" href={`/workspace/tickets/${ticket.reference}`}>
                        <span className="ws-reference">#{ticket.reference}</span>
                        <span className="ws-subject">{ticket.subject ?? "(no subject)"}</span>
                      </Link>
                      <span className="ws-customer">
                        {ticket.customerAddress ?? "unknown sender"} &middot; {ticket.messageCount} message
                        {ticket.messageCount === 1 ? "" : "s"}
                      </span>
                    </td>
                    <td>
                      <span className={`ws-badge ws-badge--${state.toLowerCase().replaceAll("_", "-")}`}>
                        {stateLabel(state)}
                      </span>
                    </td>
                    <td className="ws-dim">{ticket.mailboxAddress}</td>
                    <td className="ws-dim">{ticket.assigneeName ?? "Unassigned"}</td>
                    <td>
                      <span className={`ws-sla ws-sla--${sla.toLowerCase()}`}>{slaLabel(sla)}</span>
                    </td>
                    <td className="ws-dim">{relativeTime(ticket.lastMessageAt, now)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}

      {mayAssign && page.tickets.length > 0 && (
        <BulkAssign assignees={options.assignees} />
      )}

      {page.pageCount > 1 && (
        <nav className="ws-pagination" aria-label="Pagination">
          {page.page > 1 && (
            <Link href={ticketQueryPath({ ...query, page: page.page - 1 })} rel="prev">
              Previous
            </Link>
          )}
          <span aria-current="page">
            Page {page.page} of {page.pageCount}
          </span>
          {page.page < page.pageCount && (
            <Link href={ticketQueryPath({ ...query, page: page.page + 1 })} rel="next">
              Next
            </Link>
          )}
        </nav>
      )}

      {query.pageSize !== DEFAULT_PAGE_SIZE && (
        <p className="ws-note">Showing {query.pageSize} per page.</p>
      )}
    </div>
  );
}
