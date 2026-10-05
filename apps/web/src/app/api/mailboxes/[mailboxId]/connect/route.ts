/**
 * Start connecting a mailbox. Owner and Admin only.
 *
 * Redirects the administrator to Google's consent screen - but only after
 * confirming the URL returned by the internal API really is Google's.
 */

import { NextResponse } from "next/server";

import { AuthorizationError } from "@/lib/authz/authorize";
import { requireAccess } from "@/lib/auth/session";
import { callAiApi } from "@/lib/bff/ai-api";
import {
  isGoogleAuthorizationUrl,
  isSameOrigin,
  isValidMailboxId,
} from "@/lib/mailbox/redirects";

export const dynamic = "force-dynamic";

type StartResponse = { authorization_url?: unknown };

export async function POST(
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
    const result = await callAiApi<StartResponse>(context, {
      path: `/v1/mailboxes/${encodeURIComponent(mailboxId)}/connection/start`,
      method: "POST",
    });

    if (!result.ok || !result.data) {
      return NextResponse.json(
        { error: "connection_not_started", requestId: result.requestId },
        { status: result.status === 403 ? 403 : 502 },
      );
    }

    if (!isGoogleAuthorizationUrl(result.data.authorization_url)) {
      // Fail closed. A non-Google URL here is a misconfiguration or worse, and
      // redirecting to it would hand an administrator to an unknown page.
      return NextResponse.json(
        { error: "untrusted_authorization_url", requestId: result.requestId },
        { status: 502 },
      );
    }

    return NextResponse.redirect(result.data.authorization_url, 303);
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
