/**
 * Session cookie naming and attributes.
 *
 * Defined explicitly rather than relying on Auth.js's default, because the
 * guard in `session.ts` reads this cookie directly to check server-side
 * revocation. If the two disagreed on the name, every request would look
 * signed out. One constant, used by both, removes the guess.
 */

export const SESSION_COOKIE_BASE_NAME = "authjs.session-token";

/** `__Secure-` is only valid on HTTPS, so it tracks the deployment scheme. */
export function sessionCookieName(useSecureCookies: boolean): string {
  return useSecureCookies ? `__Secure-${SESSION_COOKIE_BASE_NAME}` : SESSION_COOKIE_BASE_NAME;
}

/**
 * Whether to use secure cookies.
 *
 * Cloud Run always serves HTTPS, so anything that is not an explicit local
 * HTTP URL gets the secure treatment. Defaulting to secure means a
 * misconfiguration fails closed - the cookie is not sent - rather than
 * silently transmitting a session token over plaintext.
 */
export function shouldUseSecureCookies(authUrl: string | undefined): boolean {
  if (!authUrl) {
    return true;
  }
  return !authUrl.trim().toLowerCase().startsWith("http://");
}

export interface SessionCookieOptions {
  readonly httpOnly: true;
  readonly sameSite: "lax";
  readonly path: "/";
  readonly secure: boolean;
}

/**
 * `sameSite: "lax"` rather than `"strict"`: the OAuth provider redirects back
 * with a top-level GET, and a strict cookie would not be sent on that
 * navigation, breaking sign-in. Auth.js's own CSRF token protects state-changing
 * requests.
 */
export function sessionCookieOptions(useSecureCookies: boolean): SessionCookieOptions {
  return {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    secure: useSecureCookies,
  };
}
