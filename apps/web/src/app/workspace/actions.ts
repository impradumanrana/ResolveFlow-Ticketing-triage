"use server";

/**
 * Server actions for the workspace.
 *
 * Every one re-derives identity and re-checks permission on the server. A
 * server action is a public endpoint like any other: the button that called it
 * being hidden proves nothing.
 */

import { revalidatePath } from "next/cache";

import { requireAccess } from "@/lib/auth/session";
import { assignTickets } from "@/lib/workspace/repository";
import { enforceRateLimit } from "@/lib/security/limiter.server";
import {
  RateLimitExceededError,
  RateLimitUnavailableError,
} from "@/lib/security/rate-limit";

const IDENTIFIER = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_BULK_TICKETS = 100;

export interface AssignmentResult {
  readonly ok: boolean;
  readonly assigned: number;
  readonly message: string;
}

export async function assignSelectedTickets(
  _previous: AssignmentResult | null,
  formData: FormData,
): Promise<AssignmentResult> {
  const context = await requireAccess("ticket.assign");

  // Each call already moves up to the bulk maximum, so the limit bounds how
  // fast one account can reassign a whole queue (C13). This tier writes
  // directly here, so it enforces the limit itself rather than relying on the
  // internal API's.
  try {
    await enforceRateLimit("bulk_assign", context.membershipId, {
      organizationId: context.organizationId,
    });
  } catch (error) {
    if (error instanceof RateLimitExceededError) {
      return {
        ok: false,
        assigned: 0,
        message: `Too many assignment requests. Try again in ${error.retryAfterSeconds} seconds.`,
      };
    }
    if (error instanceof RateLimitUnavailableError) {
      return {
        ok: false,
        assigned: 0,
        message: "That could not be processed right now. Try again shortly.",
      };
    }
    throw error;
  }

  const ticketIds = formData
    .getAll("ticketId")
    .map((value) => String(value))
    .filter((value) => IDENTIFIER.test(value));

  if (ticketIds.length === 0) {
    return { ok: false, assigned: 0, message: "Select at least one conversation first." };
  }
  if (ticketIds.length > MAX_BULK_TICKETS) {
    return {
      ok: false,
      assigned: 0,
      message: `Select at most ${MAX_BULK_TICKETS} conversations at once.`,
    };
  }

  const rawAssignee = String(formData.get("assignee") ?? "");
  if (rawAssignee !== "unassigned" && !IDENTIFIER.test(rawAssignee)) {
    return { ok: false, assigned: 0, message: "Choose who to assign these to." };
  }
  const assignee = rawAssignee === "unassigned" ? null : rawAssignee;

  // The repository scopes the update to what this person may see, so a crafted
  // ticket id simply matches nothing.
  const assigned = await assignTickets(context, ticketIds, assignee);

  revalidatePath("/workspace/inbox");
  revalidatePath("/workspace");

  if (assigned === 0) {
    return { ok: false, assigned: 0, message: "Nothing was assigned. Those conversations may no longer be visible to you." };
  }
  if (assigned < ticketIds.length) {
    return {
      ok: true,
      assigned,
      message: `Assigned ${assigned} of ${ticketIds.length}. The rest are no longer visible to you.`,
    };
  }
  return {
    ok: true,
    assigned,
    message: `Assigned ${assigned} conversation${assigned === 1 ? "" : "s"}.`,
  };
}
