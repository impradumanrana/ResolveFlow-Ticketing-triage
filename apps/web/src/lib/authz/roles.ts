/**
 * Roles, permissions, and the matrix between them.
 *
 * This module is deliberately free of framework, database, and network
 * imports. Authorization is the decision this product cannot get wrong, so it
 * is expressed as data and pure functions that can be exhaustively tested
 * without a server, a session, or a database.
 */

export const ROLES = [
  "OWNER",
  "ADMIN",
  "SUPERVISOR",
  "AGENT",
  "KNOWLEDGE_MANAGER",
  "AUDITOR",
] as const;

export type Role = (typeof ROLES)[number];

export const MEMBERSHIP_STATUSES = ["INVITED", "ACTIVE", "SUSPENDED", "REVOKED"] as const;

export type MembershipStatus = (typeof MEMBERSHIP_STATUSES)[number];

/**
 * Every permission the product can check. Adding a capability means adding a
 * permission here and placing it in the matrix, which forces a deliberate
 * decision for all six roles rather than an implicit grant.
 */
export const PERMISSIONS = [
  // Workspace
  "workspace.view",

  // Tickets and queues (surfaces land in C08; the decisions are owned here)
  "ticket.view",
  "ticket.assign",
  "ticket.update",
  "ticket.review_draft",
  "ticket.approve_draft",
  "ticket.reroute",

  // Knowledge
  "knowledge.view",
  "knowledge.ingest",
  "knowledge.delete",

  // Quality and evaluation
  "quality.view",
  "quality.run",

  // Mailboxes and departments
  "mailbox.view",
  "mailbox.connect",
  "department.view",
  "department.manage",

  // AI configuration
  "ai_settings.view",
  "ai_settings.manage",

  // People and governance
  "member.view",
  "member.invite",
  "member.change_role",
  "member.revoke",
  "audit.view",
  "organization.manage",
  "retention.manage",
] as const;

export type Permission = (typeof PERMISSIONS)[number];

/**
 * Permissions granted to each role.
 *
 * Read the matrix as the answer to "what is this person trusted to do", not as
 * a hierarchy. An Auditor outranks an Agent on `audit.view` and is below them
 * on every operational action; a Knowledge Manager owns the corpus and touches
 * no ticket. Roles are not ordered, so there is no "greater than" shortcut
 * anywhere in this codebase.
 */
const MATRIX: Readonly<Record<Role, readonly Permission[]>> = {
  OWNER: [
    "workspace.view",
    "ticket.view",
    "ticket.assign",
    "ticket.update",
    "ticket.review_draft",
    "ticket.approve_draft",
    "ticket.reroute",
    "knowledge.view",
    "knowledge.ingest",
    "knowledge.delete",
    "quality.view",
    "quality.run",
    "mailbox.view",
    "mailbox.connect",
    "department.view",
    "department.manage",
    "ai_settings.view",
    "ai_settings.manage",
    "member.view",
    "member.invite",
    "member.change_role",
    "member.revoke",
    "audit.view",
    "organization.manage",
    "retention.manage",
  ],

  // Everything the Owner can do except transferring ownership and setting
  // retention, which are the two decisions that need the account holder.
  ADMIN: [
    "workspace.view",
    "ticket.view",
    "ticket.assign",
    "ticket.update",
    "ticket.review_draft",
    "ticket.approve_draft",
    "ticket.reroute",
    "knowledge.view",
    "knowledge.ingest",
    "knowledge.delete",
    "quality.view",
    "quality.run",
    "mailbox.view",
    "mailbox.connect",
    "department.view",
    "department.manage",
    "ai_settings.view",
    "ai_settings.manage",
    "member.view",
    "member.invite",
    "member.change_role",
    "member.revoke",
    "audit.view",
  ],

  SUPERVISOR: [
    "workspace.view",
    "ticket.view",
    "ticket.assign",
    "ticket.update",
    "ticket.review_draft",
    "ticket.approve_draft",
    "ticket.reroute",
    "knowledge.view",
    "quality.view",
    "quality.run",
    "mailbox.view",
    "department.view",
    "ai_settings.view",
    "member.view",
  ],

  AGENT: [
    "workspace.view",
    "ticket.view",
    "ticket.update",
    "ticket.review_draft",
    "ticket.approve_draft",
    "knowledge.view",
    "department.view",
  ],

  KNOWLEDGE_MANAGER: [
    "workspace.view",
    "knowledge.view",
    "knowledge.ingest",
    "knowledge.delete",
    "quality.view",
    "quality.run",
    "department.view",
  ],

  // Read-only by construction. An Auditor must be able to prove what happened
  // without being able to change it.
  AUDITOR: [
    "workspace.view",
    "ticket.view",
    "knowledge.view",
    "quality.view",
    "mailbox.view",
    "department.view",
    "ai_settings.view",
    "member.view",
    "audit.view",
  ],
};

function buildMatrixSets(): Record<Role, ReadonlySet<Permission>> {
  const sets = {} as Record<Role, ReadonlySet<Permission>>;
  for (const role of ROLES) {
    sets[role] = new Set<Permission>(MATRIX[role]);
  }
  return sets;
}

const MATRIX_SETS: Readonly<Record<Role, ReadonlySet<Permission>>> = Object.freeze(
  buildMatrixSets(),
);

/** Permissions a role holds, sorted for stable display and snapshotting. */
export function permissionsForRole(role: Role): readonly Permission[] {
  return [...MATRIX[role]].sort();
}

/** Whether a role holds a permission. Deny by default for unknown input. */
export function roleHasPermission(role: Role, permission: Permission): boolean {
  return MATRIX_SETS[role]?.has(permission) ?? false;
}

export function isRole(value: unknown): value is Role {
  return typeof value === "string" && (ROLES as readonly string[]).includes(value);
}

export function isPermission(value: unknown): value is Permission {
  return typeof value === "string" && (PERMISSIONS as readonly string[]).includes(value);
}

export function isMembershipStatus(value: unknown): value is MembershipStatus {
  return (
    typeof value === "string" && (MEMBERSHIP_STATUSES as readonly string[]).includes(value)
  );
}

/**
 * Roles that see every department's work by design.
 *
 * Everyone else is limited to the departments they are assigned to. Defined
 * once here because the authorization decision, the SQL that scopes queries,
 * and the empty state that explains an empty queue must all agree.
 */
export const ORGANIZATION_WIDE_ROLES: ReadonlySet<Role> = new Set<Role>([
  "OWNER",
  "ADMIN",
  "AUDITOR",
]);

export function isOrganizationWideRole(role: string): boolean {
  return ORGANIZATION_WIDE_ROLES.has(role as Role);
}

/**
 * Roles that may never perform a write, whatever the matrix says. This is a
 * second, independent expression of the same intent: if someone adds a write
 * permission to AUDITOR by mistake, `authorize` still refuses it.
 */
export const READ_ONLY_ROLES: ReadonlySet<Role> = new Set<Role>(["AUDITOR"]);

/** Permissions that mutate state. Used for the read-only cross-check. */
export const WRITE_PERMISSIONS: ReadonlySet<Permission> = new Set<Permission>([
  "ticket.assign",
  "ticket.update",
  "ticket.approve_draft",
  "ticket.reroute",
  "knowledge.ingest",
  "knowledge.delete",
  "quality.run",
  "mailbox.connect",
  "department.manage",
  "ai_settings.manage",
  "member.invite",
  "member.change_role",
  "member.revoke",
  "organization.manage",
  "retention.manage",
]);
