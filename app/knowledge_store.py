from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Sequence

from openai import OpenAI
from qdrant_client import QdrantClient, models

from app.config import (
    HAS_OPENAI_KEY,
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_DIMENSIONS,
    OPENAI_EMBEDDING_MODEL,
    QDRANT_COLLECTION,
)

APP_ROOT = Path(__file__).resolve().parent
FIXTURE_DIR = APP_ROOT / "fixtures"
DEFAULT_DB_PATH = APP_ROOT / "data" / "knowledge.db"
TEST_EMBEDDING_MODEL = "resolveflow-test-dense-v1"
TEST_EMBEDDING_DIMENSIONS = 384
VALID_CATEGORIES = {"technical", "billing", "account"}

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "for", "from", "how",
    "i", "if", "in", "is", "it", "me", "my", "of", "on", "or", "please", "the",
    "this", "to", "two", "was", "what", "when", "where", "with", "you", "your",
}
CONCEPT_GROUPS = {
    "access": {"access", "login", "signin", "authenticate", "enter"},
    "password": {"password", "passcode", "credential"},
    "recover": {"recover", "recovery", "restore", "forgot", "reset"},
    "invoice": {"invoice", "receipt", "bill", "statement"},
    "payment": {"payment", "pay", "card", "transaction", "charge"},
    "duplicate": {"duplicate", "double", "twice", "repeated"},
    "cancel": {"cancel", "stop", "end", "terminate"},
    "subscription": {"subscription", "membership", "plan", "renewal"},
    "crash": {"crash", "crashes", "freeze", "freezes", "frozen", "hang", "closing", "closes"},
    "application": {"app", "application", "program", "software", "mobile"},
    "sync": {"sync", "synchronize", "pair", "connect", "reconnect"},
    "download": {"download", "export", "file", "attachment"},
    "security": {"security", "compromised", "hacked", "unauthorized", "takeover"},
    "twofactor": {"2fa", "mfa", "otp", "authenticator", "verification"},
    "upgrade": {"upgrade", "increase", "higher", "premium"},
    "update": {"update", "change", "replace", "edit", "switch"},
    "dashboard": {"dashboard", "homepage", "screen", "portal"},
    "missing": {"missing", "absent", "gone", "unavailable", "find", "nobody"},
    "delivery": {"delivery", "delivered", "arrived", "arrival", "courier", "parcel", "shipment", "box"},
    "order": {"order", "purchase", "purchased", "bought"},
    "warranty": {"warranty", "fault", "faulty", "failed", "failure", "defective", "broke", "broken"},
    "bulk": {"bulk", "wholesale", "quote", "quantity", "units", "monitors"},
}
CONCEPT_LOOKUP = {term: concept for concept, terms in CONCEPT_GROUPS.items() for term in terms}


def database_path() -> Path:
    configured = os.getenv("KB_DB_PATH")
    return Path(configured).expanduser() if configured else DEFAULT_DB_PATH


def demo_database_path() -> Path:
    """Protected corpus for repeatable judge demos, separate from user knowledge."""
    configured = os.getenv("DEMO_KB_DB_PATH")
    return Path(configured).expanduser() if configured else database_path().parent / "demo" / "knowledge.db"


def qdrant_path(db_path: Path | None = None) -> Path:
    configured = os.getenv("QDRANT_PATH")
    return Path(configured).expanduser() if configured else (db_path or database_path()).parent / "qdrant"


def embedding_model() -> str:
    return TEST_EMBEDDING_MODEL if os.getenv("RESOLVEFLOW_TEST_MODE") == "1" else str(OPENAI_EMBEDDING_MODEL)


def embedding_dimensions() -> int:
    return TEST_EMBEDDING_DIMENSIONS if os.getenv("RESOLVEFLOW_TEST_MODE") == "1" else OPENAI_EMBEDDING_DIMENSIONS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tokens(text: str, *, expand_concepts: bool = True) -> list[str]:
    normalized = (text or "").lower()
    for phrase, replacement in {
        "sign in": "signin", "signed in": "signin", "log in": "login", "logged in": "login", "one time password": "otp",
        "one-time password": "otp", "otp app": "otp authenticator",
        "without permission": "unauthorized", "someone else": "unauthorized person",
        "home screen": "dashboard", "card on file": "payment method",
    }.items():
        normalized = normalized.replace(phrase, replacement)
    raw = [token for token in re.findall(r"[a-z0-9]+", normalized) if token not in STOP_WORDS and len(token) > 1]
    if not expand_concepts:
        return raw
    return raw + [f"concept_{CONCEPT_LOOKUP[token]}" for token in raw if token in CONCEPT_LOOKUP]


def _test_dense_embedding(text: str) -> list[float]:
    """Network-free dense vector used only when tests explicitly enable test mode."""
    words = _tokens(text)
    features: list[tuple[str, float]] = [(f"w:{word}", 1.0) for word in words]
    features.extend((f"b:{left}_{right}", 1.35) for left, right in zip(words, words[1:]))
    vector = [0.0] * TEST_EMBEDDING_DIMENSIONS
    for feature, weight in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        vector[value % TEST_EMBEDDING_DIMENSIONS] += weight if value & 1 else -weight
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    if os.getenv("RESOLVEFLOW_TEST_MODE") == "1":
        return [_test_dense_embedding(text) for text in texts]
    if not HAS_OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY is required to create knowledge embeddings")
    response = OpenAI(api_key=OPENAI_API_KEY, timeout=60.0, max_retries=2).embeddings.create(
        model=OPENAI_EMBEDDING_MODEL,
        input=[text.replace("\n", " ") for text in texts],
        dimensions=OPENAI_EMBEDDING_DIMENSIONS,
        encoding_format="float",
    )
    return [list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)]


def _article_text(article: dict[str, Any]) -> str:
    keywords = " ".join(article.get("keywords") or [])
    return f"Title: {article['title']}\nCategory: {article['category']}\nKeywords: {keywords}\nApproved guidance: {article['excerpt']}"


def _validate_article(article: dict[str, Any]) -> dict[str, Any]:
    clean = {
        "article_id": str(article.get("article_id", "")).strip(),
        "title": str(article.get("title", "")).strip(),
        "category": str(article.get("category", "")).strip().lower(),
        "excerpt": str(article.get("excerpt", "")).strip(),
        "keywords": [str(item).strip() for item in (article.get("keywords") or []) if str(item).strip()],
    }
    if not all(clean[key] for key in ("article_id", "title", "category", "excerpt")):
        raise ValueError("Each knowledge article needs an ID, title, category, and approved answer.")
    if clean["category"] not in VALID_CATEGORIES:
        raise ValueError(f"Unsupported category: {clean['category']}")
    return clean


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize_database(db_path: Path | None = None, fixture_dir: Path | None = None) -> Path:
    path = db_path or database_path()
    fixtures = fixture_dir or FIXTURE_DIR
    with _connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS articles (
                article_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                category TEXT NOT NULL CHECK(category IN ('technical', 'billing', 'account')),
                excerpt TEXT NOT NULL, keywords_json TEXT NOT NULL, source TEXT NOT NULL,
                embedding_json TEXT NOT NULL, embedding_model TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category);
            """
        )
        if not connection.execute("SELECT value FROM metadata WHERE key='initialized'").fetchone():
            seed: list[tuple[dict[str, Any], str]] = []
            for filename, source in (("faqs.json", "Built-in"), ("custom_faqs.json", "Added by user")):
                fixture = fixtures / filename
                if fixture.exists():
                    data = json.loads(fixture.read_text())
                    if isinstance(data, list):
                        seed.extend((article, source) for article in data)
            _upsert_many(connection, seed)
            connection.execute("INSERT INTO metadata(key, value) VALUES('initialized', ?)", (_now(),))
            connection.execute("INSERT INTO metadata(key, value) VALUES('schema_version', '2')")
    return path


def _upsert_many(connection: sqlite3.Connection, articles: Iterable[tuple[dict[str, Any], str]]) -> list[dict[str, Any]]:
    prepared = [(_validate_article(raw), source) for raw, source in articles]
    if not prepared:
        return []
    vectors = embed_texts([_article_text(article) for article, _ in prepared])
    timestamp = _now()
    for (article, source), vector in zip(prepared, vectors):
        connection.execute(
            """
            INSERT INTO articles(article_id, title, category, excerpt, keywords_json, source,
                embedding_json, embedding_model, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                title=excluded.title, category=excluded.category, excerpt=excluded.excerpt,
                keywords_json=excluded.keywords_json, source=excluded.source,
                embedding_json=excluded.embedding_json, embedding_model=excluded.embedding_model,
                updated_at=excluded.updated_at
            """,
            (article["article_id"], article["title"], article["category"], article["excerpt"],
             json.dumps(article["keywords"]), source, json.dumps(vector), embedding_model(), timestamp, timestamp),
        )
    return [article for article, _ in prepared]


def _point_id(article_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"resolveflow:{article_id}"))


def _qdrant_client(db_path: Path | None = None) -> QdrantClient:
    path = qdrant_path(db_path)
    path.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(path))


def _rebuild_qdrant(rows: Sequence[sqlite3.Row], db_path: Path | None = None) -> None:
    client = _qdrant_client(db_path)
    try:
        if client.collection_exists(QDRANT_COLLECTION):
            client.delete_collection(QDRANT_COLLECTION)
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=models.VectorParams(size=embedding_dimensions(), distance=models.Distance.COSINE),
        )
        if rows:
            client.upsert(collection_name=QDRANT_COLLECTION, wait=True, points=[
                models.PointStruct(
                    id=_point_id(row["article_id"]), vector=json.loads(row["embedding_json"]),
                    payload={"article_id": row["article_id"], "category": row["category"], "source": row["source"]},
                ) for row in rows
            ])
    finally:
        client.close()


def ensure_vector_index(db_path: Path | None = None) -> None:
    initialize_database(db_path)
    with _connect(db_path) as connection:
        rows = connection.execute("SELECT * FROM articles ORDER BY article_id").fetchall()
        stale = [row for row in rows if row["embedding_model"] != embedding_model() or len(json.loads(row["embedding_json"])) != embedding_dimensions()]
        if stale:
            vectors = embed_texts([_article_text({
                "article_id": row["article_id"], "title": row["title"], "category": row["category"],
                "excerpt": row["excerpt"], "keywords": json.loads(row["keywords_json"]),
            }) for row in stale])
            for row, vector in zip(stale, vectors):
                connection.execute(
                    "UPDATE articles SET embedding_json=?, embedding_model=?, updated_at=? WHERE article_id=?",
                    (json.dumps(vector), embedding_model(), _now(), row["article_id"]),
                )
            rows = connection.execute("SELECT * FROM articles ORDER BY article_id").fetchall()
    rebuild = bool(stale)
    client = _qdrant_client(db_path)
    try:
        if not client.collection_exists(QDRANT_COLLECTION):
            rebuild = True
        else:
            info = client.get_collection(QDRANT_COLLECTION)
            size = getattr(info.config.params.vectors, "size", None)
            rebuild = rebuild or size != embedding_dimensions() or info.points_count != len(rows)
    finally:
        client.close()
    if rebuild:
        _rebuild_qdrant(rows, db_path)


def list_articles(db_path: Path | None = None) -> list[dict[str, Any]]:
    initialize_database(db_path)
    with _connect(db_path) as connection:
        rows = connection.execute(
            "SELECT article_id, title, category, excerpt, keywords_json, source, updated_at FROM articles ORDER BY article_id"
        ).fetchall()
    return [{"article_id": row["article_id"], "title": row["title"], "category": row["category"],
             "excerpt": row["excerpt"], "keywords": json.loads(row["keywords_json"]),
             "_source": row["source"], "updated_at": row["updated_at"]} for row in rows]


def add_articles(articles: Iterable[dict[str, Any]], source: str = "Added by user", db_path: Path | None = None) -> int:
    initialize_database(db_path)
    prepared = list(articles)
    with _connect(db_path) as connection:
        existing = {row[0] for row in connection.execute("SELECT article_id FROM articles")}
        duplicates = [str(article.get("article_id", "")).strip() for article in prepared if str(article.get("article_id", "")).strip() in existing]
        if duplicates:
            raise ValueError(f"Article ID already exists: {', '.join(duplicates)}")
        _upsert_many(connection, ((article, source) for article in prepared))
    ensure_vector_index(db_path)
    return len(prepared)


def replace_articles(articles: Iterable[dict[str, Any]], source: str = "Added by user", db_path: Path | None = None) -> int:
    initialize_database(db_path)
    prepared = list(articles)
    with _connect(db_path) as connection:
        connection.execute("DELETE FROM articles")
        _upsert_many(connection, ((article, source) for article in prepared))
    ensure_vector_index(db_path)
    return len(prepared)


def clear_articles(scope: str = "all", db_path: Path | None = None) -> int:
    initialize_database(db_path)
    if scope not in {"user", "all"}:
        raise ValueError("Clear scope must be 'user' or 'all'.")
    with _connect(db_path) as connection:
        cursor = connection.execute("DELETE FROM articles" if scope == "all" else "DELETE FROM articles WHERE source != 'Built-in'")
    ensure_vector_index(db_path)
    return max(0, cursor.rowcount)


def restore_built_in_articles(db_path: Path | None = None, fixture_dir: Path | None = None) -> int:
    fixtures = fixture_dir or FIXTURE_DIR
    articles = json.loads((fixtures / "faqs.json").read_text())
    initialize_database(db_path, fixtures)
    with _connect(db_path) as connection:
        _upsert_many(connection, ((article, "Built-in") for article in articles))
    ensure_vector_index(db_path)
    return len(articles)


def prepare_demo_knowledge(fixture_dir: Path | None = None) -> Path:
    """Create or repair the immutable starter corpus used by sample batches and evals."""
    fixtures = fixture_dir or FIXTURE_DIR
    path = demo_database_path()
    expected = json.loads((fixtures / "faqs.json").read_text())
    initialize_database(path, fixtures)
    current_ids = {article["article_id"] for article in list_articles(path)}
    expected_ids = {article["article_id"] for article in expected}
    if current_ids != expected_ids:
        replace_articles(expected, source="Protected demo corpus", db_path=path)
    else:
        ensure_vector_index(path)
    return path


def _fuzzy_coverage(query_tokens: set[str], document_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    matches = sum(1 for query_token in query_tokens if query_token in document_tokens or any(
        len(query_token) >= 5 and len(token) >= 5 and SequenceMatcher(None, query_token, token).ratio() >= 0.84
        for token in document_tokens
    ))
    return matches / len(query_tokens)


def search_articles(query: str, category: str = "", top_k: int = 3, db_path: Path | None = None) -> list[dict[str, Any]]:
    ensure_vector_index(db_path)
    with _connect(db_path) as connection:
        rows = connection.execute("SELECT * FROM articles").fetchall()
    if not rows or not _tokens(query):
        return []

    query_vector = embed_texts([query])[0]
    client = _qdrant_client(db_path)
    try:
        dense_hits = client.query_points(
            collection_name=QDRANT_COLLECTION, query=query_vector, with_payload=True, limit=min(12, len(rows)),
        ).points
    finally:
        client.close()
    dense_scores = {str(hit.payload.get("article_id")): max(0.0, min(1.0, float(hit.score))) for hit in dense_hits}
    dense_ranks = {str(hit.payload.get("article_id")): rank for rank, hit in enumerate(dense_hits, 1)}

    query_tokens = _tokens(query)
    query_counts = Counter(query_tokens)
    documents = [_tokens(f"{row['title']} {row['title']} {' '.join(json.loads(row['keywords_json']))} {row['excerpt']}") for row in rows]
    document_frequency = Counter(term for terms in documents for term in set(terms))
    average_length = sum(len(terms) for terms in documents) / len(documents)
    raw_lexical: list[float] = []
    for terms in documents:
        counts, score = Counter(terms), 0.0
        for term, query_frequency in query_counts.items():
            frequency = counts.get(term, 0)
            if frequency:
                inverse_frequency = math.log(1 + (len(documents) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
                denominator = frequency + 1.5 * (0.25 + 0.75 * len(terms) / max(1, average_length))
                score += inverse_frequency * frequency * 2.5 / denominator * query_frequency
        raw_lexical.append(score)
    lexical_max = max(raw_lexical) or 1.0
    lexical_scores = {row["article_id"]: value / lexical_max if value else 0.0 for row, value in zip(rows, raw_lexical)}
    lexical_order = sorted(lexical_scores, key=lexical_scores.get, reverse=True)
    lexical_ranks = {article_id: rank for rank, article_id in enumerate(lexical_order, 1)}
    candidate_ids = set(list(dense_ranks)[:12] + lexical_order[:12])

    results: list[dict[str, Any]] = []
    for row, document_tokens in zip(rows, documents):
        if row["article_id"] not in candidate_ids:
            continue
        keywords = json.loads(row["keywords_json"])
        semantic = dense_scores.get(row["article_id"], 0.0)
        lexical = lexical_scores[row["article_id"]]
        fuzzy = _fuzzy_coverage(set(query_tokens), set(document_tokens))
        keyword_hits = sum(bool(set(_tokens(keyword)).intersection(query_tokens)) for keyword in keywords)
        keyword = min(1.0, keyword_hits / max(1, min(3, len(keywords))))
        category_match = 1.0 if category and row["category"] == category else 0.0
        rrf = 0.5 / dense_ranks.get(row["article_id"], 20) + 0.5 / lexical_ranks[row["article_id"]]
        score = 0.46 * semantic + 0.28 * lexical + 0.08 * fuzzy + 0.08 * keyword + 0.06 * category_match + 0.04 * rrf
        # A top lexical hit in the predicted category is strong corroborating evidence,
        # so calibrate the combined confidence instead of treating the dense score alone as probability.
        if lexical >= 0.9 and category_match:
            score += 0.05
        if semantic < 0.15 and lexical == 0 and keyword == 0:
            score = 0.0
        results.append({
            "article_id": row["article_id"], "title": row["title"], "score": round(min(0.99, score), 3),
            "excerpt": row["excerpt"], "category": row["category"], "source": row["source"],
            "retrieval": {
                "method": "qdrant_dense_bm25_hybrid_rerank",
                "semantic_similarity": round(semantic, 3), "lexical_relevance": round(lexical, 3),
                "fuzzy_coverage": round(fuzzy, 3), "keyword_relevance": round(keyword, 3),
                "category_preference": bool(category_match), "fusion_score": round(rrf, 3),
                "dense_rank": dense_ranks.get(row["article_id"]), "lexical_rank": lexical_ranks[row["article_id"]],
                "embedding_model": embedding_model(), "vector_database": "Qdrant",
            },
        })
    results.sort(key=lambda item: (item["score"], item["retrieval"]["semantic_similarity"]), reverse=True)
    for rank, item in enumerate(results, 1):
        item["retrieval"]["reranked_position"] = rank
    return results[:max(1, min(top_k, len(results)))]


def knowledge_stats(db_path: Path | None = None) -> dict[str, Any]:
    articles = list_articles(db_path)
    return {
        "articles": len(articles), "built_in": sum(item["_source"] == "Built-in" for item in articles),
        "user_added": sum(item["_source"] != "Built-in" for item in articles),
        "database": str(db_path or database_path()), "vector_database": "Qdrant (local persistent mode)",
        "vector_database_path": str(qdrant_path(db_path)), "embedding_model": embedding_model(),
        "embedding_dimensions": embedding_dimensions(), "search_method": "Qdrant dense + BM25 hybrid rerank",
    }
