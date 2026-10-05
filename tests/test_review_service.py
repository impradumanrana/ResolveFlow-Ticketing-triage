"""C12 gate: no path auto-sends, concurrency and duplicates are decided, and
every decision is attributable.

The service runs against the in-memory store, which mirrors the database's
guarantees: visibility, optimistic locking, idempotent replay, and one live
provider draft per conversation.
"""

from __future__ import annotations

import pathlib
import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.mailbox.scopes import GMAIL_READONLY
from app.review.access import PERMISSIONS, ROLES
from app.review.drafts import GMAIL_COMPOSE, CreatedDraft, DraftRefused
from app.review.memory_store import InMemoryReviewStore
from app.review.records import (
    Actor,
    Decision,
    ReviewRefused,
    ReviewRequest,
    TicketFacts,
    VersionConflict,
    digest,
)
from app.review.service import ReviewService

ORG = "00000000-0000-0000-0000-0000000000a1"
TICKET = "11111111-1111-1111-1111-111111111111"
MAILBOX = "22222222-2222-2222-2222-222222222222"
THREAD = "33333333-3333-3333-3333-333333333333"
AGENT = "44444444-4444-4444-4444-444444444444"
OTHER_AGENT = "55555555-5555-5555-5555-555555555555"
QUEUE = "66666666-6666-6666-6666-666666666666"
DEPARTMENT = "77777777-7777-7777-7777-777777777777"
NOW = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
MODEL_DRAFT = "Open the sign-in page and choose Forgot password. [KB-001]"


def actor(role: str = "AGENT", membership: str = AGENT, status: str = "ACTIVE") -> Actor:
    return Actor(membership_id=membership, organization_id=ORG, role=role, status=status)


def facts(**overrides) -> TicketFacts:
    base = {
        "ticket_id": TICKET,
        "organization_id": ORG,
        "mailbox_id": MAILBOX,
        "thread_id": THREAD,
        "version": 3,
        "status": "WAITING_ON_REVIEW",
        "reference": 42,
        "subject": "Reset my password",
        "customer_address": "customer@example.net",
        "triage_run_id": None,
        "model_draft": MODEL_DRAFT,
        "citations": ("KB-001",),
        "grounding_validated": True,
        "route": "AUTO_RESOLVE",
        "granted_scopes": (GMAIL_READONLY,),
    }
    return TicketFacts(**{**base, **overrides})


class ScriptedComposer:
    """Stands in for Gmail. Records what it was asked to create."""

    def __init__(self, outcome: object = None):
        self.outcome = outcome
        self.calls: list[dict[str, object]] = []

    def create(self, access_token: str, *, raw: str, thread_id: str | None) -> CreatedDraft:
        self.calls.append({"token": access_token, "raw": raw, "thread_id": thread_id})
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return CreatedDraft(provider_draft_id="draft-1", provider_thread_id=thread_id)


def build(ticket: TicketFacts | None = None, *, composer=None, scopes=None, permit=True):
    ticket = ticket or facts()
    if scopes is not None:
        ticket = replace(ticket, granted_scopes=scopes)
    store = InMemoryReviewStore(
        tickets={ticket.ticket_id: ticket},
        action_permissions={AGENT: {MAILBOX} if permit else set(), OTHER_AGENT: {MAILBOX}},
        inbound={
            THREAD: {
                "from_address": "Customer <customer@example.net>",
                "subject": "Reset my password",
                "rfc822_message_id": "<first@mail>",
                "to_addresses": ["support@acme.example"],
                "cc_addresses": [],
            }
        },
        mailbox_addresses={MAILBOX: "support@acme.example"},
    )
    service = ReviewService(store, token_provider=lambda mailbox: "ya29.token", now=lambda: NOW)
    if composer is not None:
        service._compose_with = composer  # type: ignore[attr-defined]
        import app.review.service as module

        service.transport = object()
        module.composer_for = lambda scopes, transport: composer  # type: ignore[assignment]
    return service, store


@pytest.fixture(autouse=True)
def restore_composer_factory():
    import app.review.service as module

    original = module.composer_for
    yield
    module.composer_for = original


def request_for(decision: Decision, *, version: int = 3, key: str | None = None, **overrides):
    return ReviewRequest(
        organization_id=ORG,
        ticket_id=TICKET,
        actor=overrides.pop("actor", actor()),
        decision=decision,
        idempotency_key=f"key-{uuid.uuid4()}" if key is None else key,
        expected_version=version,
        **overrides,
    )


# --------------------------------------------------------------------------
# Nothing is ever sent
# --------------------------------------------------------------------------


def code_only(path) -> str:
    """Source with comments and docstrings removed.

    The guarantee is about what the code does, not what the prose says: this
    file's own docstrings name the send endpoints in order to say they are
    absent, and C06's scope policy names them in a blocklist.
    """
    import ast
    import re

    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        tree = ast.parse(text)
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        kept: list[str] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                kept.append(node.value)
            elif isinstance(node, ast.Attribute):
                kept.append(node.attr)
            elif isinstance(node, ast.Name):
                kept.append(node.id)
        # Strings and identifiers only: a comment or docstring cannot call anything.
        return "\n".join(kept)
    body = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    body = re.sub(r"^\s*//.*$", "", body, flags=re.M)
    return body


def test_no_code_path_calls_a_send_endpoint():
    """The structural guarantee: there is no send path to reach."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in [
        *root.glob("app/**/*.py"),
        *root.glob("scripts/*.py"),
        *root.glob("apps/web/src/**/*.ts"),
        *root.glob("apps/web/src/**/*.tsx"),
    ]:
        if "__pycache__" in str(path):
            continue
        source = code_only(path)
        for pattern in ("messages/send", "drafts/send", "users/me/messages/send", "sendMessage"):
            if pattern in source:
                offenders.append(f"{path.relative_to(root)}: {pattern}")
    assert offenders == [], offenders


def test_the_scan_would_notice_a_send_call(tmp_path):
    """The scan is only worth having if it fails on a real one."""
    offender = tmp_path / "sender.py"
    offender.write_text(
        'ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"\n'
    )
    assert "messages/send" in code_only(offender)
    commentary = tmp_path / "prose.py"
    commentary.write_text('"""We never call messages/send."""\n# not drafts/send either\n')
    assert "messages/send" not in code_only(commentary)


def test_the_only_provider_endpoint_is_the_drafts_endpoint():
    from app.review import drafts

    assert drafts.DRAFTS_ENDPOINT == "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
    from pathlib import Path

    assert "send" not in code_only(Path(drafts.__file__))
    assert not hasattr(drafts.GmailDraftComposer, "send")
    assert [name for name in dir(drafts.GmailDraftComposer) if not name.startswith("_")] == [
        "create"
    ]


def test_approval_on_a_read_only_mailbox_records_the_decision_and_creates_nothing():
    service, store = build()
    outcome = service.review(request_for(Decision.APPROVE))

    assert outcome.ok and outcome.code == "APPLIED"
    assert outcome.draft is not None
    assert (outcome.draft.status, outcome.draft.failure_code) == (
        "REFUSED",
        "DRAFT_SCOPE_NOT_GRANTED",
    )
    assert outcome.provider_draft_created is False
    # The human decision is not lost because the provider cannot be written to.
    assert [a["action_type"] for a in store.actions] == ["DRAFT_APPROVED", "PROVIDER_DRAFT_CREATED"]
    assert store.actions[0]["outcome"] == "SUCCEEDED"
    assert store.actions[1]["outcome"] == "FAILED"
    assert store.drafts[0]["status"] == "REFUSED"


def _load_migration(name: str):
    """Import a migration by filename. `migrations/versions` is not a package."""
    import importlib.util

    path = pathlib.Path("migrations/versions") / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_database_refuses_to_store_a_credential_that_could_send():
    """A migration, not just policy, is what keeps sending impossible.

    C06 forbade every write-capable scope, which also made a draft impossible.
    0010 permits `gmail.compose` alone. Asserted against the constraint the
    migration actually builds, and against the scope policy's own list, so the
    two cannot drift apart.
    """
    from app.mailbox.scopes import GMAIL_COMPOSE, NEVER_PERMITTED_SCOPES

    module = _load_migration("20260918_0010_compose_scope")

    assert set(module.REFUSED_SCOPES) == set(NEVER_PERMITTED_SCOPES), (
        "the migration and the scope policy disagree about what may be stored"
    )
    for scope in NEVER_PERMITTED_SCOPES:
        assert f"NOT ('{scope}' = ANY(granted_scopes))" in module.NO_SEND_SCOPE, scope
    # Permitted, so absent from the new constraint. Still present in the C06
    # text the downgrade restores, which is why this reads the constraint only.
    assert GMAIL_COMPOSE not in module.NO_SEND_SCOPE
    assert GMAIL_COMPOSE in module.NO_WRITE_SCOPE_IN_V1

    source = pathlib.Path("migrations/versions/20260918_0010_compose_scope.py").read_text()
    assert 'create_check_constraint("no_send_capable_scope"' in source
    assert 'drop_constraint("no_write_scope_in_v1"' in source


def test_sending_stays_disabled_at_the_organization_level():
    from pathlib import Path

    identity = Path("migrations/versions/20260915_0002_identity.py").read_text()
    assert "sending_disabled_in_v1" in identity
    assert "sending_enabled = false" in identity


# --------------------------------------------------------------------------
# A draft is created only when the mailbox was granted the scope
# --------------------------------------------------------------------------


def test_a_drafting_mailbox_gets_a_draft_addressed_to_the_customer():
    composer = ScriptedComposer()
    service, store = build(scopes=(GMAIL_READONLY, GMAIL_COMPOSE), composer=composer)

    outcome = service.review(request_for(Decision.APPROVE))

    assert outcome.provider_draft_created
    assert outcome.draft is not None and outcome.draft.provider_draft_id == "draft-1"
    assert store.drafts[0]["status"] == "CREATED"
    assert store.drafts[0]["approved_by_membership_id"] == AGENT

    import base64

    mime = base64.urlsafe_b64decode(str(composer.calls[0]["raw"])).decode()
    assert "To: customer@example.net" in mime
    assert "Subject: Re: Reset my password" in mime
    assert "In-Reply-To: <first@mail>" in mime
    assert MODEL_DRAFT in mime
    assert composer.calls[0]["thread_id"] == THREAD
    assert composer.calls[0]["token"] == "ya29.token"


def test_a_provider_failure_is_recorded_and_the_approval_still_stands():
    composer = ScriptedComposer(DraftRefused("PROVIDER_RATE_LIMITED", transient=True, status=429))
    service, store = build(scopes=(GMAIL_READONLY, GMAIL_COMPOSE), composer=composer)

    outcome = service.review(request_for(Decision.APPROVE))

    assert outcome.ok, "the decision was made; the provider call is what failed"
    assert outcome.draft is not None and outcome.draft.status == "FAILED"
    assert outcome.draft.failure_code == "PROVIDER_RATE_LIMITED"
    assert store.drafts[0]["status"] == "FAILED"


def test_a_conversation_with_no_customer_address_produces_no_draft():
    composer = ScriptedComposer()
    service, store = build(
        facts(customer_address=None), scopes=(GMAIL_READONLY, GMAIL_COMPOSE), composer=composer
    )
    service.store.inbound.clear()

    outcome = service.review(request_for(Decision.APPROVE))

    assert outcome.draft is not None and outcome.draft.failure_code == "NO_CUSTOMER_ADDRESS"
    assert composer.calls == [], "nothing was created without a recipient"


def test_a_claim_is_taken_before_the_provider_is_called():
    order: list[str] = []
    store_holder: dict[str, InMemoryReviewStore] = {}

    class WatchingComposer(ScriptedComposer):
        def create(self, access_token, *, raw, thread_id):
            order.append(f"provider-call:{store_holder['store'].drafts[0]['status']}")
            return super().create(access_token, raw=raw, thread_id=thread_id)

    composer = WatchingComposer()
    service, store = build(scopes=(GMAIL_READONLY, GMAIL_COMPOSE), composer=composer)
    store_holder["store"] = store

    service.review(request_for(Decision.APPROVE))
    assert order == ["provider-call:PENDING"], "the draft was not claimed first"
    assert store.drafts[0]["status"] == "CREATED"


def test_a_claim_whose_outcome_is_unknown_is_closed_not_reused():
    composer = ScriptedComposer()
    service, store = build(scopes=(GMAIL_READONLY, GMAIL_COMPOSE), composer=composer)
    store.drafts.append(
        {
            "id": "stale",
            "organization_id": ORG,
            "ticket_id": TICKET,
            "mailbox_id": MAILBOX,
            "revision_id": None,
            "status": "PENDING",
            "failure_code": None,
            "provider_draft_id": None,
            "provider_thread_id": None,
            "approved_by_membership_id": AGENT,
            "idempotency_key": "old:draft",
            "body_sha256": digest("x"),
            "claimed_at": NOW - timedelta(minutes=30),
            "settled_at": None,
        }
    )
    assert service.release_stale_claims() == 1
    assert store.drafts[0]["failure_code"] == "DRAFT_OUTCOME_UNKNOWN"
    assert service.release_stale_claims() == 0


# --------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------


def test_two_reviewers_on_the_same_version_produce_one_change_and_one_conflict():
    service, store = build()

    first = service.review(request_for(Decision.REJECT, version=3, reason="Needs a human reply."))
    assert first.ok and first.ticket_version == 4

    with pytest.raises(VersionConflict) as conflict:
        service.review(request_for(Decision.REJECT, version=3, reason="Also needs a human."))
    assert conflict.value.current_version == 4
    assert conflict.value.code == "VERSION_CONFLICT"

    # Both attempts are attributable: one applied, one refused with the reason.
    outcomes = [(a["action_type"], a["outcome"], a["error_code"]) for a in store.actions]
    assert outcomes == [
        ("DRAFT_REJECTED", "SUCCEEDED", None),
        ("DRAFT_REJECTED", "REJECTED", "VERSION_CONFLICT"),
    ]
    assert store.tickets[TICKET].version == 4


def test_concurrent_approvals_create_exactly_one_draft():
    composer = ScriptedComposer()
    service, store = build(scopes=(GMAIL_READONLY, GMAIL_COMPOSE), composer=composer)
    results: list[str] = []
    lock = threading.Lock()

    def approve(number: int) -> None:
        try:
            outcome = service.review(request_for(Decision.APPROVE, key=f"approval-{number}"))
            with lock:
                results.append(outcome.code)
        except (VersionConflict, ReviewRefused) as refused:
            with lock:
                results.append(refused.code)

    threads = [threading.Thread(target=approve, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert results.count("APPLIED") == 1, results
    assert results.count("VERSION_CONFLICT") == 7, results
    assert len(composer.calls) == 1, "more than one draft was created"
    assert len([d for d in store.drafts if d["status"] == "CREATED"]) == 1


def test_a_decision_against_a_stale_version_is_refused_before_anything_changes():
    service, store = build()
    with pytest.raises(VersionConflict):
        service.review(request_for(Decision.RESOLVE, version=2))
    assert store.tickets[TICKET].version == 3
    assert store.tickets[TICKET].status == "WAITING_ON_REVIEW"


# --------------------------------------------------------------------------
# Duplicate actions
# --------------------------------------------------------------------------


def test_the_same_idempotency_key_replays_the_first_outcome():
    composer = ScriptedComposer()
    service, store = build(scopes=(GMAIL_READONLY, GMAIL_COMPOSE), composer=composer)

    first = service.review(request_for(Decision.APPROVE, key="one-click"))
    again = service.review(request_for(Decision.APPROVE, key="one-click"))

    assert first.ok and again.ok
    assert again.replayed is True
    assert again.ticket_version == first.ticket_version
    assert again.draft is not None and again.draft.provider_draft_id == "draft-1"
    assert len(composer.calls) == 1, "the provider was called twice"
    assert store.tickets[TICKET].version == 4, "the ticket moved twice"
    assert len([a for a in store.actions if a["action_type"] == "DRAFT_APPROVED"]) == 1


def test_a_replay_reports_a_refusal_as_a_refusal():
    service, store = build()
    with pytest.raises(ReviewRefused):
        service.review(request_for(Decision.REJECT, key="no-reason-given"))
    # A refusal carries no idempotency key, so the same key may be used again
    # once the mistake is corrected.
    outcome = service.review(
        request_for(Decision.REJECT, key="no-reason-given", reason="Not safe.")
    )
    assert outcome.ok


def test_an_unusable_idempotency_key_is_refused_before_anything_is_read():
    service, store = build()
    for key in ("", "short", "x" * 300):
        with pytest.raises(ReviewRefused) as refused:
            service.review(request_for(Decision.RESOLVE, key=key))
        assert refused.value.code == "IDEMPOTENCY_KEY_INVALID"
    assert store.actions == []


# --------------------------------------------------------------------------
# Every decision is attributable
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decision", "extra", "action_type"),
    [
        (Decision.EDIT, {"body": "A clearer reply. [KB-001]"}, "DRAFT_EDITED"),
        (Decision.APPROVE, {}, "DRAFT_APPROVED"),
        (Decision.REJECT, {"reason": "Customer needs a person."}, "DRAFT_REJECTED"),
        (
            Decision.REROUTE,
            {"reason": "Billing owns this.", "queue_id": QUEUE, "actor": actor("SUPERVISOR")},
            "TICKET_REROUTED",
        ),
        (
            Decision.ASSIGN,
            {"assignee_membership_id": OTHER_AGENT, "actor": actor("SUPERVISOR")},
            "TICKET_ASSIGNED",
        ),
        (Decision.RESOLVE, {}, "TICKET_RESOLVED"),
    ],
)
def test_every_decision_records_who_what_and_which_version(decision, extra, action_type):
    service, store = build()
    outcome = service.review(request_for(decision, **extra))

    assert outcome.ok
    action = store.actions[0]
    assert action["action_type"] == action_type
    assert action["actor_membership_id"] == AGENT
    assert action["reason"] == extra.get("reason")
    assert action["ticket_version_before"] == 3
    assert action["ticket_version_after"] == 4
    assert action["idempotency_key"]

    audit = store.audits[0]
    assert audit["outcome"] == "ALLOWED"
    assert audit["action"] == f"review.{decision.value.lower()}"
    assert audit["actor_membership_id"] == AGENT
    assert audit["target_id"] == TICKET


def test_a_refused_decision_is_recorded_too():
    service, store = build()
    with pytest.raises(ReviewRefused) as refused:
        service.review(request_for(Decision.APPROVE, actor=actor("AUDITOR")))
    assert refused.value.code == "ROLE_IS_READ_ONLY"
    assert store.actions[0]["outcome"] == "REJECTED"
    assert store.audits[0]["outcome"] == "DENIED"
    assert store.audits[0]["reason_code"] == "ROLE_IS_READ_ONLY"
    assert store.tickets[TICKET].version == 3


def test_an_edit_is_a_new_revision_and_never_overwrites_the_model():
    service, store = build()
    outcome = service.review(request_for(Decision.EDIT, body="A clearer reply. [KB-001]"))

    assert outcome.revision == 1
    (revision,) = store.revisions
    assert revision["source"] == "HUMAN"
    assert revision["created_by_membership_id"] == AGENT
    assert revision["body"] == "A clearer reply. [KB-001]"
    # The model's own words are still what the triage run recorded.
    assert store.tickets[TICKET].model_draft == MODEL_DRAFT


def test_an_approval_after_an_edit_creates_the_edited_text():
    composer = ScriptedComposer()
    service, store = build(scopes=(GMAIL_READONLY, GMAIL_COMPOSE), composer=composer)
    service.review(request_for(Decision.EDIT, body="Edited reply. [KB-001]"))
    outcome = service.review(request_for(Decision.APPROVE, version=4))

    import base64

    mime = base64.urlsafe_b64decode(str(composer.calls[0]["raw"])).decode()
    assert "Edited reply. [KB-001]" in mime
    assert MODEL_DRAFT not in mime
    assert outcome.provider_draft_created


# --------------------------------------------------------------------------
# Permissions, visibility, and validation
# --------------------------------------------------------------------------


def test_the_python_permissions_match_the_web_matrix():
    import re
    from pathlib import Path

    source = Path("apps/web/src/lib/authz/roles.ts").read_text()
    matrix = source[source.index("MATRIX") :]
    for decision, permission in PERMISSIONS.items():
        roles = {
            role
            for role, body in re.findall(
                r"^  ([A-Z_]+): \[(.*?)\]", matrix, re.MULTILINE | re.DOTALL
            )
            if f'"{permission}"' in body
        }
        assert roles == ROLES[permission], f"{decision}: {permission}"


@pytest.mark.parametrize(
    ("role", "decision", "code"),
    [
        ("AUDITOR", Decision.APPROVE, "ROLE_IS_READ_ONLY"),
        ("AGENT", Decision.REROUTE, "ROLE_LACKS_PERMISSION"),
        ("AGENT", Decision.ASSIGN, "ROLE_LACKS_PERMISSION"),
        ("KNOWLEDGE_MANAGER", Decision.APPROVE, "ROLE_LACKS_PERMISSION"),
        ("SUPERVISOR", Decision.REROUTE, None),
        ("AGENT", Decision.APPROVE, None),
    ],
)
def test_permission_is_checked_per_decision(role, decision, code):
    service, store = build()
    extra = {"reason": "Because."} if decision is Decision.REROUTE else {}
    if decision is Decision.REROUTE:
        extra["queue_id"] = QUEUE
    request = request_for(decision, actor=actor(role, membership=AGENT), **extra)
    if code is None:
        assert service.review(request).ok
    else:
        with pytest.raises(ReviewRefused) as refused:
            service.review(request)
        assert refused.value.code == code


def test_a_suspended_membership_cannot_decide():
    service, store = build()
    with pytest.raises(ReviewRefused) as refused:
        service.review(request_for(Decision.RESOLVE, actor=actor(status="SUSPENDED")))
    assert refused.value.code == "MEMBERSHIP_NOT_ACTIVE"


def test_a_conversation_the_person_cannot_act_on_is_not_found():
    service, store = build(permit=False)
    with pytest.raises(ReviewRefused) as refused:
        service.review(request_for(Decision.RESOLVE))
    assert refused.value.code == "TICKET_NOT_FOUND"
    # Nothing is written against a ticket they may not know about.
    assert store.actions == []


def test_a_department_they_do_not_belong_to_is_not_found():
    service, store = build(facts(department_id=DEPARTMENT))
    with pytest.raises(ReviewRefused) as refused:
        service.review(request_for(Decision.RESOLVE))
    assert refused.value.code == "TICKET_NOT_FOUND"

    store.department_memberships[AGENT] = {DEPARTMENT}
    assert service.review(request_for(Decision.RESOLVE)).ok


def test_an_organization_wide_role_does_not_need_a_mailbox_permission():
    service, store = build(permit=False)
    assert service.review(request_for(Decision.RESOLVE, actor=actor("ADMIN"))).ok


@pytest.mark.parametrize(
    ("decision", "extra", "code"),
    [
        (Decision.REJECT, {}, "REASON_REQUIRED"),
        (Decision.REJECT, {"reason": "   "}, "REASON_REQUIRED"),
        (
            Decision.REROUTE,
            {"reason": "x", "queue_id": "not-an-id", "actor": actor("SUPERVISOR")},
            "REROUTE_TARGET_INVALID",
        ),
        (
            Decision.REROUTE,
            {"reason": "x", "actor": actor("SUPERVISOR")},
            "REROUTE_TARGET_REQUIRED",
        ),
        (
            Decision.ASSIGN,
            {"assignee_membership_id": "nobody", "actor": actor("SUPERVISOR")},
            "ASSIGNEE_INVALID",
        ),
        (Decision.ASSIGN, {"actor": actor("SUPERVISOR")}, "ASSIGNEE_INVALID"),
        (Decision.EDIT, {}, "BODY_REQUIRED"),
        (Decision.EDIT, {"body": "   "}, "BODY_REQUIRED"),
    ],
)
def test_a_decision_missing_what_it_needs_is_refused(decision, extra, code):
    service, store = build()
    with pytest.raises(ReviewRefused) as refused:
        service.review(request_for(decision, **extra))
    assert refused.value.code == code
    assert store.tickets[TICKET].version == 3


def test_an_ungrounded_model_draft_cannot_be_approved_as_it_stands():
    service, store = build(facts(grounding_validated=False))
    with pytest.raises(ReviewRefused) as refused:
        service.review(request_for(Decision.APPROVE))
    assert refused.value.code == "DRAFT_NOT_GROUNDED"


def test_an_uncited_model_draft_cannot_be_approved_as_it_stands():
    service, store = build(facts(citations=()))
    with pytest.raises(ReviewRefused) as refused:
        service.review(request_for(Decision.APPROVE))
    assert refused.value.code == "DRAFT_NOT_CITED"


def test_a_person_may_approve_their_own_words_for_an_ungrounded_draft():
    """Their own text is their own accountability, and it is recorded as theirs."""
    composer = ScriptedComposer()
    service, store = build(
        facts(grounding_validated=False, citations=()),
        scopes=(GMAIL_READONLY, GMAIL_COMPOSE),
        composer=composer,
    )
    outcome = service.review(request_for(Decision.APPROVE, body="I have checked this myself."))

    assert outcome.ok and outcome.provider_draft_created
    assert store.revisions[0]["source"] == "HUMAN"
    assert store.revisions[0]["created_by_membership_id"] == AGENT


def test_nothing_to_approve_is_refused():
    service, store = build(facts(model_draft=None, latest_body=None))
    with pytest.raises(ReviewRefused) as refused:
        service.review(request_for(Decision.APPROVE))
    assert refused.value.code == "NOTHING_TO_APPROVE"


def test_a_closed_conversation_takes_no_further_decisions_but_can_be_reassigned():
    service, store = build(facts(status="RESOLVED"))
    with pytest.raises(ReviewRefused) as refused:
        service.review(request_for(Decision.APPROVE))
    assert refused.value.code == "CONVERSATION_IS_CLOSED"
    assert service.review(
        request_for(Decision.ASSIGN, assignee_membership_id=OTHER_AGENT, actor=actor("SUPERVISOR"))
    ).ok


def test_an_oversized_body_or_reason_is_refused():
    service, store = build()
    with pytest.raises(ReviewRefused) as refused:
        service.review(request_for(Decision.EDIT, body="x" * 20001))
    assert refused.value.code == "BODY_TOO_LONG"
    with pytest.raises(ReviewRefused) as refused:
        service.review(request_for(Decision.REJECT, reason="x" * 2001))
    assert refused.value.code == "REASON_TOO_LONG"


def test_the_decision_moves_the_conversation_where_it_should():
    for decision, extra, status in (
        (Decision.APPROVE, {}, "IN_PROGRESS"),
        (Decision.REJECT, {"reason": "A person should reply."}, "IN_PROGRESS"),
        (Decision.RESOLVE, {}, "RESOLVED"),
        (
            Decision.ASSIGN,
            {"assignee_membership_id": OTHER_AGENT, "actor": actor("SUPERVISOR")},
            "WAITING_ON_REVIEW",
        ),
    ):
        service, store = build()
        service.review(request_for(decision, **extra))
        assert store.tickets[TICKET].status == status, decision


def test_reroute_changes_where_the_work_goes():
    service, store = build()
    service.review(
        request_for(
            Decision.REROUTE,
            reason="Billing owns this.",
            queue_id=QUEUE,
            department_id=DEPARTMENT,
            actor=actor("SUPERVISOR"),
        )
    )
    ticket = store.tickets[TICKET]
    assert (ticket.queue_id, ticket.department_id) == (QUEUE, DEPARTMENT)
    assert store.actions[0]["payload"]["queue_id"] == QUEUE


def test_the_preview_is_what_would_be_created():
    service, store = build()
    preview = service.preview(ORG, TICKET, actor())
    assert preview.to == ("customer@example.net",)
    assert preview.subject == "Re: Reset my password"
    assert preview.body == MODEL_DRAFT
    assert preview.in_reply_to == "<first@mail>"
    assert preview.thread_id == THREAD


def test_a_stale_view_is_reported_as_a_conflict_before_any_other_refusal():
    """Found by the API tests: a stale view produced a misleading reason.

    Someone resolves the conversation while a reviewer is reading it. The
    reviewer's approval is refused - but "it is closed" describes a state they
    never saw, where "reload and decide again" describes what to do.
    """
    service, store = build()
    service.review(request_for(Decision.RESOLVE))
    assert store.tickets[TICKET].status == "RESOLVED"

    with pytest.raises(VersionConflict) as conflict:
        service.review(request_for(Decision.APPROVE, version=3))
    assert conflict.value.code == "VERSION_CONFLICT"
    assert conflict.value.current_version == 4
    assert store.actions[-1]["error_code"] == "VERSION_CONFLICT"


def test_a_closed_conversation_is_still_refused_for_an_up_to_date_view():
    service, store = build(facts(status="RESOLVED", version=5))
    with pytest.raises(ReviewRefused) as refused:
        service.review(request_for(Decision.APPROVE, version=5))
    assert refused.value.code == "CONVERSATION_IS_CLOSED"


def test_approving_with_edited_text_claims_the_revision_it_just_wrote():
    """Found live: the claim used the facts loaded before the decision.

    Approving with an edit on a conversation that has no revision yet writes
    revision 1 in the decision, and the claim must point at that revision
    rather than trying to write revision 1 again.
    """
    composer = ScriptedComposer()
    service, store = build(
        facts(latest_revision=0, latest_revision_id=None, latest_body=None),
        scopes=(GMAIL_READONLY, GMAIL_COMPOSE),
        composer=composer,
    )
    outcome = service.review(request_for(Decision.APPROVE, body="My own words. [KB-001]"))

    assert outcome.ok and outcome.provider_draft_created
    assert len(store.revisions) == 1, "revision 1 was written twice"
    assert store.revisions[0]["source"] == "HUMAN"
    assert store.drafts[0]["revision_id"] == store.revisions[0]["id"]
    assert outcome.revision == 1


def test_the_claim_is_told_which_revision_rather_than_guessing():
    """The in-memory store must not be more forgiving than the database."""
    import inspect

    from app.review.memory_store import InMemoryReviewStore
    from app.review.store import PostgresReviewStore

    for store_class in (PostgresReviewStore, InMemoryReviewStore):
        signature = inspect.signature(store_class.claim_provider_draft)
        assert "revision_id" in signature.parameters, store_class.__name__
        assert "revision" in signature.parameters, store_class.__name__


def test_a_read_only_mailbox_is_told_the_scope_reason_not_an_address_reason():
    """Found live: the preview ran first and reported a misleading cause.

    A mailbox that cannot hold a draft cannot hold one whatever the reply looks
    like, so the scope is the reason worth reporting.
    """
    service, store = build(facts(customer_address=None))
    service.store.inbound.clear()
    outcome = service.review(request_for(Decision.APPROVE))
    assert outcome.draft is not None
    assert outcome.draft.failure_code == "DRAFT_SCOPE_NOT_GRANTED"


def test_the_address_a_draft_would_come_from_travels_with_the_ticket():
    """Not looked up through duck typing: the Postgres store had no such method."""
    service, store = build()
    facts_now = store.load_ticket(request_for(Decision.APPROVE))
    assert facts_now is not None
    assert facts_now.mailbox_address == "support@acme.example"

    from app.review.records import TicketFacts as Facts

    assert "mailbox_address" in Facts.__dataclass_fields__
    import inspect

    source = inspect.getsource(__import__("app.review.service", fromlist=["service"]))
    assert 'getattr(self.store, "mailbox_address"' not in source
