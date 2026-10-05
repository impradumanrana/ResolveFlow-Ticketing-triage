"""In-memory triage store for offline tests."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.triage.records import Conversation, TriageOutcome


@dataclass
class InMemoryTriageStore:
    runs: list[tuple[Conversation, TriageOutcome]] = field(default_factory=list)

    def record_run(self, conversation: Conversation, outcome: TriageOutcome) -> str:
        # Mirrors the correlation-id uniqueness the database enforces.
        for index, (_, stored) in enumerate(self.runs):
            if stored.correlation_id == outcome.correlation_id:
                self.runs[index] = (conversation, outcome)
                return stored.run_id or str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        self.runs.append((conversation, outcome))
        return run_id

    @property
    def last(self) -> TriageOutcome:
        return self.runs[-1][1]
