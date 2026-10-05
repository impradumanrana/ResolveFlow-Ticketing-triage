/**
 * Identity records as the server reads them.
 *
 * These mirror the C03 migration. C04 extends the schema; it should extend
 * these types rather than replace them.
 */

import type { MembershipStatus, Role } from "../authz/roles";

export interface OrganizationRecord {
  readonly id: string;
  readonly slug: string;
  readonly name: string;
  readonly sendingEnabled: boolean;
  readonly allowedDomains: readonly string[];
}

export interface UserRecord {
  readonly id: string;
  readonly email: string;
  readonly name: string | null;
}

export interface MembershipRecord {
  readonly id: string;
  readonly organizationId: string;
  readonly userId: string;
  readonly role: Role;
  readonly status: MembershipStatus;
  readonly departmentIds: readonly string[];
}

export interface SessionRecord {
  readonly sessionToken: string;
  readonly userId: string;
  /** Epoch milliseconds. */
  readonly expiresAt: number;
  readonly revokedAt: number | null;
}

/**
 * Everything the server needs to build an AuthContext, read in one place so a
 * request cannot assemble identity from several inconsistent sources.
 */
export interface ResolvedIdentity {
  readonly organization: OrganizationRecord;
  readonly user: UserRecord;
  readonly membership: MembershipRecord;
}
