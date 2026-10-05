/**
 * Pure guards for the mailbox connection routes.
 *
 * Framework-free so the offline test build can exercise them. Each guards a
 * specific way a connection flow goes wrong in a browser:
 *
 * - An open redirect: the BFF redirects the person to whatever URL the internal
 *   API returned. If that service were misconfigured or compromised, an
 *   unchecked redirect would send an administrator to a lookalike consent page.
 * - Reflected input: the OAuth callback carries attacker-influenceable query
 *   parameters. Nothing from them is echoed; outcomes collapse to a fixed set.
 * - Cross-site requests: disconnecting a mailbox is a state change, and a
 *   same-site cookie is defence in depth, not a guarantee.
 */

export const GOOGLE_AUTHORIZATION_HOST = "accounts.google.com";

export type MailboxResultCategory =
  | "connected"
  | "denied"
  | "wrong-account"
  | "expired"
  | "failed";

const MAILBOX_ID = /^[A-Za-z0-9][A-Za-z0-9-]{2,79}$/;

export function isValidMailboxId(value: string | null | undefined): value is string {
  return typeof value === "string" && MAILBOX_ID.test(value);
}

/** Only Google's own authorization endpoint, over HTTPS, exactly. */
export function isGoogleAuthorizationUrl(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 4096) {
    return false;
  }
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return false;
  }
  return (
    parsed.protocol === "https:" &&
    parsed.hostname === GOOGLE_AUTHORIZATION_HOST &&
    parsed.port === "" &&
    parsed.username === "" &&
    parsed.password === "" &&
    parsed.pathname.startsWith("/o/oauth2/")
  );
}

const CATEGORY_BY_CODE: Readonly<Record<string, MailboxResultCategory>> = {
  CONNECTED: "connected",
  MAILBOX_SCOPE_DENIED: "denied",
  IDENTITY_SCOPE_DENIED: "denied",
  CONSENT_DENIED: "denied",
  WRONG_MAILBOX_AUTHORIZED: "wrong-account",
  ATTEMPT_EXPIRED: "expired",
};

/** Collapse any outcome code - including an unknown one - to a fixed category. */
export function mailboxResultCategory(code: string | null | undefined): MailboxResultCategory {
  if (!code) {
    return "failed";
  }
  return CATEGORY_BY_CODE[code] ?? "failed";
}

/** Where the person lands after the callback. Never contains request input. */
export function callbackResultPath(code: string | null | undefined): string {
  return `/workspace?mailbox=${mailboxResultCategory(code)}`;
}

/**
 * True only when the request's Origin matches the URL it was made to.
 * A missing Origin is refused: browsers send one on cross-origin and
 * state-changing requests, so its absence is not evidence of safety.
 */
export function isSameOrigin(origin: string | null | undefined, requestUrl: string): boolean {
  if (!origin) {
    return false;
  }
  try {
    return new URL(origin).origin === new URL(requestUrl).origin;
  } catch {
    return false;
  }
}
