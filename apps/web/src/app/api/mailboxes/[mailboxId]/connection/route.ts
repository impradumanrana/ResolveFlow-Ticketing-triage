/**
 * Disconnect a mailbox. Owner and Admin only.
 *
 * The internal service destroys the stored credential even if Google's
 * revocation endpoint is unreachable, so a person who disconnects is never left
 * connected by a slow provider.
 */

import { NextResponse } from "next/server";

import { AuthorizationError } from "@/lib/authz/authorize";
import { requireAccess } from "@/lib/auth/session";
import { callAiApi } from "@/lib/bff/ai-api";
import { isSameOrigin, isValidMailboxId } from "@/lib/mailbox/redirects";

export const dynamic = "force-dynamic";

type OutcomeResponse = { ok?: boolean; code?: string };

export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ mailboxId: string }> },
) {
  if (!isSameOrigin(request.headers.get("origin"), request.url)) {
    return NextResponse.json({ error: "cross_origin_refused" }, { status: 403 });
  }

  const { mailboxId } = await params;
  if (!isValidMailboxId(mailboxId)) {
    return NextResponse.json({ error: "invalid_mailbox" }, { status: 400 });
  }

  try {
    const context = await requireAccess("mailbox.connect");
    const result = await callAiApi<OutcomeResponse>(context, {
      path: `/v1/mailboxes/${encodeURIComponent(mailboxId)}/connection`,
      method: "DELETE",
    });
    return NextResponse.json(
      {
        ok: Boolean(result.data?.ok),
        code: result.data?.code ?? "unavailable",
        requestId: result.requestId,
      },
      { status: result.ok ? 200 : result.status === 403 ? 403 : 502 },
    );
  } catch (error) {
    if (error instanceof AuthorizationError) {
      return NextResponse.json(
        { error: error.reason, message: error.message },
        { status: error.status },
      );
    }
    throw error;
  }
}
