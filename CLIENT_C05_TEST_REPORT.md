# ResolveFlow AI - C05 Verification Report

**Phase:** C05 - Knowledge ingestion and production RAG

**Result:** GREEN

**Date:** 15 September 2026

## Scope and safety

No cloud resource was created, read, or mutated. No Gmail mailbox was connected. **No paid model was called**: every embedding in every run came from the deterministic offline embedder selected by `RESOLVEFLOW_TEST_MODE=1`. No production data was touched.

A local `pgvector/pgvector:pg16` container was used to ingest the synthetic starter corpus, run the labelled comparison, and verify isolation. It was stopped and its volumes removed.

## Delivered

`app/knowledge/`:

- **`extraction.py`** - validation and extraction for PDF, Markdown, text, HTML, and CSV, with one deterministic normalisation pass. HTML uses the standard library and drops script/style rather than indexing markup.
- **`chunking.py`** - passage chunking with exact character offsets and an enforced `body[start:end] == content` invariant.
- **`embeddings.py`** - production-width embeddings, with the MVP's deterministic scheme widened to 1536 for offline runs.
- **`repository.py`** - pgvector and full-text candidate generation, scoped in SQL.
- **`retrieval.py`** - the MVP's rerank formula over passages.
- **`ingestion.py`** - ingest, reindex, archive, and typed-confirmation clear-all.
- **`evaluation.py`** - Recall@K, MRR, versioned thresholds, and an explicit old-vs-new comparison.

Plus migration `20260915_0005` (curated `search_terms`), `scripts/compare_retrieval.py`, `make retrieval-check`, and 9 labelled cases in `app/fixtures/retrieval_cases.json`.

## Automated gate evidence

| Check | Result |
|---|---|
| Python suite | **246 passed** (was 162 at C04) |
| Web suite | 62 passed |
| `make check` | exit 0 |
| Ruff / mypy / ESLint / tsc | all clean |
| Next.js production build | passed |
| Infrastructure checks | 0 findings |
| Ingestion and offset tests | 52 passed |
| Retrieval scoring and evaluation tests | 30 passed |
| Schema invariants | 29 passed |

### The gate: labelled Recall@K and MRR

Both paths ingest the same 15-article starter corpus and answer the same 9 labelled paraphrase queries. The MVP baseline is built in a temporary directory from the same fixtures — using the developer's local corpus would compare the two paths over different content.

| metric | MVP | pgvector | delta |
|---|---|---|---|
| recall@1 | 1.000 | 1.000 | +0.000 |
| recall@3 | 1.000 | 1.000 | +0.000 |
| MRR | 1.000 | 1.000 | +0.000 |

Thresholds (`c05.2026-09-15`): recall@1 ≥ 0.80, recall@3 ≥ 0.90, MRR ≥ 0.85. **All pass on both paths, with no regression.**

Reproduce with `RESOLVEFLOW_TEST_MODE=1 DATABASE_URL=... make retrieval-check`.

### The gate: archived or unauthorized content cannot be retrieved

| Check | Result |
|---|---|
| Archived article disappears from results | KB-001 present before, absent after |
| Archived article still in the database | `status=ARCHIVED`, recoverable |
| Soft-deleted article unretrievable | KB-002 absent from results |
| A second organization querying this corpus | **0 results** |
| Clear-all with a wrong phrase (3 variants) | all refused |
| Clear-all with the exact phrase | 15 deleted |

### The gate: citations map to exact passages

Every returned passage's `citation()` was sliced back out of the stored article body by its offsets and compared. **All exact.** The invariant is enforced by `verify_offsets` on every chunking run, and the offline suite checks it across six body shapes including unicode, unstructured text, and text with no paragraph breaks.

### Defects the verification caught

1. **The lexical arm was dead.** `plainto_tsquery` ANDs every lexeme, so a natural-language question matched no passage at all. `lexical_rank` was `None` for every candidate — the hybrid was running on dense similarity alone while appearing to work. Fixed by building an explicit OR tsquery (C-D048).
2. **A flattering baseline.** The first comparison showed pgvector *beating* the MVP by +0.111 recall@1. That was an artifact: `search_articles` had been reading the developer's local corpus rather than a clean 15-article one. With a fair baseline the MVP scored 9/9 and pgvector 8/9 — a real regression the first run had hidden.
3. **A dropped capability, found by that regression.** The one miss was a query for "receipt" returning the invoice article instead of the order-confirmation article. Cause: the MVP scores curated per-article keywords, and the port dropped them. Restored as a first-class column (C-D049), after which both paths score 9/9. This was a real gap, not test tuning: customers say "receipt" for what the article calls an "order confirmation".
4. **A downgrade that could not run.** Migration 0005 passed every offline check but failed on a real database: Alembic applies `env.py`'s naming convention on *drop* as well as create, so passing the rendered `ck_<table>_<name>` produced a doubled, truncated, non-existent name. Fixed, and a static check now rejects any `drop_constraint` given a rendered name.
5. **Unrelated files edited.** A `ruff --fix` over `tests/` reordered imports in three proven MVP test files outside C05 scope. Reverted; the MVP tests are byte-identical to before.

## What C05 deliberately did not do

- **No knowledge UI.** Upload, parse status, chunk preview, retrieval test, and clear-all confirmation are implemented as functions with tested behaviour; their screens are C08.
- **No MCP rewiring.** The MCP boundary still serves the MVP retrieval path. Moving the tool onto pgvector is C11, where the triage graph moves with it.
- **No live-model evaluation.** Every measurement used deterministic embeddings. Numbers against a real embedding model will differ and must be re-measured in staging.
- **No reindex-on-model-change automation.** `reindex_article` exists; detecting that the configured embedding model changed and driving a full reindex is C10 work.

## Residual risks

| Risk | Treatment |
|---|---|
| All metrics use deterministic embeddings; real OpenAI embeddings will rank differently | Re-run `make retrieval-check` against the client corpus and real models during the C14 pilot before relying on the thresholds |
| 9 labelled cases over 15 articles is a small set | Expand with client-approved cases at C14; the harness and thresholds are versioned and ready |
| BM25 corpus statistics issue one query per query-term | Bounded by query length and GIN-indexed; revisit if the corpus reaches a scale where it shows in p95 |
| Curated terms are bounded at 24 but not otherwise policed | A knowledge manager could stuff terms to boost an article; visible in the article record and a C08 review concern |
| HNSW recall is approximate; a true nearest neighbour can be missed | Accepted for latency. The lexical arm is the mitigation, which is why defect 1 mattered |
| Chunk overlap means a passage can exceed `max_chars` | Intentional; offsets remain exact and sizes stay well inside embedding limits |

## Preserved working-tree state

`ResolveFlow-AI-Presentation.pptx` remained deleted and untouched. The untracked planning documents, local `.env`, and `app/data` were not modified. The three MVP test files briefly touched by `ruff --fix` were restored. Docker volumes created for verification were removed.

## Gate conclusion

C05 meets its gate: labelled Recall@K and MRR thresholds pass with no regression against the proven path, archived and cross-organization content cannot be retrieved, and citations map to exact stored passages. C06 may begin.
