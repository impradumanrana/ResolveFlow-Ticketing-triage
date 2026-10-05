"""Compare MVP retrieval against the pgvector path on labelled cases.

The C05 gate is a measurement. This script ingests the same starter corpus into
both paths, runs the same labelled queries through each, and prints Recall@1,
Recall@3, and MRR side by side so a regression is visible rather than inferred.

Runs entirely offline: `RESOLVEFLOW_TEST_MODE=1` selects deterministic
embeddings on both sides, so no paid model is called.

    RESOLVEFLOW_TEST_MODE=1 DATABASE_URL=... python -m scripts.compare_retrieval
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "app" / "fixtures" / "retrieval_cases.json"
FAQS = ROOT / "app" / "fixtures" / "faqs.json"


def main() -> int:
    if os.getenv("RESOLVEFLOW_TEST_MODE") != "1":
        print(
            "Set RESOLVEFLOW_TEST_MODE=1. This comparison must not spend "
            "provider credit.",
            file=sys.stderr,
        )
        return 2

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 2

    from sqlalchemy import create_engine, text

    from app.knowledge import ingestion, retrieval
    from app.knowledge.evaluation import compare, evaluate, load_cases
    from app.knowledge_store import initialize_database, search_articles

    cases = load_cases(CASES)
    articles = json.loads(FAQS.read_text(encoding="utf-8"))

    # --- baseline: the proven MVP path -------------------------------------
    #
    # Built in a temporary directory from the same 15 starter fixtures. Using
    # the developer's local corpus would compare the two paths over different
    # content, which is not a comparison at all.
    with tempfile.TemporaryDirectory() as workspace:
        baseline_db = Path(workspace) / "knowledge.db"
        initialize_database(baseline_db)

        baseline = evaluate(
            cases,
            lambda case: [
                hit["article_id"]
                for hit in search_articles(case.query, case.category, 3, baseline_db)
            ],
            label="MVP",
        )

    # --- candidate: pgvector + PostgreSQL full text ------------------------
    engine = create_engine(database_url)
    with engine.begin() as connection:
        organization_id = connection.execute(
            text("SELECT id::text FROM organizations LIMIT 1")
        ).scalar_one()

        # Sources as well as articles: the sha256 uniqueness index would
        # otherwise reject re-ingesting the same corpus on a second run.
        for table in ("knowledge_articles", "knowledge_sources"):
            connection.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :org"),  # noqa: S608
                {"org": organization_id},
            )

        # The same 15 starter articles, ingested through the production path.
        # Same fields the MVP scores, including curated keywords, so the two
        # paths see the same information rather than the new one seeing less.
        csv_lines = ["article_id,title,body,category,keywords"]
        for article in articles:
            body = article["excerpt"].replace('"', "'")
            title = article["title"].replace('"', "'")
            keywords = ";".join(article.get("keywords") or [])
            csv_lines.append(
                f'{article["article_id"]},"{title}","{body}",'
                f'{article["category"]},"{keywords}"'
            )
        payload = "\n".join(csv_lines).encode("utf-8")

        result = ingestion.ingest_file(
            connection, organization_id, payload, "starter-corpus.csv"
        )
        if not result.succeeded:
            print(f"Ingestion failed: {result.error}", file=sys.stderr)
            return 1
        print(
            f"Ingested {result.articles_created} articles, "
            f"{result.chunks_created} chunks, {result.embeddings_created} embeddings."
        )

        def retrieve(case):
            passages = retrieval.search(
                connection,
                organization_id,
                case.query,
                category=case.category,
                top_k=10,
            )
            return [p.external_ref for p in retrieval.best_per_article(passages)][:3]

        candidate = evaluate(cases, retrieve, label="pgvector")

    print()
    print(baseline.describe())
    print()
    print(candidate.describe())
    print()
    print(compare(baseline, candidate))

    return 0 if candidate.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
