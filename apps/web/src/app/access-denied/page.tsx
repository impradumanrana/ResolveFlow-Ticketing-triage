import type { Metadata } from "next";
import Link from "next/link";

import { messageForRefusal, SIGN_IN_REFUSALS } from "@/lib/identity/signin-policy";

export const metadata: Metadata = { title: "Access denied" };

// Rendered per request so the C13 content-security-policy nonce can be
// stamped onto the framework's bootstrap scripts. A prerendered page has no
// nonce, and `strict-dynamic` makes `'self'` inoperative, so a static copy of
// this page would load no JavaScript at all.
export const dynamic = "force-dynamic";

type SearchParams = Promise<{ reason?: string }>;

function describe(reason: string | undefined): string {
  if (reason && (SIGN_IN_REFUSALS as readonly string[]).includes(reason)) {
    return messageForRefusal(reason as (typeof SIGN_IN_REFUSALS)[number]);
  }
  // Anything unrecognised gets the generic message. The specific reason is in
  // the audit log; echoing arbitrary input back to the page is not useful and
  // would be a reflection risk.
  return "You do not have access to this workspace.";
}

export default async function AccessDeniedPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const { reason } = await searchParams;

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="denied-heading">
        <p className="auth-eyebrow">Access denied</p>
        <h1 id="denied-heading">You cannot open this workspace</h1>
        <p className="auth-lede">{describe(reason)}</p>
        <p className="auth-note">
          If you believe this is a mistake, ask a workspace administrator to check
          your invitation and role. This attempt has been recorded.
        </p>
        <Link className="auth-link" href="/signin">
          Back to sign in
        </Link>
      </section>
    </main>
  );
}
