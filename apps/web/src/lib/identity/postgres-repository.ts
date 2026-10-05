import "server-only";

/**
 * PostgreSQL identity repository.
 *
 * Every statement is parameterised - there is no string interpolation into SQL
 * anywhere in this file. Membership queries always constrain on
 * `organization_id` even though this deployment holds one organization, so the
 * query shape stays correct when the same core runs multi-tenant.
 */

import { query } from "./db";
import type { IdentityRepository } from "./repository";
import type {
  MembershipRecord,
  OrganizationRecord,
  SessionRecord,
  UserRecord,
} from "./types";
import { isMembershipStatus, isRole } from "../authz/roles";

interface OrganizationRow extends Record<string, unknown> {
  id: string;
  slug: string;
  name: string;
  sending_enabled: boolean;
  allowed_domains: string[] | null;
}

interface UserRow extends Record<string, unknown> {
  id: string;
  email: string;
  name: string | null;
}

interface MembershipRow extends Record<string, unknown> {
  id: string;
  organization_id: string;
  user_id: string;
  role: string;
  status: string;
  department_ids: string[] | null;
}

interface SessionRow extends Record<string, unknown> {
  session_token: string;
  user_id: string;
  expires: Date;
  revoked_at: Date | null;
}

function toMembership(row: MembershipRow): MembershipRecord | null {
  if (!isRole(row.role) || !isMembershipStatus(row.status)) {
    return null;
  }
  return {
    id: row.id,
    organizationId: row.organization_id,
    userId: row.user_id,
    role: row.role,
    status: row.status,
    departmentIds: row.department_ids ?? [],
  };
}

export class PostgresIdentityRepository implements IdentityRepository {
  async getOrganization(): Promise<OrganizationRecord | null> {
    const rows = await query<OrganizationRow>(
      `SELECT o.id,
              o.slug,
              o.name,
              o.sending_enabled,
              coalesce(
                array_agg(d.domain) FILTER (WHERE d.domain IS NOT NULL),
                '{}'
              ) AS allowed_domains
         FROM organizations o
         LEFT JOIN organization_domains d ON d.organization_id = o.id
        GROUP BY o.id
        ORDER BY o.created_at
        LIMIT 1`,
    );

    const row = rows[0];
    if (!row) {
      return null;
    }

    return {
      id: row.id,
      slug: row.slug,
      name: row.name,
      sendingEnabled: row.sending_enabled,
      allowedDomains: row.allowed_domains ?? [],
    };
  }

  async findSessionByToken(sessionToken: string): Promise<SessionRecord | null> {
    const rows = await query<SessionRow>(
      `SELECT session_token, user_id, expires, revoked_at
         FROM sessions
        WHERE session_token = $1
        LIMIT 1`,
      [sessionToken],
    );

    const row = rows[0];
    if (!row) {
      return null;
    }

    return {
      sessionToken: row.session_token,
      userId: row.user_id,
      expiresAt: row.expires.getTime(),
      revokedAt: row.revoked_at ? row.revoked_at.getTime() : null,
    };
  }

  async findUserById(userId: string): Promise<UserRecord | null> {
    const rows = await query<UserRow>(
      `SELECT id, email, name FROM users WHERE id = $1 LIMIT 1`,
      [userId],
    );
    const row = rows[0];
    return row ? { id: row.id, email: row.email, name: row.name } : null;
  }

  async findUserByEmail(email: string): Promise<UserRecord | null> {
    const rows = await query<UserRow>(
      `SELECT id, email, name FROM users WHERE email = lower($1) LIMIT 1`,
      [email],
    );
    const row = rows[0];
    return row ? { id: row.id, email: row.email, name: row.name } : null;
  }

  async findMembership(
    organizationId: string,
    userId: string,
  ): Promise<MembershipRecord | null> {
    const rows = await query<MembershipRow>(
      `SELECT m.id,
              m.organization_id,
              m.user_id,
              m.role::text AS role,
              m.status::text AS status,
              coalesce(
                array_agg(dm.department_id) FILTER (WHERE dm.department_id IS NOT NULL),
                '{}'
              ) AS department_ids
         FROM memberships m
         LEFT JOIN department_memberships dm ON dm.membership_id = m.id
        WHERE m.organization_id = $1
          AND m.user_id = $2
        GROUP BY m.id
        LIMIT 1`,
      [organizationId, userId],
    );

    const row = rows[0];
    return row ? toMembership(row) : null;
  }

  async findMembershipByEmail(
    organizationId: string,
    email: string,
  ): Promise<MembershipRecord | null> {
    const rows = await query<MembershipRow>(
      `SELECT m.id,
              m.organization_id,
              m.user_id,
              m.role::text AS role,
              m.status::text AS status,
              coalesce(
                array_agg(dm.department_id) FILTER (WHERE dm.department_id IS NOT NULL),
                '{}'
              ) AS department_ids
         FROM memberships m
         JOIN users u ON u.id = m.user_id
         LEFT JOIN department_memberships dm ON dm.membership_id = m.id
        WHERE m.organization_id = $1
          AND u.email = lower($2)
        GROUP BY m.id
        LIMIT 1`,
      [organizationId, email],
    );

    const row = rows[0];
    return row ? toMembership(row) : null;
  }
}
