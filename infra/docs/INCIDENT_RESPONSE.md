# Incident Response

**Audience:** whoever is on call, reading this during an incident.

Short sections, commands you can paste, and the decision stated before the
detail. If you are mid-incident, read §1 and §2 and skip the rest until the
bleeding stops.

**Support contacts and escalation paths are in `docs/SUPPORT.md`.** This file
is what to *do*; that one is who to *tell*.

---

## 1. First five minutes

1. **Write down the time** and what made you look. Everything else is easier
   with a start time.
2. **Decide if customer data is exposed.** If yes, it is a breach and the clock
   in §5 starts now. If unsure, assume yes until shown otherwise.
3. **Stop the bleeding before diagnosing.** The containment actions in §2 are
   all reversible and none of them destroys evidence.
4. **Tell the client's named contact.** Not a summary — just that you are
   investigating, and what you have stopped.
5. **Do not delete anything.** Logs, rows and failed jobs are the evidence.
   Erasure and retention sweeps stay off until the incident is closed.

## 2. Containment, in order of blast radius

Each is reversible. Prefer the narrowest that stops the problem.

### Stop reading one mailbox

```bash
# In the product, as an Owner or Admin: disconnect the mailbox. This destroys
# the local credential immediately, even if Google's revocation is unreachable.
# Or directly, if the product is not usable:
psql "$DATABASE_URL" -c "UPDATE mailboxes SET status='REVOKED' WHERE email_address='support@client.example';"
psql "$DATABASE_URL" -c "DELETE FROM mailbox_credentials WHERE mailbox_id = (SELECT id FROM mailboxes WHERE email_address='support@client.example');"
```

Then confirm the grant is gone at Google, in the Workspace admin console —
deleting the row locally does not revoke it there.

### Stop all drafting, keep reading

Set `RESOLVEFLOW_MAILBOX_SCOPES=read_only` and redeploy. The deployment returns
to Observe Mode: triage continues, nothing is written to any mailbox.
`GET /v1/capabilities` confirms it (`pilot_stage: OBSERVE`).

### Stop all AI calls

```bash
psql "$DATABASE_URL" -c "UPDATE ai_configs SET monthly_budget_minor_units = 0;"
```

A zero budget refuses every call with a code an operator can read. Cheaper and
faster than rotating the key, and it leaves the key valid for recovery.

### Stop one person

```bash
psql "$DATABASE_URL" -c "UPDATE memberships SET status='SUSPENDED' WHERE id='<membership>';"
psql "$DATABASE_URL" -c "DELETE FROM sessions WHERE user_id = (SELECT user_id FROM memberships WHERE id='<membership>');"
```

Sessions are database-backed, so deleting the row ends access on the next
request. Suspending without deleting the session leaves them in until it
expires.

### Stop everything

Scale the Cloud Run services to zero. The mailbox watch expires on its own and
no mail is lost — Gmail keeps it; ingestion resumes from its history cursor.

## 3. Triage by symptom

### "Mail has stopped arriving"

```bash
psql "$DATABASE_URL" -c "SELECT email_address, status, last_error_code, last_refreshed_at FROM mailboxes;"
```

- `DEGRADED` with `CREDENTIAL_UNREADABLE` → the vault key is missing or was
  rotated badly. Restore the previous secret version; credentials are not
  deleted, so this is recoverable.
- `REVOKED` with `INVALID_GRANT` or `SCOPE_REMOVED` → someone changed the grant
  at Google. Reconnect the mailbox.
- `CONNECTED` but nothing new → the Gmail watch has lapsed. Re-register it.

### "The AI has stopped answering"

```bash
psql "$DATABASE_URL" -c "SELECT provider, last_failure_code, last_failure_at, last_success_at FROM ai_configs;"
psql "$DATABASE_URL" -c "SELECT period_start, reserved_micro_units, spent_micro_units FROM provider_budget_ledgers ORDER BY period_start DESC LIMIT 3;"
```

Budget exhausted, key rejected and provider unavailable are different codes and
different fixes. A reservation held by a worker that died inflates `reserved`:
sweep stale reservations rather than raising the budget.

### "Everything is slow" or "requests are being refused"

```bash
psql "$DATABASE_URL" -c "SELECT policy, count(*), max(request_count) FROM rate_limit_counters WHERE window_start > now() - interval '15 minutes' GROUP BY policy;"
```

`RATE_LIMITED` means a caller is over its allowance. `RATE_LIMIT_UNAVAILABLE`
means the limiter could not count — the database is the problem, not the
caller, and the limiter is failing closed on purpose.

### "A draft appeared that should not have"

A draft is not a send. Confirm nothing was sent (nothing can be), then:

```bash
psql "$DATABASE_URL" -c "SELECT ticket_id, status, provider_draft_id, approved_by_membership_id, created_at FROM provider_drafts ORDER BY created_at DESC LIMIT 20;"
```

Every draft names the membership that approved it. Delete the draft in Gmail;
the row stays as the record that it happened.

## 4. Suspected compromise of a credential

| Credential | Action | Where |
|---|---|---|
| AI provider key | Rotate at the provider, then replace through the product so the old version is retired | `infra/docs/RUNBOOK.md` → "Replacing the AI provider key" |
| Mailbox refresh token | Disconnect the mailbox; revoke at Google; reconnect | §2 above |
| Mailbox vault key | Add a new key first, reseal, then remove the old | RUNBOOK → "Rotating the mailbox token key" |
| Internal API token | Rotate the secret and redeploy both services together | They must match; a mismatch is a full outage |
| A staff Google account | The client revokes it in Workspace; then suspend the membership and delete the sessions | §2 above |

Rotate **before** investigating how it leaked. The investigation is slower than
the rotation and the leak keeps working while you look.

## 5. If customer data was exposed

This is the part with legal deadlines, and they are the client's deadlines, not
ours. The client is the data controller.

1. **Tell the client's data-protection contact immediately** — not after you
   understand it. Under GDPR they have 72 hours from becoming aware, and their
   clock starts when you tell them.
2. **Establish scope, and write it down as facts and gaps:** which mailboxes,
   which date range, how many conversations, whose data, and what was
   accessible as opposed to what was accessed.

```bash
# Conversations in a window, per mailbox.
psql "$DATABASE_URL" -c "SELECT m.email_address, count(*) FROM tickets t JOIN mailboxes m ON m.id = t.mailbox_id WHERE t.created_at BETWEEN '<from>' AND '<to>' GROUP BY 1;"
# Who looked at what, over the same window.
psql "$DATABASE_URL" -c "SELECT actor_email, action, target_type, count(*) FROM audit_events WHERE occurred_at BETWEEN '<from>' AND '<to>' GROUP BY 1,2,3 ORDER BY 4 DESC;"
```

3. **Preserve the evidence.** Take a database snapshot and export the relevant
   audit rows before any remediation changes them. `audit_events` cannot be
   altered, which is the point of the trigger.
4. **Do not notify data subjects yourself.** That is the client's
   communication, in their words.
5. **Write the timeline as you go.** Reconstructing it afterwards from memory
   is how dates become wrong.

## 6. Recovery

Restore and rollback are drilled, not theoretical: the procedure below was
exercised end to end in C13 (23 of 23 checks, including that every constraint
and trigger still enforces after a restore).

```bash
# Point-in-time restore of Cloud SQL to a NEW instance. Never restore over the
# instance you are investigating.
gcloud sql instances clone <instance> <instance>-recovery --point-in-time '<RFC3339>'
```

Then, before pointing anything at it:

```bash
psql "$RECOVERY_URL" -c "SELECT version_num FROM alembic_version;"              # the revision it expects
psql "$RECOVERY_URL" -c "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal;"  # append-only guards intact
psql "$RECOVERY_URL" -c "UPDATE audit_events SET action='x';"                   # must fail: append-only
```

Full procedure, including application rollback and the image-tag mechanics:
`infra/docs/RUNBOOK.md` → "Rollback" and "Database restore".

## 7. After it is over

Within a week, while it is still accurate:

- A timeline, and the one change that would have prevented it.
- A finding added to `docs/SECURITY_REVIEW.md` with a severity, so it is
  tracked like any other.
- A test that fails if the defect returns. A fix without one is a fix that
  comes back.
- If a control was missing rather than broken, a line in `docs/THREAT_MODEL.md`
  — the model was wrong, not just the code.

Blame the design, not the person who found it.
