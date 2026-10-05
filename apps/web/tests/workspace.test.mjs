/**
 * Inbox filters, saved views, and ticket state derivation.
 *
 * These decide what an agent sees and how urgent it looks. A wrong answer here
 * is not a cosmetic bug: it is a conversation nobody notices.
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  DEFAULT_PAGE_SIZE,
  DEFAULT_VIEW,
  MAX_PAGE,
  MAX_SEARCH_LENGTH,
  PAGE_SIZES,
  SAVED_VIEWS,
  SAVED_VIEW_IDS,
  findSavedView,
  hasActiveFilters,
  parseTicketQuery,
  serializeTicketQuery,
  ticketQueryPath,
  withFilter,
} from "../.test-build/lib/workspace/filters.js";
import {
  SLA_WARNING_MINUTES,
  explainRuleCode,
  isNewToMe,
  relativeTime,
  slaLabel,
  slaState,
  stateLabel,
  ticketState,
} from "../.test-build/lib/workspace/state.js";

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), "utf8");

function ticket(overrides = {}) {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    reference: 12,
    subject: "Cannot reset password",
    status: "NEW",
    route: null,
    urgency: null,
    customerAddress: "customer@example.net",
    mailboxAddress: "support@acme.example",
    queueName: "First line",
    assigneeName: null,
    assigneeMembershipId: null,
    lastMessageAt: "2026-09-16T10:00:00.000Z",
    seenThroughMessageAt: null,
    ruleCodes: [],
    hasDraft: false,
    slaState: null,
    firstResponseDueAt: null,
    messageCount: 1,
    ...overrides,
  };
}

// =========================================================================
// Filters
// =========================================================================

test("an empty query lands on the default view", () => {
  const query = parseTicketQuery({});
  assert.equal(query.view, DEFAULT_VIEW);
  assert.equal(query.page, 1);
  assert.equal(query.pageSize, DEFAULT_PAGE_SIZE);
  assert.equal(query.assignee, "any");
});

test("every saved view is parseable and has a description", () => {
  for (const id of SAVED_VIEW_IDS) {
    const view = findSavedView(id);
    assert.equal(view.id, id);
    assert.ok(view.label.length > 0);
    assert.ok(view.description.length > 10, `${id} needs a description agents can read`);
  }
});

test("a query round-trips through the URL unchanged", () => {
  const original = parseTicketQuery({
    view: "all-open",
    mailbox: "mbx-support-1",
    queue: "queue-first-line",
    assignee: "unassigned",
    q: "refund",
    sort: "newest",
    size: "50",
    page: "3",
  });

  const reparsed = parseTicketQuery(serializeTicketQuery(original));
  assert.deepEqual(reparsed, original);
});

test("defaults are omitted from the URL so links stay readable", () => {
  const path = ticketQueryPath(parseTicketQuery({}));
  assert.equal(path, "/workspace/inbox");
});

test("a hand-edited or hostile URL falls back instead of throwing", () => {
  const query = parseTicketQuery({
    view: "'; drop table tickets; --",
    mailbox: "../../etc/passwd",
    queue: "<script>alert(1)</script>",
    assignee: "%00admin",
    sort: "arbitrary",
    size: "9999",
    page: "-40",
  });

  assert.equal(query.view, DEFAULT_VIEW);
  assert.equal(query.mailboxId, null);
  assert.equal(query.queueId, null);
  assert.equal(query.assignee, "any");
  assert.ok(PAGE_SIZES.includes(query.pageSize));
  assert.equal(query.page, 1);
});

test("page numbers are clamped rather than trusted", () => {
  assert.equal(parseTicketQuery({ page: "999999" }).page, MAX_PAGE);
  assert.equal(parseTicketQuery({ page: "0" }).page, 1);
  assert.equal(parseTicketQuery({ page: "not a number" }).page, 1);
});

test("search text is trimmed and bounded", () => {
  const query = parseTicketQuery({ q: `  ${"x".repeat(500)}  ` });
  assert.equal(query.search.length, MAX_SEARCH_LENGTH);
});

test("an explicit assignee must look like an identifier", () => {
  assert.deepEqual(parseTicketQuery({ assignee: "membership-42" }).assignee, {
    membershipId: "membership-42",
  });
  assert.equal(parseTicketQuery({ assignee: "a" }).assignee, "any");
});

test("changing a filter returns to the first page", () => {
  const query = { ...parseTicketQuery({ page: "5" }), page: 5 };
  assert.equal(withFilter(query, { mailboxId: "mbx-1" }).page, 1);
});

test("paging forward keeps the page requested", () => {
  const query = parseTicketQuery({});
  assert.equal(withFilter(query, { page: 4 }).page, 4);
});

test("active filters are reported for the clear control", () => {
  assert.equal(hasActiveFilters(parseTicketQuery({})), false);
  assert.equal(hasActiveFilters(parseTicketQuery({ q: "refund" })), true);
  assert.equal(hasActiveFilters(parseTicketQuery({ assignee: "me" })), true);
});

test("each saved view carries a sensible default sort", () => {
  for (const view of SAVED_VIEWS) {
    assert.ok(["newest", "oldest", "sla"].includes(view.defaultSort), view.id);
  }
});

// =========================================================================
// New to me
// =========================================================================

test("a conversation never read is new to you", () => {
  assert.equal(isNewToMe(ticket({ seenThroughMessageAt: null })), true);
});

test("a conversation read past its latest message is not new", () => {
  assert.equal(
    isNewToMe(ticket({ seenThroughMessageAt: "2026-09-16T10:00:00.000Z" })),
    false,
  );
});

test("a reply after you read it makes the conversation new again", () => {
  assert.equal(
    isNewToMe(
      ticket({
        lastMessageAt: "2026-09-16T12:00:00.000Z",
        seenThroughMessageAt: "2026-09-16T10:00:00.000Z",
      }),
    ),
    true,
  );
});

test("a conversation with no messages is not new to anyone", () => {
  assert.equal(isNewToMe(ticket({ lastMessageAt: null })), false);
});

// =========================================================================
// State
// =========================================================================

test("a failed pipeline outranks an unread badge", () => {
  const state = ticketState(ticket({ ruleCodes: ["MCP_UNAVAILABLE"], seenThroughMessageAt: null }));
  assert.equal(state, "FAILED");
});

test("each failure rule code produces the needs-a-person state", () => {
  for (const code of ["MODEL_ERROR", "MCP_UNAVAILABLE", "GROUNDING_VALIDATION_FAILED"]) {
    assert.equal(ticketState(ticket({ ruleCodes: [code] })), "FAILED", code);
  }
});

test("a safety rule that is not a failure does not claim the pipeline broke", () => {
  assert.notEqual(ticketState(ticket({ ruleCodes: ["ANGRY_CUSTOMER"] })), "FAILED");
});

test("a draft awaiting a decision is shown as awaiting approval", () => {
  assert.equal(ticketState(ticket({ status: "WAITING_ON_REVIEW" })), "AWAITING_APPROVAL");
  assert.equal(
    ticketState(ticket({ hasDraft: true, route: "AUTO_RESOLVE" })),
    "AWAITING_APPROVAL",
  );
});

test("a resolved conversation stays resolved even when unread", () => {
  assert.equal(ticketState(ticket({ status: "RESOLVED", seenThroughMessageAt: null })), "RESOLVED");
  assert.equal(ticketState(ticket({ status: "CLOSED" })), "RESOLVED");
});

test("every state has a label an agent can read", () => {
  for (const state of ["NEW_TO_ME", "AWAITING_APPROVAL", "WAITING_ON_CUSTOMER", "FAILED", "RESOLVED", "IN_PROGRESS", "OPEN"]) {
    const label = stateLabel(state);
    assert.ok(label && label === label.trim() && !label.includes("_"), state);
  }
});

// =========================================================================
// Service levels
// =========================================================================

const NOW = Date.parse("2026-09-16T12:00:00.000Z");

test("a passed target is breached", () => {
  const state = slaState(ticket({ firstResponseDueAt: "2026-09-16T11:00:00.000Z" }), NOW);
  assert.equal(state, "BREACHED");
});

test("a target inside the warning horizon is at risk", () => {
  const due = new Date(NOW + (SLA_WARNING_MINUTES - 5) * 60_000).toISOString();
  assert.equal(slaState(ticket({ firstResponseDueAt: due }), NOW), "AT_RISK");
});

test("a target beyond the warning horizon is on track", () => {
  const due = new Date(NOW + (SLA_WARNING_MINUTES + 30) * 60_000).toISOString();
  assert.equal(slaState(ticket({ firstResponseDueAt: due }), NOW), "ON_TRACK");
});

test("no target is shown as no target, never as on time", () => {
  assert.equal(slaState(ticket({ firstResponseDueAt: null }), NOW), "NONE");
  assert.equal(slaLabel("NONE"), "No SLA target");
});

test("a breach recorded by the engine is honoured even before the due time", () => {
  const due = new Date(NOW + 10 * 60_000).toISOString();
  assert.equal(slaState(ticket({ slaState: "BREACHED", firstResponseDueAt: due }), NOW), "BREACHED");
});

test("a resolved ticket that breached still reports the breach", () => {
  assert.equal(slaState(ticket({ status: "RESOLVED", slaState: "BREACHED" }), NOW), "BREACHED");
  assert.equal(slaState(ticket({ status: "RESOLVED" }), NOW), "MET");
});

test("an unparseable due time is treated as no target", () => {
  assert.equal(slaState(ticket({ firstResponseDueAt: "not a date" }), NOW), "NONE");
});

// =========================================================================
// Explanations and time
// =========================================================================

test("rule codes are explained in plain words", () => {
  assert.match(explainRuleCode("GROUNDING_VALIDATION_FAILED"), /could not be supported/);
  assert.equal(explainRuleCode("SOMETHING_NEW").includes("_"), false);
  for (const code of ["RULE_CONFLICT", "RULE_INVALID", "RULE_FORCED_ESCALATION", "SLA_POLICY_CONFLICT"]) {
    assert.match(explainRuleCode(code), /person|no target|nothing/, code);
  }
});

test("relative time reads naturally and never invents precision", () => {
  assert.equal(relativeTime(null), "never");
  assert.equal(relativeTime("not a date"), "unknown");
  assert.equal(relativeTime(new Date(NOW - 30_000).toISOString(), NOW), "just now");
  assert.equal(relativeTime(new Date(NOW - 60_000).toISOString(), NOW), "1 minute ago");
  assert.equal(relativeTime(new Date(NOW - 2 * 3_600_000).toISOString(), NOW), "2 hours ago");
  assert.equal(relativeTime(new Date(NOW - 3 * 86_400_000).toISOString(), NOW), "3 days ago");
});

test("a future timestamp does not read as a negative age", () => {
  assert.equal(relativeTime(new Date(NOW + 60_000).toISOString(), NOW), "just now");
});

// =========================================================================
// Structure: authorization and scoping
// =========================================================================

test("every workspace page is behind a permission guard", async () => {
  for (const page of [
    "app/workspace/layout.tsx",
    "app/workspace/page.tsx",
    "app/workspace/inbox/page.tsx",
    "app/workspace/tickets/[reference]/page.tsx",
  ]) {
    assert.match(await source(page), /require(Page)?Access\(/, `${page} is unguarded`);
  }
});

test("every exported server action re-checks permission", async () => {
  const actions = await source("app/workspace/actions.ts");
  const exported = [...actions.matchAll(/export async function (\w+)/g)].map((m) => m[1]);

  assert.ok(exported.length > 0, "no server actions found");
  for (const name of exported) {
    const start = actions.indexOf(`export async function ${name}`);
    const body = actions.slice(start, start + 1200);
    assert.match(
      body,
      /await requireAccess\("[a-z_.]+"\)/,
      `${name} does not re-check permission; a hidden button proves nothing`,
    );
  }
});

test("visibility is decided in SQL, not after loading rows", async () => {
  const repository = await source("lib/workspace/repository.ts");
  assert.match(repository, /mailbox_permissions/);
  assert.match(repository, /department_id = ANY/);
  assert.ok(repository.includes("const VISIBILITY"), "no single visibility predicate");

  // Every listing query must apply it.
  for (const fragment of ["listTickets", "workspaceCounts", "ticketDetail", "assignTickets"]) {
    const body = repository.slice(repository.indexOf(`export async function ${fragment}`));
    assert.ok(body.slice(0, 2500).includes("VISIBILITY"), `${fragment} does not scope its query`);
  }
});

test("no unread flag is stored anywhere in the workspace", async () => {
  const repository = await source("lib/workspace/repository.ts");
  assert.equal(/is_unread|unread_flag|set.*unread/i.test(repository), false);
  assert.match(repository, /seen_through_message_at/);
});

/** Strip comments so prose explaining a decision is not mistaken for code. */
function withoutComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

test("the workspace never reads Gmail's shared unread label", async () => {
  for (const file of [
    "lib/workspace/repository.ts",
    "lib/workspace/state.ts",
    "app/workspace/inbox/page.tsx",
  ]) {
    const code = withoutComments(await source(file));
    assert.equal(/UNREAD/.test(code), false, `${file} uses the provider label in code`);
  }
});

test("the decision not to use the provider label is explained to the reader", async () => {
  const state = await source("lib/workspace/state.ts");
  assert.match(state, /UNREAD label is shared/);
});
