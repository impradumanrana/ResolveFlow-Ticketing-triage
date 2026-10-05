/**
 * Human review in the workspace: what is offered, what it says, and what the
 * forms carry.
 *
 * The structural tests read the sources. What they pin is the C12 gate: no
 * send anywhere, the version and the idempotency key travel with every
 * decision, and the permission is re-checked on the server.
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  DECISIONS,
  decisionKey,
  draftTone,
  outcomeMessage,
  previewSummary,
  specFor,
} from "../.test-build/lib/review/view.js";
import { PERMISSIONS } from "../.test-build/lib/authz/roles.js";

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), "utf8");

/** Code without comments: the guarantee is about what runs, not what prose says. */
const codeOnly = (text) =>
  text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

const outcome = (overrides = {}) => ({
  ok: true,
  decision: "APPROVE",
  code: "APPLIED",
  message: "Recorded.",
  ticket_version: 4,
  revision: 2,
  replayed: false,
  draft: null,
  ...overrides,
});

const preview = (overrides = {}) => ({
  from_address: "support@acme.example",
  to: ["customer@example.net"],
  cc: [],
  subject: "Re: Reset my password",
  body: "Open the sign-in page. [KB-001]",
  in_reply_to: "<first@mail>",
  notes: [],
  effect: "Approving creates a draft in the mailbox for a person to review and send. ResolveFlow never sends a message itself.",
  can_create_draft: false,
  ...overrides,
});

// =========================================================================
// There is no send
// =========================================================================

test("no decision sends anything, and none is called send", () => {
  const names = DECISIONS.map((spec) => spec.decision);
  assert.deepEqual([...names].sort(), ["APPROVE", "ASSIGN", "EDIT", "REJECT", "REROUTE", "RESOLVE"]);
  for (const spec of DECISIONS) {
    assert.doesNotMatch(spec.label.toLowerCase(), /send/);
    assert.doesNotMatch(spec.decision, /SEND/);
  }
  assert.equal(specFor("SEND"), null);
});

test("approving says what it does, every time it is offered", () => {
  const approve = specFor("APPROVE");
  assert.match(approve.effect, /draft in the mailbox for a person to review and send/);
  assert.match(approve.effect, /never sends a message itself/);
});

test("the review panel and its actions offer no send control", async () => {
  const panel = codeOnly(await source("app/workspace/tickets/[reference]/review-panel.tsx"));
  const actions = codeOnly(await source("app/workspace/tickets/[reference]/review-actions.ts"));
  // "send" survives only inside the sentence that promises nothing is sent.
  assert.doesNotMatch(panel.replace(/review and send/g, ""), /send/i);
  assert.doesNotMatch(actions, /send/i);
  assert.doesNotMatch(actions, /messages\/send|drafts\/send/);
});

test("the comment-stripping scan would notice a real send call", () => {
  assert.match(codeOnly('const url = "/drafts/send";'), /send/);
  assert.doesNotMatch(codeOnly("// we never call /drafts/send\n"), /send/);
  assert.doesNotMatch(codeOnly("/* not drafts/send either */\n"), /send/);
});

// =========================================================================
// Every decision carries its version and its key
// =========================================================================

test("one rendered form is one decision", () => {
  const key = decisionKey("11111111-1111-1111-1111-111111111111", "APPROVE", "nonce-1");
  assert.equal(key, "approve-11111111-1111-1111-1111-111111111111-nonce-1");
  // The same form submitted twice replays; a different decision is a different key.
  assert.equal(decisionKey("t", "APPROVE", "n"), decisionKey("t", "APPROVE", "n"));
  assert.notEqual(decisionKey("t", "APPROVE", "n"), decisionKey("t", "REJECT", "n"));
  assert.notEqual(decisionKey("t", "APPROVE", "n1"), decisionKey("t", "APPROVE", "n2"));
});

test("every form submits the ticket version and a nonce", async () => {
  const panel = await source("app/workspace/tickets/[reference]/review-panel.tsx");
  assert.match(panel, /name="expectedVersion" value=\{props\.version\}/);
  assert.match(panel, /name="nonce" value=\{props\.nonce\}/);
  // Every form is a server-action form, so it works before JavaScript loads.
  assert.equal((panel.match(/<form action=\{submit\}/g) ?? []).length, 5);
});

test("a form offering two decisions names neither in a hidden field", async () => {
  // Found in a browser: a hidden `decision` plus a submit button of the same
  // name both post a value, the first wins, and Approve recorded an edit.
  const panel = await source("app/workspace/tickets/[reference]/review-panel.tsx");
  const combined = panel.slice(panel.indexOf("review-body") - 600, panel.indexOf('name="reason"'));
  assert.match(combined, /allowed\.has\("EDIT"\) && allowed\.has\("APPROVE"\) \? null/);
  assert.match(combined, /name="decision"\s+value="EDIT"/);
  assert.match(combined, /name="decision"\s+value="APPROVE"/);
});

test("the key is derived from the decision that was pressed", async () => {
  const actions = await source("app/workspace/tickets/[reference]/review-actions.ts");
  assert.match(actions, /const idempotencyKey = decisionKey\(ticketId, spec\.decision, nonce\)/);
  // Save and Approve from the same rendered form are two decisions, not a replay.
  const save = decisionKey("t", "EDIT", "n");
  const approve = decisionKey("t", "APPROVE", "n");
  assert.notEqual(save, approve);
});

test("the page mints the nonce and never reuses one across renders", async () => {
  const page = await source("app/workspace/tickets/[reference]/page.tsx");
  assert.match(page, /const nonce = randomUUID\(\)/);
  assert.match(page, /nonce=\{nonce\}/);
});

// =========================================================================
// Permissions
// =========================================================================

test("each decision names a permission the authorization core knows", () => {
  for (const spec of DECISIONS) {
    assert.ok(PERMISSIONS.includes(spec.permission), spec.permission);
  }
});

test("the page offers only the decisions the person holds", async () => {
  const page = await source("app/workspace/tickets/[reference]/page.tsx");
  assert.match(page, /DECISIONS\.filter\(\(spec\) => can\(context, spec\.permission\)\)/);
  assert.match(page, /Your role can read this conversation but not decide on it/);
});

test("the server action re-checks the permission for the decision it was given", async () => {
  const actions = await source("app/workspace/tickets/[reference]/review-actions.ts");
  assert.match(actions, /^"use server";/);
  assert.match(actions, /const spec = specFor\(String\(formData\.get\("decision"\)/);
  assert.match(actions, /await requireAccess\(spec\.permission\)/);
  // The permission comes from the decision, never from the form.
  assert.doesNotMatch(actions, /requireAccess\(String\(/);
});

test("a decision the service does not offer is refused before any call", async () => {
  const actions = await source("app/workspace/tickets/[reference]/review-actions.ts");
  assert.match(actions, /if \(!spec\) \{/);
  assert.match(actions, /That is not something you can do here/);
});

// =========================================================================
// What the person is told
// =========================================================================

test("a conflict tells the person to look again", () => {
  const result = outcomeMessage(null, 409);
  assert.equal(result.ok, false);
  assert.equal(result.reload, true);
  assert.match(result.message, /Reload and decide again/);
});

test("a replay is reported as already recorded rather than as new", () => {
  assert.match(outcomeMessage(outcome({ replayed: true }), null).message, /Already recorded/);
  assert.match(outcomeMessage(outcome(), null).message, /Recorded/);
});

test("the provider-draft outcome is reported alongside the decision", () => {
  const created = outcomeMessage(
    outcome({ draft: { status: "CREATED", failure_code: null, message: "A draft is waiting in the mailbox for a person to review and send.", provider_draft_id: "d-1" } }),
    null,
  );
  assert.match(created.message, /Recorded/);
  assert.match(created.message, /draft is waiting in the mailbox/);

  const refused = outcomeMessage(
    outcome({ draft: { status: "REFUSED", failure_code: "DRAFT_SCOPE_NOT_GRANTED", message: "No provider draft was created: this mailbox is connected read-only.", provider_draft_id: null } }),
    null,
  );
  assert.equal(refused.ok, true, "the decision still stands");
  assert.match(refused.message, /read-only/);
});

test("a transport failure says nothing was changed", () => {
  assert.match(outcomeMessage(null, 502).message, /Nothing was changed/);
  assert.match(outcomeMessage(null, 404).message, /not available to you/);
});

test("a refusal is shown in the service's own words", () => {
  const refused = outcomeMessage(outcome({ ok: false, code: "DRAFT_NOT_GROUNDED", message: "This answer was not verified against its sources." }), null);
  assert.equal(refused.ok, false);
  assert.match(refused.message, /not verified against its sources/);
});

test("each draft outcome has a tone, and a created draft reads as success", async () => {
  const css = await source("app/globals.css");
  for (const [status, expected] of [["CREATED", "resolved"], ["REFUSED", "awaiting-approval"], ["FAILED", "failed"]]) {
    const tone = draftTone({ status, failure_code: null, message: null, provider_draft_id: null });
    assert.equal(tone, expected);
    assert.match(css, new RegExp(`\\.ws-badge--${tone}[ ,]`));
  }
  assert.equal(draftTone(null), null);
});

test("the preview summary says whether a draft will exist", () => {
  assert.match(previewSummary(preview({ can_create_draft: true })), /creates a draft to customer@example\.net/);
  assert.match(previewSummary(preview()), /No draft is created: support@acme\.example is connected read-only/);
  assert.match(previewSummary(null), /cannot be prepared/);
});

test("the panel shows what would be created before anyone approves", async () => {
  const panel = await source("app/workspace/tickets/[reference]/review-panel.tsx");
  assert.match(panel, /What approving would create/);
  assert.match(panel, /props\.preview\.to\.join/);
  assert.match(panel, /props\.preview\.subject/);
  assert.match(panel, /props\.preview\.body/);
  assert.match(panel, /props\.preview\.effect/);
});

test("a reason is required where a reason is owed", async () => {
  for (const decision of ["REJECT", "REROUTE"]) {
    assert.equal(specFor(decision).needsReason, true, decision);
  }
  for (const decision of ["APPROVE", "ASSIGN", "RESOLVE", "EDIT"]) {
    assert.equal(specFor(decision).needsReason, false, decision);
  }
  const panel = await source("app/workspace/tickets/[reference]/review-panel.tsx");
  assert.equal((panel.match(/name="reason" maxLength=\{2000\} required/g) ?? []).length, 2);
});

test("only a verified answer pre-fills the reply box", async () => {
  // Found in a browser: an escalated conversation pre-filled the box with the
  // internal review note, codes and all.
  const page = await source("app/workspace/tickets/[reference]/page.tsx");
  assert.match(page, /draftBody=\{detail\.groundingValidated \? detail\.draft : null\}/);

  const panel = await source("app/workspace/tickets/[reference]/review-panel.tsx");
  assert.match(panel, /There is no verified answer for this conversation/);
  assert.match(panel, /recorded as yours/);
  assert.match(panel, /checked against its cited sources/);
});
