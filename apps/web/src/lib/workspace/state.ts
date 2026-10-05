/**
 * What state a ticket is in, from the point of view of the person looking.
 *
 * The plan requires distinguishing "new to me", provider unread, waiting,
 * approval, failed, and resolved. Five of those are derived here. **Provider
 * unread deliberately is not**: Gmail's UNREAD label is shared by everyone with
 * mailbox access, so it cannot answer "has this agent seen this ticket"
 * (C-D042). ResolveFlow keeps its own per-person seen position, and the UI says
 * so rather than showing a number that would be wrong for everyone but the
 * first reader.
 *
 * Pure and framework-free so the offline test build can exercise it.
 */

export const TICKET_STATES = [
  "NEW_TO_ME",
  "AWAITING_APPROVAL",
  "WAITING_ON_CUSTOMER",
  "FAILED",
  "RESOLVED",
  "IN_PROGRESS",
  "OPEN",
] as const;

export type TicketState = (typeof TICKET_STATES)[number];

export const SLA_STATES = ["BREACHED", "AT_RISK", "ON_TRACK", "MET", "NONE"] as const;
export type SlaState = (typeof SLA_STATES)[number];

/** Rule codes that mean the pipeline could not reach a safe answer. */
const FAILURE_RULE_CODES = new Set([
  "MODEL_ERROR",
  "MCP_UNAVAILABLE",
  "GROUNDING_VALIDATION_FAILED",
]);

export interface TicketRow {
  /** Optimistic-locking version: a decision names the version it was made against (C12). */
  readonly version: number;
  readonly id: string;
  readonly reference: number;
  readonly subject: string | null;
  readonly status: string;
  readonly route: string | null;
  readonly urgency: string | null;
  readonly customerAddress: string | null;
  readonly mailboxAddress: string;
  readonly queueName: string | null;
  readonly assigneeName: string | null;
  readonly assigneeMembershipId: string | null;
  readonly lastMessageAt: string | null;
  readonly seenThroughMessageAt: string | null;
  readonly ruleCodes: readonly string[];
  readonly hasDraft: boolean;
  readonly slaState: string | null;
  readonly firstResponseDueAt: string | null;
  readonly messageCount: number;
}

/**
 * True when this ticket has activity the viewer has not seen.
 *
 * Derived by comparing positions, never stored as a flag: a stored flag would
 * have to be reset for every member on every arrival, and would be wrong the
 * moment one of them read it (C-D078).
 */
export function isNewToMe(ticket: Pick<TicketRow, "lastMessageAt" | "seenThroughMessageAt">): boolean {
  if (!ticket.lastMessageAt) {
    return false;
  }
  if (!ticket.seenThroughMessageAt) {
    return true;
  }
  return Date.parse(ticket.lastMessageAt) > Date.parse(ticket.seenThroughMessageAt);
}

export function ticketState(ticket: TicketRow): TicketState {
  if (ticket.status === "RESOLVED" || ticket.status === "CLOSED") {
    return "RESOLVED";
  }
  // A failed pipeline outranks an unread badge: someone must look at it, and
  // saying only "new" would understate why.
  if (ticket.ruleCodes.some((code) => FAILURE_RULE_CODES.has(code))) {
    return "FAILED";
  }
  if (ticket.status === "WAITING_ON_REVIEW" || (ticket.hasDraft && ticket.route === "AUTO_RESOLVE")) {
    return "AWAITING_APPROVAL";
  }
  if (ticket.status === "WAITING_ON_CUSTOMER") {
    return "WAITING_ON_CUSTOMER";
  }
  if (isNewToMe(ticket)) {
    return "NEW_TO_ME";
  }
  if (ticket.status === "IN_PROGRESS") {
    return "IN_PROGRESS";
  }
  return "OPEN";
}

/** Minutes before a first-response target at which a ticket is "at risk". */
export const SLA_WARNING_MINUTES = 30;

export function slaState(
  ticket: TicketRow,
  now: number = Date.now(),
  warningMinutes: number = SLA_WARNING_MINUTES,
): SlaState {
  if (ticket.status === "RESOLVED" || ticket.status === "CLOSED") {
    return ticket.slaState === "BREACHED" ? "BREACHED" : "MET";
  }
  if (ticket.slaState === "BREACHED") {
    return "BREACHED";
  }
  if (!ticket.firstResponseDueAt) {
    return "NONE";
  }
  const due = Date.parse(ticket.firstResponseDueAt);
  if (Number.isNaN(due)) {
    return "NONE";
  }
  if (due <= now) {
    return "BREACHED";
  }
  // A fixed warning horizon rather than a proportion of the target: the
  // useful question is "is there still time to act", and that answer does not
  // change because one queue's target is four hours and another's is one.
  return due - now <= warningMinutes * 60_000 ? "AT_RISK" : "ON_TRACK";
}

const STATE_LABELS: Readonly<Record<TicketState, string>> = {
  NEW_TO_ME: "New to you",
  AWAITING_APPROVAL: "Awaiting your approval",
  WAITING_ON_CUSTOMER: "Waiting on customer",
  FAILED: "Needs a person",
  RESOLVED: "Resolved",
  IN_PROGRESS: "In progress",
  OPEN: "Open",
};

const SLA_LABELS: Readonly<Record<SlaState, string>> = {
  BREACHED: "SLA breached",
  AT_RISK: "SLA at risk",
  ON_TRACK: "Within SLA",
  MET: "SLA met",
  NONE: "No SLA target",
};

export function stateLabel(state: TicketState): string {
  return STATE_LABELS[state];
}

export function slaLabel(state: SlaState): string {
  return SLA_LABELS[state];
}

/**
 * Why a ticket needs a person, in plain words.
 *
 * Rule codes are evidence, not explanation. An agent should not have to know
 * that GROUNDING_VALIDATION_FAILED means the draft could not be supported.
 */
const RULE_EXPLANATIONS: Readonly<Record<string, string>> = {
  MODEL_ERROR: "The AI provider failed, so nothing was drafted.",
  MCP_UNAVAILABLE: "The knowledge search was unavailable.",
  GROUNDING_VALIDATION_FAILED: "A draft was written but could not be supported by the sources, so it was withheld.",
  LOW_KB_CONFIDENCE: "No stored article matched closely enough.",
  LOW_CLASSIFICATION_CONFIDENCE: "The classification was not confident enough to act on.",
  HIGH_URGENCY: "Marked urgent.",
  CRITICAL_URGENCY: "Marked critical.",
  ANGRY_CUSTOMER: "The message reads as angry.",
  THREAT_DETECTED: "The message contains a threat.",
  SECURITY_RISK: "Possible security or account-takeover risk.",
  PAYMENT_FAILURE: "Involves a failed payment.",
  REFUND_OR_LEGAL: "Involves a refund or legal risk.",
  MISSING_INFORMATION: "A specific detail is missing from the customer.",
  RULE_CONFLICT: "Two routing rules disagree about this conversation, so a person decides.",
  RULE_INVALID: "A routing rule could not be read, so nothing was routed automatically.",
  RULE_FORCED_ESCALATION: "A routing rule sends conversations like this to a person.",
  SLA_POLICY_CONFLICT: "Two service-level policies apply equally, so no target was set.",
};

export function explainRuleCode(code: string): string {
  return RULE_EXPLANATIONS[code] ?? code.replaceAll("_", " ").toLowerCase();
}

/** Relative time that never lies about precision. */
export function relativeTime(value: string | null, now: number = Date.now()): string {
  if (!value) {
    return "never";
  }
  const then = Date.parse(value);
  if (Number.isNaN(then)) {
    return "unknown";
  }
  const seconds = Math.round((now - then) / 1000);
  if (seconds < 0) {
    return "just now";
  }
  if (seconds < 60) {
    return "just now";
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}
