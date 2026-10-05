/**
 * Boundary tests: cookies, audit redaction, and the browser-to-Python path.
 *
 * The rule under test throughout is that nothing the browser controls reaches
 * an authorization decision or the private API's tenant context.
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  SESSION_COOKIE_BASE_NAME,
  sessionCookieName,
  sessionCookieOptions,
  shouldUseSecureCookies,
} from "../.test-build/lib/auth/cookies.js";
import { sanitizeMetadata, hashSourceIp } from "../.test-build/lib/audit/events.js";

const sourceUrl = (path) => new URL(`../src/${path}`, import.meta.url);

// -------------------------------------------------------------------------
// Session cookie
// -------------------------------------------------------------------------

test("the cookie is __Secure- prefixed unless the deployment is explicitly http", () => {
  assert.equal(sessionCookieName(true), `__Secure-${SESSION_COOKIE_BASE_NAME}`);
  assert.equal(sessionCookieName(false), SESSION_COOKIE_BASE_NAME);

  assert.equal(shouldUseSecureCookies("https://support.client.example"), true);
  assert.equal(shouldUseSecureCookies("http://localhost:3000"), false);
  assert.equal(shouldUseSecureCookies("HTTP://localhost:3000"), false);

  // A missing or unparseable value must fail closed to secure.
  assert.equal(shouldUseSecureCookies(undefined), true);
  assert.equal(shouldUseSecureCookies(""), true);
  assert.equal(shouldUseSecureCookies("nonsense"), true);
});

test("the session cookie is httpOnly and not readable by script", () => {
  const options = sessionCookieOptions(true);
  assert.equal(options.httpOnly, true);
  assert.equal(options.secure, true);
  assert.equal(options.sameSite, "lax");
  assert.equal(options.path, "/");
});

test("the config and the request guard read the same cookie name", async () => {
  const config = await readFile(sourceUrl("lib/auth/config.ts"), "utf8");
  const session = await readFile(sourceUrl("lib/auth/session.ts"), "utf8");
  const middleware = await readFile(sourceUrl("middleware.ts"), "utf8");

  // All three derive the name from the shared helper rather than hardcoding it.
  for (const [name, source] of [
    ["config", config],
    ["session guard", session],
    ["middleware", middleware],
  ]) {
    assert.ok(
      source.includes("sessionCookieName("),
      `${name} does not use the shared cookie name helper`,
    );
    assert.equal(
      source.includes('"authjs.session-token"'),
      false,
      `${name} hardcodes the cookie name`,
    );
  }
});

// -------------------------------------------------------------------------
// Audit redaction
// -------------------------------------------------------------------------

test("credential-shaped metadata keys are redacted", () => {
  const safe = sanitizeMetadata({
    role: "AGENT",
    access_token: "ya29.secret",
    refreshToken: "1//secret",
    api_key: "sk-secret",
    Authorization: "Bearer secret",
    cookie: "session=abc",
    sessionToken: "abc",
    password: "hunter2",
  });

  assert.equal(safe.role, "AGENT");
  for (const key of [
    "access_token",
    "refreshToken",
    "api_key",
    "Authorization",
    "cookie",
    "sessionToken",
    "password",
  ]) {
    assert.equal(safe[key], "[redacted]", `${key} was not redacted`);
  }
});

test("message content is redacted rather than audited", () => {
  const safe = sanitizeMetadata({
    body: "customer's card number is ...",
    message: "confidential",
    content: "confidential",
  });
  assert.equal(safe.body, "[redacted]");
  assert.equal(safe.message, "[redacted]");
  assert.equal(safe.content, "[redacted]");
});

test("long values are truncated and objects are not serialised", () => {
  const safe = sanitizeMetadata({
    long: "x".repeat(1000),
    nested: { a: 1 },
    list: [1, 2, 3],
  });
  assert.equal(safe.long.length, 256);
  assert.ok(safe.long.endsWith("..."));
  assert.equal(safe.nested, "[unsupported]");
  assert.equal(safe.list, "[unsupported]");
});

test("scalars survive sanitisation unchanged", () => {
  const safe = sanitizeMetadata({ count: 3, ok: true, absent: null });
  assert.equal(safe.count, 3);
  assert.equal(safe.ok, true);
  assert.equal(safe.absent, null);
});

test("source addresses are hashed, never stored raw", async () => {
  const hash = await hashSourceIp("203.0.113.9", "salt-value");
  assert.match(hash, /^[0-9a-f]{64}$/);
  assert.equal(hash.includes("203.0.113.9"), false);

  // The same address hashes differently under a different salt.
  const other = await hashSourceIp("203.0.113.9", "different-salt");
  assert.notEqual(hash, other);

  // No salt or no address means no value, rather than a weak one.
  assert.equal(await hashSourceIp("203.0.113.9", ""), null);
  assert.equal(await hashSourceIp(null, "salt-value"), null);
  assert.equal(await hashSourceIp("", "salt-value"), null);
});

// -------------------------------------------------------------------------
// The browser cannot choose its organization
// -------------------------------------------------------------------------

test("outbound internal headers come only from the server-derived context", async () => {
  const { buildInternalHeaders, ORGANIZATION_HEADER, REQUEST_ID_HEADER } = await import(
    "../src/lib/bff/ai-api.ts"
  ).catch(() => ({}));

  // The module imports `server-only` and cannot be loaded here, so the
  // guarantee is asserted against the source instead.
  const source = await readFile(sourceUrl("lib/bff/ai-api.ts"), "utf8");

  assert.ok(source.includes("context.organizationId"));
  // No request, header, cookie, or body value is read when building the call.
  for (const forbidden of ["request.headers", "req.headers", "cookies()", "searchParams"]) {
    assert.equal(source.includes(forbidden), false, `ai-api reads ${forbidden}`);
  }
  assert.equal(buildInternalHeaders, undefined);
  assert.equal(ORGANIZATION_HEADER, undefined);
  assert.equal(REQUEST_ID_HEADER, undefined);
});

test("no protected route reads identity from the request", async () => {
  const routes = [
    "app/api/workspace/capabilities/route.ts",
    "app/workspace/page.tsx",
  ];

  for (const route of routes) {
    const source = await readFile(sourceUrl(route), "utf8");

    // Identity must come from the guard, never from the wire.
    assert.ok(
      source.includes("requireAccess(") || source.includes("requirePageAccess("),
      `${route} has no access guard`,
    );

    // Reading identity *from the wire*. Writing `organizationId` into a
    // response from the server-derived context is fine and expected, so the
    // patterns below describe the read side specifically.
    const forbiddenReads = [
      /\b(?:body|params|searchParams|query|payload)\s*[.[]\s*["']?organization/i,
      /\bsearchParams\.get\(\s*["'](?:role|organization[^"']*|membership[^"']*)["']/i,
      /\bheaders\(\)\.get\(\s*["']x-resolveflow-/i,
      /\brequest\.headers\.get\(\s*["'](?:x-resolveflow-|x-role)/i,
      /\bcookies\(\)\.get\(\s*["'](?:role|organization)/i,
    ];

    for (const pattern of forbiddenReads) {
      assert.equal(
        pattern.test(source),
        false,
        `${route} appears to read identity from the request: ${pattern}`,
      );
    }

    // And the identity it uses must be the one the guard returned, whether it
    // reads a field, passes it to `can`, or hands it to the internal API.
    assert.match(
      source,
      /const context = await require(Page)?Access\(/,
      `${route} does not capture the guard's context`,
    );
    assert.ok(
      (source.match(/\bcontext\b/g) ?? []).length >= 2,
      `${route} captures a context it never uses`,
    );
  }
});

test("the middleware treats only the intended paths as public", async () => {
  const source = await readFile(sourceUrl("middleware.ts"), "utf8");
  const match = source.match(/const PUBLIC_PATHS = \[(.*?)\]/s);
  assert.ok(match, "PUBLIC_PATHS not found");

  const paths = [...match[1].matchAll(/"([^"]+)"/g)].map((entry) => entry[1]).sort();
  assert.deepEqual(paths, ["/access-denied", "/api/auth", "/api/health", "/signin"]);
});

test("the middleware is a filter, not the authorization boundary", async () => {
  const source = await readFile(sourceUrl("middleware.ts"), "utf8");

  // It must not try to decide roles at the edge, where it has no database.
  for (const forbidden of ["roleHasPermission", "authorize(", "resolveIdentity"]) {
    assert.equal(source.includes(forbidden), false, `middleware calls ${forbidden}`);
  }
});

test("no client component imports a server-only identity module", async () => {
  for (const file of [
    "lib/identity/db.ts",
    "lib/identity/postgres-repository.ts",
    "lib/identity/factory.ts",
    "lib/auth/config.ts",
    "lib/auth/session.ts",
    "lib/bff/ai-api.ts",
    "lib/audit/sink.ts",
  ]) {
    const source = await readFile(sourceUrl(file), "utf8");
    if (file === "lib/identity/factory.ts") {
      // factory only re-exports; its dependency carries the guard.
      continue;
    }
    assert.ok(
      source.startsWith('import "server-only";'),
      `${file} is missing the server-only guard`,
    );
  }
});
