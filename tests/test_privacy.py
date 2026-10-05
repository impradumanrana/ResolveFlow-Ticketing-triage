"""Data-subject export and erasure (C13).

The live behaviour - that every column is actually scrubbed, that a hold
actually blocks, that the other client's copy survives - is verified against
real PostgreSQL and recorded in `CLIENT_C13_TEST_REPORT.md`. These are the
parts that can be held offline: the gates, the address handling, the
third-party redaction, and the structural rules that stop a future column of
personal data from being quietly missed.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from app.privacy import (
    _ERASURE_STATEMENTS,
    BLOCKING_HOLD_CLASSES,
    ERASED_ADDRESS,
    ERASURE_ROLES,
    TOMBSTONE,
    TOMBSTONE_TRACE,
    Actor,
    PrivacyRefused,
    SubjectFootprint,
    SubjectRequest,
    _redact_others,
    erase_subject,
    normalize_address,
    subject_digest,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
ORG = "11111111-1111-1111-1111-111111111111"
OTHER_ORG = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OWNER = Actor("33333333-3333-3333-3333-333333333333", ORG, "OWNER")


# ===========================================================================
# ADDRESSES
# ===========================================================================


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("  Jo.Rivera@Example.NET ", "jo.rivera@example.net"),
        ("a+tag@sub.example.co.uk", "a+tag@sub.example.co.uk"),
    ],
)
def test_an_address_is_compared_the_way_mail_is(given: str, expected: str) -> None:
    assert normalize_address(given) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "not-an-address",
        "@example.net",  # no local part
        "jo@",  # no domain
        "jo@example",  # no dot in the domain
        "jo@.net",
        "a b@c.io",  # whitespace inside
        "x" * 400,
    ],
)
def test_an_unusable_address_is_refused_rather_than_guessed(bad: str) -> None:
    with pytest.raises(PrivacyRefused) as refused:
        normalize_address(bad)
    assert refused.value.code == "SUBJECT_ADDRESS_INVALID"


def test_the_digest_identifies_a_subject_without_storing_them() -> None:
    digest = subject_digest("Jo.Rivera@Example.NET")

    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == subject_digest("jo.rivera@example.net"), (
        "case must not create a second subject"
    )
    assert "rivera" not in digest
    assert digest != subject_digest("jo.rivera@example.com")


def test_the_sentinel_address_can_never_route_anywhere() -> None:
    """RFC 2606 reserves `.invalid`, so an erased row cannot be mailed."""
    assert ERASED_ADDRESS.endswith(".invalid")
    assert "@" in ERASED_ADDRESS


# ===========================================================================
# THE GATES
# ===========================================================================


class FakeConnection:
    """Enough of a connection to reach the gates, and nothing more.

    Deliberately explodes on any statement: these tests assert that erasure
    refuses *before* it touches data, so a query reaching here is a failure.
    """

    def __init__(self, holds: list[str | None] | None = None):
        self.holds = holds or []
        self.statements: list[str] = []

    def execute(self, statement: object, parameters: dict[str, object] | None = None):  # noqa: ANN202
        text = str(statement)
        self.statements.append(text)
        if "legal_holds" in text:
            return _Rows([(hold,) for hold in self.holds])
        raise AssertionError(f"erasure reached the data before refusing: {text[:80]}")


class _Rows:
    def __init__(self, rows: list[tuple[object, ...]]):
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


@pytest.mark.parametrize("role", ["ADMIN", "SUPERVISOR", "AGENT", "AUDITOR", "KNOWLEDGE_MANAGER"])
def test_only_the_owner_may_erase(role: str) -> None:
    """The same authority as retention: an irreversible deletion belongs with
    the person accountable for the workspace."""
    connection = FakeConnection()

    with pytest.raises(PrivacyRefused) as refused:
        erase_subject(
            connection,
            SubjectRequest(ORG, "jo@example.net", Actor("m-1", ORG, role)),
        )

    assert refused.value.code == "ROLE_LACKS_PERMISSION"
    assert connection.statements == [], "the gate must come before any query"


def test_the_owner_role_is_the_only_one_declared() -> None:
    assert ERASURE_ROLES == {"OWNER"}


def test_an_actor_from_another_organization_is_refused_first() -> None:
    """Checked before the role, so a cross-tenant attempt is reported as such."""
    connection = FakeConnection()

    with pytest.raises(PrivacyRefused) as refused:
        erase_subject(
            connection,
            SubjectRequest(ORG, "jo@example.net", Actor("m-1", OTHER_ORG, "OWNER")),
        )

    assert refused.value.code == "RESOURCE_NOT_IN_ORGANIZATION"
    assert connection.statements == []


@pytest.mark.parametrize("hold", ["MESSAGE", "TICKET", None])
def test_a_legal_hold_blocks_erasure(hold: str | None) -> None:
    """A hold exists to stop data being destroyed. Honouring an erasure over
    one would be the more serious failure of the two."""
    connection = FakeConnection(holds=[hold])

    with pytest.raises(PrivacyRefused) as refused:
        erase_subject(connection, SubjectRequest(ORG, "jo@example.net", OWNER))

    assert refused.value.code == "LEGAL_HOLD_ACTIVE"
    # It refused after reading the holds, and before reading any data.
    assert len(connection.statements) == 1
    assert "legal_holds" in connection.statements[0]


def test_a_hold_on_something_else_does_not_block_erasure() -> None:
    """An unrelated hold must not become a way to refuse every request."""
    connection = FakeConnection(holds=["USAGE"])

    with pytest.raises(AssertionError):
        # Proceeds past the gates and reaches the data, which the fake refuses
        # to serve. That is the pass condition here.
        erase_subject(connection, SubjectRequest(ORG, "jo@example.net", OWNER))


def test_the_blocking_hold_classes_are_the_ones_customer_data_lives_in() -> None:
    assert BLOCKING_HOLD_CLASSES == {"__ALL__", "MESSAGE", "TICKET"}


# ===========================================================================
# EXPORT REDACTS OTHER PEOPLE
# ===========================================================================


def test_the_subject_sees_their_own_address_and_nobody_elses() -> None:
    """Answering one access request must not create a breach for someone else."""
    kept = _redact_others(
        ["jo.rivera@example.net", "colleague@partner.example", "Support@Acme.example"],
        "jo.rivera@example.net",
    )

    assert kept[0] == "jo.rivera@example.net"
    assert kept[1] == "[email]"
    assert kept[2] == "[email]"


def test_the_subjects_own_address_is_matched_case_insensitively() -> None:
    assert _redact_others(["Jo.Rivera@Example.NET"], "jo.rivera@example.net") == [
        "Jo.Rivera@Example.NET"
    ]


def test_an_empty_recipient_list_is_handled() -> None:
    assert _redact_others(None, "jo@example.net") == []
    assert _redact_others([], "jo@example.net") == []


# ===========================================================================
# STRUCTURE: NOTHING PERSONAL IS MISSED, NOTHING IS UNSCOPED
# ===========================================================================

# Every table that holds something about a customer, and what it holds. A new
# one has to be added here *and* covered by a statement, which is the point.
PERSONAL_TABLES: dict[str, str] = {
    "messages": "their words, their address, the subject line",
    "attachments": "filenames carry names, invoice numbers and case references",
    "threads": "the participant list and the subject line",
    "tickets": "the customer address and the subject line",
    "draft_revisions": "the reply prepared for them quotes their situation",
    "triage_runs": "the trace holds the ticket text sent to the model",
    "actions": "a reviewer's reason can quote the customer",
    "vip_contacts": "the address is the whole record",
}


def test_every_table_holding_personal_data_is_erased() -> None:
    covered = {table for table, _ in _ERASURE_STATEMENTS}

    assert covered == set(PERSONAL_TABLES), (
        f"missing: {sorted(set(PERSONAL_TABLES) - covered)}; "
        f"undeclared: {sorted(covered - set(PERSONAL_TABLES))}"
    )


@pytest.mark.parametrize(
    ("table", "statement"), _ERASURE_STATEMENTS, ids=[t for t, _ in _ERASURE_STATEMENTS]
)
def test_every_erasure_statement_is_scoped_to_one_organization(table: str, statement: str) -> None:
    """An erasure that reaches another client's data is a breach, not a fix."""
    assert "organization_id = CAST(:org AS uuid)" in statement, table


@pytest.mark.parametrize(
    ("table", "statement"), _ERASURE_STATEMENTS, ids=[t for t, _ in _ERASURE_STATEMENTS]
)
def test_no_erasure_statement_drops_a_row_that_holds_history(table: str, statement: str) -> None:
    """Deleting the rows would take the decision history with them.

    `vip_contacts` is the one exception, and it is an exception because the
    address *is* the record: there is nothing left to keep.
    """
    if table == "vip_contacts":
        assert statement.startswith("DELETE FROM")
        return
    assert statement.startswith("UPDATE"), table
    assert "DELETE" not in statement, table


def test_the_trace_tombstone_satisfies_the_shape_the_schema_requires() -> None:
    """C11 constrains `triage_runs.trace` to a JSON array, so the tombstone is
    one - and it says "erased" rather than being an empty array, which would
    be indistinguishable from a run that recorded nothing."""
    import json

    parsed = json.loads(TOMBSTONE_TRACE)
    assert isinstance(parsed, list)
    assert parsed == [{"erased": True}]


def test_the_tombstone_is_non_empty_because_a_column_requires_it() -> None:
    """`draft_revisions.body` is constrained to be non-blank (C12)."""
    assert TOMBSTONE.strip()


def test_erasure_does_not_touch_the_append_only_audit_trail() -> None:
    """Audit rows are retained by design; the way that stays compatible with
    erasure is that they never carry customer content in the first place."""
    for table, statement in _ERASURE_STATEMENTS:
        assert "audit_events" not in statement, table


# ===========================================================================
# WHICH IS ONLY TRUE IF AUDIT METADATA NEVER CARRIES CONTENT
# ===========================================================================

# Field names whose value is a customer's content or address. If one of these
# is ever handed to an audit writer, erasure becomes incomplete and there is no
# way to fix it afterwards, because the row cannot be updated.
CONTENT_FIELDS = frozenset(
    {
        "body",
        "body_text",
        "snippet",
        "subject",
        "customer_address",
        "from_address",
        "to_addresses",
        "cc_addresses",
        "participant_addresses",
        "filename",
        "draft",
        "trace",
        "reason_text",
    }
)

PYTHON_SOURCES = sorted(
    path for path in (ROOT / "app").rglob("*.py") if "__pycache__" not in path.parts
)


def _audit_metadata_keys(tree: ast.AST) -> list[tuple[int, str]]:
    """Keys of any dict passed as `metadata=` to a call."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "metadata" or not isinstance(keyword.value, ast.Dict):
                continue
            for key in keyword.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    found.append((node.lineno, key.value))
    return found


def test_the_audit_metadata_scan_finds_the_writers_it_checks() -> None:
    total = 0
    for source in PYTHON_SOURCES:
        total += len(_audit_metadata_keys(ast.parse(source.read_text(encoding="utf-8"))))

    assert total > 5, f"only {total} audit metadata keys found; the scan is broken"


def test_no_audit_event_carries_customer_content() -> None:
    offenders: list[str] = []
    for source in PYTHON_SOURCES:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for line, key in _audit_metadata_keys(tree):
            if key in CONTENT_FIELDS:
                offenders.append(f"{source.relative_to(ROOT)}:{line} metadata[{key!r}]")

    assert not offenders, (
        "audit rows are append-only, so content written here outlives an "
        f"erasure request and cannot be removed: {offenders}"
    )


def test_the_erasure_record_holds_no_address() -> None:
    migration = (ROOT / "migrations/versions/20260920_0012_erasure_records.py").read_text()

    assert "subject_digest" in migration
    assert "subject_is_a_sha256_digest" in migration
    # And it is append-only, like the audit trail it complements.
    assert "no_update_or_delete" in migration


def test_the_retention_sweep_still_refuses_to_delete_tickets() -> None:
    """C04 left ticket deletion to this workflow; it must not have become a
    sweepable class in the meantime."""
    from app.retention import RETENTION_TARGETS, UNSWEEPABLE_CLASSES

    assert "TICKET" in UNSWEEPABLE_CLASSES
    assert "AUDIT_EVENT" in UNSWEEPABLE_CLASSES
    assert "TICKET" not in RETENTION_TARGETS


def test_a_footprint_knows_when_it_is_empty() -> None:
    assert SubjectFootprint().empty
    assert not SubjectFootprint(ticket_ids=("t-1",)).empty
    assert not SubjectFootprint(message_ids=("m-1",)).empty
    assert not SubjectFootprint(thread_ids=("h-1",)).empty
