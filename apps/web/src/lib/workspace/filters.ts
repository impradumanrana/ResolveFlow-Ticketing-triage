/**
 * The inbox query, carried in the URL.
 *
 * Queue state lives in the address bar so a filtered view can be linked,
 * bookmarked, reloaded, and opened in a second tab - which is how support teams
 * actually work ("look at the one I'm on"). It also means the back button
 * behaves, and a server component can render the right page directly.
 *
 * Parsing never throws. A hand-edited or stale URL falls back to defaults
 * rather than showing an error, because the cost of a wrong filter is a
 * confusing list, while the cost of a crash is a blocked agent.
 */

export const SAVED_VIEW_IDS = [
  "new-to-me",
  "mine",
  "needs-a-person",
  "awaiting-approval",
  "waiting",
  "all-open",
  "resolved",
] as const;

export type SavedViewId = (typeof SAVED_VIEW_IDS)[number];

export const SORT_ORDERS = ["newest", "oldest", "sla"] as const;
export type SortOrder = (typeof SORT_ORDERS)[number];

export const ASSIGNEE_FILTERS = ["any", "me", "unassigned"] as const;
export type AssigneeFilter = (typeof ASSIGNEE_FILTERS)[number] | { readonly membershipId: string };

export const PAGE_SIZES = [25, 50, 100] as const;
export const DEFAULT_PAGE_SIZE = 25;
export const MAX_PAGE = 400;
export const MAX_SEARCH_LENGTH = 120;

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9-]{2,79}$/;

export interface TicketQuery {
  readonly view: SavedViewId;
  readonly mailboxId: string | null;
  readonly queueId: string | null;
  readonly assignee: AssigneeFilter;
  readonly search: string;
  readonly sort: SortOrder;
  readonly page: number;
  readonly pageSize: number;
}

export interface SavedView {
  readonly id: SavedViewId;
  readonly label: string;
  readonly description: string;
  /** Statuses this view includes; empty means "any open status". */
  readonly statuses: readonly string[];
  readonly onlyUnseen?: boolean;
  readonly onlyMine?: boolean;
  readonly onlyFailed?: boolean;
  readonly defaultSort: SortOrder;
}

export const SAVED_VIEWS: readonly SavedView[] = [
  {
    id: "new-to-me",
    label: "New to me",
    description: "Conversations with activity you have not read yet.",
    statuses: [],
    onlyUnseen: true,
    defaultSort: "newest",
  },
  {
    id: "mine",
    label: "Assigned to me",
    description: "Open conversations assigned to you.",
    statuses: [],
    onlyMine: true,
    defaultSort: "sla",
  },
  {
    id: "needs-a-person",
    label: "Needs a person",
    description: "Triage could not reach a safe answer, so a human must decide.",
    statuses: [],
    onlyFailed: true,
    defaultSort: "sla",
  },
  {
    id: "awaiting-approval",
    label: "Awaiting approval",
    description: "A grounded draft is waiting for someone to approve or reject it.",
    statuses: ["WAITING_ON_REVIEW"],
    defaultSort: "sla",
  },
  {
    id: "waiting",
    label: "Waiting on customer",
    description: "We asked a question and are waiting for a reply.",
    statuses: ["WAITING_ON_CUSTOMER"],
    defaultSort: "newest",
  },
  {
    id: "all-open",
    label: "All open",
    description: "Everything not yet resolved, across the mailboxes you can see.",
    statuses: ["NEW", "TRIAGED", "IN_PROGRESS", "WAITING_ON_REVIEW", "WAITING_ON_CUSTOMER"],
    defaultSort: "sla",
  },
  {
    id: "resolved",
    label: "Resolved",
    description: "Closed conversations, most recently updated first.",
    statuses: ["RESOLVED", "CLOSED"],
    defaultSort: "newest",
  },
];

export const DEFAULT_VIEW: SavedViewId = "new-to-me";

export function findSavedView(id: string | null | undefined): SavedView {
  return SAVED_VIEWS.find((view) => view.id === id) ?? SAVED_VIEWS.find((view) => view.id === DEFAULT_VIEW)!;
}

type ParamSource =
  | URLSearchParams
  | Readonly<Record<string, string | string[] | undefined>>;

function read(params: ParamSource, key: string): string | null {
  if (params instanceof URLSearchParams) {
    return params.get(key);
  }
  const value = params[key];
  if (Array.isArray(value)) {
    return value[0] ?? null;
  }
  return value ?? null;
}

function identifierOrNull(value: string | null): string | null {
  return value && IDENTIFIER.test(value) ? value : null;
}

function parseAssignee(value: string | null): AssigneeFilter {
  if (!value || value === "any") {
    return "any";
  }
  if (value === "me" || value === "unassigned") {
    return value;
  }
  const membershipId = identifierOrNull(value);
  return membershipId ? { membershipId } : "any";
}

export function parseTicketQuery(params: ParamSource): TicketQuery {
  const view = findSavedView(read(params, "view"));

  const rawSort = read(params, "sort");
  const sort = (SORT_ORDERS as readonly string[]).includes(rawSort ?? "")
    ? (rawSort as SortOrder)
    : view.defaultSort;

  const rawPageSize = Number.parseInt(read(params, "size") ?? "", 10);
  const pageSize = (PAGE_SIZES as readonly number[]).includes(rawPageSize)
    ? rawPageSize
    : DEFAULT_PAGE_SIZE;

  const rawPage = Number.parseInt(read(params, "page") ?? "", 10);
  const page = Number.isFinite(rawPage) ? Math.min(Math.max(rawPage, 1), MAX_PAGE) : 1;

  return {
    view: view.id,
    mailboxId: identifierOrNull(read(params, "mailbox")),
    queueId: identifierOrNull(read(params, "queue")),
    assignee: parseAssignee(read(params, "assignee")),
    search: (read(params, "q") ?? "").trim().slice(0, MAX_SEARCH_LENGTH),
    sort,
    page,
    pageSize,
  };
}

/** Serialize back to a URL, omitting anything at its default. */
export function serializeTicketQuery(query: TicketQuery): URLSearchParams {
  const params = new URLSearchParams();
  const view = findSavedView(query.view);

  if (query.view !== DEFAULT_VIEW) {
    params.set("view", query.view);
  }
  if (query.mailboxId) {
    params.set("mailbox", query.mailboxId);
  }
  if (query.queueId) {
    params.set("queue", query.queueId);
  }
  if (query.assignee !== "any") {
    params.set(
      "assignee",
      typeof query.assignee === "string" ? query.assignee : query.assignee.membershipId,
    );
  }
  if (query.search) {
    params.set("q", query.search);
  }
  if (query.sort !== view.defaultSort) {
    params.set("sort", query.sort);
  }
  if (query.pageSize !== DEFAULT_PAGE_SIZE) {
    params.set("size", String(query.pageSize));
  }
  if (query.page > 1) {
    params.set("page", String(query.page));
  }
  return params;
}

export function ticketQueryPath(query: TicketQuery, basePath = "/workspace/inbox"): string {
  const params = serializeTicketQuery(query);
  const search = params.toString();
  return search ? `${basePath}?${search}` : basePath;
}

/** A query with one field changed, always returning to page one. */
export function withFilter(query: TicketQuery, changes: Partial<TicketQuery>): TicketQuery {
  const next = { ...query, ...changes };
  return { ...next, page: changes.page ?? 1 };
}

export function hasActiveFilters(query: TicketQuery): boolean {
  return Boolean(query.mailboxId || query.queueId || query.search || query.assignee !== "any");
}
