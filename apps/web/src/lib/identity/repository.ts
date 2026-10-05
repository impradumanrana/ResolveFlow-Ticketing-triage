/**
 * The port the identity layer reads through.
 *
 * Runtime uses the PostgreSQL adapter; tests use the in-memory one. Keeping
 * this an interface is what lets the adversarial authorization suite run with
 * no database, which in turn is what lets it run on every commit.
 */

import type {
  MembershipRecord,
  OrganizationRecord,
  SessionRecord,
  UserRecord,
} from "./types";

export interface IdentityRepository {
  /**
   * The single organization this deployment serves.
   *
   * C-D004: the client deployment initializes exactly one organization, while
   * the schema stays tenant-safe. Returning one organization here is what makes
   * the "which tenant is this?" question unanswerable by the browser.
   */
  getOrganization(): Promise<OrganizationRecord | null>;

  findSessionByToken(sessionToken: string): Promise<SessionRecord | null>;

  findUserById(userId: string): Promise<UserRecord | null>;

  findUserByEmail(email: string): Promise<UserRecord | null>;

  /** Membership of a user in the deployment's organization, if any. */
  findMembership(
    organizationId: string,
    userId: string,
  ): Promise<MembershipRecord | null>;

  /** Membership matched by invited email, used during sign-in. */
  findMembershipByEmail(
    organizationId: string,
    email: string,
  ): Promise<MembershipRecord | null>;
}
