"""The production triage pipeline (C11).

C05 put the knowledge in PostgreSQL, C09 made the rules deterministic, and C10
put every model call behind one gateway. This package runs them as one
pipeline on the durable schema:

    message -> classify -> guardrails -> knowledge over MCP -> decide
            -> evidence-only draft -> grounding validation -> persist

Two properties hold throughout, and the tests exist to keep them holding:

* **The proven behaviour is the MVP's.** The LangGraph workflow, its prompts,
  and its grounding validator are imported and run, not reimplemented. What
  changes is where the knowledge comes from, which model answers, and where
  the result is written.
* **Deterministic decisions win.** The rules engine decides the route from the
  same evidence, and it can only narrow it toward a person (C-D091).
"""
