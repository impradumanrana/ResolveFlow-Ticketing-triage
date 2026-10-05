# ResolveFlow AI - C04 Verification Report

**Phase:** C04 - Durable data model and audit

**Result:** GREEN

**Date:** 15 September 2026

## Scope and safety

No cloud resource was created, read, or mutated. No Gmail mailbox was connected. No paid model was called. No production data was touched.

A local `pgvector/pgvector:pg16` container was started from `compose.client.yml` to apply the migrations, exercise every constraint, run a retention sweep, and perform a dump/restore round trip. It held synthetic data only (`acme.example`, `customer@example.net`). It was stopped and its volumes removed at the end.

## Delivered

Two migrations extending the C03 identity schema:

- **`20260915_0003`** - queues, mailboxes, mailbox permissions, threads, messages, attachments, tickets, ticket assignments, per-user seen state, SLA policies, ticket SLA state.
- **`20260915_0004`** - the pgvector extension, knowledge sources/articles/chunks/embeddings, AI configs, triage runs, actions, jobs, provider usage, evaluation runs and results, retention policies, legal holds, and an append-only retention sweep log.

Plus `app/retention.py`: pure sweep planning and a hold-aware executor that defaults to a dry run.

**37 tables, 108 indexes, 74 check constraints, 3 triggers.**

## Automated gate evidence

| Check | Result |
|---|---|
| Python suite | **162 passed** (was 121 at C03) |
| Web suite | **62 passed** |
| `make check` | exit 0 |
| Ruff / mypy / ESLint / tsc | all clean |
| Next.js production build | passed |
| Infrastructure checks | 63 files, 13 modules, 0 findings |
| Schema invariants (offline) | 27 passed |
| Retention planning (offline) | 19 passed |
| Migration chain | one linear head `20260915_0004`; every revision has a downgrade |

### Live database verification

**Migration and rollback**

| Step | Result |
|---|---|
| Apply the full chain to an empty database | 37 tables, pgvector 0.8.6 |
| HNSW vector index and GIN full-text index | both present |
| `search_vector` generated column | `ALWAYS` |
| Downgrade 0004 → 0003 → 0002 → base | 37 → 23 → 12 → 1 tables |
| Orphaned enum types after downgrade | 0 |
| Orphaned functions after downgrade | 0 |
| Re-upgrade to head | 37 tables, head restored |

**Constraints — each rejected what it should**

Idempotency: redelivering the same provider message, the same thread id in one mailbox, a second ticket on one thread, a replayed action idempotency key, a duplicate job key, a duplicate triage correlation id.

Mailboxes: a `CONNECTED` mailbox with no credential reference, a mixed-case address, `can_action` without `can_view`.

Grounding (C-D038): an `AUTO_RESOLVE` draft that is not grounding-validated, and one with no citations. A grounded, cited draft and an `ESCALATE` run with no draft were both accepted.

BYOK (C-D040): a secret name beginning `sk-`, one beginning `AIza`, and a `credential_last_four` longer than four. A Secret Manager reference was accepted; a second active config for one provider was rejected.

Knowledge: a 768-dimension embedding (rejected by pgvector), a `dimensions` column that disagreed with the vector, the same chunk+model twice, reversed chunk offsets, an empty chunk, an unapproved content type, and a re-upload of identical content.

Jobs: attempts beyond `max_attempts`, and `DEAD_LETTERED` with no timestamp.

Actions: a human action with no actor was rejected; a system action with no actor was accepted.

Numbering: per-organization ticket references assigned 1, 2, 3 by the trigger.

**Hybrid retrieval works**

Full-text: `plainto_tsquery('english', 'reset password')` matched the seeded chunk through the generated `tsvector`. Vector: cosine distance computed against a stored 1536-dimension embedding.

**Retention and legal hold**

| Step | Messages | Result |
|---|---|---|
| Seeded (3 older than 90 days, 2 recent) | 5 | |
| Dry run | 5 | reports 3, deletes nothing |
| Sweep with an active legal hold | 5 | deletes 0, records 3 held |
| Sweep after releasing the hold | 2 | deletes 3 |

The append-only `retention_sweeps` log recorded all three passes: `(MESSAGE, 3 deleted, dry_run)`, `(MESSAGE, 0 deleted, 3 held)`, `(MESSAGE, 3 deleted, live)`.

**Restore**

`pg_dump -Fc` → `DROP SCHEMA public CASCADE` → `pg_restore`, with **0 errors**. Row and object counts matched exactly before and after (37 tables, 1 organization, 2 tickets, 2 messages, 1 audit event, 3 sweeps, 1 embedding).

Row counts alone do not prove the protections survived, so each was re-tested against the restored database:

| After restore | Result |
|---|---|
| Triggers and functions | all 3 present |
| Check constraints / unique indexes | 74 / 69 |
| HNSW index and generated `tsvector` | present, `ALWAYS` |
| `UPDATE audit_events` | rejected: "audit_events is append-only" |
| `DELETE FROM retention_sweeps` | rejected: "retention_sweeps is append-only" |
| `UPDATE organizations SET sending_enabled = true` | rejected |
| Ungrounded `AUTO_RESOLVE` draft | rejected |
| Ticket numbering trigger | fired, assigned reference 3 |

### Defects the verification caught

1. **A migration that rendered valid SQL but would not apply.** `CREATE EXTENSION`, enums, and tables all rendered correctly, but two `INSERT 0 0` results in the first constraint sweep were **inconclusive, not passing** — an earlier statement in the same `psql -c` had rolled the transaction back, so the tables were empty. Re-seeded and re-run with rows present, at which point the constraints genuinely rejected every case. This is the second time this trap has appeared (C03 defect 3); `INSERT 0 0` and `UPDATE 0` must always be read as "proved nothing".
2. **`%` escaped into `%%` in rendered SQL.** The `ai_configs` key-shape constraint used `NOT LIKE 'sk-%'`, which Alembic's offline output renders as `%%`. Harmless when applied through Alembic, wrong if an operator applies the rendered SQL. Rewritten with `left(...)`, then re-verified live that it still rejects both key shapes.
3. **A test that could never fail.** `test_provider_identifiers_are_unique_per_mailbox_not_globally` asserted a truthy f-string `or` a substring check, which is true regardless. Rewritten to assert the exact `CREATE UNIQUE INDEX` statement.
4. **An invariant with an undocumented exception.** The tenancy test failed on `audit_events`, whose `organization_id` is nullable so that a sign-in refused before any organization resolves is still recorded. Rather than loosen the test, the exception is now pinned to exactly that one table with its reason, so any other nullable scope fails the build.

## What C04 deliberately did not do

- **No ingestion, retrieval, or triage code.** C04 is the data model. C05 migrates retrieval onto pgvector, C07 fills these tables from Gmail.
- **No retention values.** Policies are rows, not defaults. Real retention periods are a recorded client gate (C13).
- **No attachment scanner.** `scan_state` exists and a clean attachment must have a storage object; the scanner is C13.
- **No SLA calculation engine.** Policies, business hours, time zones, and ticket SLA state are modelled; the C09 rule engine computes against them.
- **No sending path.** The database forbids enabling it.

## Residual risks

| Risk | Treatment |
|---|---|
| The schema has never held real volume; index choices are reasoned, not measured | C07 load simulation (50 mailboxes) and the C14 pilot |
| HNSW build time and memory grow with corpus size | Revisit at C05 with the client's real corpus; parameters are index-level and changeable without a data migration |
| Retention deletes rows but not the Cloud Storage objects they reference | Object lifecycle rules exist in C02 Terraform; the two must be reconciled in C13 |
| `execute_sweep` is tested live but its live behaviour is not in CI | The planning half is pure and covered; the executor needs a database service container, which is a C07/C13 CI addition |
| Restore was proven on a 134 KB dump | A realistic-volume restore drill belongs to the C14 staging pilot, as does the RPO/RTO measurement |
| `alembic_version` is not tenant-scoped, and neither are the Auth.js identity tables | Correct by design and pinned by a test; any *other* unscoped table fails the build |

## Preserved working-tree state

`ResolveFlow-AI-Presentation.pptx` remained deleted and untouched. The untracked planning documents, local `.env`, and `app/data` were not modified. Docker volumes created for verification were removed.

## Gate conclusion

C04 meets its gate: constraints, indexes, migrations, restore, retention, and audit all pass, and each was proven against a real PostgreSQL with pgvector rather than inferred from rendered SQL. C05 may begin.
