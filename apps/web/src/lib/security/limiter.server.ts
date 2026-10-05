import "server-only";

import { createHash } from "node:crypto";

import { query } from "../identity/db";
import {
  COUNT_SQL,
  POLICIES,
  RateLimitExceededError,
  RateLimitUnavailableError,
  retryAfterSeconds,
  windowStart,
} from "./rate-limit";

/**
 * The database half of the limiter.
 *
 * Keys are hashed before they are stored, so the counter table holds no
 * membership id, mailbox id or address - a control that exists to protect the
 * system should not become another store of personal data, and these rows then
 * fall outside an erasure request.
 *
 * Fails closed. Every action this guards already needs the same database, so
 * refusing when the count cannot be taken removes no availability that was not
 * already gone.
 */
export function hashKey(value: string): string {
  return createHash("sha256").update(value.trim().toLowerCase(), "utf8").digest("hex");
}

export async function enforceRateLimit(
  policyName: keyof typeof POLICIES | string,
  key: string,
  options: { organizationId: string; now?: Date },
): Promise<number> {
  const policy = POLICIES[policyName];
  if (!policy) {
    throw new Error(`Unknown rate limit policy: ${policyName}`);
  }
  const moment = options.now ?? new Date();

  let count: number;
  try {
    const rows = await query<{ request_count: number }>(COUNT_SQL, [
      options.organizationId,
      policy.name,
      hashKey(key),
      windowStart(moment, policy.windowSeconds),
    ]);
    count = Number(rows[0]?.request_count ?? 0);
  } catch {
    throw new RateLimitUnavailableError(policy);
  }

  if (count > policy.limit) {
    throw new RateLimitExceededError(policy, retryAfterSeconds(policy, moment));
  }
  return count;
}
