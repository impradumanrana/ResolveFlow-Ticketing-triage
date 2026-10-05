"""The mailbox connection lifecycle: start, complete, refresh, revoke.

Reconnect is not a separate operation. It is start + complete on a mailbox that
was previously connected, degraded, or revoked, and it replaces the credential
rather than accumulating a second one.

The rules that make this safe:

* **The callback cannot choose the mailbox.** `complete` receives only the
  state and the code. The mailbox comes from the stored attempt the state
  identifies, so a crafted callback cannot direct a token onto another inbox.
* **The account Google returns is verified.** `login_hint` pre-selects the
  intended address but a person can pick any account. The Gmail profile of the
  new token must equal the mailbox being connected, or the token is revoked.
* **A token that fails policy is revoked, not stored or discarded.** Merely
  not saving a wrongly scoped or wrong-account token would leave a live grant
  at Google that nobody here knows exists.
* **An attempt is single-use and bound to its initiator.** A stolen state
  cannot be completed by a different membership, and cannot be replayed.
* **Refresh failures are classified.** A revoked grant withdraws the mailbox;
  a timeout degrades it and keeps the credential for the next attempt.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.mailbox.access import (
    Actor,
    MailboxAction,
    MailboxFacts,
    authorize_mailbox,
)
from app.mailbox.google import (
    GoogleOAuthClient,
    InvalidGrant,
    ProviderError,
    build_authorization_url,
    pkce_pair,
)
from app.mailbox.records import (
    CONNECTABLE_KINDS,
    ConnectionAttempt,
    MailboxStore,
    StoredCredential,
)
from app.mailbox.scopes import evaluate_granted_scopes
from app.mailbox.vault import TokenVault, VaultError

ATTEMPT_LIFETIME = timedelta(minutes=10)
PURPOSE_REFRESH_TOKEN = "gmail-refresh-token"
PURPOSE_PKCE = "gmail-pkce-verifier"
CREDENTIAL_KEYRING = "mailbox-token-encryption-key"


class ConnectionRefused(Exception):
    """The connection cannot start. Carries a stable, auditable code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StartResult:
    attempt_id: str
    mailbox_id: str
    authorization_url: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class ConnectionOutcome:
    ok: bool
    code: str
    mailbox_id: str | None = None


@dataclass(frozen=True)
class RefreshOutcome:
    ok: bool
    code: str
    access_token: str | None = field(default=None, repr=False)


def hash_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def credential_reference(key_id: str) -> str:
    """What `mailboxes.credential_secret_name` records: which key opens it."""
    return f"vault:{CREDENTIAL_KEYRING}:{key_id}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ConnectionService:
    def __init__(
        self,
        store: MailboxStore,
        oauth: GoogleOAuthClient,
        vault: TokenVault,
        *,
        now: Callable[[], datetime] = _utc_now,
    ):
        self._store = store
        self._oauth = oauth
        self._vault = vault
        self._now = now

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    def start(self, actor: Actor, mailbox_id: str) -> StartResult:
        mailbox = self._store.get_mailbox(actor.organization_id, mailbox_id)
        facts = (
            MailboxFacts(mailbox.id, mailbox.organization_id, mailbox.status) if mailbox else None
        )
        decision = authorize_mailbox(actor, facts, MailboxAction.CONNECT)
        if not decision.allowed or mailbox is None:
            self._audit_refusal(actor, mailbox_id, "mailbox.connection_refused", decision.reason)
            raise ConnectionRefused(decision.reason or "MAILBOX_NOT_FOUND")

        if mailbox.kind not in CONNECTABLE_KINDS:
            # A Google Group has no inbox to authorize, and an alias is a
            # send-as identity of another mailbox. Connect the real mailbox.
            self._audit_refusal(
                actor, mailbox.id, "mailbox.connection_refused", "MAILBOX_KIND_NOT_CONNECTABLE"
            )
            raise ConnectionRefused("MAILBOX_KIND_NOT_CONNECTABLE")

        domain = mailbox.email_address.rsplit("@", 1)[-1]
        if domain not in self._store.allowed_domains(actor.organization_id):
            self._audit_refusal(
                actor, mailbox.id, "mailbox.connection_refused", "DOMAIN_NOT_ALLOWED"
            )
            raise ConnectionRefused("DOMAIN_NOT_ALLOWED")

        state = secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        created = self._now()
        attempt = ConnectionAttempt(
            id=str(uuid.uuid4()),
            organization_id=actor.organization_id,
            mailbox_id=mailbox.id,
            initiated_by_membership_id=actor.membership_id,
            state_hash=hash_state(state),
            verifier=self._vault.seal(
                verifier,
                organization_id=actor.organization_id,
                mailbox_id=mailbox.id,
                purpose=PURPOSE_PKCE,
            ),
            expected_address=mailbox.email_address,
            expires_at=created + ATTEMPT_LIFETIME,
            created_at=created,
        )
        self._store.create_attempt(attempt)
        self._store.record_audit(
            organization_id=actor.organization_id,
            actor_membership_id=actor.membership_id,
            action="mailbox.connection_started",
            outcome="ALLOWED",
            reason_code=None,
            target_id=mailbox.id,
            metadata={"reconnect": mailbox.status != "PENDING"},
        )

        return StartResult(
            attempt_id=attempt.id,
            mailbox_id=mailbox.id,
            authorization_url=build_authorization_url(
                self._oauth.config,
                state=state,
                code_challenge=challenge,
                login_hint=mailbox.email_address,
            ),
            expires_at=attempt.expires_at,
        )

    # ------------------------------------------------------------------
    # Complete
    # ------------------------------------------------------------------

    def complete(self, actor: Actor, *, state: str, code: str) -> ConnectionOutcome:
        if not state:
            return ConnectionOutcome(False, "STATE_MISSING")

        attempt = self._store.find_attempt(hash_state(state))
        if attempt is None:
            return ConnectionOutcome(False, "STATE_UNKNOWN")

        # Ownership before consumption: a stolen state presented by someone else
        # must not burn the legitimate person's attempt.
        if (
            attempt.initiated_by_membership_id != actor.membership_id
            or attempt.organization_id != actor.organization_id
        ):
            self._audit_refusal(
                actor, attempt.mailbox_id, "mailbox.connection_refused", "ATTEMPT_NOT_YOURS"
            )
            return ConnectionOutcome(False, "ATTEMPT_NOT_YOURS", attempt.mailbox_id)

        # Replay before expiry. A consumed state presented again is a reused
        # credential - the security-relevant fact - and must be reported and
        # audited as such even once the attempt has also expired. Checking
        # expiry first would quietly relabel a replay as a stale link.
        if attempt.consumed_at is not None:
            self._audit_refusal(
                actor, attempt.mailbox_id, "mailbox.connection_refused", "ATTEMPT_REPLAYED"
            )
            return ConnectionOutcome(False, "ATTEMPT_REPLAYED", attempt.mailbox_id)

        now = self._now()
        if attempt.expires_at <= now:
            self._store.consume_attempt(attempt.id, "ATTEMPT_EXPIRED", now)
            return ConnectionOutcome(False, "ATTEMPT_EXPIRED", attempt.mailbox_id)

        # Still guarded atomically: two concurrent callbacks can both pass the
        # check above, and only one may win the consumption.
        if not self._store.consume_attempt(attempt.id, "IN_PROGRESS", now):
            self._audit_refusal(
                actor, attempt.mailbox_id, "mailbox.connection_refused", "ATTEMPT_REPLAYED"
            )
            return ConnectionOutcome(False, "ATTEMPT_REPLAYED", attempt.mailbox_id)

        outcome = self._complete_consumed(actor, attempt, code)
        self._store.set_attempt_outcome(attempt.id, outcome.code)
        return outcome

    def _complete_consumed(
        self, actor: Actor, attempt: ConnectionAttempt, code: str
    ) -> ConnectionOutcome:
        mailbox = self._store.get_mailbox(attempt.organization_id, attempt.mailbox_id)
        facts = (
            MailboxFacts(mailbox.id, mailbox.organization_id, mailbox.status) if mailbox else None
        )
        # Re-checked: a role may have been withdrawn during the consent screen.
        decision = authorize_mailbox(actor, facts, MailboxAction.CONNECT)
        if not decision.allowed or mailbox is None:
            return self._fail(actor, attempt.mailbox_id, decision.reason or "MAILBOX_NOT_FOUND")

        try:
            verifier = self._vault.open(
                attempt.verifier,
                organization_id=attempt.organization_id,
                mailbox_id=attempt.mailbox_id,
                purpose=PURPOSE_PKCE,
            )
        except VaultError:
            return self._fail(actor, mailbox.id, "ATTEMPT_UNREADABLE")

        try:
            grant = self._oauth.exchange_code(code, verifier)
        except ProviderError as error:
            return self._fail(actor, mailbox.id, error.code)

        revocable = grant.refresh_token or grant.access_token

        scopes = evaluate_granted_scopes(grant.scope)
        if not scopes.acceptable:
            self._revoke_quietly(revocable)
            return self._fail(
                actor,
                mailbox.id,
                scopes.reason_code or "SCOPE_MISMATCH",
                metadata={
                    "missing": ",".join(sorted(scopes.missing)),
                    "excess": ",".join(sorted(scopes.excess)),
                },
            )

        if not grant.refresh_token:
            self._revoke_quietly(grant.access_token)
            return self._fail(actor, mailbox.id, "NO_REFRESH_TOKEN")

        try:
            address = self._oauth.mailbox_address(grant.access_token)
        except ProviderError as error:
            self._revoke_quietly(revocable)
            return self._fail(actor, mailbox.id, error.code)

        if address != attempt.expected_address or address != mailbox.email_address:
            # The person consented with a different account than the mailbox
            # being connected. Holding that grant would read the wrong inbox.
            self._revoke_quietly(revocable)
            return self._fail(
                actor,
                mailbox.id,
                "WRONG_MAILBOX_AUTHORIZED",
                metadata={"expected": mailbox.email_address},
            )

        sealed = self._vault.seal(
            grant.refresh_token,
            organization_id=mailbox.organization_id,
            mailbox_id=mailbox.id,
            purpose=PURPOSE_REFRESH_TOKEN,
        )
        now = self._now()
        self._store.save_credential(
            StoredCredential(
                organization_id=mailbox.organization_id,
                mailbox_id=mailbox.id,
                sealed=sealed,
                granted_scopes=scopes.granted,
                provider_account_email=address,
                connected_by_membership_id=actor.membership_id,
                last_refreshed_at=now,
            )
        )
        self._store.mark_connected(
            mailbox.id,
            credential_reference=credential_reference(sealed.key_id),
            scopes=scopes.granted,
            membership_id=actor.membership_id,
            at=now,
        )
        self._store.record_audit(
            organization_id=mailbox.organization_id,
            actor_membership_id=actor.membership_id,
            action="mailbox.connected",
            outcome="ALLOWED",
            reason_code=None,
            target_id=mailbox.id,
            metadata={"reconnect": mailbox.status != "PENDING", "key_id": sealed.key_id},
        )
        return ConnectionOutcome(True, "CONNECTED", mailbox.id)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(self, mailbox_id: str) -> RefreshOutcome:
        """Mint a short-lived access token. System-initiated; no actor."""
        mailbox = self._store.find_mailbox(mailbox_id)
        if mailbox is None:
            return RefreshOutcome(False, "MAILBOX_NOT_FOUND")
        if mailbox.status == "REVOKED":
            return RefreshOutcome(False, "MAILBOX_REVOKED")

        credential = self._store.get_credential(mailbox.id)
        if credential is None:
            return RefreshOutcome(False, "NOT_CONNECTED")

        try:
            refresh_token = self._vault.open(
                credential.sealed,
                organization_id=mailbox.organization_id,
                mailbox_id=mailbox.id,
                purpose=PURPOSE_REFRESH_TOKEN,
            )
        except VaultError:
            # Never call Google with a credential that does not authenticate for
            # this mailbox. Degraded, not revoked: a missing rotation key looks
            # the same and deleting would destroy a recoverable credential.
            now = self._now()
            self._store.mark_degraded(mailbox.id, error_code="CREDENTIAL_UNREADABLE", at=now)
            self._system_audit(
                mailbox.organization_id,
                mailbox.id,
                "mailbox.refresh_failed",
                "CREDENTIAL_UNREADABLE",
            )
            return RefreshOutcome(False, "CREDENTIAL_UNREADABLE")

        now = self._now()
        try:
            access = self._oauth.refresh(refresh_token)
        except InvalidGrant:
            self._withdraw(
                mailbox.organization_id, mailbox.id, "INVALID_GRANT", revoke_remote=False
            )
            return RefreshOutcome(False, "INVALID_GRANT")
        except ProviderError as error:
            if error.transient:
                self._store.mark_degraded(mailbox.id, error_code=error.code, at=now)
                self._system_audit(
                    mailbox.organization_id, mailbox.id, "mailbox.refresh_failed", error.code
                )
                return RefreshOutcome(False, error.code)
            self._withdraw(mailbox.organization_id, mailbox.id, error.code, revoke_remote=False)
            return RefreshOutcome(False, error.code)

        if access.scope is not None:
            scopes = evaluate_granted_scopes(access.scope)
            if not scopes.acceptable:
                code = (
                    "SCOPE_REMOVED" if scopes.missing and not scopes.excess else scopes.reason_code
                ) or "SCOPE_MISMATCH"
                self._revoke_quietly(refresh_token)
                self._withdraw(mailbox.organization_id, mailbox.id, code, revoke_remote=False)
                return RefreshOutcome(False, code)

        self._store.mark_refreshed(mailbox.id, at=now)
        return RefreshOutcome(True, "REFRESHED", access.access_token)

    # ------------------------------------------------------------------
    # Revoke
    # ------------------------------------------------------------------

    def revoke(self, actor: Actor, mailbox_id: str) -> ConnectionOutcome:
        mailbox = self._store.get_mailbox(actor.organization_id, mailbox_id)
        facts = (
            MailboxFacts(mailbox.id, mailbox.organization_id, mailbox.status) if mailbox else None
        )
        decision = authorize_mailbox(actor, facts, MailboxAction.REVOKE)
        if not decision.allowed or mailbox is None:
            self._audit_refusal(actor, mailbox_id, "mailbox.revocation_refused", decision.reason)
            return ConnectionOutcome(False, decision.reason or "MAILBOX_NOT_FOUND", mailbox_id)

        credential = self._store.get_credential(mailbox.id)
        if credential is None:
            return ConnectionOutcome(False, "NOT_CONNECTED", mailbox.id)

        remote_code: str | None = None
        try:
            token = self._vault.open(
                credential.sealed,
                organization_id=mailbox.organization_id,
                mailbox_id=mailbox.id,
                purpose=PURPOSE_REFRESH_TOKEN,
            )
            self._oauth.revoke(token)
        except VaultError:
            remote_code = "CREDENTIAL_UNREADABLE"
        except ProviderError as error:
            remote_code = error.code

        # Local destruction always proceeds. A person who asked to disconnect a
        # mailbox must not be left connected because Google was slow.
        self._store.delete_credential(mailbox.id)
        now = self._now()
        self._store.mark_revoked(mailbox.id, error_code=remote_code, at=now)
        self._store.record_audit(
            organization_id=mailbox.organization_id,
            actor_membership_id=actor.membership_id,
            action="mailbox.revoked",
            outcome="ALLOWED",
            reason_code=remote_code,
            target_id=mailbox.id,
            metadata={"remote_revocation": "failed" if remote_code else "confirmed"},
        )
        return ConnectionOutcome(
            True, "REVOKED" if not remote_code else "REVOKED_LOCALLY", mailbox.id
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _withdraw(
        self, organization_id: str, mailbox_id: str, code: str, *, revoke_remote: bool
    ) -> None:
        self._store.delete_credential(mailbox_id)
        self._store.mark_revoked(mailbox_id, error_code=code, at=self._now())
        self._system_audit(organization_id, mailbox_id, "mailbox.access_withdrawn", code)

    def _revoke_quietly(self, token: str | None) -> None:
        if not token:
            return
        try:
            self._oauth.revoke(token)
        except ProviderError:
            # Recorded by the caller's failure audit; a revocation failure must
            # not turn a refusal into a success.
            pass

    def _fail(
        self,
        actor: Actor,
        mailbox_id: str,
        code: str,
        *,
        metadata: dict[str, str] | None = None,
    ) -> ConnectionOutcome:
        self._store.record_audit(
            organization_id=actor.organization_id,
            actor_membership_id=actor.membership_id,
            action="mailbox.connection_failed",
            outcome="DENIED",
            reason_code=code,
            target_id=mailbox_id,
            metadata=metadata or {},
        )
        return ConnectionOutcome(False, code, mailbox_id)

    def _audit_refusal(
        self, actor: Actor, mailbox_id: str, action: str, reason: str | None
    ) -> None:
        self._store.record_audit(
            organization_id=actor.organization_id,
            actor_membership_id=actor.membership_id,
            action=action,
            outcome="DENIED",
            reason_code=reason,
            target_id=mailbox_id,
            metadata={},
        )

    def _system_audit(
        self, organization_id: str, mailbox_id: str, action: str, reason: str
    ) -> None:
        self._store.record_audit(
            organization_id=organization_id,
            actor_membership_id=None,
            action=action,
            outcome="FAILED",
            reason_code=reason,
            target_id=mailbox_id,
            metadata={},
        )
