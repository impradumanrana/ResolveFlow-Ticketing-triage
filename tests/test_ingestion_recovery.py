"""C07 gate: duplicate, delayed, out-of-order, expired watch, revoked token,
quota, and restart - plus a fifty-mailbox load simulation.

Everything runs against a simulated Gmail. No account, no network, no paid
service. The simulation is strict where Gmail is strict: it answers 404 when a
cursor predates its retained history, refuses unknown tokens, and returns only
the history records after the requested cursor.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.ingestion.gmail import GmailClient
from app.ingestion.memory_store import InMemoryIngestionStore
from app.ingestion.records import MailboxSync, TokenLease
from app.ingestion.service import (
    DEFAULT_MAX_ATTEMPTS,
    JOB_LEASE_SECONDS,
    IngestionService,
    compute_backoff,
)
from app.mailbox.google import HttpResponse

ORG = "org-acme"
MAILBOX_ID = "mbx-support"
ADDRESS = "support@acme.example"
TOPIC = "projects/client/topics/rf-staging-gmail-notifications"


class FakeGmail:
    """A strict stand-in for the Gmail API surface ingestion uses."""

    def __init__(self) -> None:
        self.messages: dict[str, dict] = {}
        self.records: list[dict] = []
        self.history_id = 1000
        self.retained_from = 0
        self.valid_tokens = {"ya29.token"}
        self.watch_expiration_ms = int(
            (datetime(2026, 9, 23, 12, 0, tzinfo=UTC)).timestamp() * 1000
        )
        self.calls: list[str] = []
        self.fail_history_with: tuple[int, dict] | None = None
        self.fail_get_with: tuple[int, dict] | None = None
        self.watch_calls = 0

    # -- simulation controls ------------------------------------------

    def deliver(self, message_id: str, *, thread_id: str, body: str = "Help please") -> int:
        self.history_id += 1
        self.messages[message_id] = {
            "id": message_id,
            "threadId": thread_id,
            "internalDate": str(int(datetime(2026, 9, 16, 10, 0, tzinfo=UTC).timestamp() * 1000)),
            "snippet": body[:50],
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "customer@example.net"},
                    {"name": "To", "value": ADDRESS},
                    {"name": "Subject", "value": f"Ticket about {message_id}"},
                ],
                "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
            },
        }
        self.records.append(
            {"id": str(self.history_id), "messagesAdded": [{"message": {"id": message_id}}]}
        )
        return self.history_id

    def delete(self, message_id: str) -> int:
        self.history_id += 1
        self.records.append(
            {"id": str(self.history_id), "messagesDeleted": [{"message": {"id": message_id}}]}
        )
        self.messages.pop(message_id, None)
        return self.history_id

    def expire_history_before(self, history_id: int) -> None:
        self.retained_from = history_id

    # -- transport -----------------------------------------------------

    def request(self, method, url, *, form=None, json=None, bearer=None, timeout=15.0):
        self.calls.append(url.split("?")[0].rsplit("/", 1)[-1])
        if bearer not in self.valid_tokens:
            return HttpResponse(401, {"error": {"code": 401}})

        if url.endswith("/watch"):
            self.watch_calls += 1
            return HttpResponse(
                200,
                {"historyId": str(self.history_id), "expiration": str(self.watch_expiration_ms)},
            )

        if "/history" in url:
            if self.fail_history_with:
                status, body = self.fail_history_with
                self.fail_history_with = None
                return HttpResponse(status, body)
            start = int(_query(url, "startHistoryId"))
            if start < self.retained_from:
                return HttpResponse(404, {"error": {"code": 404}})
            records = [r for r in self.records if int(r["id"]) > start]
            return HttpResponse(200, {"history": records, "historyId": str(self.history_id)})

        if "/messages/" in url:
            if self.fail_get_with:
                status, body = self.fail_get_with
                self.fail_get_with = None
                return HttpResponse(status, body)
            message_id = url.split("/messages/")[1].split("?")[0]
            resource = self.messages.get(message_id)
            if resource is None:
                return HttpResponse(404, {"error": {"code": 404}})
            return HttpResponse(200, resource)

        if "/messages" in url:
            return HttpResponse(200, {"messages": [{"id": key} for key in sorted(self.messages)]})

        return HttpResponse(404, {})


def _query(url: str, key: str) -> str:
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(url).query)[key][0]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class Tokens:
    def __init__(self) -> None:
        self.lease = TokenLease(True, "REFRESHED", "ya29.token")
        self.calls = 0

    def __call__(self, mailbox_id: str) -> TokenLease:
        self.calls += 1
        return self.lease


class Harness:
    def __init__(self, mailboxes: list[MailboxSync] | None = None) -> None:
        self.gmail = FakeGmail()
        self.clock = Clock()
        self.tokens = Tokens()
        self.store = InMemoryIngestionStore(
            mailboxes
            or [
                MailboxSync(
                    id=MAILBOX_ID,
                    organization_id=ORG,
                    email_address=ADDRESS,
                    status="CONNECTED",
                    history_cursor="1000",
                    watch_expires_at=datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
                )
            ]
        )
        self.service = IngestionService(
            self.store,
            GmailClient(self.gmail),
            self.tokens,
            topic_name=TOPIC,
            now=self.clock,
        )

    def notify(
        self, *, message_id: str = "psm-1", history_id: str | None = None, address: str = ADDRESS
    ):
        payload = {
            "emailAddress": address,
            "historyId": history_id or str(self.gmail.history_id),
        }
        return self.service.accept_notification(
            {
                "message": {
                    "data": base64.b64encode(json.dumps(payload).encode()).decode(),
                    "messageId": message_id,
                },
                "subscription": "sub",
            }
        )

    def cursor(self, mailbox_id: str = MAILBOX_ID) -> str | None:
        mailbox = self.store.find_mailbox(mailbox_id)
        return mailbox.history_cursor if mailbox else None


@pytest.fixture
def harness() -> Harness:
    return Harness()


# ===========================================================================
# Baseline
# ===========================================================================


def test_a_notification_queues_one_sync_that_ingests_the_message(harness: Harness) -> None:
    harness.gmail.deliver("m1", thread_id="t1")

    accepted = harness.notify()
    outcomes = harness.service.run_due_jobs()

    assert accepted.accepted and accepted.code == "QUEUED"
    assert [o.code for o in outcomes] == ["SYNCED"]
    assert outcomes[0].ingested == 1
    assert len(harness.store.live_messages()) == 1
    assert len(harness.store.tickets) == 1


def test_messages_in_one_thread_share_a_ticket(harness: Harness) -> None:
    harness.gmail.deliver("m1", thread_id="t1")
    harness.gmail.deliver("m2", thread_id="t1")

    harness.notify()
    harness.service.run_due_jobs()

    assert len(harness.store.live_messages()) == 2
    assert len(harness.store.tickets) == 1
    assert next(iter(harness.store.threads.values())).message_count == 2


# ===========================================================================
# 1. Duplicate
# ===========================================================================


def test_a_redelivered_notification_queues_no_second_job(harness: Harness) -> None:
    harness.gmail.deliver("m1", thread_id="t1")

    first = harness.notify(message_id="psm-1")
    second = harness.notify(message_id="psm-1")

    assert first.accepted
    assert not second.accepted and second.code == "DUPLICATE"
    assert second.job_id == first.job_id
    assert len(harness.store.jobs) == 1


def test_syncing_again_with_nothing_new_does_no_work(harness: Harness) -> None:
    harness.gmail.deliver("m1", thread_id="t1")

    first = harness.service.sync_mailbox(MAILBOX_ID)
    second = harness.service.sync_mailbox(MAILBOX_ID)

    assert first.ingested == 1
    assert second.ingested == 0 and second.duplicates == 0
    assert len(harness.store.live_messages()) == 1


def test_the_same_message_in_two_history_records_is_fetched_once(harness: Harness) -> None:
    """Gmail repeats a message across history records; the fetch must not repeat.

    Deduplication happens before the fetch, so this costs one API call, not two.
    """
    harness.gmail.deliver("m1", thread_id="t1")
    harness.gmail.records.append(
        {"id": str(harness.gmail.history_id + 5), "messagesAdded": [{"message": {"id": "m1"}}]}
    )

    outcome = harness.service.sync_mailbox(MAILBOX_ID)

    assert outcome.ingested == 1
    assert harness.gmail.calls.count("m1") == 1
    assert len(harness.store.live_messages()) == 1


# ===========================================================================
# 2. Delayed  3. Out of order
# ===========================================================================


def test_a_delayed_notification_cannot_rewind_the_cursor(harness: Harness) -> None:
    harness.gmail.deliver("m1", thread_id="t1")
    harness.service.sync_mailbox(MAILBOX_ID)
    advanced = harness.cursor()

    # A notification published before the sync finally arrives.
    harness.notify(message_id="psm-late", history_id="500")
    harness.service.run_due_jobs()

    assert harness.cursor() == advanced
    assert all(
        int(b) >= int(a)
        for (_, a), (_, b) in zip(
            harness.store.cursor_writes, harness.store.cursor_writes[1:], strict=False
        )
    )


def test_out_of_order_notifications_still_ingest_every_message(harness: Harness) -> None:
    harness.gmail.deliver("m1", thread_id="t1")
    first_point = str(harness.gmail.history_id)
    harness.gmail.deliver("m2", thread_id="t2")

    # The later notification is processed first.
    harness.notify(message_id="psm-new")
    harness.notify(message_id="psm-old", history_id=first_point)
    harness.service.run_due_jobs()

    assert {m.provider_message_id for m in harness.store.live_messages()} == {"m1", "m2"}
    assert harness.cursor() == str(harness.gmail.history_id)


def test_a_notification_whose_history_id_is_ahead_is_still_only_a_hint(harness: Harness) -> None:
    """Sync reads from the stored cursor, so a bogus hint changes nothing."""
    harness.gmail.deliver("m1", thread_id="t1")

    harness.notify(message_id="psm-1", history_id="999999999")
    harness.service.run_due_jobs()

    assert len(harness.store.live_messages()) == 1
    assert harness.cursor() == str(harness.gmail.history_id)


def test_a_deletion_marks_the_message_rather_than_removing_history(harness: Harness) -> None:
    harness.gmail.deliver("m1", thread_id="t1")
    harness.service.sync_mailbox(MAILBOX_ID)
    harness.gmail.delete("m1")

    outcome = harness.service.sync_mailbox(MAILBOX_ID)

    assert outcome.deleted == 1
    assert harness.store.live_messages() == []
    assert len(harness.store.messages) == 1


# ===========================================================================
# 4. Expired watch
# ===========================================================================


def test_a_watch_near_expiry_is_renewed(harness: Harness) -> None:
    harness.clock.advance(timedelta(days=5))

    outcomes = harness.service.renew_watches()

    assert [o.code for o in outcomes] == ["RENEWED"]
    assert harness.gmail.watch_calls == 1
    mailbox = harness.store.find_mailbox(MAILBOX_ID)
    assert mailbox is not None and mailbox.watch_expires_at is not None
    assert mailbox.watch_expires_at > harness.clock()


def test_renewal_advances_the_cursor_only_forwards(harness: Harness) -> None:
    harness.gmail.deliver("m1", thread_id="t1")
    harness.service.sync_mailbox(MAILBOX_ID)
    before = harness.cursor()
    harness.clock.advance(timedelta(days=5))

    harness.service.renew_watches()

    assert before is not None and harness.cursor() is not None
    assert int(harness.cursor()) >= int(before)


def test_a_mailbox_with_a_healthy_watch_is_not_renewed(harness: Harness) -> None:
    assert harness.service.renew_watches() == []
    assert harness.gmail.watch_calls == 0


def test_a_never_watched_mailbox_is_renewed(harness: Harness) -> None:
    harness.store.mailboxes[MAILBOX_ID] = MailboxSync(
        id=MAILBOX_ID,
        organization_id=ORG,
        email_address=ADDRESS,
        status="CONNECTED",
        history_cursor="1000",
        watch_expires_at=None,
    )

    assert [o.code for o in harness.service.renew_watches()] == ["RENEWED"]


def test_an_expired_history_cursor_triggers_reconciliation(harness: Harness) -> None:
    """Gmail keeps about a week of history; a quiet mailbox outlives it."""
    harness.gmail.deliver("m1", thread_id="t1")
    harness.gmail.deliver("m2", thread_id="t2")
    harness.gmail.expire_history_before(harness.gmail.history_id + 1)

    outcome = harness.service.sync_mailbox(MAILBOX_ID)

    assert outcome.ok and outcome.code == "RECONCILED"
    assert outcome.ingested == 2
    assert "ingestion.history_expired" in harness.store.audit_actions()
    assert harness.cursor() == str(harness.gmail.history_id)


def test_reconciliation_ingests_only_what_is_missing(harness: Harness) -> None:
    harness.gmail.deliver("m1", thread_id="t1")
    harness.service.sync_mailbox(MAILBOX_ID)
    harness.gmail.deliver("m2", thread_id="t2")

    outcome = harness.service.reconcile_mailbox(MAILBOX_ID)

    assert outcome.ingested == 1
    assert outcome.duplicates == 1
    assert len(harness.store.live_messages()) == 2


# ===========================================================================
# 5. Revoked token
# ===========================================================================


def test_a_revoked_grant_stops_the_mailbox_without_calling_gmail(harness: Harness) -> None:
    harness.tokens.lease = TokenLease(False, "INVALID_GRANT")

    outcome = harness.service.sync_mailbox(MAILBOX_ID)

    assert outcome.code == "INVALID_GRANT"
    assert harness.gmail.calls == []
    assert "ingestion.stopped" in harness.store.audit_actions()


def test_a_revoked_grant_does_not_retry_forever(harness: Harness) -> None:
    harness.tokens.lease = TokenLease(False, "INVALID_GRANT")
    harness.gmail.deliver("m1", thread_id="t1")
    harness.notify()

    harness.service.run_due_jobs()

    job = next(iter(harness.store.jobs.values()))
    assert job.status == "SUCCEEDED", "a permanent failure must not be retried"


def test_a_notification_for_a_revoked_mailbox_is_refused(harness: Harness) -> None:
    harness.store.mailboxes[MAILBOX_ID] = MailboxSync(
        id=MAILBOX_ID,
        organization_id=ORG,
        email_address=ADDRESS,
        status="REVOKED",
    )

    outcome = harness.notify()

    assert not outcome.accepted and outcome.code == "MAILBOX_REVOKED"
    assert harness.store.jobs == {}


def test_a_notification_for_an_unknown_mailbox_is_acknowledged_not_retried(
    harness: Harness,
) -> None:
    outcome = harness.notify(address="stranger@elsewhere.example")

    assert not outcome.accepted and outcome.code == "UNKNOWN_MAILBOX"
    assert harness.store.jobs == {}


def test_gmail_rejecting_a_cached_token_drops_it(harness: Harness) -> None:
    harness.gmail.deliver("m1", thread_id="t1")
    harness.service.sync_mailbox(MAILBOX_ID)
    harness.gmail.valid_tokens.clear()

    outcome = harness.service.sync_mailbox(MAILBOX_ID)

    assert outcome.code == "UNAUTHORIZED"
    assert "ingestion.unauthorized" in harness.store.audit_actions()
    # The next attempt asks for a fresh token rather than reusing the rejected one.
    before = harness.tokens.calls
    harness.service.sync_mailbox(MAILBOX_ID)
    assert harness.tokens.calls > before


# ===========================================================================
# 6. Quota
# ===========================================================================


def test_a_rate_limit_defers_rather_than_failing(harness: Harness) -> None:
    harness.gmail.deliver("m1", thread_id="t1")
    harness.gmail.fail_history_with = (429, {})
    harness.notify()

    outcomes = harness.service.run_due_jobs()

    assert outcomes[0].code == "RATE_LIMITED"
    job = next(iter(harness.store.jobs.values()))
    assert job.status == "QUEUED"
    assert job.attempts == 1
    assert job.available_at > harness.clock()


def test_a_deferred_job_succeeds_once_the_quota_clears(harness: Harness) -> None:
    harness.gmail.deliver("m1", thread_id="t1")
    harness.gmail.fail_history_with = (429, {})
    harness.notify()
    harness.service.run_due_jobs()

    deferred = harness.service.run_due_jobs()
    assert deferred == [] or deferred[0].code == "RATE_LIMITED"

    # Wait exactly as long as the service asked to be retried after.
    job = next(iter(harness.store.jobs.values()))
    harness.clock.now = job.available_at + timedelta(seconds=1)
    outcomes = harness.service.run_due_jobs()

    assert [o.code for o in outcomes] == ["SYNCED"]
    assert len(harness.store.live_messages()) == 1


def test_a_403_quota_error_is_treated_as_a_rate_limit(harness: Harness) -> None:
    harness.gmail.fail_history_with = (
        403,
        {"error": {"errors": [{"reason": "rateLimitExceeded"}]}},
    )

    assert harness.service.sync_mailbox(MAILBOX_ID).code == "RATE_LIMITED"


def test_a_job_that_keeps_failing_is_dead_lettered(harness: Harness) -> None:
    harness.notify()
    for _ in range(DEFAULT_MAX_ATTEMPTS):
        harness.gmail.fail_history_with = (503, {})
        harness.service.run_due_jobs()
        harness.clock.advance(timedelta(seconds=compute_backoff(12) + 1))

    job = next(iter(harness.store.jobs.values()))
    assert job.status == "DEAD_LETTERED"
    assert job.dead_lettered_at is not None


def test_backoff_grows_and_is_bounded() -> None:
    delays = [compute_backoff(n) for n in range(0, 10)]
    assert delays == sorted(delays)
    assert delays[0] == 30
    assert max(delays) <= 3600


# ===========================================================================
# 7. Restart
# ===========================================================================


def test_a_job_abandoned_by_a_crashed_worker_is_reclaimed(harness: Harness) -> None:
    harness.gmail.deliver("m1", thread_id="t1")
    harness.notify()
    job_id = next(iter(harness.store.jobs))
    harness.store.start_job(job_id, at=harness.clock())  # worker dies here

    harness.clock.advance(timedelta(seconds=JOB_LEASE_SECONDS + 1))
    reclaimed = harness.service.reclaim_stalled_jobs()
    outcomes = harness.service.run_due_jobs()

    assert reclaimed == [job_id]
    assert [o.code for o in outcomes] == ["SYNCED"]
    assert len(harness.store.live_messages()) == 1


def test_a_job_still_within_its_lease_is_left_alone(harness: Harness) -> None:
    harness.notify()
    harness.store.start_job(next(iter(harness.store.jobs)), at=harness.clock())

    harness.clock.advance(timedelta(seconds=JOB_LEASE_SECONDS - 60))

    assert harness.service.reclaim_stalled_jobs() == []


def test_a_crash_before_the_cursor_advanced_reprocesses_without_duplicating(
    harness: Harness,
) -> None:
    """The cursor moves only after the window's work succeeded."""
    harness.gmail.deliver("m1", thread_id="t1")
    harness.service.sync_mailbox(MAILBOX_ID)
    # Simulate the cursor write having been lost with the crashed transaction.
    harness.store.mailboxes[MAILBOX_ID] = MailboxSync(
        id=MAILBOX_ID,
        organization_id=ORG,
        email_address=ADDRESS,
        status="CONNECTED",
        history_cursor="1000",
        watch_expires_at=datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
    )

    outcome = harness.service.sync_mailbox(MAILBOX_ID)

    assert outcome.duplicates == 1
    assert outcome.ingested == 0
    assert len(harness.store.live_messages()) == 1


def test_two_workers_cannot_run_the_same_job(harness: Harness) -> None:
    harness.notify()
    job_id = next(iter(harness.store.jobs))

    assert harness.store.start_job(job_id, at=harness.clock()) is True
    assert harness.store.start_job(job_id, at=harness.clock()) is False


# ===========================================================================
# Token caching
# ===========================================================================


def test_repeated_syncs_reuse_one_access_token(harness: Harness) -> None:
    for _ in range(5):
        harness.service.sync_mailbox(MAILBOX_ID)

    assert harness.tokens.calls == 1


def test_a_cached_token_is_refreshed_after_its_lifetime(harness: Harness) -> None:
    harness.service.sync_mailbox(MAILBOX_ID)
    harness.clock.advance(timedelta(minutes=46))

    harness.service.sync_mailbox(MAILBOX_ID)

    assert harness.tokens.calls == 2


# ===========================================================================
# Load: fifty mailboxes
# ===========================================================================


def test_fifty_mailboxes_ingest_exactly_once_and_replay_changes_nothing() -> None:
    """The plan requires simulating fifty mailboxes before expanding live ones.

    The simulated Gmail serves one history stream, so every mailbox legitimately
    sees every delivered message. That makes the arithmetic exact and checkable:
    50 mailboxes times 100 messages is 5,000 rows, and a full replay must add none.
    """
    mailbox_count = 50
    messages_per_stream = 100

    mailboxes = [
        MailboxSync(
            id=f"mbx-{index}",
            organization_id=ORG,
            email_address=f"support{index}@acme.example",
            status="CONNECTED",
            history_cursor="1000",
            watch_expires_at=datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
        )
        for index in range(mailbox_count)
    ]
    harness = Harness(mailboxes)

    for number in range(messages_per_stream):
        harness.gmail.deliver(f"m-{number}", thread_id=f"t-{number}")

    for index, mailbox in enumerate(mailboxes):
        assert harness.notify(message_id=f"psm-{index}", address=mailbox.email_address).accepted

    outcomes = harness.service.run_due_jobs(limit=mailbox_count * 2)

    assert len(outcomes) == mailbox_count
    assert all(outcome.ok for outcome in outcomes)
    assert all(outcome.ingested == messages_per_stream for outcome in outcomes)
    assert len(harness.store.live_messages()) == mailbox_count * messages_per_stream
    assert len(harness.store.tickets) == mailbox_count * messages_per_stream

    # A full replay of every notification must add nothing whatsoever.
    for index, mailbox in enumerate(mailboxes):
        replay = harness.notify(message_id=f"psm-{index}", address=mailbox.email_address)
        assert replay.code == "DUPLICATE"

    assert harness.service.run_due_jobs(limit=mailbox_count * 2) == []
    assert len(harness.store.live_messages()) == mailbox_count * messages_per_stream


def test_one_failing_mailbox_does_not_stop_the_others() -> None:
    mailboxes = [
        MailboxSync(
            id=f"mbx-{index}",
            organization_id=ORG,
            email_address=f"support{index}@acme.example",
            status="CONNECTED",
            history_cursor="1000",
            watch_expires_at=datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
        )
        for index in range(3)
    ]
    harness = Harness(mailboxes)
    harness.store.mailboxes["mbx-1"] = MailboxSync(
        id="mbx-1",
        organization_id=ORG,
        email_address="support1@acme.example",
        status="REVOKED",
    )
    harness.gmail.deliver("m1", thread_id="t1")

    for index, mailbox in enumerate(mailboxes):
        harness.notify(message_id=f"psm-{index}", address=mailbox.email_address)
    outcomes = harness.service.run_due_jobs(limit=10)

    assert len(outcomes) == 2
    assert all(o.ok for o in outcomes)
