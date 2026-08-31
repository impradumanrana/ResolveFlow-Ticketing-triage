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

Partially superseded by D-014: the protected corpus still backs sample batches, but Quality Check now measures the operational corpus.

## D-014 — 2026-08-31 — Quality Check measures the knowledge actually in service

Partially supersede D-013. Sample batches keep the protected 15-article corpus so the guided six-ticket demo stays reproducible, but Quality Check now runs against the operational Support knowledge base instead.

A check against a fixed corpus could only ever confirm that the shipped starter articles still retrieve well. It could not answer the question an operator actually has after ingesting their own material: is *this* knowledge base fit to serve. Fixed retrieval labels do not exist for user-uploaded articles, so the check samples up to eight stored articles and has OpenAI write customer-style questions for them, then searches for each one through the real MCP boundary. The 15 labelled golden tickets are still classified, and they now route against the operational corpus too.

The cost is deliberate and must be stated when presenting: Quality Check results are no longer reproducible across different knowledge bases, and route accuracy can move when the corpus changes. To keep the numbers honest, each run records a knowledge fingerprint and a UTC timestamp, the view warns when the corpus has changed since the run, results are discarded when their scope does not match, an empty knowledge base disables the run rather than reporting a vacuous pass, and a corpus that is more than 80% one category raises a tagging warning because skewed categories weaken reranking.

## D-015 — 2026-08-31 — Style Streamlit controls by data-testid, never by data-baseweb

The dashboard's control styling was originally written against `data-baseweb` attributes. The installed Streamlit build emits **zero** such attributes, so every rule for inputs, dropdowns, textareas, tabs, radios, and dropdown popovers matched nothing and failed silently. Controls fell back to a 1px white border on a near-white page and were effectively invisible.

Style controls by their stable `data-testid` values and ARIA roles instead: `stTextInputRootElement`, `stTextAreaRootElement`, `stSelectbox div[role="group"]`, `stTab`, `stBaseButton-*`, and the detached `portal` element that hosts an open dropdown. The page background is a tinted gradient rather than white, so white control surfaces read as raised even before their borders are considered.

A CSS selector that matches nothing produces no error, no warning, and no visual clue that it is broken — which is why this survived several rounds of visual work. A regression test therefore asserts that no `[data-baseweb=` selector reappears and that the explicit field border colour is present.
