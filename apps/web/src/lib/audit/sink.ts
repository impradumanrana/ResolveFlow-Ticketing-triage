import "server-only";

import { query } from "../identity/db";
import { sanitizeMetadata, type AuditEvent, type AuditSink } from "./events";

/**
 * Writes audit events to the append-only table.
 *
 * Failures are logged and swallowed. That is a deliberate trade: an audit
 * write that fails must not take down sign-in or hide the underlying error,
 * and the database trigger plus C02 alerting are what detect a sink that has
 * stopped working. A phase that needs hard-fail auditing (C12, C13) should
 * revisit this.
 */
export class PostgresAuditSink implements AuditSink {
  async record(event: AuditEvent): Promise<void> {
    try {
      await query(
        `INSERT INTO audit_events (
           organization_id, actor_user_id, actor_membership_id, actor_email,
           action, outcome, reason_code, target_type, target_id,
           request_id, source_ip_hash, user_agent, metadata
         ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)`,
        [
          event.organizationId,
          event.actorUserId,
          event.actorMembershipId,
          event.actorEmail,
          event.action,
          event.outcome,
          event.reasonCode,
          event.targetType,
          event.targetId,
          event.requestId,
          event.sourceIpHash,
          event.userAgent,
          JSON.stringify(sanitizeMetadata(event.metadata)),
        ],
      );
    } catch (error) {
      console.error("audit_write_failed", {
        action: event.action,
        outcome: event.outcome,
        error: error instanceof Error ? error.message : "unknown",
      });
    }
  }
}

let sink: AuditSink | null = null;

export function getAuditSink(): AuditSink {
  if (!sink) {
    sink = new PostgresAuditSink();
  }
  return sink;
}
