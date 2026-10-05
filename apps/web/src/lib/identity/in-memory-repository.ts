/**
 * In-memory identity repository.
 *
 * A test fixture, following the same rule as the deterministic model provider
 * (decision D-012): it exists for automated tests and is never selectable at
 * runtime. `createIdentityRepository` in `./factory` will not return it.
 */

import type { IdentityRepository } from "./repository";
import type {
  MembershipRecord,
  OrganizationRecord,
  SessionRecord,
  UserRecord,
} from "./types";

export interface InMemoryIdentityState {
  organization: OrganizationRecord | null;
  users: UserRecord[];
  memberships: MembershipRecord[];
  sessions: SessionRecord[];
}

export class InMemoryIdentityRepository implements IdentityRepository {
  private readonly state: InMemoryIdentityState;

  constructor(state: Partial<InMemoryIdentityState> = {}) {
    this.state = {
      organization: state.organization ?? null,
      users: state.users ? [...state.users] : [],
      memberships: state.memberships ? [...state.memberships] : [],
      sessions: state.sessions ? [...state.sessions] : [],
    };
  }

  async getOrganization(): Promise<OrganizationRecord | null> {
    return this.state.organization;
  }

  async findSessionByToken(sessionToken: string): Promise<SessionRecord | null> {
    return (
      this.state.sessions.find((session) => session.sessionToken === sessionToken) ?? null
    );
  }

  async findUserById(userId: string): Promise<UserRecord | null> {
    return this.state.users.find((user) => user.id === userId) ?? null;
  }

  async findUserByEmail(email: string): Promise<UserRecord | null> {
    const normalized = email.trim().toLowerCase();
    return this.state.users.find((user) => user.email === normalized) ?? null;
  }

  async findMembership(
    organizationId: string,
    userId: string,
  ): Promise<MembershipRecord | null> {
    return (
      this.state.memberships.find(
        (membership) =>
          membership.organizationId === organizationId && membership.userId === userId,
      ) ?? null
    );
  }

  async findMembershipByEmail(
    organizationId: string,
    email: string,
  ): Promise<MembershipRecord | null> {
    const user = await this.findUserByEmail(email);
    if (!user) {
      return null;
    }
    return this.findMembership(organizationId, user.id);
  }
}
