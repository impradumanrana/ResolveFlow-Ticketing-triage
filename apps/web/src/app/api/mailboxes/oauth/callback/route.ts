/**
 * Google redirects the administrator here after the consent screen.
 *
 * Deliberately not a public path. The session identifies who is completing the
 * flow, and the internal service refuses unless it is the same membership that
 * started it - so a callback link shared or intercepted is useless to anyone
 * else.
 *
 * The query string is attacker-influenceable. Only `state` and `code` are
 * forwarded, no mailbox is ever read from it, and nothing from it is echoed back.
 */

import { NextResponse } from "next/server";

import { requirePageAccess } from "@/lib/auth/session";
import { callAiApi } from "@/lib/bff/ai-api";
import { callbackResultPath } from "@/lib/mailbox/redirects";

export const dynamic = "force-dynamic";

type CompleteResponse = { ok?: boolean; code?: string };

export async function GET(request: Request) {
  const context = await requirePageAccess("mailbox.connect");
  const url = new URL(request.url);

  // The person cancelled or denied consent at Google. Nothing to exchange.
  if (url.searchParams.get("error")) {
    return NextResponse.redirect(new URL(callbackResultPath("CONSENT_DENIED"), request.url), 303);
  }

  const state = url.searchParams.get("state") ?? "";
  const code = url.searchParams.get("code") ?? "";
  if (!state || !code) {
    return NextResponse.redirect(new URL(callbackResultPath(null), request.url), 303);
  }

  const result = await callAiApi<CompleteResponse>(context, {
    path: "/v1/mailboxes/connection/complete",
    method: "POST",
    body: { state, code },
  });

  const outcome = result.ok && result.data ? result.data.code : null;
  return NextResponse.redirect(new URL(callbackResultPath(outcome), request.url), 303);
}
