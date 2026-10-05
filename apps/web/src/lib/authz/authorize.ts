/**
 * The single authorization decision function.
 *
 * Every protected page, route handler, and server action funnels through
 * `authorize`. It is pure: it takes a context the server derived and a request,
 * and returns a decision with a reason code. It never reads a cookie, a header,
 * a request body, or the database, because a decision that can be influenced by
 * the caller is not an authorization decision.
 */

import {
  ORGANIZATION_WIDE_ROLES,
  READ_ONLY_ROLES,
  WRITE_PERMISSIONS,
  isPermission,
  roleHasPermission,
  type MembershipStatus,
  type Permission,
  type Role,
} from "./roles";

/** Why a request was refused. Stable strings: they are audited and displayed. */
export const DENIAL_REASONS = [
  "NOT_AUTHENTICATED",
  "SESSION_EXPIRED",
  "SESSION_REVOKED",
  "NO_MEMBERSHIP",
  "MEMBERSHIP_NOT_ACTIVE",
  "MEMBERSHIP_REVOKED",
  "ORGANIZATION_MISMATCH",
  "ROLE_LACKS_PERMISSION",
  "ROLE_IS_READ_ONLY",
  "DEPARTMENT_NOT_ASSIGNED",
  "UNKNOWN_PERMISSION",
  "RESOURCE_NOT_IN_ORGANIZATION",
] as const;

export type DenialReason = (typeof DENIAL_REASONS)[number];

/**
 * Identity as the server derived it from the session and membership records.
 * Nothing here may originate from the browser.
 */
export interface AuthContext {
  readonly userId: string;
  readonly email: string;
  readonly organizationId: string;
  readonly membershipId: string;
  readonly role: Role;
  readonly status: MembershipStatus;
  /** Departments this membership belongs to. Empty means organization-wide. */
  readonly departmentIds: readonly string[];
}

/** A tenant-owned object being acted on. */
export interface ResourceRef {
  readonly organizationId: string;
  readonly departmentId?: string | null;
}

export type Decision =
  | { readonly allowed: true }
  | { readonly allowed: false; readonly reason: DenialReason };

const ALLOW: Decision = Object.freeze({ allowed: true });

function deny(reason: DenialReason): Decision {
  return Object.freeze({ allowed: false, reason });
}

/**
 * Decide whether `context` may exercise `permission`, optionally against a
 * specific `resource`.
 *
 * The checks run cheapest-and-most-fundamental first, and each returns a
 * distinct reason so a denial is explainable in the audit log without leaking
 * whether the resource exists.
 */
export function authorize(
  context: AuthContext | null | undefined,
  permission: Permission | string,
  resource?: ResourceRef | null,
): Decision {
  if (!context) {
    return deny("NOT_AUTHENTICATED");
  }

  // An unrecognised permission is a programming error. Fail closed rather than
  // treating it as ungated.
  if (!isPermission(permission)) {
    return deny("UNKNOWN_PERMISSION");
  }

  switch (context.status) {
    case "ACTIVE":
      break;
    case "REVOKED":
      return deny("MEMBERSHIP_REVOKED");
    default:
      // INVITED and SUSPENDED are both "not yet, or not any more".
      return deny("MEMBERSHIP_NOT_ACTIVE");
  }

  // Tenant isolation, checked before anything role-specific and applied to
  // every role including the Owner.
  //
  // Order matters for the audit trail, not for the outcome: both orderings
  // deny. A reference to another organization's object is a security event
  // worth naming precisely, and it should be reported identically whichever
  // role attempted it. Checking the role first would report the same attempt
  // as ROLE_LACKS_PERMISSION for some roles and as a cross-tenant reference
  // for others, which makes the signal hard to search for.
  if (resource && resource.organizationId !== context.organizationId) {
    return deny("RESOURCE_NOT_IN_ORGANIZATION");
  }

  // Independent of the matrix: a read-only role never writes, even if a future
  // edit to the matrix says otherwise.
  if (READ_ONLY_ROLES.has(context.role) && WRITE_PERMISSIONS.has(permission)) {
    return deny("ROLE_IS_READ_ONLY");
  }

  if (!roleHasPermission(context.role, permission)) {
    return deny("ROLE_LACKS_PERMISSION");
  }

  if (
    resource?.departmentId &&
    !ORGANIZATION_WIDE_ROLES.has(context.role) &&
    !context.departmentIds.includes(resource.departmentId)
  ) {
    return deny("DEPARTMENT_NOT_ASSIGNED");
  }

  return ALLOW;
}

/** Boolean form, for rendering. Never use this in place of an enforcement check. */
export function can(
  context: AuthContext | null | undefined,
  permission: Permission | string,
  resource?: ResourceRef | null,
): boolean {
  return authorize(context, permission, resource).allowed;
}

/** HTTP status for a denial: 401 to sign in again, 403 otherwise. */
export function statusForDenial(reason: DenialReason): 401 | 403 {
  return reason === "NOT_AUTHENTICATED" ||
    reason === "SESSION_EXPIRED" ||
    reason === "SESSION_REVOKED"
    ? 401
    : 403;
}

/**
 * Message shown to the person who was refused.
 *
 * Deliberately uniform for every "you may not" case: telling an unauthorized
 * caller whether a resource exists, or which department it belongs to, leaks
 * the organization's shape. The specific reason goes to the audit log.
 */
export function messageForDenial(reason: DenialReason): string {
  switch (reason) {
    case "NOT_AUTHENTICATED":
      return "Sign in to continue.";
    case "SESSION_EXPIRED":
      return "Your session has expired. Sign in again.";
    case "SESSION_REVOKED":
      return "Your session was ended. Sign in again.";
    case "NO_MEMBERSHIP":
      return "This account has not been invited to this workspace.";
    case "MEMBERSHIP_NOT_ACTIVE":
      return "This account's access is not active. Ask an administrator.";
    case "MEMBERSHIP_REVOKED":
      return "This account's access has been removed.";
    default:
      return "You do not have access to this.";
  }
}

export class AuthorizationError extends Error {
  readonly reason: DenialReason;
  readonly status: 401 | 403;

  constructor(reason: DenialReason) {
    super(messageForDenial(reason));
    this.name = "AuthorizationError";
    this.reason = reason;
    this.status = statusForDenial(reason);
  }
}

/** Throwing form for server components and route handlers. */
export function requirePermission(
  context: AuthContext | null | undefined,
  permission: Permission | string,
  resource?: ResourceRef | null,
): asserts context is AuthContext {
  const decision = authorize(context, permission, resource);
  if (!decision.allowed) {
    throw new AuthorizationError(decision.reason);
  }
}
