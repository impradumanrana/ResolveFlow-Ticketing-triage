"""Production knowledge ingestion and retrieval (C05).

The MVP's proven behaviour is preserved rather than reimplemented:

* Tokenisation, concept expansion, fuzzy coverage, and embeddings are imported
  from `app.knowledge_store`, so there is exactly one implementation and no
  drift between the Streamlit reference and production.
* The rerank formula in `retrieval.py` is the MVP's formula, component for
  component and weight for weight. Only the *sources* change: pgvector supplies
  the dense candidates and PostgreSQL full-text supplies the lexical ones, both
  scoped to one organization in SQL.

What is new in C05: exact passage offsets so a citation resolves to the stored
text rather than to a whole document, tenant and status filtering that happens
in the database rather than after the fact, and a labelled evaluation that
compares the new path against the old instead of assuming parity.
"""
