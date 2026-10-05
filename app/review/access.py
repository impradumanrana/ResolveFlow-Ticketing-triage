"""Who may decide what.

Mirrors the TypeScript role matrix for the review permissions, so the service
refuses on its own rather than trusting the caller to have checked. The web
tier checks the same permissions to decide what to render; neither check is
sufficient alone - one hides a button, the other refuses the request.
"""

from __future__ import annotations

from app.review.records import Decision

# Mirrors apps/web/src/lib/authz/roles.ts.
PERMISSIONS: dict[Decision, str] = {
    Decision.EDIT: "ticket.review_draft",
    Decision.APPROVE: "ticket.approve_draft",
    Decision.REJECT: "ticket.review_draft",
    Decision.REROUTE: "ticket.reroute",
    Decision.ASSIGN: "ticket.assign",
    Decision.RESOLVE: "ticket.update",
}

ROLES: dict[str, frozenset[str]] = {
    "ticket.review_draft": frozenset({"OWNER", "ADMIN", "SUPERVISOR", "AGENT"}),
    "ticket.approve_draft": frozenset({"OWNER", "ADMIN", "SUPERVISOR", "AGENT"}),
    "ticket.reroute": frozenset({"OWNER", "ADMIN", "SUPERVISOR"}),
    "ticket.assign": frozenset({"OWNER", "ADMIN", "SUPERVISOR"}),
    "ticket.update": frozenset({"OWNER", "ADMIN", "SUPERVISOR", "AGENT"}),
}

# A role that may only read can never decide, whatever the matrix says.
READ_ONLY_ROLES = frozenset({"AUDITOR"})


def permission_for(decision: Decision) -> str:
    return PERMISSIONS[decision]


def refusal_for(actor_role: str, actor_status: str, decision: Decision) -> str | None:
    """The reason this actor may not make this decision, or None."""
    if actor_status != "ACTIVE":
        return "MEMBERSHIP_NOT_ACTIVE"
    if actor_role in READ_ONLY_ROLES:
        return "ROLE_IS_READ_ONLY"
    if actor_role not in ROLES[permission_for(decision)]:
        return "ROLE_LACKS_PERMISSION"
    return None
