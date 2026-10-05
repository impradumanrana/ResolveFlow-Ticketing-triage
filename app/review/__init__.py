"""Human review, and the provider draft an approval produces (C12).

Everything a person decides about a conversation goes through one service:
edit, approve, reject, reroute, assign, resolve. Four properties hold for all
of them, and the tests exist to keep them holding:

* **Nothing is ever sent.** An approval creates a *draft* in the client's
  mailbox for a person to send. There is no send call anywhere in this package,
  `organizations.sending_enabled` is constrained to false, and C06's schema
  refuses to store a credential that could send at all (C-D010).
* **Every decision is attributable.** Each one records the person, the reason
  where a reason is owed, the ticket version before and after, and an audit
  event - including the decisions that were refused.
* **Concurrency is decided, not raced.** A decision names the ticket version it
  was made against. Two reviewers acting on the same version produce one change
  and one visible conflict, never two silent ones.
* **A retry changes nothing twice.** An idempotency key replays the first
  outcome, and the provider draft is claimed before the provider is called.
"""
