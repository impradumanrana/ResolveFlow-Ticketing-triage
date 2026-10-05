import "server-only";

/**
 * Reading the operations workspace out of PostgreSQL.
 *
 * Visibility is decided in SQL, in one place. Organization scope, mailbox
 * permission, and department assignment are all predicates on the query, so a
 * ticket the viewer may not see is never loaded into this process - and a
 * future page that forgets to filter cannot leak one, because there is nothing
 * to forget.
 *
 * The rules mirror the authorization core exactly (C-D028, C-D065):
 * Owner, Admin, and Auditor see every mailbox; everyone else needs an explicit
 * `mailbox_permissions` row and, where a ticket names a department, membership
 * of it.
 */

import { query } from "../identity/db";
import type { AuthContext } from "../authz/authorize";
import { isOrganizationWideRole } from "../authz/roles";
import type { TicketQuery } from "./filters";
import { findSavedView } from "./filters";
import type { TicketRow } from "./state";

/** SQL that limits tickets to what this person may see. */
const VISIBILITY = `
  t.organization_id = $1
  AND t.deleted_at IS NULL
  AND (
    $2::boolean
    OR EXISTS (
      SELECT 1 FROM mailbox_permissions mp
       WHERE mp.mailbox_id = t.mailbox_id
         AND mp.membership_id = $3::uuid
         AND mp.can_view
    )
  )
  AND (
    $2::boolean
    OR t.department_id IS NULL
    OR t.department_id = ANY($4::uuid[])
  )
`;

function scopeParameters(context: AuthContext): [string, boolean, string, string[]] {
  return [
    context.organizationId,
    isOrganizationWideRole(context.role),
    context.membershipId,
    [...context.departmentIds],
  ];
}

interface TicketDbRow extends Record<string, unknown> {
  id: string;
  reference: string | number;
  version: number;
  subject: string | null;
  status: string;
  route: string | null;
  urgency: string | null;
  customer_address: string | null;
  mailbox_address: string;
  queue_name: string | null;
  assignee_name: string | null;
  assignee_membership_id: string | null;
  last_message_at: Date | null;
  seen_through_message_at: Date | null;
  rule_codes: string[] | null;
  has_draft: boolean;
  sla_state: string | null;
  first_response_due_at: Date | null;
  message_count: number;
}

function toTicket(row: TicketDbRow): TicketRow {
  return {
    id: row.id,
    reference: Number(row.reference),
    version: Number(row.version ?? 0),
    subject: row.subject,
    status: row.status,
    route: row.route,
    urgency: row.urgency,
    customerAddress: row.customer_address,
    mailboxAddress: row.mailbox_address,
    queueName: row.queue_name,
    assigneeName: row.assignee_name,
    assigneeMembershipId: row.assignee_membership_id,
    lastMessageAt: row.last_message_at ? row.last_message_at.toISOString() : null,
    seenThroughMessageAt: row.seen_through_message_at
      ? row.seen_through_message_at.toISOString()
      : null,
    ruleCodes: row.rule_codes ?? [],
    hasDraft: Boolean(row.has_draft),
    slaState: row.sla_state,
    firstResponseDueAt: row.first_response_due_at
      ? row.first_response_due_at.toISOString()
      : null,
    messageCount: Number(row.message_count ?? 0),
  };
}

/**
 * The columns every ticket listing needs.
 *
 * The latest triage run is joined laterally rather than aggregated: a ticket
 * can be triaged many times and only the most recent decision is current.
 */
const TICKET_SELECT = `
  SELECT t.id::text,
         t.reference,
         t.version,
         t.subject,
         t.status::text                          AS status,
         latest.route,
         t.urgency::text                         AS urgency,
         t.customer_address,
         m.email_address                         AS mailbox_address,
         q.name                                  AS queue_name,
         u.name                                  AS assignee_name,
         t.assigned_membership_id::text          AS assignee_membership_id,
         th.last_message_at,
         seen.seen_through_message_at,
         latest.rule_codes,
         (latest.draft IS NOT NULL)              AS has_draft,
         sla.state::text                         AS sla_state,
         sla.first_response_due_at,
         th.message_count
    FROM tickets t
    JOIN mailboxes m ON m.id = t.mailbox_id
    JOIN threads   th ON th.id = t.thread_id
    LEFT JOIN queues q ON q.id = t.queue_id
    LEFT JOIN memberships mem ON mem.id = t.assigned_membership_id
    LEFT JOIN users u ON u.id = mem.user_id
    LEFT JOIN ticket_sla_states sla ON sla.ticket_id = t.id
    LEFT JOIN ticket_seen_state seen
           ON seen.ticket_id = t.id AND seen.membership_id = $3::uuid
    LEFT JOIN LATERAL (
      SELECT tr.route::text AS route, tr.rule_codes, tr.draft
        FROM triage_runs tr
       WHERE tr.ticket_id = t.id AND tr.deleted_at IS NULL
       ORDER BY tr.created_at DESC
       LIMIT 1
    ) latest ON true
`;

export interface TicketPage {
  readonly tickets: readonly TicketRow[];
  /**
   * The instant this page was read, taken once on the server.
   *
   * Every row's relative time and service-level state is measured against it,
   * so a long render cannot show two rows as different ages when they are not.
   */
  readonly now: number;
  readonly total: number;
  readonly page: number;
  readonly pageSize: number;
  readonly pageCount: number;
}

function viewPredicates(ticketQuery: TicketQuery, nextIndex: number): {
  clauses: string[];
  values: unknown[];
} {
  const view = findSavedView(ticketQuery.view);
  const clauses: string[] = [];
  const values: unknown[] = [];
  let index = nextIndex;

  if (view.statuses.length > 0) {
    clauses.push(`t.status::text = ANY($${index}::text[])`);
    values.push([...view.statuses]);
    index += 1;
  } else if (ticketQuery.view !== "resolved") {
    clauses.push(`t.status NOT IN ('RESOLVED', 'CLOSED')`);
  }

  if (view.onlyUnseen) {
    // Derived, never a stored flag (C-D078).
    clauses.push(
      `(seen.seen_through_message_at IS NULL OR th.last_message_at > seen.seen_through_message_at)`,
    );
  }
  if (view.onlyMine) {
    clauses.push(`t.assigned_membership_id = $3::uuid`);
  }
  if (view.onlyFailed) {
    clauses.push(
      `latest.rule_codes && ARRAY['MODEL_ERROR','MCP_UNAVAILABLE','GROUNDING_VALIDATION_FAILED']::text[]`,
    );
  }

  if (ticketQuery.mailboxId) {
    clauses.push(`t.mailbox_id = $${index}::uuid`);
    values.push(ticketQuery.mailboxId);
    index += 1;
  }
  if (ticketQuery.queueId) {
    clauses.push(`t.queue_id = $${index}::uuid`);
    values.push(ticketQuery.queueId);
    index += 1;
  }
  if (ticketQuery.assignee === "unassigned") {
    clauses.push(`t.assigned_membership_id IS NULL`);
  } else if (ticketQuery.assignee === "me") {
    clauses.push(`t.assigned_membership_id = $3::uuid`);
  } else if (typeof ticketQuery.assignee !== "string") {
    clauses.push(`t.assigned_membership_id = $${index}::uuid`);
    values.push(ticketQuery.assignee.membershipId);
    index += 1;
  }
  if (ticketQuery.search) {
    // Subject and customer address only. Message bodies are deliberately not
    // searched here: that is a retrieval question, not a filter.
    clauses.push(`(t.subject ILIKE $${index} OR t.customer_address ILIKE $${index})`);
    values.push(`%${ticketQuery.search.replaceAll("%", "").replaceAll("_", "")}%`);
    index += 1;
  }

  return { clauses, values };
}

const ORDER_BY: Readonly<Record<string, string>> = {
  newest: "th.last_message_at DESC NULLS LAST",
  oldest: "th.last_message_at ASC NULLS LAST",
  // Nulls last so tickets without a target do not crowd out ones at risk.
  sla: "sla.first_response_due_at ASC NULLS LAST, th.last_message_at DESC",
};

export async function listTickets(
  context: AuthContext,
  ticketQuery: TicketQuery,
): Promise<TicketPage> {
  const scope = scopeParameters(context);
  const { clauses, values } = viewPredicates(ticketQuery, scope.length + 1);
  const where = [VISIBILITY, ...clauses].join(" AND ");
  const order = ORDER_BY[ticketQuery.sort] ?? ORDER_BY.newest;

  const limitIndex = scope.length + values.length + 1;
  const offsetIndex = limitIndex + 1;

  const rows = await query<TicketDbRow>(
    `${TICKET_SELECT} WHERE ${where} ORDER BY ${order} LIMIT $${limitIndex} OFFSET $${offsetIndex}`,
    [...scope, ...values, ticketQuery.pageSize, (ticketQuery.page - 1) * ticketQuery.pageSize],
  );

  const counted = await query<{ total: string }>(
    `SELECT count(*)::text AS total FROM tickets t
       JOIN mailboxes m ON m.id = t.mailbox_id
       JOIN threads th ON th.id = t.thread_id
       LEFT JOIN ticket_sla_states sla ON sla.ticket_id = t.id
       LEFT JOIN ticket_seen_state seen
              ON seen.ticket_id = t.id AND seen.membership_id = $3::uuid
       LEFT JOIN LATERAL (
         SELECT tr.rule_codes FROM triage_runs tr
          WHERE tr.ticket_id = t.id AND tr.deleted_at IS NULL
          ORDER BY tr.created_at DESC LIMIT 1
       ) latest ON true
      WHERE ${where}`,
    [...scope, ...values],
  );

  const total = Number(counted[0]?.total ?? 0);
  return {
    tickets: rows.map(toTicket),
    now: Date.now(),
    total,
    page: ticketQuery.page,
    pageSize: ticketQuery.pageSize,
    pageCount: Math.max(1, Math.ceil(total / ticketQuery.pageSize)),
  };
}

export interface WorkspaceCounts {
  readonly newToMe: number;
  readonly awaitingApproval: number;
  readonly needsAPerson: number;
  readonly waitingOnCustomer: number;
  readonly open: number;
  readonly slaAtRisk: number;
}

export async function workspaceCounts(context: AuthContext): Promise<WorkspaceCounts> {
  const scope = scopeParameters(context);
  const rows = await query<Record<string, string>>(
    `SELECT
       count(*) FILTER (
         WHERE t.status NOT IN ('RESOLVED','CLOSED')
           AND (seen.seen_through_message_at IS NULL
                OR th.last_message_at > seen.seen_through_message_at)
       )::text AS new_to_me,
       count(*) FILTER (WHERE t.status = 'WAITING_ON_REVIEW')::text AS awaiting_approval,
       count(*) FILTER (
         WHERE t.status NOT IN ('RESOLVED','CLOSED')
           AND latest.rule_codes && ARRAY['MODEL_ERROR','MCP_UNAVAILABLE','GROUNDING_VALIDATION_FAILED']::text[]
       )::text AS needs_a_person,
       count(*) FILTER (WHERE t.status = 'WAITING_ON_CUSTOMER')::text AS waiting,
       count(*) FILTER (WHERE t.status NOT IN ('RESOLVED','CLOSED'))::text AS open,
       count(*) FILTER (
         WHERE t.status NOT IN ('RESOLVED','CLOSED')
           AND sla.first_response_due_at IS NOT NULL
           AND sla.first_response_due_at <= now() + interval '30 minutes'
       )::text AS sla_at_risk
     FROM tickets t
     JOIN mailboxes m ON m.id = t.mailbox_id
     JOIN threads th ON th.id = t.thread_id
     LEFT JOIN ticket_sla_states sla ON sla.ticket_id = t.id
     LEFT JOIN ticket_seen_state seen
            ON seen.ticket_id = t.id AND seen.membership_id = $3::uuid
     LEFT JOIN LATERAL (
       SELECT tr.rule_codes FROM triage_runs tr
        WHERE tr.ticket_id = t.id AND tr.deleted_at IS NULL
        ORDER BY tr.created_at DESC LIMIT 1
     ) latest ON true
    WHERE ${VISIBILITY}`,
    scope,
  );

  const row = rows[0] ?? {};
  return {
    newToMe: Number(row.new_to_me ?? 0),
    awaitingApproval: Number(row.awaiting_approval ?? 0),
    needsAPerson: Number(row.needs_a_person ?? 0),
    waitingOnCustomer: Number(row.waiting ?? 0),
    open: Number(row.open ?? 0),
    slaAtRisk: Number(row.sla_at_risk ?? 0),
  };
}

export interface ConversationMessage {
  readonly id: string;
  readonly direction: string;
  readonly fromAddress: string | null;
  readonly subject: string | null;
  readonly bodyText: string | null;
  readonly sentAt: string;
  readonly hasAttachments: boolean;
  readonly attachments: readonly { filename: string; contentType: string; scanState: string }[];
}

export interface TicketDetail {
  readonly ticket: TicketRow;
  readonly now: number;
  readonly messages: readonly ConversationMessage[];
  readonly mailboxStatus: string;
  readonly decisionSummary: string | null;
  readonly draft: string | null;
  readonly citations: readonly string[];
  readonly groundingValidated: boolean;
  readonly correlationId: string | null;
  readonly trace: readonly { node: string; status: string; message: string; durationMs: number }[];
  readonly actions: readonly { actionType: string; outcome: string; actorName: string | null; occurredAt: string }[];
}

export async function ticketDetail(
  context: AuthContext,
  reference: number,
): Promise<TicketDetail | null> {
  const scope = scopeParameters(context);
  const rows = await query<TicketDbRow & { mailbox_status: string }>(
    `${TICKET_SELECT.replace("SELECT t.id::text", "SELECT t.id::text, m.status::text AS mailbox_status")}
      WHERE ${VISIBILITY} AND t.reference = $${scope.length + 1}`,
    [...scope, reference],
  );
  const row = rows[0];
  if (!row) {
    // Indistinguishable from "not visible to you" on purpose.
    return null;
  }

  const messages = await query<Record<string, never> & {
    id: string;
    direction: string;
    from_address: string | null;
    subject: string | null;
    body_text: string | null;
    sent_at: Date;
    has_attachments: boolean;
  }>(
    `SELECT msg.id::text, msg.direction::text AS direction, msg.from_address, msg.subject,
            msg.body_text, msg.sent_at, msg.has_attachments
       FROM messages msg
       JOIN tickets t ON t.thread_id = msg.thread_id
      WHERE t.id = $1::uuid AND msg.deleted_at IS NULL
      ORDER BY msg.sent_at ASC`,
    [row.id],
  );

  const attachments = await query<{
    message_id: string;
    filename: string;
    content_type: string | null;
    scan_state: string;
  }>(
    `SELECT a.message_id::text, a.filename, a.content_type, a.scan_state
       FROM attachments a
       JOIN messages msg ON msg.id = a.message_id
       JOIN tickets t ON t.thread_id = msg.thread_id
      WHERE t.id = $1::uuid AND a.deleted_at IS NULL`,
    [row.id],
  );

  const runs = await query<{
    decision_summary: string | null;
    draft: string | null;
    citations: string[] | null;
    grounding_validated: boolean;
    correlation_id: string;
    trace: unknown;
  }>(
    `SELECT decision_summary, draft, citations, grounding_validated, correlation_id, trace
       FROM triage_runs WHERE ticket_id = $1::uuid AND deleted_at IS NULL
      ORDER BY created_at DESC LIMIT 1`,
    [row.id],
  );

  const actions = await query<{
    action_type: string;
    outcome: string;
    actor_name: string | null;
    occurred_at: Date;
  }>(
    `SELECT a.action_type::text AS action_type, a.outcome, u.name AS actor_name, a.occurred_at
       FROM actions a
       LEFT JOIN memberships mem ON mem.id = a.actor_membership_id
       LEFT JOIN users u ON u.id = mem.user_id
      WHERE a.ticket_id = $1::uuid
      ORDER BY a.occurred_at DESC LIMIT 50`,
    [row.id],
  );

  const run = runs[0];
  const rawTrace = Array.isArray(run?.trace) ? (run?.trace as Record<string, unknown>[]) : [];

  return {
    ticket: toTicket(row),
    now: Date.now(),
    mailboxStatus: row.mailbox_status,
    messages: messages.map((message) => ({
      id: message.id,
      direction: message.direction,
      fromAddress: message.from_address,
      subject: message.subject,
      bodyText: message.body_text,
      sentAt: message.sent_at.toISOString(),
      hasAttachments: message.has_attachments,
      attachments: attachments
        .filter((attachment) => attachment.message_id === message.id)
        .map((attachment) => ({
          filename: attachment.filename,
          contentType: attachment.content_type ?? "unknown",
          scanState: attachment.scan_state,
        })),
    })),
    decisionSummary: run?.decision_summary ?? null,
    draft: run?.draft ?? null,
    citations: run?.citations ?? [],
    groundingValidated: Boolean(run?.grounding_validated),
    correlationId: run?.correlation_id ?? null,
    trace: rawTrace.map((event) => ({
      node: String(event.node ?? "step"),
      status: String(event.status ?? "unknown"),
      message: String(event.message ?? ""),
      durationMs: Number(event.duration_ms ?? 0),
    })),
    actions: actions.map((action) => ({
      actionType: action.action_type,
      outcome: action.outcome,
      actorName: action.actor_name,
      occurredAt: action.occurred_at.toISOString(),
    })),
  };
}

export interface FilterOption {
  readonly id: string;
  readonly label: string;
}

export async function filterOptions(
  context: AuthContext,
): Promise<{
  mailboxes: FilterOption[];
  queues: FilterOption[];
  assignees: FilterOption[];
  departments: FilterOption[];
}> {
  const scope = scopeParameters(context);

  const mailboxes = await query<{ id: string; email_address: string }>(
    `SELECT DISTINCT m.id::text, m.email_address
       FROM mailboxes m
       JOIN tickets t ON t.mailbox_id = m.id
      WHERE ${VISIBILITY}
      ORDER BY m.email_address`,
    scope,
  );
  const queues = await query<{ id: string; name: string }>(
    `SELECT DISTINCT q.id::text, q.name
       FROM queues q
       JOIN tickets t ON t.queue_id = q.id
      WHERE ${VISIBILITY}
      ORDER BY q.name`,
    scope,
  );
  const assignees = await query<{ id: string; name: string | null; email: string }>(
    `SELECT mem.id::text, u.name, u.email
       FROM memberships mem
       JOIN users u ON u.id = mem.user_id
      WHERE mem.organization_id = $1::uuid AND mem.status = 'ACTIVE'
      ORDER BY coalesce(u.name, u.email)`,
    [context.organizationId],
  );

  // Departments a reroute may target: all of them for an organization-wide
  // role, and otherwise only the ones this person belongs to - moving work
  // somewhere you cannot see it is how a conversation gets lost.
  const departments = await query<{ id: string; name: string }>(
    `SELECT d.id::text, d.name
       FROM departments d
      WHERE d.organization_id = $1::uuid
        AND d.archived_at IS NULL
        AND (
          $2::boolean
          OR EXISTS (
            SELECT 1 FROM department_memberships dm
             WHERE dm.department_id = d.id AND dm.membership_id = $3::uuid
          )
        )
      ORDER BY d.name`,
    [context.organizationId, isOrganizationWideRole(context.role), context.membershipId],
  );

  return {
    mailboxes: mailboxes.map((row) => ({ id: row.id, label: row.email_address })),
    queues: queues.map((row) => ({ id: row.id, label: row.name })),
    assignees: assignees.map((row) => ({ id: row.id, label: row.name ?? row.email })),
    departments: departments.map((row) => ({ id: row.id, label: row.name })),
  };
}

/**
 * Record that this person has read a ticket up to its latest message.
 *
 * Per person, never a shared flag, and only ever moved forward.
 */
export async function markTicketSeen(context: AuthContext, ticketId: string): Promise<void> {
  await query(
    `INSERT INTO ticket_seen_state
       (organization_id, ticket_id, membership_id, seen_through_message_at)
     SELECT t.organization_id, t.id, $3::uuid, th.last_message_at
       FROM tickets t JOIN threads th ON th.id = t.thread_id
      WHERE t.id = $5::uuid AND ${VISIBILITY}
     ON CONFLICT (ticket_id, membership_id) DO UPDATE
       SET last_seen_at = now(),
           seen_through_message_at = GREATEST(
             ticket_seen_state.seen_through_message_at,
             EXCLUDED.seen_through_message_at
           )`,
    [...scopeParameters(context), ticketId],
  );
}

/**
 * Assign several tickets at once.
 *
 * Scoped like every other write, and it bumps each ticket's version so the
 * optimistic locking C12 relies on stays meaningful.
 */
export async function assignTickets(
  context: AuthContext,
  ticketIds: readonly string[],
  membershipId: string | null,
): Promise<number> {
  if (ticketIds.length === 0) {
    return 0;
  }
  const scope = scopeParameters(context);
  const rows = await query<{ id: string }>(
    `UPDATE tickets SET assigned_membership_id = $6::uuid,
                        version = version + 1,
                        updated_at = now()
      WHERE id = ANY($5::uuid[])
        AND id IN (SELECT t.id FROM tickets t WHERE ${VISIBILITY})
      RETURNING id::text`,
    [...scope, [...ticketIds], membershipId],
  );
  return rows.length;
}
