# Decision Log

## D-001 — 2026-08-31 — Python-first implementation

Use Python 3.11+, LangGraph, FastMCP, and Streamlit for the shortest reliable hackathon path. This is the best fit for local execution, structured output, and a visible graph without introducing a heavier stack.

## D-002 — 2026-08-31 — OpenAI-first hosted model for the demo

Use OpenAI as the default provider for the demo because it offers the most reliable structured-output behavior for support-ticket classification and route decisions on a prepaid account. The recommended model is `gpt-4.1-mini` for the best balance of price, reliability, and schema adherence. Gemini, Groq, and Ollama remain as fallback providers in that order, but they should only be used if OpenAI is unavailable or rate-limited.

Superseded by D-012: normal product operation is OpenAI-only and fails safely rather than silently changing providers.

## D-003 — 2026-08-31 — Real MCP boundary with transparent KB scoring

Use a lightweight FAQ corpus and deterministic fuzzy/TF-IDF similarity inside the MCP server instead of a vector database or heavy retrieval stack. The scoring must be inspectable and explainable for judge-facing evidence. This minimizes project risk and respects the time-box.

Superseded by D-011 after the user explicitly requested production-style RAG.

## D-004 — 2026-08-31 — Guardrails override the model

The deterministic safety layer must run before route approval. Model or tool failures default to CLARIFY or ESCALATE. This is a deliberate safety decision: the system is allowed to be conservative, but it must never guess when the policy says no.

## D-005 — 2026-08-31 — No autonomous customer contact

Auto-resolution produces an approved draft for review, not a live outbound message. The scope remains triage and routing, not production communication workflows. This keeps the demo safe and within the hackathon brief.

## D-006 — 2026-08-31 — Evidence, not chain-of-thought

Display concise state facts, triggered rules, KB citations, and tool traces. Do not expose hidden reasoning or private internal thought. The dashboard explains what happened and why, without showing chain-of-thought text.

## D-007 — 2026-08-31 — Minimal viable demo before dashboard polish

The first validated milestone is a command-line triage slice with a real MCP server, the LangGraph flow, and three routing fixtures. The dashboard and evaluation view are valuable second-order work and should not block the local sprint if the core flow is proven safe.

## D-008 — 2026-08-31 — Deterministic provider for measured demo evidence

Use the deterministic provider for the repeatable six-case demo and golden metrics while keeping the MCP process boundary and LangGraph execution real. Hosted OpenAI runs remain optional. Provider failures are surfaced as `MODEL_ERROR` and escalated instead of silently relabelled as successful model output.

## D-009 — 2026-08-31 — Low evidence escalates

Use `CLARIFY` only when the system knows which specific customer fact is missing. Weak or out-of-scope KB evidence routes to `ESCALATE`, because asking an arbitrary question would not safely unblock the request.

## D-010 — 2026-08-31 — Persistent hybrid knowledge retrieval

Supersede D-003's JSON-only retrieval core with SQLite persistence and stored deterministic local vectors. Combine vector similarity with BM25-style relevance, fuzzy coverage, keyword evidence, and category preference. Keep the method offline, dependency-light, inspectable in the MCP trace, and safe under the existing confidence threshold. SQLite stores serialized vectors; there is no separate vector-database engine. Support append, full replacement, confirmed full clearing, and starter-library restoration from the UI.

Superseded by D-011 for vector retrieval; SQLite remains the source-of-truth article database.

## D-011 — 2026-08-31 — Dedicated Qdrant RAG with fail-closed grounding

Use OpenAI `text-embedding-3-small` dense embeddings in persistent Qdrant, fuse dense candidates with BM25, and transparently rerank using semantic, lexical, fuzzy, keyword, category, and reciprocal-rank evidence. Generate drafts only from retrieved passages. Require retrieved citation IDs and exact supporting quotes; validation failure withholds the draft and routes to human review. Keep SQLite as the durable article and provenance store and MCP stdio as the mandatory retrieval boundary.

## D-012 — 2026-08-31 — OpenAI-only product runtime

Supersede D-008 for normal app operation. The dashboard, CLI, and user-facing Quality Check always use the OpenAI model configured in `.env`; no processing-mode selector or silent deterministic fallback is exposed. The deterministic provider remains only as an explicit automated-test fixture. OpenAI failure produces `MODEL_ERROR` and safe human review.

## D-013 — 2026-08-31 — Protected demo and evaluation knowledge

Sample batches and Quality Check use a separate 15-article SQLite/Qdrant corpus. Manual and CSV tickets continue to use the operational user-managed corpus. This prevents ingestion experiments from changing the six-ticket judge narrative or invalidating fixed retrieval labels.
