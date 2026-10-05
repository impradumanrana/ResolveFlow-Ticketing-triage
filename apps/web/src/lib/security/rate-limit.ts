/**
 * Rate limit policies, mirrored from `app/security/rate_limit.py`.
 *
 * Mutations happen in two tiers: the internal API writes through Python, and
 * the workspace writes a few things - bulk assignment - directly. So the
 * limiter exists twice over one table, and a Python test asserts these numbers
 * equal the ones in `POLICIES` there. The same arrangement as C03's role
 * matrix: two runtimes, one source of truth, one test that fails if they
 * drift.
 *
 * Framework-free: the arithmetic is tested offline, and the database call
 * lives in `limiter.server.ts`.
 */

export type RateLimitScope = "ORGANIZATION" | "MEMBERSHIP" | "MAILBOX";

export interface RateLimitPolicy {
  readonly name: string;
  readonly limit: number;
  readonly windowSeconds: number;
  readonly scope: RateLimitScope;
}

/** Only the policies this tier enforces. The rest are the API's. */
export const POLICIES: Readonly<Record<string, RateLimitPolicy>> = Object.freeze({
  bulk_assign: Object.freeze({
    name: "bulk_assign",
    limit: 10,
    windowSeconds: 60,
    scope: "MEMBERSHIP" as const,
  }),
});

export class RateLimitExceededError extends Error {
  readonly code = "RATE_LIMITED";
  constructor(
    readonly policy: RateLimitPolicy,
    readonly retryAfterSeconds: number,
  ) {
    super(policy.name);
    this.name = "RateLimitExceededError";
  }
}

export class RateLimitUnavailableError extends Error {
  readonly code = "RATE_LIMIT_UNAVAILABLE";
  constructor(readonly policy: RateLimitPolicy) {
    super(policy.name);
    this.name = "RateLimitUnavailableError";
  }
}

/**
 * The clock-aligned bucket a moment belongs to.
 *
 * Aligned to the epoch, not to first use, so this tier and the Python tier
 * derive the same window from the same timestamp without coordinating.
 */
export function windowStart(moment: Date, windowSeconds: number): Date {
  const seconds = Math.floor(moment.getTime() / 1000);
  return new Date((seconds - (seconds % windowSeconds)) * 1000);
}

/** Whole seconds until this caller's allowance resets. */
export function retryAfterSeconds(policy: RateLimitPolicy, moment: Date): number {
  const next = windowStart(moment, policy.windowSeconds).getTime() + policy.windowSeconds * 1000;
  return Math.max(1, Math.ceil((next - moment.getTime()) / 1000));
}

/**
 * The upsert both tiers run. One statement, so the count is correct under
 * concurrency; a read-then-write limiter does not limit anything when it
 * matters.
 */
export const COUNT_SQL = `
  INSERT INTO rate_limit_counters
    (organization_id, policy, key_hash, window_start, request_count)
  VALUES ($1::uuid, $2, $3, $4, 1)
  ON CONFLICT (organization_id, policy, key_hash, window_start)
  DO UPDATE SET request_count = rate_limit_counters.request_count + 1, updated_at = now()
  RETURNING request_count
`;
