"""Who may see, act on, or connect a mailbox.

Organization membership never implies mailbox access. A mailbox is a specific
inbox with specific customers' mail in it, and the client decides who reads it
(C-D007). Access is therefore the conjunction of:

* an active membership in the mailbox's organization,
* a role permission for the kind of action, and
* for viewing and acting, an explicit `mailbox_permissions` row - unless the
  role is organization-wide by design.

Connecting and revoking are administrative and follow the role alone: the
person connecting a mailbox is establishing access, not exercising it.

Pure: no database, no session. The caller loads the facts; this decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Mirrors the TypeScript role matrix for the mailbox-relevant permissions.
ORGANIZATION_WIDE_ROLES = frozenset({"OWNER", "ADMIN", "AUDITOR"})
CONNECT_ROLES = frozenset({"OWNER", "ADMIN"})
VIEW_ROLES = frozenset({"OWNER", "ADMIN", "SUPERVISOR", "AGENT", "AUDITOR"})
ACTION_ROLES = frozenset({"OWNER", "ADMIN", "SUPERVISOR", "AGENT"})
READ_ONLY_ROLES = frozenset({"AUDITOR"})


class MailboxAction(StrEnum):
    VIEW = "view"
    ACT = "act"
    CONNECT = "connect"
    REVOKE = "revoke"


@dataclass(frozen=True)
class Actor:
    membership_id: str
    organization_id: str
    role: str
    status: str


@dataclass(frozen=True)
class MailboxFacts:
    mailbox_id: str
    organization_id: str
    status: str


@dataclass(frozen=True)
class PermissionGrant:
    mailbox_id: str
    membership_id: str
    can_view: bool
    can_action: bool


@dataclass(frozen=True)
class MailboxDecision:
    allowed: bool
    reason: str | None = None


ALLOW = MailboxDecision(True)


def authorize_mailbox(
    actor: Actor | None,
    mailbox: MailboxFacts | None,
    action: MailboxAction,
    grant: PermissionGrant | None = None,
) -> MailboxDecision:
    if actor is None:
        return MailboxDecision(False, "NOT_AUTHENTICATED")
    if mailbox is None:
        # Indistinguishable from "exists in another organization" by design.
        return MailboxDecision(False, "MAILBOX_NOT_FOUND")
    if actor.status != "ACTIVE":
        return MailboxDecision(False, "MEMBERSHIP_NOT_ACTIVE")
    if actor.organization_id != mailbox.organization_id:
        return MailboxDecision(False, "MAILBOX_NOT_IN_ORGANIZATION")

    # A grant for one mailbox is never evidence about another.
    if grant is not None and (
        grant.mailbox_id != mailbox.mailbox_id or grant.membership_id != actor.membership_id
    ):
        return MailboxDecision(False, "GRANT_DOES_NOT_MATCH")

    if action in (MailboxAction.CONNECT, MailboxAction.REVOKE):
        if actor.role not in CONNECT_ROLES:
            return MailboxDecision(False, "ROLE_CANNOT_ADMINISTER_MAILBOXES")
        return ALLOW

    if action is MailboxAction.ACT and actor.role in READ_ONLY_ROLES:
        return MailboxDecision(False, "ROLE_IS_READ_ONLY")

    allowed_roles = VIEW_ROLES if action is MailboxAction.VIEW else ACTION_ROLES
    if actor.role not in allowed_roles:
        return MailboxDecision(False, "ROLE_LACKS_PERMISSION")

    if mailbox.status == "REVOKED" and action is MailboxAction.ACT:
        return MailboxDecision(False, "MAILBOX_REVOKED")

    if actor.role in ORGANIZATION_WIDE_ROLES:
        return ALLOW

    if grant is None:
        return MailboxDecision(False, "NO_MAILBOX_PERMISSION")
    if action is MailboxAction.VIEW and not grant.can_view:
        return MailboxDecision(False, "NO_MAILBOX_PERMISSION")
    if action is MailboxAction.ACT and not (grant.can_view and grant.can_action):
        return MailboxDecision(False, "NO_MAILBOX_ACTION_PERMISSION")
    return ALLOW
