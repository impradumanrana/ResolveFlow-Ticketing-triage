"""Gmail ingestion: watches, history sync, normalization, and recovery (C07).

Delivery is at-least-once and out-of-order. Gmail redelivers push
notifications, Pub/Sub redelivers them again, watches expire every seven days,
history identifiers go stale, and tokens are revoked without warning. None of
that may produce a duplicate ticket, a lost message, or a silent stall.

The design rule throughout: a notification is a *hint that something changed*,
never a description of what changed. Every sync reads from the mailbox's own
stored history cursor, so a delayed, duplicated, or out-of-order notification
converges on the same result.
"""
