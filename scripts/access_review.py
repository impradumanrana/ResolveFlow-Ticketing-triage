"""Access review (C15).

Who can reach this workspace, in what role, and over which mailboxes. Run
monthly by the client's administrator, and once before launch as a gate item.

The report is in two parts, and the split is the point:

* **Findings** are wrong now. A revoked member with a live session, a mailbox
  permission for somebody who is no longer active, an account with no
  membership. Each one is a door that should be shut. The exit status is
  non-zero if any exists, so this can gate a release.
* **Observations** need a human to decide. Six Owners might be right for a
  large team and wrong for a small one; a dormant account might be somebody on
  leave. Reporting these as failures would train an administrator to ignore
  the output, which is worse than not running it.

**Every statement is read-only.** A review that changes access is not a
review, and a test asserts no statement here mutates anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# How long without a session before an account is worth asking about.
DORMANT_DAYS = 60

# More than this many Owners in one workspace is worth a question. Ownership
# carries retention and erasure, which cannot be undone.
EXPECTED_OWNERS = 2

QUERIES: dict[str, str] = {
    "members": (
        "SELECT m.id, u.email, m.role::text, m.status::text, m.created_at, "
        "       (SELECT max(s.expires) FROM sessions s WHERE s.user_id = u.id) AS last_session "
        "FROM memberships m JOIN users u ON u.id = m.user_id "
        "WHERE m.organization_id = CAST(:org AS uuid) ORDER BY m.role, u.email"
    ),
    "mailbox_permissions": (
        "SELECT u.email, mb.email_address, mp.can_view, mp.can_action "
        "FROM mailbox_permissions mp "
        "JOIN memberships m ON m.id = mp.membership_id "
        "JOIN users u ON u.id = m.user_id "
        "JOIN mailboxes mb ON mb.id = mp.mailbox_id "
        "WHERE mp.organization_id = CAST(:org AS uuid) ORDER BY u.email, mb.email_address"
    ),
    "departments": (
        "SELECT u.email, d.name FROM department_memberships dm "
        "JOIN memberships m ON m.id = dm.membership_id "
        "JOIN users u ON u.id = m.user_id "
        "JOIN departments d ON d.id = dm.department_id "
        "WHERE dm.organization_id = CAST(:org AS uuid) ORDER BY u.email, d.name"
    ),
    # Findings, each as a count plus the rows behind it.
    "live_session_for_inactive_member": (
        "SELECT u.email, m.status::text FROM memberships m "
        "JOIN users u ON u.id = m.user_id "
        "JOIN sessions s ON s.user_id = u.id "
        "WHERE m.organization_id = CAST(:org AS uuid) "
        "AND m.status <> 'ACTIVE' AND s.expires > now()"
    ),
    "permission_for_inactive_member": (
        "SELECT u.email, mb.email_address, m.status::text FROM mailbox_permissions mp "
        "JOIN memberships m ON m.id = mp.membership_id "
        "JOIN users u ON u.id = m.user_id "
        "JOIN mailboxes mb ON mb.id = mp.mailbox_id "
        "WHERE mp.organization_id = CAST(:org AS uuid) AND m.status <> 'ACTIVE'"
    ),
    "account_without_membership": (
        "SELECT u.email FROM users u WHERE NOT EXISTS "
        "(SELECT 1 FROM memberships m WHERE m.user_id = u.id)"
    ),
    "expired_session_still_present": "SELECT count(*) FROM sessions WHERE expires < now()",
    "domain_allowlist": (
        "SELECT domain FROM organization_domains "
        "WHERE organization_id = CAST(:org AS uuid) ORDER BY domain"
    ),
}


@dataclass
class Review:
    findings: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.findings


def _rows(connection: Any, statement: str, organization_id: str) -> list[Any]:
    from sqlalchemy import text

    parameters = {"org": organization_id} if ":org" in statement else {}
    return connection.execute(text(statement), parameters).all()


def review(connection: Any, organization_id: str) -> Review:
    from datetime import UTC, datetime, timedelta

    result = Review()
    now = datetime.now(UTC)

    members = _rows(connection, QUERIES["members"], organization_id)
    permissions = _rows(connection, QUERIES["mailbox_permissions"], organization_id)
    departments = _rows(connection, QUERIES["departments"], organization_id)
    domains = [row[0] for row in _rows(connection, QUERIES["domain_allowlist"], organization_id)]

    result.data["members"] = [
        {
            "email": row[1],
            "role": row[2],
            "status": row[3],
            "since": row[4].isoformat() if row[4] else None,
            "last_session_expiry": row[5].isoformat() if row[5] else None,
        }
        for row in members
    ]
    result.data["mailbox_permissions"] = [
        {"email": r[0], "mailbox": r[1], "can_view": r[2], "can_action": r[3]} for r in permissions
    ]
    result.data["departments"] = [{"email": r[0], "department": r[1]} for r in departments]
    result.data["allowed_domains"] = domains

    # -- findings -------------------------------------------------------
    for email, status in _rows(
        connection, QUERIES["live_session_for_inactive_member"], organization_id
    ):
        result.findings.append(
            f"{email} is {status} but has a session that has not expired - delete the session"
        )
    for email, mailbox, status in _rows(
        connection, QUERIES["permission_for_inactive_member"], organization_id
    ):
        result.findings.append(
            f"{email} is {status} but still holds a permission on {mailbox} - remove it"
        )
    for (email,) in _rows(connection, QUERIES["account_without_membership"], organization_id):
        result.findings.append(
            f"{email} has an account with no membership - it cannot sign in, so remove the account"
        )
    if not domains:
        result.findings.append(
            "no sign-in domain is allowlisted, so domain restriction is not in force"
        )

    active = [m for m in result.data["members"] if m["status"] == "ACTIVE"]
    owners = [m for m in active if m["role"] == "OWNER"]
    if not owners:
        result.findings.append("there is no active Owner - retention and erasure are unreachable")

    # -- observations ---------------------------------------------------
    if len(owners) > EXPECTED_OWNERS:
        result.observations.append(
            f"{len(owners)} active Owners. Ownership carries retention and erasure; confirm each "
            "one needs it"
        )
    expired = _rows(connection, QUERIES["expired_session_still_present"], organization_id)
    expired_count = expired[0][0] if expired else 0
    if expired_count:
        result.observations.append(
            f"{expired_count} expired sessions are still stored. Harmless, but they are rows "
            "about people; the retention sweep can remove them"
        )

    permitted = {p["email"] for p in result.data["mailbox_permissions"]}
    organization_wide = {"OWNER", "ADMIN", "AUDITOR"}
    for member in active:
        if member["role"] in organization_wide or member["role"] == "KNOWLEDGE_MANAGER":
            continue
        if member["email"] not in permitted:
            result.observations.append(
                f"{member['email']} ({member['role']}) holds no mailbox permission, so they see "
                "nothing. Intended, or a half-finished setup?"
            )

    cutoff = now - timedelta(days=DORMANT_DAYS)
    for member in active:
        expiry = member["last_session_expiry"]
        if expiry is None:
            result.observations.append(f"{member['email']} ({member['role']}) has never signed in")
        elif datetime.fromisoformat(expiry) < cutoff:
            result.observations.append(
                f"{member['email']} ({member['role']}) has not signed in for over "
                f"{DORMANT_DAYS} days"
            )

    actioners = {p["email"] for p in result.data["mailbox_permissions"] if p["can_action"]}
    result.data["summary"] = {
        "active_members": len(active),
        "by_role": {
            role: sum(1 for m in active if m["role"] == role)
            for role in sorted({m["role"] for m in active})
        },
        "can_action_a_mailbox": len(actioners),
        "allowed_domains": domains,
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--organization", required=True, help="organization id")
    parser.add_argument("--json", type=Path, help="write the full review here")
    arguments = parser.parse_args(argv)

    from sqlalchemy import create_engine

    engine = create_engine(arguments.database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        result = review(connection, arguments.organization)

    summary = result.data["summary"]
    print(f"\naccess review - {summary['active_members']} active members")
    print(f"by role: {summary['by_role']}")
    print(f"can act on a mailbox: {summary['can_action_a_mailbox']}")
    print(f"sign-in domains: {', '.join(summary['allowed_domains']) or 'NONE'}\n")

    print(f"{'member':34} {'role':18} {'status':10} last session expiry")
    for member in result.data["members"]:
        print(
            f"{member['email'][:33]:34} {member['role']:18} {member['status']:10} "
            f"{member['last_session_expiry'] or 'never'}"
        )

    if result.findings:
        print(f"\n{len(result.findings)} finding(s) - these are wrong now:")
        for finding in result.findings:
            print(f"  - {finding}")
    if result.observations:
        print(f"\n{len(result.observations)} observation(s) - these need a decision:")
        for observation in result.observations:
            print(f"  - {observation}")
    if not result.findings and not result.observations:
        print("\nNothing to report.")

    print(f"\nVerdict: {'PASS' if result.clean else 'FAIL'}")

    if arguments.json:
        arguments.json.write_text(
            json.dumps(
                {
                    "clean": result.clean,
                    "findings": result.findings,
                    "observations": result.observations,
                    **result.data,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    return 0 if result.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
