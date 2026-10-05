"use server";

/**
 * Server actions for human review.
 *
 * Each one re-derives identity, re-checks the permission that decision needs,
 * and forwards the ticket version the person was shown. A hidden button proves
 * nothing: a server action is a public endpoint.
 *
 * None of these can send anything - there is no send decision to pass on.
 */

import { revalidatePath } from "next/cache";

import { requireAccess } from "@/lib/auth/session";
import { callAiApi } from "@/lib/bff/ai-api";
import {
  decisionKey,
  outcomeMessage,
  specFor,
  type ActionResult,
  type ReviewOutcomeView,
} from "@/lib/review/view";

const IDENTIFIER = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function optional(formData: FormData, field: string): string | undefined {
  const value = String(formData.get(field) ?? "").trim();
  return value && IDENTIFIER.test(value) ? value : undefined;
}

export async function decide(
  _previous: ActionResult | null,
  formData: FormData,
): Promise<ActionResult> {
  const spec = specFor(String(formData.get("decision") ?? ""));
  if (!spec) {
    return { ok: false, message: "That is not something you can do here." };
  }

  const context = await requireAccess(spec.permission);

  const ticketId = String(formData.get("ticketId") ?? "");
  const nonce = String(formData.get("nonce") ?? "");
  const expectedVersion = Number(formData.get("expectedVersion") ?? Number.NaN);
  if (
    !IDENTIFIER.test(ticketId) ||
    !nonce ||
    !Number.isInteger(expectedVersion) ||
    expectedVersion < 0
  ) {
    return { ok: false, message: "That request could not be identified. Reload and try again." };
  }
  // Derived from the decision that was actually pressed: one rendered form can
  // offer Save and Approve, and they must not share an idempotency key.
  const idempotencyKey = decisionKey(ticketId, spec.decision, nonce);

  const reason = String(formData.get("reason") ?? "").trim();
  const body = String(formData.get("body") ?? "").trim();
  if (spec.needsReason && !reason) {
    return { ok: false, message: "Say why, so the next person reading this knows." };
  }
  if (spec.needsBody && !body) {
    return { ok: false, message: "There is nothing to save: the reply is empty." };
  }

  const result = await callAiApi<ReviewOutcomeView>(context, {
    path: `/v1/tickets/${ticketId}/review`,
    method: "POST",
    body: {
      decision: spec.decision,
      expected_version: expectedVersion,
      idempotency_key: idempotencyKey,
      reason: reason || null,
      body: body || null,
      assignee_membership_id: optional(formData, "assigneeMembershipId") ?? null,
      queue_id: optional(formData, "queueId") ?? null,
      department_id: optional(formData, "departmentId") ?? null,
    },
  });

  revalidatePath(`/workspace/tickets/${String(formData.get("reference") ?? "")}`);
  revalidatePath("/workspace/inbox");
  revalidatePath("/workspace");

  return outcomeMessage(result.data, result.ok ? null : result.status);
}
