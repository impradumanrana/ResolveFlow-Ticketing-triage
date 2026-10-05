/**
 * Turn a session token into an AuthContext, or refuse.
 *
 * This is the only place identity is assembled. Everything downstream receives
 * an `AuthContext` that the server built from database records, so no route,
 * component, or API handler ever has to decide whether to trust an input.
 *
 * Pure apart from the repository it is handed, so the adversarial cases -
 * expired session, revoked session, revoked membership, a user with no
 * membership, a token for a deleted user - are all testable without a server.
 */

import type { AuthContext, DenialReason } from "../authz/authorize";
import { isMembershipStatus, isRole } from "../authz/roles";
import type { IdentityRepository } from "./repository";
import type { ResolvedIdentity } from "./types";

export type IdentityResolution =
  | { readonly ok: true; readonly context: AuthContext; readonly identity: ResolvedIdentity }
  | { readonly ok: false; readonly reason: DenialReason };

export interface ResolveOptions {
  /** Epoch milliseconds. Injected so expiry is testable without faking time. */
  readonly now?: number;
}

export async function resolveIdentity(
  repository: IdentityRepository,
  sessionToken: string | null | undefined,
  options: ResolveOptions = {},
): Promise<IdentityResolution> {
  const now = options.now ?? Date.now();

  if (!sessionToken || !sessionToken.trim()) {
    return { ok: false, reason: "NOT_AUTHENTICATED" };
  }

  const session = await repository.findSessionByToken(sessionToken);
  if (!session) {
    return { ok: false, reason: "NOT_AUTHENTICATED" };
  }

  // Revocation is checked before expiry so an administrator who removes access
  // sees the specific reason in the audit log.
  if (session.revokedAt !== null && session.revokedAt <= now) {
    return { ok: false, reason: "SESSION_REVOKED" };
  }

  if (session.expiresAt <= now) {
    return { ok: false, reason: "SESSION_EXPIRED" };
  }

  const organization = await repository.getOrganization();
  if (!organization) {
    // No organization means the deployment has not been bootstrapped. Refusing
    // is the only safe answer; there is no tenant to authorize against.
    return { ok: false, reason: "NO_MEMBERSHIP" };
  }

  const user = await repository.findUserById(session.userId);
  if (!user) {
    return { ok: false, reason: "NOT_AUTHENTICATED" };
  }

  const membership = await repository.findMembership(organization.id, user.id);
  if (!membership) {
    return { ok: false, reason: "NO_MEMBERSHIP" };
  }

  // Defensive: the database constrains these, but a repository is an interface
  // and an adapter could return anything. An unrecognised role must not become
  // an implicitly permissive one.
  if (!isRole(membership.role) || !isMembershipStatus(membership.status)) {
    return { ok: false, reason: "MEMBERSHIP_NOT_ACTIVE" };
  }

  if (membership.status === "REVOKED") {
    return { ok: false, reason: "MEMBERSHIP_REVOKED" };
  }

  if (membership.status !== "ACTIVE") {
    return { ok: false, reason: "MEMBERSHIP_NOT_ACTIVE" };
  }

  // Belt and braces: a membership row that somehow belongs to another
  // organization must never produce a context for this one.
  if (membership.organizationId !== organization.id) {
    return { ok: false, reason: "ORGANIZATION_MISMATCH" };
  }

  const context: AuthContext = Object.freeze({
    userId: user.id,
    email: user.email,
    organizationId: organization.id,
    membershipId: membership.id,
    role: membership.role,
    status: membership.status,
    departmentIds: Object.freeze([...membership.departmentIds]),
  });

  return {
    ok: true,
    context,
    identity: { organization, user, membership },
  };
}
