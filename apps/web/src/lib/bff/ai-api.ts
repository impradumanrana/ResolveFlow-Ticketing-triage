import "server-only";

/**
 * The only path from the browser to the private Python API.
 *
 * Organization and request context are attached here from the server-derived
 * `AuthContext`. Nothing from the incoming browser request is forwarded: a
 * caller cannot choose the organization it is treated as by setting a header,
 * a cookie, or a body field, because none of those are read.
 */

import { randomUUID } from "node:crypto";

import type { AuthContext } from "../authz/authorize";

export const ORGANIZATION_HEADER = "X-Resolveflow-Organization-Id";
export const MEMBERSHIP_HEADER = "X-Resolveflow-Membership-Id";
export const ROLE_HEADER = "X-Resolveflow-Role";
export const REQUEST_ID_HEADER = "X-Request-Id";

export interface AiApiCall {
  readonly path: string;
  readonly method?: "GET" | "POST" | "PUT" | "DELETE";
  readonly body?: unknown;
  readonly timeoutMs?: number;
}

export interface AiApiResult<T> {
  readonly ok: boolean;
  readonly status: number;
  readonly requestId: string;
  readonly data: T | null;
}

/**
 * Build the outbound headers for a call.
 *
 * Exported separately so the "the browser cannot influence this" property is
 * directly testable without a server or a network.
 */
export function buildInternalHeaders(
  context: AuthContext,
  internalToken: string,
  requestId: string,
): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${internalToken}`,
    // Every value below comes from the AuthContext the server derived from the
    // database, so an action reaching the AI service is attributable to a
    // person without that service needing its own view of membership.
    [ORGANIZATION_HEADER]: context.organizationId,
    [MEMBERSHIP_HEADER]: context.membershipId,
    [ROLE_HEADER]: context.role,
    [REQUEST_ID_HEADER]: requestId,
  };
}

export async function callAiApi<T>(
  context: AuthContext,
  call: AiApiCall,
): Promise<AiApiResult<T>> {
  const baseUrl = process.env.AI_API_BASE_URL?.trim();
  const internalToken = process.env.RESOLVEFLOW_INTERNAL_API_TOKEN?.trim();
  const requestId = randomUUID();

  if (!baseUrl || !internalToken) {
    // Fail closed and visibly rather than calling an unauthenticated endpoint.
    return { ok: false, status: 503, requestId, data: null };
  }

  try {
    const response = await fetch(new URL(call.path, baseUrl), {
      method: call.method ?? "GET",
      cache: "no-store",
      headers: buildInternalHeaders(context, internalToken, requestId),
      body: call.body === undefined ? undefined : JSON.stringify(call.body),
      signal: AbortSignal.timeout(call.timeoutMs ?? 30_000),
    });

    const data = response.ok ? ((await response.json()) as T) : null;
    return { ok: response.ok, status: response.status, requestId, data };
  } catch {
    return { ok: false, status: 502, requestId, data: null };
  }
}
