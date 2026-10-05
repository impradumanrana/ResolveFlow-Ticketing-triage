"""Failure codes, and what each one permits.

Each code answers three questions up front, so no call site has to decide:
may the same model be retried, may another approved model be tried, and what
may an operator be told. The table is the policy; tests pin it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FailureCode(StrEnum):
    # Configuration and policy - nothing was called.
    CONFIG_MISSING = "CONFIG_MISSING"
    MODEL_NOT_APPROVED = "MODEL_NOT_APPROVED"
    REGION_NOT_SUPPORTED = "REGION_NOT_SUPPORTED"
    BUDGET_NOT_CONFIGURED = "BUDGET_NOT_CONFIGURED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    EMBEDDING_DIMENSIONS_MISMATCH = "EMBEDDING_DIMENSIONS_MISMATCH"
    PROVIDER_NOT_SUPPORTED = "PROVIDER_NOT_SUPPORTED"
    # Credential.
    CREDENTIAL_MISSING = "CREDENTIAL_MISSING"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    CREDENTIAL_INVALID = "CREDENTIAL_INVALID"
    CREDENTIAL_ACCESS_DENIED = "CREDENTIAL_ACCESS_DENIED"
    # Provider.
    PERMISSION_DENIED = "PERMISSION_DENIED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    REQUEST_REJECTED = "REQUEST_REJECTED"
    # Output.
    INVALID_SCHEMA = "INVALID_SCHEMA"
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"
    CONTENT_FILTERED = "CONTENT_FILTERED"
    # Aggregate.
    ALL_CANDIDATES_FAILED = "ALL_CANDIDATES_FAILED"


@dataclass(frozen=True)
class FailurePolicy:
    retry_same_model: bool
    try_fallback: bool
    message: str


# Retrying helps only when the provider was momentarily unable to serve.
# Fallback is allowed for exactly the same reasons: the configuration is sound
# and the provider was not available. Credential, budget, quota, policy, and
# output failures need a person, and silently moving to another model would
# hide them - or, for budget and quota, spend money the client did not choose
# to spend.
FAILURE_POLICIES: dict[FailureCode, FailurePolicy] = {
    FailureCode.CONFIG_MISSING: FailurePolicy(False, False, "No AI provider is configured."),
    FailureCode.MODEL_NOT_APPROVED: FailurePolicy(
        False, False, "The configured model is not approved for this use."
    ),
    FailureCode.REGION_NOT_SUPPORTED: FailurePolicy(
        False, False, "The configured processing region is not available for this provider."
    ),
    FailureCode.BUDGET_NOT_CONFIGURED: FailurePolicy(
        False, False, "No monthly budget is set, so AI calls are paused."
    ),
    FailureCode.BUDGET_EXCEEDED: FailurePolicy(False, False, "The monthly AI budget is used up."),
    FailureCode.EMBEDDING_DIMENSIONS_MISMATCH: FailurePolicy(
        False, False, "The embedding model does not match the knowledge index."
    ),
    FailureCode.PROVIDER_NOT_SUPPORTED: FailurePolicy(
        False, False, "This provider is not supported."
    ),
    FailureCode.CREDENTIAL_MISSING: FailurePolicy(False, False, "No API key has been stored."),
    FailureCode.CREDENTIAL_EXPIRED: FailurePolicy(
        False, False, "The stored API key version is disabled or destroyed."
    ),
    FailureCode.CREDENTIAL_INVALID: FailurePolicy(
        False, False, "The provider rejected the API key."
    ),
    FailureCode.CREDENTIAL_ACCESS_DENIED: FailurePolicy(
        False, False, "The service cannot read the stored API key."
    ),
    FailureCode.PERMISSION_DENIED: FailurePolicy(
        False, False, "The API key is not permitted to use this model or region."
    ),
    FailureCode.MODEL_UNAVAILABLE: FailurePolicy(
        False, False, "The provider does not offer the configured model."
    ),
    FailureCode.RATE_LIMITED: FailurePolicy(True, True, "The provider is rate limiting requests."),
    FailureCode.QUOTA_EXHAUSTED: FailurePolicy(
        False, False, "The provider account has no remaining quota."
    ),
    FailureCode.PROVIDER_UNAVAILABLE: FailurePolicy(
        True, True, "The provider is temporarily unavailable."
    ),
    FailureCode.PROVIDER_TIMEOUT: FailurePolicy(
        True, True, "The provider did not respond in time."
    ),
    FailureCode.REQUEST_REJECTED: FailurePolicy(False, False, "The provider rejected the request."),
    FailureCode.INVALID_SCHEMA: FailurePolicy(
        False, False, "The model's answer did not match the required structure."
    ),
    FailureCode.OUTPUT_TRUNCATED: FailurePolicy(False, False, "The model's answer was cut off."),
    FailureCode.CONTENT_FILTERED: FailurePolicy(False, False, "The provider withheld the answer."),
    FailureCode.ALL_CANDIDATES_FAILED: FailurePolicy(False, False, "Every approved model failed."),
}


# What a successful server-side key check actually proves: the key is readable,
# accepted, permitted, and every configured model exists in the region. Budget,
# quota, rate limits, and output problems are not tested by it, so a passing
# check must not clear them.
CLEARED_BY_VERIFICATION = frozenset(
    {
        FailureCode.CREDENTIAL_MISSING,
        FailureCode.CREDENTIAL_EXPIRED,
        FailureCode.CREDENTIAL_INVALID,
        FailureCode.CREDENTIAL_ACCESS_DENIED,
        FailureCode.PERMISSION_DENIED,
        FailureCode.MODEL_UNAVAILABLE,
    }
)


def failure_message(code: str | None) -> str | None:
    """The operator message for a stored code; None for anything unrecognised."""
    if code is None or code not in FailureCode.__members__:
        return None
    return FAILURE_POLICIES[FailureCode(code)].message


class GatewayFailure(Exception):
    """A call that did not produce a usable answer.

    The string form is the code and nothing else. Provider error text is never
    attached: an authentication error from OpenAI quotes part of the key.
    """

    def __init__(
        self,
        code: FailureCode,
        *,
        provider: str | None = None,
        model: str | None = None,
        status: int | None = None,
        retry_after_seconds: float | None = None,
        attempts: tuple[AttemptRecord, ...] = (),
    ):
        super().__init__(code.value)
        self.code = code
        self.provider = provider
        self.model = model
        self.status = status
        self.retry_after_seconds = retry_after_seconds
        self.attempts = attempts

    @property
    def policy(self) -> FailurePolicy:
        return FAILURE_POLICIES[self.code]

    @property
    def message(self) -> str:
        return self.policy.message

    def __repr__(self) -> str:
        return (
            f"GatewayFailure({self.code.value}, provider={self.provider!r}, model={self.model!r})"
        )


@dataclass(frozen=True)
class AttemptRecord:
    """One attempt, as an operator may see it. No content, no provider text."""

    provider: str
    model: str
    outcome: str
    failure_code: str | None = None
    fallback_from: str | None = None
