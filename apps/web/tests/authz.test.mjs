/**
 * Role and permission tests.
 *
 * The gate for C03 is that unauthorized access fails. These tests assert the
 * negative case for every role against every permission, so a future edit that
 * widens a role has to change an explicit expectation rather than slipping
 * through.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  PERMISSIONS,
  READ_ONLY_ROLES,
  ROLES,
  WRITE_PERMISSIONS,
  permissionsForRole,
  roleHasPermission,
} from "../.test-build/lib/authz/roles.js";
import {
  authorize,
  can,
  messageForDenial,
  requirePermission,
  statusForDenial,
  AuthorizationError,
} from "../.test-build/lib/authz/authorize.js";

const ORG = "11111111-1111-1111-1111-111111111111";
const OTHER_ORG = "22222222-2222-2222-2222-222222222222";

function contextFor(role, overrides = {}) {
  return {
    userId: "user-1",
    email: "person@client.example",
    organizationId: ORG,
    membershipId: "membership-1",
    role,
    status: "ACTIVE",
    departmentIds: [],
    ...overrides,
  };
}

test("every role is covered by the matrix", () => {
  for (const role of ROLES) {
    assert.ok(Array.isArray(permissionsForRole(role)), `${role} has no entry`);
  }
});

test("no role is granted a permission outside the declared permission list", () => {
  for (const role of ROLES) {
    for (const permission of permissionsForRole(role)) {
      assert.ok(
        PERMISSIONS.includes(permission),
        `${role} grants unknown permission ${permission}`,
      );
    }
  }
});

test("the full role x permission matrix matches the recorded expectation", () => {
  // A snapshot of intent. Changing a role's authority must change this table.
  const expected = {
    OWNER: 25,
    ADMIN: 23,
    SUPERVISOR: 14,
    AGENT: 7,
    KNOWLEDGE_MANAGER: 7,
    AUDITOR: 9,
  };

  for (const role of ROLES) {
    assert.equal(
      permissionsForRole(role).length,
      expected[role],
      `${role} permission count changed; confirm the change is intended`,
    );
  }
});

test("only Owner may transfer ownership or set retention", () => {
  for (const role of ROLES) {
    const allowed = role === "OWNER";
    assert.equal(roleHasPermission(role, "organization.manage"), allowed, role);
    assert.equal(roleHasPermission(role, "retention.manage"), allowed, role);
  }
});

test("read-only roles are refused every write permission", () => {
  for (const role of READ_ONLY_ROLES) {
    for (const permission of WRITE_PERMISSIONS) {
      const decision = authorize(contextFor(role), permission);
      assert.equal(decision.allowed, false, `${role} was allowed ${permission}`);
      assert.equal(decision.reason, "ROLE_IS_READ_ONLY");
    }
  }
});

test("an Agent cannot administer people, mailboxes, or AI settings", () => {
  const agent = contextFor("AGENT");
  for (const permission of [
    "member.invite",
    "member.change_role",
    "member.revoke",
    "mailbox.connect",
    "ai_settings.manage",
    "audit.view",
    "organization.manage",
    "retention.manage",
    "knowledge.delete",
  ]) {
    const decision = authorize(agent, permission);
    assert.equal(decision.allowed, false, `agent was allowed ${permission}`);
    assert.equal(decision.reason, "ROLE_LACKS_PERMISSION");
  }
});

test("a Knowledge Manager owns the corpus and touches no ticket", () => {
  const km = contextFor("KNOWLEDGE_MANAGER");
  assert.ok(can(km, "knowledge.ingest"));
  assert.ok(can(km, "knowledge.delete"));
  assert.equal(can(km, "ticket.view"), false);
  assert.equal(can(km, "ticket.approve_draft"), false);
});

test("an unauthenticated caller is refused everything", () => {
  for (const permission of PERMISSIONS) {
    for (const context of [null, undefined]) {
      const decision = authorize(context, permission);
      assert.equal(decision.allowed, false);
      assert.equal(decision.reason, "NOT_AUTHENTICATED");
    }
  }
});

test("a non-active membership is refused everything, whatever the role", () => {
  for (const role of ROLES) {
    for (const [status, reason] of [
      ["INVITED", "MEMBERSHIP_NOT_ACTIVE"],
      ["SUSPENDED", "MEMBERSHIP_NOT_ACTIVE"],
      ["REVOKED", "MEMBERSHIP_REVOKED"],
    ]) {
      const decision = authorize(contextFor(role, { status }), "workspace.view");
      assert.equal(decision.allowed, false, `${role}/${status} was allowed`);
      assert.equal(decision.reason, reason);
    }
  }
});

test("an unknown permission is denied rather than treated as ungated", () => {
  const decision = authorize(contextFor("OWNER"), "totally.made.up");
  assert.equal(decision.allowed, false);
  assert.equal(decision.reason, "UNKNOWN_PERMISSION");
});

test("an unknown role holds no permissions", () => {
  for (const permission of PERMISSIONS) {
    assert.equal(roleHasPermission("SUPERUSER", permission), false);
    const decision = authorize(contextFor("SUPERUSER"), permission);
    assert.equal(decision.allowed, false);
  }
});

// -------------------------------------------------------------------------
// Object-level authorization: the cross-tenant cases
// -------------------------------------------------------------------------

test("no role may act on a resource in another organization", () => {
  for (const role of ROLES) {
    const decision = authorize(contextFor(role), "ticket.view", {
      organizationId: OTHER_ORG,
    });
    assert.equal(decision.allowed, false, `${role} reached another organization`);
    assert.equal(decision.reason, "RESOURCE_NOT_IN_ORGANIZATION");
  }
});

test("the Owner is not exempt from tenant isolation", () => {
  const decision = authorize(contextFor("OWNER"), "organization.manage", {
    organizationId: OTHER_ORG,
  });
  assert.equal(decision.allowed, false);
  assert.equal(decision.reason, "RESOURCE_NOT_IN_ORGANIZATION");
});

test("a cross-tenant reference is reported as such even when the role also lacks the permission", () => {
  // KNOWLEDGE_MANAGER has no ticket.view at all. The cross-tenant attempt is
  // still the more important fact, and must be the reported one.
  const decision = authorize(contextFor("KNOWLEDGE_MANAGER"), "ticket.view", {
    organizationId: OTHER_ORG,
  });
  assert.equal(decision.allowed, false);
  assert.equal(decision.reason, "RESOURCE_NOT_IN_ORGANIZATION");
});

test("tenant isolation is checked before department scoping", () => {
  const supervisor = contextFor("SUPERVISOR", { departmentIds: ["dept-a"] });
  const decision = authorize(supervisor, "ticket.view", {
    organizationId: OTHER_ORG,
    departmentId: "dept-a",
  });
  assert.equal(decision.reason, "RESOURCE_NOT_IN_ORGANIZATION");
});

test("a department-scoped role cannot reach another department's work", () => {
  const supervisor = contextFor("SUPERVISOR", { departmentIds: ["dept-a"] });

  assert.ok(can(supervisor, "ticket.view", { organizationId: ORG, departmentId: "dept-a" }));

  const decision = authorize(supervisor, "ticket.view", {
    organizationId: ORG,
    departmentId: "dept-b",
  });
  assert.equal(decision.allowed, false);
  assert.equal(decision.reason, "DEPARTMENT_NOT_ASSIGNED");
});

test("organization-wide roles are not limited by department assignment", () => {
  for (const role of ["OWNER", "ADMIN", "AUDITOR"]) {
    const context = contextFor(role, { departmentIds: [] });
    assert.ok(
      can(context, "ticket.view", { organizationId: ORG, departmentId: "dept-z" }),
      `${role} was blocked by department scoping`,
    );
  }
});

test("an agent with no department assignment cannot reach a departmental resource", () => {
  const agent = contextFor("AGENT", { departmentIds: [] });
  const decision = authorize(agent, "ticket.view", {
    organizationId: ORG,
    departmentId: "dept-a",
  });
  assert.equal(decision.allowed, false);
  assert.equal(decision.reason, "DEPARTMENT_NOT_ASSIGNED");
});

// -------------------------------------------------------------------------
// Failure shape
// -------------------------------------------------------------------------

test("denials map to 401 only when signing in again would help", () => {
  assert.equal(statusForDenial("NOT_AUTHENTICATED"), 401);
  assert.equal(statusForDenial("SESSION_EXPIRED"), 401);
  assert.equal(statusForDenial("SESSION_REVOKED"), 401);
  assert.equal(statusForDenial("ROLE_LACKS_PERMISSION"), 403);
  assert.equal(statusForDenial("RESOURCE_NOT_IN_ORGANIZATION"), 403);
  assert.equal(statusForDenial("DEPARTMENT_NOT_ASSIGNED"), 403);
});

test("a denial message never reveals whether the resource exists", () => {
  const leaky = ["RESOURCE_NOT_IN_ORGANIZATION", "DEPARTMENT_NOT_ASSIGNED"];
  for (const reason of leaky) {
    assert.equal(messageForDenial(reason), "You do not have access to this.");
  }
});

test("requirePermission throws with the right status", () => {
  assert.throws(
    () => requirePermission(contextFor("AGENT"), "member.revoke"),
    (error) => {
      assert.ok(error instanceof AuthorizationError);
      assert.equal(error.reason, "ROLE_LACKS_PERMISSION");
      assert.equal(error.status, 403);
      return true;
    },
  );

  assert.doesNotThrow(() => requirePermission(contextFor("ADMIN"), "member.revoke"));
});

test("a decision cannot be mutated by its caller", () => {
  const decision = authorize(contextFor("AGENT"), "member.revoke");
  assert.throws(() => {
    decision.allowed = true;
  }, TypeError);
});
