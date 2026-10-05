/**
 * What the review controls offer, and what they say afterwards.
 *
 * Framework-free so the rules that matter - which decisions a role may make,
 * what each one requires, and how an outcome reads - are tested directly.
 *
 * There is no "send" here, and no decision that could become one. Approving
 * creates a draft in the mailbox for a person to send themselves.
 */

import type { Permission } from "../authz/roles";

export type DecisionName = "EDIT" | "APPROVE" | "REJECT" | "REROUTE" | "ASSIGN" | "RESOLVE";

export interface DecisionSpec {
  readonly decision: DecisionName;
  readonly label: string;
  readonly permission: Permission;
  readonly needsReason: boolean;
  readonly needsBody: boolean;
  /** Said on the button's own panel, before anyone clicks it. */
  readonly effect: string;
}

export const DECISIONS: readonly DecisionSpec[] = [
  {
    decision: "APPROVE",
    label: "Approve",
    permission: "ticket.approve_draft",
    needsReason: false,
    needsBody: false,
    effect:
      "Creates a draft in the mailbox for a person to review and send. ResolveFlow never sends a message itself.",
  },
  {
    decision: "EDIT",
    label: "Save changes",
    permission: "ticket.review_draft",
    needsReason: false,
    needsBody: true,
    effect: "Saves your wording as a new revision. The model's own answer is kept as written.",
  },
  {
    decision: "REJECT",
    label: "Reject",
    permission: "ticket.review_draft",
    needsReason: true,
    needsBody: false,
    effect: "Withholds the answer and leaves the conversation with a person.",
  },
  {
    decision: "REROUTE",
    label: "Move",
    permission: "ticket.reroute",
    needsReason: true,
    needsBody: false,
    effect: "Moves the conversation to another queue or department.",
  },
  {
    decision: "ASSIGN",
    label: "Assign",
    permission: "ticket.assign",
    needsReason: false,
    needsBody: false,
    effect: "Gives the conversation to one person.",
  },
  {
    decision: "RESOLVE",
    label: "Mark resolved",
    permission: "ticket.update",
    needsReason: false,
    needsBody: false,
    effect: "Closes the conversation. Nothing is sent.",
  },
] as const;

export function specFor(decision: string): DecisionSpec | null {
  return DECISIONS.find((spec) => spec.decision === decision) ?? null;
}

export interface ProviderDraftView {
  readonly status: "PENDING" | "CREATED" | "FAILED" | "REFUSED";
  readonly failure_code: string | null;
  readonly message: string | null;
  readonly provider_draft_id: string | null;
}

export interface ReviewOutcomeView {
  readonly ok: boolean;
  readonly decision: DecisionName;
  readonly code: string;
  readonly message: string;
  readonly ticket_version: number;
  readonly revision: number | null;
  readonly replayed: boolean;
  readonly draft: ProviderDraftView | null;
}

export interface DraftPreviewView {
  readonly from_address: string;
  readonly to: readonly string[];
  readonly cc: readonly string[];
  readonly subject: string;
  readonly body: string;
  readonly in_reply_to: string | null;
  readonly notes: readonly string[];
  readonly effect: string;
  readonly can_create_draft: boolean;
}

export interface ActionResult {
  readonly ok: boolean;
  readonly message: string;
  /** Present on a conflict, so the page can tell the person what to do. */
  readonly reload?: boolean;
}

/** One rendered form is one decision: the key is minted with the form. */
export function decisionKey(ticketId: string, decision: DecisionName, nonce: string): string {
  return `${decision.toLowerCase()}-${ticketId}-${nonce}`;
}

export function outcomeMessage(outcome: ReviewOutcomeView | null, status: number | null): ActionResult {
  if (status === 409) {
    return {
      ok: false,
      message:
        outcome?.message ??
        "Someone else changed this conversation first. Reload and decide again.",
      reload: true,
    };
  }
  if (status === 404) {
    return { ok: false, message: "That conversation is not available to you." };
  }
  if (outcome === null) {
    return { ok: false, message: "That could not be done. Nothing was changed." };
  }
  if (!outcome.ok) {
    return { ok: false, message: outcome.message };
  }
  const parts = [outcome.replayed ? "Already recorded." : outcome.message];
  if (outcome.draft?.message) {
    parts.push(outcome.draft.message);
  }
  return { ok: true, message: parts.join(" ") };
}

/** How a provider-draft outcome should read, and how loudly. */
export function draftTone(draft: ProviderDraftView | null): "resolved" | "awaiting-approval" | "failed" | null {
  if (!draft) {
    return null;
  }
  if (draft.status === "CREATED") {
    return "resolved";
  }
  return draft.status === "REFUSED" ? "awaiting-approval" : "failed";
}

export function previewSummary(preview: DraftPreviewView | null): string {
  if (!preview) {
    return "A draft cannot be prepared for this conversation yet.";
  }
  const recipients = preview.to.join(", ");
  return preview.can_create_draft
    ? `Approving creates a draft to ${recipients} in ${preview.from_address}.`
    : `Approving records your decision. No draft is created: ${preview.from_address} is connected read-only.`;
}
