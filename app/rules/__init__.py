"""Deterministic routing, service levels, and guardrails (C09).

Three things happen here, in this order, and the order is the point:

1. **Deterministic safety runs first.** The MVP's proven guardrails decide
   whether a conversation may be answered automatically at all. They are
   imported, not reimplemented (C-D046 applies here too).
2. **Client rules route.** Versioned rules assign department, queue, urgency,
   and owner. They may change *where* a conversation goes; they may never make
   an unsafe conversation safe.
3. **Service levels are computed** against each policy's own business hours in
   its own time zone.

A rule that cannot be evaluated, a policy that is missing, and two rules that
genuinely conflict all resolve the same way: to a person, with a reason code.
"""
