/**
 * Mailbox connection routes: open redirects, reflected input, cross-origin
 * requests, and the rule that the browser can never name the mailbox a
 * callback completes.
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  callbackResultPath,
  isGoogleAuthorizationUrl,
  isSameOrigin,
  isValidMailboxId,
  mailboxResultCategory,
} from "../.test-build/lib/mailbox/redirects.js";

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), "utf8");

const START_ROUTE = "app/api/mailboxes/[mailboxId]/connect/route.ts";
const CALLBACK_ROUTE = "app/api/mailboxes/oauth/callback/route.ts";
const REVOKE_ROUTE = "app/api/mailboxes/[mailboxId]/connection/route.ts";

// -------------------------------------------------------------------------
// Open redirect
// -------------------------------------------------------------------------

test("Google's authorization endpoint is accepted", () => {
  assert.equal(
    isGoogleAuthorizationUrl("https://accounts.google.com/o/oauth2/v2/auth?client_id=x&state=y"),
    true,
  );
});

test("anything that is not exactly Google's endpoint is refused", () => {
  for (const url of [
    "http://accounts.google.com/o/oauth2/v2/auth",
    "https://accounts.google.com.attacker.example/o/oauth2/v2/auth",
    "https://attacker.example/o/oauth2/v2/auth?accounts.google.com",
    "https://accounts.google.com@attacker.example/o/oauth2/v2/auth",
    "https://user:pass@accounts.google.com/o/oauth2/v2/auth",
    "https://accounts.google.com:8443/o/oauth2/v2/auth",
    "https://accounts.google.com/signin/elsewhere",
    "https://evil-accounts.google.com/o/oauth2/v2/auth",
    "javascript:alert(1)",
    "//accounts.google.com/o/oauth2/v2/auth",
    "/o/oauth2/v2/auth",
    "",
    null,
    42,
  ]) {
    assert.equal(isGoogleAuthorizationUrl(url), false, `accepted ${String(url)}`);
  }
});

test("an oversized URL is refused", () => {
  const long = `https://accounts.google.com/o/oauth2/v2/auth?pad=${"a".repeat(5000)}`;
  assert.equal(isGoogleAuthorizationUrl(long), false);
});

// -------------------------------------------------------------------------
// Reflected input
// -------------------------------------------------------------------------

test("outcome codes collapse to a fixed set of categories", () => {
  assert.equal(mailboxResultCategory("CONNECTED"), "connected");
  assert.equal(mailboxResultCategory("MAILBOX_SCOPE_DENIED"), "denied");
  assert.equal(mailboxResultCategory("CONSENT_DENIED"), "denied");
  assert.equal(mailboxResultCategory("WRONG_MAILBOX_AUTHORIZED"), "wrong-account");
  assert.equal(mailboxResultCategory("ATTEMPT_EXPIRED"), "expired");
  assert.equal(mailboxResultCategory("WRITE_SCOPE_GRANTED"), "failed");
  assert.equal(mailboxResultCategory(null), "failed");
});

test("the result path never contains caller-supplied text", () => {
  for (const hostile of [
    "<script>alert(1)</script>",
    "CONNECTED&next=https://attacker.example",
    "../../admin",
    "javascript:alert(1)",
    "%0d%0aSet-Cookie:x=y",
  ]) {
    const path = callbackResultPath(hostile);
    assert.equal(path, "/workspace?mailbox=failed", `reflected ${hostile}`);
  }
});

test("every possible result path stays on this site", () => {
  for (const code of ["CONNECTED", "ATTEMPT_EXPIRED", "anything"]) {
    const path = callbackResultPath(code);
    assert.ok(path.startsWith("/workspace?mailbox="));
    assert.equal(new URL(path, "https://support.acme.example").host, "support.acme.example");
  }
});

// -------------------------------------------------------------------------
// Cross-origin and input validation
// -------------------------------------------------------------------------

test("same-origin requests are recognised", () => {
  assert.equal(
    isSameOrigin("https://support.acme.example", "https://support.acme.example/api/mailboxes/x/connect"),
    true,
  );
});

test("cross-origin, missing, and malformed origins are refused", () => {
  const target = "https://support.acme.example/api/mailboxes/x/connection";
  for (const origin of [
    "https://attacker.example",
    "http://support.acme.example",
    "https://support.acme.example:8443",
    "https://support.acme.example.attacker.example",
    "null",
    "",
    null,
    undefined,
  ]) {
    assert.equal(isSameOrigin(origin, target), false, `accepted origin ${String(origin)}`);
  }
});

test("mailbox ids are validated before reaching the internal URL", () => {
  assert.equal(isValidMailboxId("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"), true);
  for (const id of ["", "a", "../connection/start", "x/y", "id?role=OWNER", "id%2F..", " spaced", null]) {
    assert.equal(isValidMailboxId(id), false, `accepted ${String(id)}`);
  }
});

// -------------------------------------------------------------------------
// Route structure
// -------------------------------------------------------------------------

test("the start route checks the authorization URL before redirecting", async () => {
  const text = await source(START_ROUTE);
  const guard = text.indexOf("isGoogleAuthorizationUrl(");
  const redirect = text.indexOf("NextResponse.redirect(");
  assert.ok(guard > 0, "no authorization URL guard");
  assert.ok(redirect > guard, "redirect happens before the guard");
});

test("state-changing routes refuse cross-origin requests first", async () => {
  for (const route of [START_ROUTE, REVOKE_ROUTE]) {
    const text = await source(route);
    const originCheck = text.indexOf("isSameOrigin(");
    const guard = text.indexOf("requireAccess(");
    assert.ok(originCheck > 0, `${route} has no origin check`);
    assert.ok(originCheck < guard, `${route} authorizes before checking origin`);
  }
});

test("every mailbox route is guarded by the connect permission", async () => {
  for (const route of [START_ROUTE, CALLBACK_ROUTE, REVOKE_ROUTE]) {
    const text = await source(route);
    assert.match(text, /require(Page)?Access\("mailbox\.connect"\)/, `${route} is unguarded`);
  }
});

test("the callback forwards only state and code, and never names a mailbox", async () => {
  const text = await source(CALLBACK_ROUTE);
  assert.match(text, /body: \{ state, code \}/);
  assert.equal(/mailbox[_]?id/i.test(text.replace(/\/\*[\s\S]*?\*\//g, "")), false);
  assert.equal(text.includes('searchParams.get("mailbox'), false);
});

test("the callback never reflects its query string into the redirect", async () => {
  const text = await source(CALLBACK_ROUTE);
  for (const match of text.matchAll(/NextResponse\.redirect\(([^;]+)\)/g)) {
    assert.match(match[1], /callbackResultPath\(/, `unguarded redirect: ${match[1]}`);
  }
});

test("the callback is not a public path", async () => {
  const middleware = await source("middleware.ts");
  const publicPaths = middleware.match(/const PUBLIC_PATHS = \[(.*?)\]/s)?.[1] ?? "";
  assert.equal(publicPaths.includes("/api/mailboxes"), false);
});
