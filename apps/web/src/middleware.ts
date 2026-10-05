/**
 * Edge gate.
 *
 * This is a first filter, not the authorization boundary. It rejects requests
 * with no session cookie so unauthenticated traffic never reaches application
 * code, and it sets security headers. It deliberately does NOT decide roles or
 * organizations: the presence of a cookie proves nothing, and the real decision
 * needs the database. Every protected page and route still calls
 * `requirePageAccess` or `requireAccess`.
 */

import { NextResponse, type NextRequest } from "next/server";

import { sessionCookieName, shouldUseSecureCookies } from "@/lib/auth/cookies";
import { securityHeaders } from "@/lib/security/headers";

const PUBLIC_PATHS = ["/signin", "/access-denied", "/api/auth", "/api/health"];

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  );
}

/**
 * One nonce per request, from the platform's own CSPRNG.
 *
 * `crypto.randomUUID` is available on the edge runtime and gives 122 bits of
 * randomness, which is far more than a nonce needs; what matters is that it is
 * unpredictable and never reused across responses.
 */
function mintNonce(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

function applySecurityHeaders(response: NextResponse, nonce: string, secure: boolean) {
  for (const [name, value] of Object.entries(securityHeaders({ nonce, secure }))) {
    response.headers.set(name, value);
  }
  return response;
}

export function middleware(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;
  const secure = shouldUseSecureCookies(process.env.AUTH_URL ?? process.env.NEXTAUTH_URL);
  const nonce = mintNonce();

  // The nonce travels on the *request* as well, which is how Next finds it and
  // stamps it onto its own bootstrap scripts. Without this the strict policy
  // would block the framework's own code.
  const forwarded = new Headers(request.headers);
  forwarded.set("x-nonce", nonce);

  const proceed = () =>
    applySecurityHeaders(NextResponse.next({ request: { headers: forwarded } }), nonce, secure);

  if (isPublic(pathname)) {
    return proceed();
  }

  const cookieName = sessionCookieName(secure);

  if (!request.cookies.get(cookieName)?.value) {
    if (pathname.startsWith("/api/")) {
      return applySecurityHeaders(
        NextResponse.json(
          { error: "unauthenticated", message: "Sign in to continue." },
          { status: 401 },
        ),
        nonce,
        secure,
      );
    }
    const signIn = new URL("/signin", request.url);
    signIn.searchParams.set("next", pathname);
    return applySecurityHeaders(NextResponse.redirect(signIn), nonce, secure);
  }

  return proceed();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
