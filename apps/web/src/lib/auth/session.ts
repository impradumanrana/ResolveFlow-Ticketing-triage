import "server-only";

/**
 * Server-side access guards.
 *
 * The rule this file exists to enforce: a protected page or route handler must
 * call one of these, and must never read a role, organization, department, or
 * membership from a cookie, header, query parameter, or request body.
 *
 * `requireAccess` re-derives identity from the database on every request rather
 * than trusting the session payload, so revoking a membership takes effect on
 * the next request instead of when a token happens to expire.
 */

import { cache } from "react";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";

import { getAuditSink } from "../audit/events.server";
import {
  AuthorizationError,
  authorize,
  type AuthContext,
  type DenialReason,
  type ResourceRef,
} from "../authz/authorize";
import type { Permission } from "../authz/roles";
import { createIdentityRepository } from "../identity/factory";
import { resolveIdentity, type IdentityResolution } from "../identity/resolve";
import { sessionCookieName, shouldUseSecureCookies } from "./cookies";

/**
 * Resolve the caller once per request.
 *
 * `cache` deduplicates within a single render pass; it does not persist across
 * requests, so a revoked membership cannot be served from a stale entry.
 */
export const getIdentity = cache(async (): Promise<IdentityResolution> => {
  const cookieName = sessionCookieName(
    shouldUseSecureCookies(process.env.AUTH_URL ?? process.env.NEXTAUTH_URL),
  );
  const sessionToken = (await cookies()).get(cookieName)?.value ?? null;

  // Validated against the database here rather than trusting the session
  // payload, so a revoked session or membership stops working on the next
  // request instead of when a token happens to expire.
  return resolveIdentity(createIdentityRepository(), sessionToken);
});

/** The context, or null. For rendering decisions only. */
export async function getAuthContext(): Promise<AuthContext | null> {
  const resolution = await getIdentity();
  return resolution.ok ? resolution.context : null;
}

const SIGN_IN_REASONS: ReadonlySet<DenialReason> = new Set<DenialReason>([
  "NOT_AUTHENTICATED",
  "SESSION_EXPIRED",
  "SESSION_REVOKED",
]);

async function auditDenial(reason: DenialReason, permission: string): Promise<void> {
  const audit = getAuditSink();
  const requestHeaders = await headers();
  await audit.record({
    organizationId: null,
    actorUserId: null,
    actorMembershipId: null,
    actorEmail: null,
    action: "authz.denied",
    outcome: "DENIED",
    reasonCode: reason,
    targetType: "permission",
    targetId: permission,
    requestId: requestHeaders.get("x-request-id"),
    sourceIpHash: null,
    userAgent: requestHeaders.get("user-agent"),
    metadata: {},
  });
}

/**
 * Guard a page. Redirects rather than throwing, because a person who is simply
 * signed out should land on sign-in, not on an error boundary.
 */
export async function requirePageAccess(
  permission: Permission,
  resource?: ResourceRef | null,
): Promise<AuthContext> {
  const resolution = await getIdentity();

  if (!resolution.ok) {
    if (SIGN_IN_REASONS.has(resolution.reason)) {
      redirect("/signin");
    }
    redirect(`/access-denied?reason=${resolution.reason}`);
  }

  const decision = authorize(resolution.context, permission, resource);
  if (!decision.allowed) {
    await auditDenial(decision.reason, permission);
    redirect(`/access-denied?reason=${decision.reason}`);
  }

  return resolution.context;
}

/**
 * Guard a route handler or server action. Throws `AuthorizationError`, which
 * carries the correct 401 or 403 status.
 */
export async function requireAccess(
  permission: Permission,
  resource?: ResourceRef | null,
): Promise<AuthContext> {
  const resolution = await getIdentity();

  if (!resolution.ok) {
    await auditDenial(resolution.reason, permission);
    throw new AuthorizationError(resolution.reason);
  }

  const decision = authorize(resolution.context, permission, resource);
  if (!decision.allowed) {
    await auditDenial(decision.reason, permission);
    throw new AuthorizationError(decision.reason);
  }

  return resolution.context;
}
