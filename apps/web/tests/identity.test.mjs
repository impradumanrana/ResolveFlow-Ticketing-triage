/**
 * Sign-in policy and session resolution.
 *
 * Together these decide who gets a context at all. The authorization matrix is
 * only as good as this layer: a bug here hands a valid context to the wrong
 * person, and every downstream check then says yes correctly.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  decideSignIn,
  domainOf,
  isDomainAllowed,
  messageForRefusal,
  normalizeEmail,
} from "../.test-build/lib/identity/signin-policy.js";
import { InMemoryIdentityRepository } from "../.test-build/lib/identity/in-memory-repository.js";
import { resolveIdentity } from "../.test-build/lib/identity/resolve.js";

const ORG_ID = "11111111-1111-1111-1111-111111111111";
const ALLOWED = ["client.example", "client-support.example"];

function membership(overrides = {}) {
  return {
    membershipId: "membership-1",
    organizationId: ORG_ID,
    role: "AGENT",
    status: "ACTIVE",
    ...overrides,
  };
}

function candidate(overrides = {}) {
  return {
    email: "person@client.example",
    emailVerified: true,
    provider: "google",
    ...overrides,
  };
}

// -------------------------------------------------------------------------
// Domain allowlist
// -------------------------------------------------------------------------

test("email normalisation lowercases and trims only", () => {
  assert.equal(normalizeEmail("  Person@Client.Example  "), "person@client.example");
  // Dots and +tags are preserved: an invitation is matched literally.
  assert.equal(normalizeEmail("first.last+team@client.example"), "first.last+team@client.example");
});

test("domain extraction uses the last @", () => {
  assert.equal(domainOf("person@client.example"), "client.example");
  assert.equal(domainOf("odd@name@client.example"), "client.example");
  assert.equal(domainOf("no-at-sign"), null);
  assert.equal(domainOf("@leading"), null);
  assert.equal(domainOf("trailing@"), null);
});

test("only an exact domain match is allowed", () => {
  assert.ok(isDomainAllowed("person@client.example", ALLOWED));
  assert.ok(isDomainAllowed("PERSON@CLIENT.EXAMPLE".toLowerCase(), ALLOWED));

  for (const spoof of [
    "person@evil.example",
    "person@client.example.attacker.net",
    "person@sub.client.example",
    "person@notclient.example",
    "person@client-example",
    "person@xclient.example",
  ]) {
    assert.equal(isDomainAllowed(spoof, ALLOWED), false, `${spoof} was allowed`);
  }
});

// -------------------------------------------------------------------------
// Sign-in decision
// -------------------------------------------------------------------------

test("an invited person on an approved domain may sign in", () => {
  const decision = decideSignIn(candidate(), {
    allowedDomains: ALLOWED,
    membership: membership(),
  });
  assert.equal(decision.allowed, true);
  assert.equal(decision.email, "person@client.example");
});

test("an approved domain alone is never enough", () => {
  const decision = decideSignIn(candidate(), {
    allowedDomains: ALLOWED,
    membership: null,
  });
  assert.equal(decision.allowed, false);
  assert.equal(decision.reason, "NO_MEMBERSHIP");
});

test("an invited person on an unapproved domain is refused", () => {
  const decision = decideSignIn(candidate({ email: "person@other.example" }), {
    allowedDomains: ALLOWED,
    membership: membership(),
  });
  assert.equal(decision.allowed, false);
  assert.equal(decision.reason, "DOMAIN_NOT_ALLOWED");
});

test("an unverified email is refused even with a membership", () => {
  const decision = decideSignIn(candidate({ emailVerified: false }), {
    allowedDomains: ALLOWED,
    membership: membership(),
  });
  assert.equal(decision.allowed, false);
  assert.equal(decision.reason, "EMAIL_NOT_VERIFIED");
});

test("a revoked membership cannot sign back in", () => {
  const decision = decideSignIn(candidate(), {
    allowedDomains: ALLOWED,
    membership: membership({ status: "REVOKED" }),
  });
  assert.equal(decision.allowed, false);
  assert.equal(decision.reason, "MEMBERSHIP_REVOKED");
});

test("a suspended membership cannot sign in", () => {
  const decision = decideSignIn(candidate(), {
    allowedDomains: ALLOWED,
    membership: membership({ status: "SUSPENDED" }),
  });
  assert.equal(decision.allowed, false);
  assert.equal(decision.reason, "MEMBERSHIP_NOT_ACTIVE");
});

test("only Google is an accepted identity provider", () => {
  for (const provider of ["github", "credentials", "email", "okta", "unknown"]) {
    const decision = decideSignIn(candidate({ provider }), {
      allowedDomains: ALLOWED,
      membership: membership(),
    });
    assert.equal(decision.allowed, false, `${provider} was accepted`);
    assert.equal(decision.reason, "PROVIDER_NOT_ALLOWED");
  }
});

test("missing and malformed emails are refused", () => {
  for (const [email, reason] of [
    [null, "EMAIL_MISSING"],
    ["", "EMAIL_MISSING"],
    ["   ", "EMAIL_MISSING"],
    ["no-at-sign", "EMAIL_MALFORMED"],
    ["two@at@signs.example", "EMAIL_MALFORMED"],
  ]) {
    const decision = decideSignIn(candidate({ email }), {
      allowedDomains: ALLOWED,
      membership: membership(),
    });
    assert.equal(decision.allowed, false, `${email} was accepted`);
    assert.equal(decision.reason, reason);
  }
});

test("an empty allowlist admits nobody", () => {
  const decision = decideSignIn(candidate(), {
    allowedDomains: [],
    membership: membership(),
  });
  assert.equal(decision.allowed, false);
  assert.equal(decision.reason, "DOMAIN_NOT_ALLOWED");
});

test("a refusal message never names the approved domains", () => {
  for (const reason of ["DOMAIN_NOT_ALLOWED", "NO_MEMBERSHIP", "MEMBERSHIP_REVOKED"]) {
    const message = messageForRefusal(reason);
    for (const domain of ALLOWED) {
      assert.equal(message.includes(domain), false);
    }
  }
});

// -------------------------------------------------------------------------
// Session resolution
// -------------------------------------------------------------------------

const NOW = Date.UTC(2026, 8, 15, 12, 0, 0);

function repositoryWith({ sessionOverrides = {}, membershipOverrides = {}, ...rest } = {}) {
  return new InMemoryIdentityRepository({
    organization: {
      id: ORG_ID,
      slug: "client",
      name: "Client",
      sendingEnabled: false,
      allowedDomains: ALLOWED,
    },
    users: [{ id: "user-1", email: "person@client.example", name: "Person" }],
    memberships: [
      {
        id: "membership-1",
        organizationId: ORG_ID,
        userId: "user-1",
        role: "AGENT",
        status: "ACTIVE",
        departmentIds: ["dept-a"],
        ...membershipOverrides,
      },
    ],
    sessions: [
      {
        sessionToken: "valid-token",
        userId: "user-1",
        expiresAt: NOW + 60_000,
        revokedAt: null,
        ...sessionOverrides,
      },
    ],
    ...rest,
  });
}

test("a valid session resolves to a server-derived context", async () => {
  const result = await resolveIdentity(repositoryWith(), "valid-token", { now: NOW });
  assert.equal(result.ok, true);
  assert.equal(result.context.organizationId, ORG_ID);
  assert.equal(result.context.role, "AGENT");
  assert.deepEqual([...result.context.departmentIds], ["dept-a"]);
});

test("the resolved context is frozen", async () => {
  const result = await resolveIdentity(repositoryWith(), "valid-token", { now: NOW });
  assert.throws(() => {
    result.context.role = "OWNER";
  }, TypeError);
});

test("a missing or unknown session token resolves to unauthenticated", async () => {
  for (const token of [null, undefined, "", "   ", "not-a-real-token"]) {
    const result = await resolveIdentity(repositoryWith(), token, { now: NOW });
    assert.equal(result.ok, false);
    assert.equal(result.reason, "NOT_AUTHENTICATED");
  }
});

test("an expired session is refused", async () => {
  const repository = repositoryWith({ sessionOverrides: { expiresAt: NOW - 1 } });
  const result = await resolveIdentity(repository, "valid-token", { now: NOW });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "SESSION_EXPIRED");
});

test("a revoked session is refused before expiry is considered", async () => {
  const repository = repositoryWith({
    sessionOverrides: { revokedAt: NOW - 1000, expiresAt: NOW + 600_000 },
  });
  const result = await resolveIdentity(repository, "valid-token", { now: NOW });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "SESSION_REVOKED");
});

test("a session revoked in the future is still usable until then", async () => {
  const repository = repositoryWith({ sessionOverrides: { revokedAt: NOW + 30_000 } });
  const result = await resolveIdentity(repository, "valid-token", { now: NOW });
  assert.equal(result.ok, true);
});

test("a revoked membership is refused even with a live session", async () => {
  const repository = repositoryWith({
    membershipOverrides: { status: "REVOKED" },
  });
  const result = await resolveIdentity(repository, "valid-token", { now: NOW });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "MEMBERSHIP_REVOKED");
});

test("an invited-but-not-active membership does not yield a context", async () => {
  for (const status of ["INVITED", "SUSPENDED"]) {
    const repository = repositoryWith({ membershipOverrides: { status } });
    const result = await resolveIdentity(repository, "valid-token", { now: NOW });
    assert.equal(result.ok, false);
    assert.equal(result.reason, "MEMBERSHIP_NOT_ACTIVE");
  }
});

test("a session for a user with no membership is refused", async () => {
  const repository = repositoryWith({ memberships: [] });
  const result = await resolveIdentity(repository, "valid-token", { now: NOW });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "NO_MEMBERSHIP");
});

test("a session for a deleted user is refused", async () => {
  const repository = repositoryWith({ users: [] });
  const result = await resolveIdentity(repository, "valid-token", { now: NOW });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "NOT_AUTHENTICATED");
});

test("an unbootstrapped deployment grants nobody a context", async () => {
  const repository = repositoryWith({ organization: null });
  const result = await resolveIdentity(repository, "valid-token", { now: NOW });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "NO_MEMBERSHIP");
});

test("an unrecognised role or status from the adapter is refused, not trusted", async () => {
  for (const overrides of [{ role: "SUPERUSER" }, { status: "PROBATION" }]) {
    const repository = repositoryWith({ membershipOverrides: overrides });
    const result = await resolveIdentity(repository, "valid-token", { now: NOW });
    assert.equal(result.ok, false, `${JSON.stringify(overrides)} produced a context`);
    assert.equal(result.reason, "MEMBERSHIP_NOT_ACTIVE");
  }
});

test("a membership belonging to another organization never yields a context", async () => {
  const repository = repositoryWith({
    membershipOverrides: { organizationId: "99999999-9999-9999-9999-999999999999" },
  });
  const result = await resolveIdentity(repository, "valid-token", { now: NOW });
  assert.equal(result.ok, false);
  // The repository is asked for this organization's membership, so a row from
  // elsewhere is simply not found.
  assert.equal(result.reason, "NO_MEMBERSHIP");
});
