/**
 * Security and authorization audit events.
 *
 * Every event is attributable to an actor and an outcome. The record is
 * append-only in the database (see migration 20260915_0002): the application
 * identity can insert and read, and cannot update or delete.
 *
 * What is deliberately not recorded: message bodies, knowledge content, tokens,
 * secrets, raw IP addresses, or model reasoning. An audit trail that contains
 * the data it is protecting is a second copy of the risk.
 */

export const AUDIT_ACTIONS = [
  "auth.sign_in",
  "auth.sign_in_refused",
  "auth.sign_out",
  "auth.session_revoked",
  "authz.denied",
  "member.invited",
  "member.role_changed",
  "member.revoked",
  "organization.bootstrapped",
] as const;

export type AuditAction = (typeof AUDIT_ACTIONS)[number];

export type AuditOutcome = "ALLOWED" | "DENIED" | "FAILED";

export interface AuditEvent {
  readonly organizationId: string | null;
  readonly actorUserId: string | null;
  readonly actorMembershipId: string | null;
  /** Recorded even when no user row exists, so refusals are still attributable. */
  readonly actorEmail: string | null;
  readonly action: AuditAction;
  readonly outcome: AuditOutcome;
  readonly reasonCode: string | null;
  readonly targetType: string | null;
  readonly targetId: string | null;
  readonly requestId: string | null;
  readonly sourceIpHash: string | null;
  readonly userAgent: string | null;
  readonly metadata: Readonly<Record<string, string | number | boolean | null>>;
}

export interface AuditSink {
  record(event: AuditEvent): Promise<void>;
}

/** Fields that must never appear in audit metadata, whatever a caller passes. */
const FORBIDDEN_METADATA_KEYS = [
  "password",
  "token",
  "secret",
  "api_key",
  "apikey",
  "authorization",
  "cookie",
  "session",
  "refresh_token",
  "access_token",
  "id_token",
  "body",
  "message",
  "content",
] as const;

/**
 * Strip anything credential- or content-shaped before it is written.
 *
 * A denylist is normally the weaker choice, but audit metadata is written by
 * many call sites over time and this is a cheap backstop. Values are also
 * truncated: an audit row is evidence, not a log drain.
 */
export function sanitizeMetadata(
  metadata: Readonly<Record<string, unknown>>,
): Record<string, string | number | boolean | null> {
  const safe: Record<string, string | number | boolean | null> = {};

  for (const [key, value] of Object.entries(metadata)) {
    const lowered = key.toLowerCase();
    if (FORBIDDEN_METADATA_KEYS.some((forbidden) => lowered.includes(forbidden))) {
      safe[key] = "[redacted]";
      continue;
    }

    if (value === null || typeof value === "boolean" || typeof value === "number") {
      safe[key] = value;
      continue;
    }

    if (typeof value === "string") {
      safe[key] = value.length > 256 ? `${value.slice(0, 253)}...` : value;
      continue;
    }

    safe[key] = "[unsupported]";
  }

  return safe;
}

/** Hash a source address: enough to correlate an incident, not to profile. */
export async function hashSourceIp(
  ip: string | null | undefined,
  salt: string,
): Promise<string | null> {
  if (!ip || !ip.trim() || !salt) {
    return null;
  }
  const data = new TextEncoder().encode(`${salt}:${ip.trim()}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
