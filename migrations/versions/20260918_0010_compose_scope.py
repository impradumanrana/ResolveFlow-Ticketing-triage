"""Permit a compose-scoped mailbox credential, keep sending impossible.

C06 refused to store any write-capable scope at all (`no_write_scope_in_v1`).
That was right while the product only read mail, but it also made C12's whole
point unreachable: an approved answer cannot become a draft in the client's
mailbox without `gmail.compose`.

The client authorized compose, so the constraint is narrowed rather than
dropped. `gmail.compose` becomes storable; every other write-capable scope
stays refused at the database level, which is the one place an application bug
cannot talk its way past:

* `gmail.send` and `https://mail.google.com/` - a credential that exists only
  to send. Nothing here sends (C-D010).
* `gmail.modify` and `gmail.insert` - can alter or fabricate the client's mail.
* the `gmail.settings.*` scopes and `gmail.labels` - can reconfigure the
  mailbox, including forwarding.

`gmail.compose` does also permit sending at Google's end. That is why the
no-send guarantees are structural rather than a matter of which scope is held:
no send endpoint appears anywhere in the tree (asserted per file),
`organizations.sending_enabled` is constrained to false, and `provider_drafts`
has no column that could represent a send.

The permitted list is mirrored by `app.mailbox.scopes.NEVER_PERMITTED_SCOPES`,
and a test asserts the two cannot drift apart.

Revision ID: 20260918_0010
Revises: 20260917_0009
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260918_0010"
down_revision: str | Sequence[str] | None = "20260917_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Order is alphabetical so the rendered constraint is stable and reviewable.
REFUSED_SCOPES: tuple[str, ...] = (
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.insert",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/gmail.settings.sharing",
)

NO_SEND_SCOPE = " AND ".join(f"NOT ('{scope}' = ANY(granted_scopes))" for scope in REFUSED_SCOPES)

# What C06 had, restored on downgrade. A downgrade therefore refuses to leave
# a compose-scoped credential behind: see the note in `downgrade`.
NO_WRITE_SCOPE_IN_V1 = (
    "NOT ('https://mail.google.com/' = ANY(granted_scopes)) "
    "AND NOT ('https://www.googleapis.com/auth/gmail.send' = ANY(granted_scopes)) "
    "AND NOT ('https://www.googleapis.com/auth/gmail.compose' = ANY(granted_scopes)) "
    "AND NOT ('https://www.googleapis.com/auth/gmail.modify' = ANY(granted_scopes))"
)


def upgrade() -> None:
    # Added before the old one is dropped, so there is no instant in which a
    # send-capable credential could be written.
    op.create_check_constraint("no_send_capable_scope", "mailbox_credentials", NO_SEND_SCOPE)
    op.drop_constraint("no_write_scope_in_v1", "mailbox_credentials", type_="check")


def downgrade() -> None:
    # Deliberately not deleting rows: a compose-scoped credential that exists
    # is a mailbox someone consented to, and this migration is not the place to
    # revoke it. Re-adding C06's constraint fails loudly if one is present, and
    # the operator disconnects the mailbox through the product first.
    op.create_check_constraint("no_write_scope_in_v1", "mailbox_credentials", NO_WRITE_SCOPE_IN_V1)
    op.drop_constraint("no_send_capable_scope", "mailbox_credentials", type_="check")
