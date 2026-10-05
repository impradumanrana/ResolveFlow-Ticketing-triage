/**
 * AI provider settings: what an operator reads, and how the key is handled.
 *
 * The structural tests read the page and action sources. The rules they pin -
 * permission checks on the server, the key only in a request body, a password
 * field that is cleared - are the ones a refactor would most easily break.
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  BUDGET_WARNING_RATIO,
  KEY_FIELD,
  MAX_KEY_INPUT,
  PROVIDER_PATTERN,
  budgetUsage,
  formatMoney,
  readKeyInput,
  describeFailure,
  describeFallbacks,
  statusLabel,
  statusTone,
  verificationMessage,
} from "../.test-build/lib/ai-settings/view.js";

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), "utf8");

function provider(overrides = {}) {
  return {
    provider: "openai",
    status: "HEALTHY",
    credential_hint: "••••7788",
    region: "eu",
    models: { classification: "m1", generation: "m2", embedding: "m3" },
    fallback: [],
    approved_models: [],
    budget_minor_units: 10_000,
    currency: "USD",
    spent_minor_units: 0,
    reserved_minor_units: 0,
    last_verified_at: null,
    last_failure_code: null,
    last_failure_message: null,
    last_failure_at: null,
    ...overrides,
  };
}

// =========================================================================
// Status and money
// =========================================================================

test("each status has a label and an existing, contrast-checked badge", async () => {
  const css = await source("app/globals.css");
  for (const status of ["HEALTHY", "FAILING", "UNVERIFIED"]) {
    assert.notEqual(statusLabel(status), "Unknown");
    assert.match(css, new RegExp(`\\.ws-badge--${statusTone(status)}[ ,]`), status);
  }
  assert.equal(statusLabel("SOMETHING"), "Unknown");
});

test("money is shown in major units with the currency's own precision", () => {
  assert.equal(formatMoney(12_345, "USD"), "$123.45");
  assert.equal(formatMoney(0, "USD"), "$0.00");
  assert.equal(formatMoney(500, "JPY"), "¥500");
  assert.equal(formatMoney(1_000, "EUR"), "€10.00");
});

test("spend smaller than a cent is not shown as zero", () => {
  assert.equal(formatMoney(0.024, "USD"), "less than $0.01");
});

test("an unknown currency code still renders", () => {
  assert.equal(formatMoney(150, "ZZ"), "1.50 ZZ");
});

test("budget usage counts in-flight reservations and warns before exhaustion", () => {
  assert.equal(budgetUsage(provider({ spent_minor_units: 1_000 })).state, "OK");
  const near = budgetUsage(provider({ spent_minor_units: 7_000, reserved_minor_units: 1_000 }));
  assert.equal(near.state, "NEAR");
  assert.equal(near.percent, 80);
  assert.match(near.summary, /Nearly used up/);
  assert.equal(budgetUsage(provider({ spent_minor_units: 7_999 })).state, "OK");
  assert.equal(BUDGET_WARNING_RATIO, 0.8);

  const done = budgetUsage(provider({ spent_minor_units: 10_050 }));
  assert.equal(done.state, "EXHAUSTED");
  assert.equal(done.percent, 100);
  assert.match(done.summary, /paused/);
  assert.match(done.summary, /\$100\.50 of \$100\.00/);
});

test("a zero budget is exhausted, and no budget is explained as paused", () => {
  assert.equal(budgetUsage(provider({ budget_minor_units: 0 })).state, "EXHAUSTED");
  const unset = budgetUsage(provider({ budget_minor_units: null }));
  assert.equal(unset.state, "UNSET");
  assert.equal(unset.percent, null);
  assert.match(unset.summary, /paused/);
});

test("a problem is described in the gateway's words, with a count", () => {
  assert.equal(
    describeFailure({ code: "BUDGET_EXCEEDED", count: 5, message: "The monthly AI budget is used up." }),
    "The monthly AI budget is used up. (5 times)",
  );
  assert.equal(
    describeFailure({ code: "SOMETHING_NEW", count: 1, message: null }),
    "something new (1 time)",
  );
});

test("fallback use is reported, and silence means it did not happen", () => {
  assert.equal(describeFallbacks(0), null);
  assert.equal(describeFallbacks(-1), null);
  assert.match(describeFallbacks(1), /answered 1 time because the first choice was unavailable/);
  assert.match(describeFallbacks(4), /4 times/);
});

// =========================================================================
// Result messages
// =========================================================================

const result = (overrides = {}) => ({
  provider: "openai", ok: false, stored: false, code: null, message: null, checked_models: [], ...overrides,
});

test("a stored key says what was checked and that the old key is retired", () => {
  const message = verificationMessage(result({ ok: true, stored: true, checked_models: ["a", "b"] }), null);
  assert.match(message, /works for 2 models and has been stored/);
  assert.match(message, /previous key is no longer used/);
});

test("a failed check says nothing changed", () => {
  const message = verificationMessage(
    result({ code: "CREDENTIAL_INVALID", message: "The provider rejected the API key." }),
    null,
  );
  assert.equal(message, "The provider rejected the API key. Nothing was changed.");
});

test("transport and permission failures are distinguished", () => {
  assert.match(verificationMessage(null, 403), /permission/);
  assert.match(verificationMessage(null, 404), /not configured/);
  assert.match(verificationMessage(null, 502), /could not be reached\. Nothing was changed/);
  assert.match(verificationMessage(null, null), /could not be reached/);
});

// =========================================================================
// Key input
// =========================================================================

test("the key input is bounded and never echoed", () => {
  const key = "sk-proj-a-secret-key-that-must-not-echo";
  assert.deepEqual(readKeyInput(key), { ok: true, key });
  for (const bad of [null, undefined, 42, "", "   ", "x".repeat(MAX_KEY_INPUT + 1)]) {
    const outcome = readKeyInput(bad);
    assert.equal(outcome.ok, false);
    assert.ok(!String(outcome.message).includes("x".repeat(20)));
  }
  assert.equal(KEY_FIELD, "apiKey");
});

test("provider names are constrained before they reach a URL", () => {
  for (const good of ["openai", "azure-openai"]) assert.ok(PROVIDER_PATTERN.test(good));
  for (const bad of ["", "OpenAI", "a", "../x", "openai/verify", "x".repeat(40), "openai?x=1"]) {
    assert.ok(!PROVIDER_PATTERN.test(bad), bad);
  }
});

// =========================================================================
// Structure
// =========================================================================

test("the page and actions check permission on the server", async () => {
  const page = await source("app/workspace/settings/ai/page.tsx");
  assert.match(page, /requirePageAccess\("ai_settings\.view"\)/);
  assert.match(page, /can\(context, "ai_settings\.manage"\)/);

  const actions = await source("app/workspace/settings/ai/actions.ts");
  assert.match(actions, /^"use server";/);
  const exported = [...actions.matchAll(/export async function (\w+)/g)].map((m) => m[1]);
  assert.deepEqual(exported.sort(), ["replaceProviderKey", "verifyProvider"]);
  for (const name of exported) {
    const body = actions.slice(actions.indexOf(`export async function ${name}`));
    const firstStatement = body.slice(body.indexOf("{") + 1).trim();
    assert.match(firstStatement, /^const context = await requireAccess\("ai_settings\.manage"\);/, name);
  }
});

test("the key goes only into a request body", async () => {
  const actions = await source("app/workspace/settings/ai/actions.ts");
  assert.match(actions, /body: \{ api_key: input\.key \}/);
  assert.equal((actions.match(/input\.key/g) ?? []).length, 1, "the key is used exactly once");
  assert.doesNotMatch(actions, /redirect\(|cookies\(|console\.|searchParams/);
  assert.match(actions, /method: "PUT"/);
  // The returned result carries a message and a flag, nothing else.
  assert.match(actions, /interface SettingsActionResult \{\s*readonly ok: boolean;\s*readonly message: string;\s*\}/);
});

test("the key field is a cleared password field that browsers will not remember", async () => {
  const forms = await source("app/workspace/settings/ai/forms.tsx");
  const input = forms.slice(forms.indexOf("name={KEY_FIELD}") - 200, forms.indexOf("name={KEY_FIELD}") + 400);
  assert.match(input, /type="password"/);
  assert.match(input, /autoComplete="off"/);
  assert.match(input, /spellCheck=\{false\}/);
  assert.match(input, /maxLength=\{MAX_KEY_INPUT\}/);
  assert.doesNotMatch(forms, /defaultValue|value=\{result/);
  assert.match(forms, /role="status" aria-live="polite"/);
});

test("the page reports problems and fallbacks from the API, not from raw codes", async () => {
  const page = await source("app/workspace/settings/ai/page.tsx");
  assert.match(page, /describeFailure\(failure\)/);
  assert.match(page, /describeFallbacks\(response\.data\.fallback_uses\)/);
  assert.doesNotMatch(page, /explainRuleCode/);
});

test("no workspace page renders a key or a secret name", async () => {
  const page = await source("app/workspace/settings/ai/page.tsx");
  assert.doesNotMatch(page, /api_key|secret_name|credential_secret/);
  assert.match(page, /credential_hint/);
});

test("the navigation only offers the page to people who may see it", async () => {
  const layout = await source("app/workspace/layout.tsx");
  assert.match(layout, /can\(context, "ai_settings\.view"\) \? <Link href="\/workspace\/settings\/ai">/);
});

test("the internal API client can send the key replacement", async () => {
  const client = await source("lib/bff/ai-api.ts");
  assert.match(client, /"GET" \| "POST" \| "PUT" \| "DELETE"/);
  assert.doesNotMatch(client, /console\./);
});
