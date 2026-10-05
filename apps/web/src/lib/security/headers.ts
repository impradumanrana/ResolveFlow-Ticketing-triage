/**
 * Response security headers, as data.
 *
 * Framework-free so the offline test build can assert the policy itself rather
 * than a rendered page: a Content-Security-Policy that nobody checks is a
 * string, not a control.
 *
 * The policy is strict because this application can afford to be. It loads no
 * external script, font, image or iframe, and uses no inline `style`
 * attribute, so `default-src 'none'` with a per-request nonce is achievable
 * rather than aspirational. Every allowance below names what needs it.
 */

/** Google's sign-in origin. A form POST to Auth.js redirects here. */
export const GOOGLE_SIGN_IN_ORIGIN = "https://accounts.google.com";

/** Two years, the minimum for preload lists, and long enough to matter. */
export const HSTS_MAX_AGE_SECONDS = 63_072_000;

/**
 * Build the policy for one response.
 *
 * `nonce` is minted per request. Next applies it to its own bootstrap scripts,
 * and `'strict-dynamic'` extends that trust to the chunks they load - which is
 * what makes a host allowlist unnecessary, and unnecessary allowlists are
 * where CSP bypasses live.
 */
export function contentSecurityPolicy(nonce: string, options: { secure: boolean }): string {
  const directives: string[] = [
    // Nothing loads unless a directive below says so.
    "default-src 'none'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    // Stylesheets are emitted as files by the build; no inline style is used.
    "style-src 'self'",
    // `data:` covers the inlined SVG icon route.
    "img-src 'self' data:",
    "font-src 'self'",
    // Server actions and route handlers post back to this origin only.
    "connect-src 'self'",
    // The sign-in form posts to Auth.js here, which then redirects to Google.
    // Chrome applies form-action to that redirect, so the origin is named.
    `form-action 'self' ${GOOGLE_SIGN_IN_ORIGIN}`,
    // The modern equivalent of X-Frame-Options: DENY, which is also still sent
    // for browsers that only understand that header.
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "object-src 'none'",
    "manifest-src 'self'",
  ];
  if (options.secure) {
    // Only over HTTPS: on a local HTTP origin this would break every request.
    directives.push("upgrade-insecure-requests");
  }
  return directives.join("; ");
}

/**
 * Every security header this application sets, for one response.
 *
 * `secure` follows the deployment's own view of whether it is served over
 * HTTPS, so a local HTTP development server does not advertise HSTS - a
 * browser that caches that for a local origin is painful to undo.
 */
export function securityHeaders(options: {
  nonce: string;
  secure: boolean;
}): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Security-Policy": contentSecurityPolicy(options.nonce, {
      secure: options.secure,
    }),
    "X-Content-Type-Options": "nosniff",
    // Redundant with frame-ancestors, kept for browsers without CSP3.
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy":
      "camera=(), microphone=(), geolocation=(), payment=(), usb=(), " +
      "display-capture=(), serial=(), midi=(), bluetooth=()",
    "X-Permitted-Cross-Domain-Policies": "none",
  };
  if (options.secure) {
    headers["Strict-Transport-Security"] =
      `max-age=${HSTS_MAX_AGE_SECONDS}; includeSubDomains; preload`;
  }
  return headers;
}
