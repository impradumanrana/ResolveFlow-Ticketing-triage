/**
 * Who is allowed to sign in.
 *
 * Two independent conditions must both hold (C-D005):
 *   1. The email domain is on the organization's approved list.
 *   2. An invited or active membership exists for that email.
 *
 * A matching domain alone never grants access. That is the difference between
 * "anyone at the client's company can read every support mailbox" and "the
 * people the client named can".
 *
 * Pure by design: no database, no network, no session. The caller loads the
 * facts; this decides.
 */

import type { MembershipStatus, Role } from "../authz/roles";

export const SIGN_IN_REFUSALS = [
  "EMAIL_MISSING",
  "EMAIL_MALFORMED",
  "EMAIL_NOT_VERIFIED",
  "DOMAIN_NOT_ALLOWED",
  "NO_MEMBERSHIP",
  "MEMBERSHIP_NOT_ACTIVE",
  "MEMBERSHIP_REVOKED",
  "PROVIDER_NOT_ALLOWED",
] as const;

export type SignInRefusal = (typeof SIGN_IN_REFUSALS)[number];

export interface SignInCandidate {
  readonly email: string | null | undefined;
  readonly emailVerified: boolean;
  readonly provider: string;
}

export interface MembershipFact {
  readonly membershipId: string;
  readonly organizationId: string;
  readonly role: Role;
  readonly status: MembershipStatus;
}

export interface SignInFacts {
  /** Lower-case domains approved for this deployment. */
  readonly allowedDomains: readonly string[];
  /** The membership for this email, if one exists. */
  readonly membership: MembershipFact | null;
}

export type SignInDecision =
  | { readonly allowed: true; readonly email: string; readonly membership: MembershipFact }
  | { readonly allowed: false; readonly reason: SignInRefusal };

/** Only Google Workspace is an accepted identity provider in V1. */
const ALLOWED_PROVIDERS: ReadonlySet<string> = new Set(["google"]);

/**
 * Normalise an address for comparison.
 *
 * Deliberately does NOT strip Gmail-style dots or `+tag` suffixes. Workspace
 * treats those as the same mailbox but an invitation is matched literally, and
 * silently widening the match would let `invited+other@domain` in.
 */
export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

export function domainOf(email: string): string | null {
  const at = email.lastIndexOf("@");
  if (at <= 0 || at === email.length - 1) {
    return null;
  }
  return email.slice(at + 1);
}

/**
 * Exact domain match only. A subdomain is a different domain: `client.com` on
 * the allowlist must not admit `evil.client.com.attacker.net`, and must not
 * silently admit a subdomain the client did not name.
 */
export function isDomainAllowed(
  email: string,
  allowedDomains: readonly string[],
): boolean {
  const domain = domainOf(email);
  if (!domain) {
    return false;
  }
  return allowedDomains.some((allowed) => allowed.trim().toLowerCase() === domain);
}

export function decideSignIn(
  candidate: SignInCandidate,
  facts: SignInFacts,
): SignInDecision {
  if (!ALLOWED_PROVIDERS.has(candidate.provider)) {
    return { allowed: false, reason: "PROVIDER_NOT_ALLOWED" };
  }

  if (!candidate.email || !candidate.email.trim()) {
    return { allowed: false, reason: "EMAIL_MISSING" };
  }

  const email = normalizeEmail(candidate.email);
  const domain = domainOf(email);
  if (!domain || email.indexOf("@") !== email.lastIndexOf("@") || /\s/.test(email)) {
    return { allowed: false, reason: "EMAIL_MALFORMED" };
  }

  // An unverified address proves nothing about who is signing in.
  if (!candidate.emailVerified) {
    return { allowed: false, reason: "EMAIL_NOT_VERIFIED" };
  }

  if (!isDomainAllowed(email, facts.allowedDomains)) {
    return { allowed: false, reason: "DOMAIN_NOT_ALLOWED" };
  }

  const membership = facts.membership;
  if (!membership) {
    return { allowed: false, reason: "NO_MEMBERSHIP" };
  }

  switch (membership.status) {
    case "ACTIVE":
      return { allowed: true, email, membership };
    case "INVITED":
      // An invitation is consumed by the caller, which activates the membership
      // and then completes sign-in. Sign-in itself does not activate anything.
      return { allowed: true, email, membership };
    case "REVOKED":
      return { allowed: false, reason: "MEMBERSHIP_REVOKED" };
    default:
      return { allowed: false, reason: "MEMBERSHIP_NOT_ACTIVE" };
  }
}

/** What the person sees on the access-denied page. */
export function messageForRefusal(reason: SignInRefusal): string {
  switch (reason) {
    case "DOMAIN_NOT_ALLOWED":
      return "This workspace only accepts accounts from approved company domains.";
    case "NO_MEMBERSHIP":
      return "This account has not been invited to this workspace.";
    case "MEMBERSHIP_REVOKED":
      return "Access for this account has been removed.";
    case "MEMBERSHIP_NOT_ACTIVE":
      return "Access for this account is not active. Ask an administrator.";
    case "EMAIL_NOT_VERIFIED":
      return "Your Google account's email address is not verified.";
    case "PROVIDER_NOT_ALLOWED":
      return "Sign in with your company Google Workspace account.";
    default:
      return "Sign-in could not be completed.";
  }
}
