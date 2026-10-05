"""The pilot stages, and what it takes to leave one (C14).

The plan says to "run Observe Mode before drafts". That posture already exists
in the code — C13's `read_only` scope profile means an approved answer is
recorded and no draft is created — but it had no name, no way to confirm it
from outside, and no stated condition for advancing. A stage nobody can check
is not a stage, and an advance nobody agreed in advance becomes an argument.

So there are exactly two stages, and the switch between them is the one C13
already built:

`OBSERVE` runs with the `read_only` scope profile. A reviewer sees triage,
routing and a recommended answer; approving records the decision and reports
`DRAFT_SCOPE_NOT_GRANTED`.

`DRAFT` runs with `read_and_draft`. Approving also creates a draft in the
client's mailbox, for a person to read and send themselves.

**There is no third stage.** Sending is not a later step of this ladder; it is
absent from the product. `automatic_sending` is reported as false and is false
structurally — no send endpoint exists, `organizations.sending_enabled` is
constrained to false, and a test asserts both for every file.

`observe_mode` was previously reported to callers as a constant `true`. That
became untrue the moment C13 made drafting possible, so it is now derived from
the posture rather than asserted.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.mailbox.scopes import ScopeProfile, active_profile


class PilotStage(StrEnum):
    OBSERVE = "OBSERVE"
    DRAFT = "DRAFT"


# Which profile puts the deployment in which stage. Exhaustive over
# `ScopeProfile`, so adding a profile without deciding its stage fails a test
# rather than defaulting to the permissive one.
STAGE_FOR_PROFILE: dict[ScopeProfile, PilotStage] = {
    ScopeProfile.READ_ONLY: PilotStage.OBSERVE,
    ScopeProfile.READ_AND_DRAFT: PilotStage.DRAFT,
}

# What the client agrees must be true before leaving a stage. These are the
# conditions, not the evidence; `docs/UAT_CRITERIA.md` records who signs and
# `CLIENT_C14_TEST_REPORT.md` records what was measured.
EXIT_CRITERIA: dict[PilotStage, tuple[str, ...]] = {
    PilotStage.OBSERVE: (
        "At least two weeks of real conversations have been triaged in Observe Mode.",
        "The Quality Check passes on the client's own labelled cases, against thresholds "
        "agreed before the run.",
        "Reviewers agree the recommended answers are ones they would have sent, for a "
        "sample they chose rather than one we chose.",
        "No conversation was routed AUTO_RESOLVE that a reviewer judged unsafe to resolve.",
        "Cost per conversation is within the approved monthly budget at the observed volume.",
        "The client has authorized `gmail.compose`, and a Workspace administrator has "
        "re-consented the mailbox (see docs/MAILBOX_CONNECTION_MODES.md).",
    ),
    # Nothing follows DRAFT. Listed explicitly so the absence is a statement
    # rather than an omission.
    PilotStage.DRAFT: (),
}


def active_stage() -> PilotStage:
    """The stage this deployment is in, derived from its scope profile."""
    return STAGE_FOR_PROFILE[active_profile()]


def observing() -> bool:
    """Whether nothing can be written to the client's mailbox."""
    return active_stage() is PilotStage.OBSERVE


def exit_criteria(stage: PilotStage | None = None) -> tuple[str, ...]:
    return EXIT_CRITERIA[stage or active_stage()]


def describe() -> dict[str, Any]:
    """The posture, in the form the capabilities endpoint and the runbook use."""
    stage = active_stage()
    return {
        "pilot_stage": stage.value,
        "observe_mode": stage is PilotStage.OBSERVE,
        # Not derived from anything: there is no configuration that turns this
        # on, which is why it is a constant here and a constraint in the
        # database.
        "automatic_sending": False,
        "exit_criteria": list(EXIT_CRITERIA[stage]),
    }
