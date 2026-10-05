/**
 * Response security headers and the rate limit mirror (C13).
 *
 * Both are tested as data. A Content-Security-Policy nobody asserts is a
 * string rather than a control, and a limit that disagrees with the Python
 * tier's copy is two different limits.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  GOOGLE_SIGN_IN_ORIGIN,
  HSTS_MAX_AGE_SECONDS,
  contentSecurityPolicy,
  securityHeaders,
} from "../.test-build/lib/security/headers.js";
import {
  COUNT_SQL,
  POLICIES,
  RateLimitExceededError,
  RateLimitUnavailableError,
  retryAfterSeconds,
  windowStart,
} from "../.test-build/lib/security/rate-limit.js";

const NONCE = "0123456789abcdef0123456789abcdef";

function directives(policy) {
  return new Map(
    policy.split("; ").map((part) => {
      const [name, ...values] = part.split(" ");
      return [name, values];
    }),
  );
}

// ---------------------------------------------------------------------------
// Content-Security-Policy
// ---------------------------------------------------------------------------

test("nothing loads unless a directive allows it", () => {
  const found = directives(contentSecurityPolicy(NONCE, { secure: true }));
  assert.deepEqual(found.get("default-src"), ["'none'"]);
});

test("scripts are allowed only by nonce, and the nonce is the one supplied", () => {
  const found = directives(contentSecurityPolicy(NONCE, { secure: true }));
  const script = found.get("script-src");

  assert.ok(script.includes(`'nonce-${NONCE}'`));
  assert.ok(script.includes("'strict-dynamic'"));
  // The two that would undo the policy.
  assert.ok(!script.includes("'unsafe-inline'"));
  assert.ok(!script.includes("'unsafe-eval'"));
});

test("no directive permits inline styles or arbitrary hosts", () => {
  const policy = contentSecurityPolicy(NONCE, { secure: true });

  assert.ok(!policy.includes("'unsafe-inline'"));
  assert.ok(!policy.includes("'unsafe-eval'"));
  assert.ok(!policy.includes("*"), "a wildcard source defeats the policy");
  // `http:` would allow a downgrade; `data:` is confined to images.
  assert.ok(!policy.includes("http:"));
  assert.equal((policy.match(/data:/g) ?? []).length, 1);
  assert.ok(directives(policy).get("img-src").includes("data:"));
});

test("framing and base-uri are closed, which is what stops clickjacking and base hijacking", () => {
  const found = directives(contentSecurityPolicy(NONCE, { secure: true }));

  assert.deepEqual(found.get("frame-ancestors"), ["'none'"]);
  assert.deepEqual(found.get("base-uri"), ["'none'"]);
  assert.deepEqual(found.get("object-src"), ["'none'"]);
});

test("form-action allows this origin and Google's sign-in, and nothing else", () => {
  const found = directives(contentSecurityPolicy(NONCE, { secure: true }));

  assert.deepEqual(found.get("form-action"), ["'self'", GOOGLE_SIGN_IN_ORIGIN]);
  assert.ok(GOOGLE_SIGN_IN_ORIGIN.startsWith("https://"));
});

test("upgrade-insecure-requests is sent only over HTTPS", () => {
  assert.ok(contentSecurityPolicy(NONCE, { secure: true }).includes("upgrade-insecure-requests"));
  // On a local HTTP origin it would break every request.
  assert.ok(!contentSecurityPolicy(NONCE, { secure: false }).includes("upgrade-insecure-requests"));
});

test("a different nonce produces a different policy", () => {
  const a = contentSecurityPolicy("a".repeat(32), { secure: true });
  const b = contentSecurityPolicy("b".repeat(32), { secure: true });
  assert.notEqual(a, b);
});

// ---------------------------------------------------------------------------
// The rest of the headers
// ---------------------------------------------------------------------------

test("every header this application relies on is present", () => {
  const headers = securityHeaders({ nonce: NONCE, secure: true });

  for (const name of [
    "Content-Security-Policy",
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Cross-Origin-Opener-Policy",
    "Cross-Origin-Resource-Policy",
    "Permissions-Policy",
    "X-Permitted-Cross-Domain-Policies",
  ]) {
    assert.ok(headers[name], `${name} is missing`);
  }
});

test("HSTS is sent only over HTTPS, and lasts long enough to matter", () => {
  const secure = securityHeaders({ nonce: NONCE, secure: true });
  assert.equal(
    secure["Strict-Transport-Security"],
    `max-age=${HSTS_MAX_AGE_SECONDS}; includeSubDomains; preload`,
  );
  assert.ok(HSTS_MAX_AGE_SECONDS >= 31_536_000, "a preload-eligible max-age is at least a year");

  // A browser that caches HSTS for a local HTTP origin is painful to undo.
  const insecure = securityHeaders({ nonce: NONCE, secure: false });
  assert.equal(insecure["Strict-Transport-Security"], undefined);
});

test("powerful browser features are denied rather than left to default", () => {
  const policy = securityHeaders({ nonce: NONCE, secure: true })["Permissions-Policy"];
  for (const feature of ["camera", "microphone", "geolocation", "payment"]) {
    assert.ok(policy.includes(`${feature}=()`), feature);
  }
});

test("no header value is empty", () => {
  for (const secure of [true, false]) {
    for (const [name, value] of Object.entries(securityHeaders({ nonce: NONCE, secure }))) {
      assert.ok(typeof value === "string" && value.length > 0, name);
    }
  }
});

// ---------------------------------------------------------------------------
// Rate limits
// ---------------------------------------------------------------------------

test("every policy is well formed", () => {
  for (const [name, policy] of Object.entries(POLICIES)) {
    assert.equal(policy.name, name, "a policy's key and name must agree");
    assert.ok(policy.limit > 0);
    assert.ok(policy.windowSeconds > 0);
    assert.ok(["ORGANIZATION", "MEMBERSHIP", "MAILBOX"].includes(policy.scope));
  }
});

test("a window is aligned to the clock, not to first use", () => {
  // Two instances must derive the same bucket from the same timestamp.
  const a = windowStart(new Date("2026-10-05T09:00:37.500Z"), 60);
  const b = windowStart(new Date("2026-10-05T09:00:02.000Z"), 60);

  assert.equal(a.toISOString(), "2026-10-05T09:00:00.000Z");
  assert.equal(a.getTime(), b.getTime());
  assert.equal(
    windowStart(new Date("2026-10-05T09:01:00.000Z"), 60).toISOString(),
    "2026-10-05T09:01:00.000Z",
  );
});

test("an hour-long window aligns to the hour", () => {
  assert.equal(
    windowStart(new Date("2026-10-05T09:42:13.000Z"), 3600).toISOString(),
    "2026-10-05T09:00:00.000Z",
  );
});

test("retry-after counts whole seconds to the reset, and is never zero", () => {
  const policy = POLICIES.bulk_assign;

  assert.equal(retryAfterSeconds(policy, new Date("2026-10-05T09:00:00.000Z")), 60);
  assert.equal(retryAfterSeconds(policy, new Date("2026-10-05T09:00:59.500Z")), 1);
  // Exactly at the boundary the next window has already started.
  assert.ok(retryAfterSeconds(policy, new Date("2026-10-05T09:00:59.999Z")) >= 1);
});

test("the counting statement is a single atomic upsert", () => {
  // A read-then-write limiter does not limit anything under concurrency.
  assert.ok(COUNT_SQL.includes("ON CONFLICT"));
  assert.ok(COUNT_SQL.includes("request_count + 1"));
  assert.ok(COUNT_SQL.includes("RETURNING request_count"));
  assert.equal((COUNT_SQL.match(/INSERT INTO/g) ?? []).length, 1);
  assert.ok(!COUNT_SQL.includes("SELECT"), "no separate read");
});

test("the refusals carry the code the web tier shows", () => {
  const exceeded = new RateLimitExceededError(POLICIES.bulk_assign, 42);
  assert.equal(exceeded.code, "RATE_LIMITED");
  assert.equal(exceeded.retryAfterSeconds, 42);

  const unavailable = new RateLimitUnavailableError(POLICIES.bulk_assign);
  assert.equal(unavailable.code, "RATE_LIMIT_UNAVAILABLE");
  assert.notEqual(exceeded.code, unavailable.code);
});

test("the headers embed the nonce they were given, not one of their own", () => {
  // The gap this closes: every other test here calls `contentSecurityPolicy`
  // directly, so `securityHeaders` ignoring its nonce argument survived a
  // mutation run. A hardcoded nonce would make every response share one
  // predictable value, which is no protection at all.
  const first = securityHeaders({ nonce: "a".repeat(32), secure: true });
  const second = securityHeaders({ nonce: "b".repeat(32), secure: true });

  assert.ok(first["Content-Security-Policy"].includes(`'nonce-${"a".repeat(32)}'`));
  assert.ok(second["Content-Security-Policy"].includes(`'nonce-${"b".repeat(32)}'`));
  assert.notEqual(first["Content-Security-Policy"], second["Content-Security-Policy"]);
});

test("the headers carry exactly one nonce", () => {
  const policy = securityHeaders({ nonce: NONCE, secure: true })["Content-Security-Policy"];
  const nonces = policy.match(/'nonce-[^']+'/g) ?? [];

  assert.equal(nonces.length, 1);
  assert.equal(nonces[0], `'nonce-${NONCE}'`);
});
