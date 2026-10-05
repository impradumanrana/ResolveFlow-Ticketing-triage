import "server-only";

/**
 * Auth.js configuration (decision C-D005).
 *
 * Google Workspace OAuth, database sessions, invited membership, and an
 * approved-domain allowlist. CSRF protection, PKCE, state, and nonce are
 * handled by Auth.js; this file supplies the policy, not the protocol.
 *
 * Database sessions rather than JWTs: when access is withdrawn it must stop
 * working immediately, and a self-contained token cannot be withdrawn.
 */

import NextAuth, { type NextAuthConfig } from "next-auth";
import Google from "next-auth/providers/google";
import PostgresAdapter from "@auth/pg-adapter";

import { getAuditSink } from "../audit/sink";
import { hashSourceIp } from "../audit/events";
import { getPool } from "../identity/db";
import { createIdentityRepository } from "../identity/factory";
import { decideSignIn, normalizeEmail } from "../identity/signin-policy";
import {
  sessionCookieName,
  sessionCookieOptions,
  shouldUseSecureCookies,
} from "./cookies";

const SESSION_MAX_AGE_SECONDS = 60 * 60 * 8; // One working day.
const SESSION_UPDATE_AGE_SECONDS = 60 * 15;

const USE_SECURE_COOKIES = shouldUseSecureCookies(
  process.env.AUTH_URL ?? process.env.NEXTAUTH_URL,
);

function auditSalt(): string {
  return process.env.AUDIT_IP_SALT?.trim() ?? "";
}

/**
 * Built per request rather than at module load.
 *
 * `next build` collects page data by importing route modules, so anything
 * evaluated at module scope runs at build time. Creating the database pool
 * there would make the build require a database - and would create a pool
 * inside the build container that nothing ever uses.
 */
export function buildAuthConfig(): NextAuthConfig {
  return {
    adapter: PostgresAdapter(getPool()),

    session: {
      strategy: "database",
      maxAge: SESSION_MAX_AGE_SECONDS,
      updateAge: SESSION_UPDATE_AGE_SECONDS,
    },

    // Trust the platform-provided host. Cloud Run terminates TLS upstream.
    trustHost: true,

    // Named explicitly so the request guard can read the same cookie.
    cookies: {
      sessionToken: {
        name: sessionCookieName(USE_SECURE_COOKIES),
        options: sessionCookieOptions(USE_SECURE_COOKIES),
      },
    },

    providers: [
      Google({
        clientId: process.env.GOOGLE_OAUTH_CLIENT_ID,
        clientSecret: process.env.GOOGLE_OAUTH_CLIENT_SECRET,
        authorization: {
          params: {
            // Login only. Mailbox scopes are requested separately in C06 by an
            // explicit, auditable connection flow - never bundled into sign-in.
            scope: "openid email profile",
            prompt: "select_account",
          },
        },
      }),
    ],

    pages: {
      signIn: "/signin",
      error: "/access-denied",
    },

    callbacks: {
      /**
       * The gate. Runs before a session exists, so it must do its own lookups.
       */
      async signIn({ user, account, profile }) {
        const repository = createIdentityRepository();
        const organization = await repository.getOrganization();
        const audit = getAuditSink();

        const email = user.email ? normalizeEmail(user.email) : null;
        const emailVerified = profile?.email_verified === true;

        if (!organization) {
          await audit.record({
            organizationId: null,
            actorUserId: null,
            actorMembershipId: null,
            actorEmail: email,
            action: "auth.sign_in_refused",
            outcome: "DENIED",
            reasonCode: "NOT_BOOTSTRAPPED",
            targetType: null,
            targetId: null,
            requestId: null,
            sourceIpHash: null,
            userAgent: null,
            metadata: {},
          });
          return "/access-denied?reason=NOT_BOOTSTRAPPED";
        }

        const membership = email
          ? await repository.findMembershipByEmail(organization.id, email)
          : null;

        const decision = decideSignIn(
          {
            email,
            emailVerified,
            provider: account?.provider ?? "unknown",
          },
          {
            allowedDomains: organization.allowedDomains,
            membership: membership
              ? {
                  membershipId: membership.id,
                  organizationId: membership.organizationId,
                  role: membership.role,
                  status: membership.status,
                }
              : null,
          },
        );

        if (!decision.allowed) {
          await audit.record({
            organizationId: organization.id,
            actorUserId: null,
            actorMembershipId: null,
            actorEmail: email,
            action: "auth.sign_in_refused",
            outcome: "DENIED",
            reasonCode: decision.reason,
            targetType: "organization",
            targetId: organization.id,
            requestId: null,
            sourceIpHash: await hashSourceIp(null, auditSalt()),
            userAgent: null,
            metadata: { provider: account?.provider ?? "unknown" },
          });
          return `/access-denied?reason=${decision.reason}`;
        }

        await audit.record({
          organizationId: organization.id,
          actorUserId: user.id ?? null,
          actorMembershipId: decision.membership.membershipId,
          actorEmail: decision.email,
          action: "auth.sign_in",
          outcome: "ALLOWED",
          reasonCode: null,
          targetType: "organization",
          targetId: organization.id,
          requestId: null,
          sourceIpHash: null,
          userAgent: null,
          metadata: { role: decision.membership.role },
        });

        return true;
      },

      /**
       * The session object handed to the UI.
       *
       * It carries identity, not authority. Role and organization for
       * *enforcement* are re-derived from the database on every protected
       * request by `resolveIdentity`; anything exposed here is for rendering.
       */
      async session({ session, user }) {
        if (session.user) {
          session.user.id = user.id;
        }
        return session;
      },

      /** Never redirect off-site after sign-in. */
      async redirect({ url, baseUrl }) {
        if (url.startsWith("/")) {
          return `${baseUrl}${url}`;
        }
        try {
          return new URL(url).origin === baseUrl ? url : baseUrl;
        } catch {
          return baseUrl;
        }
      },
    },

    events: {
      async signOut(message) {
        const audit = getAuditSink();
        const userId = "session" in message ? (message.session?.userId ?? null) : null;
        await audit.record({
          organizationId: null,
          actorUserId: typeof userId === "string" ? userId : null,
          actorMembershipId: null,
          actorEmail: null,
          action: "auth.sign_out",
          outcome: "ALLOWED",
          reasonCode: null,
          targetType: null,
          targetId: null,
          requestId: null,
          sourceIpHash: null,
          userAgent: null,
          metadata: {},
        });
      },
    },
  };
}

export const { handlers, auth, signIn, signOut } = NextAuth(buildAuthConfig);
